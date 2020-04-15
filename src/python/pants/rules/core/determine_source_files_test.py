# Copyright 2020 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from pathlib import PurePath
from typing import Iterable, List, NamedTuple, Optional, Tuple, Type
from unittest.mock import Mock

from pants.base.specs import (
    AscendantAddresses,
    DescendantAddresses,
    FilesystemLiteralSpec,
    FilesystemResolvedGlobSpec,
    OriginSpec,
    SiblingAddresses,
    SingleAddress,
)
from pants.build_graph.address import Address
from pants.build_graph.files import Files
from pants.engine.legacy.structs import TargetAdaptor
from pants.engine.selectors import Params
from pants.engine.target import Sources as SourcesField
from pants.engine.target import rules as target_rules
from pants.rules.core.determine_source_files import (
    AllSourceFilesRequest,
    LegacyAllSourceFilesRequest,
    SourceFiles,
    SpecifiedSourceFilesRequest,
)
from pants.rules.core.determine_source_files import rules as determine_source_files_rules
from pants.rules.core.strip_source_roots import rules as strip_source_roots_rules
from pants.rules.core.targets import FilesSources
from pants.testutil.option.util import create_options_bootstrapper
from pants.testutil.test_base import TestBase


class TargetSources(NamedTuple):
    source_root: str
    source_files: List[str]

    @property
    def source_file_absolute_paths(self) -> List[str]:
        return [PurePath(self.source_root, name).as_posix() for name in self.source_files]


SOURCES1 = TargetSources("src/python", ["s1.py", "s2.py", "s3.py"])
SOURCES2 = TargetSources("tests/python", ["t1.py", "t2.java"])
SOURCES3 = TargetSources("src/java", ["j1.java", "j2.java"])


class DetermineSourceFilesTest(TestBase):
    @classmethod
    def rules(cls):
        return (
            *super().rules(),
            *determine_source_files_rules(),
            *strip_source_roots_rules(),
            *target_rules(),
        )

    def mock_sources_field_with_origin(
        self,
        sources: TargetSources,
        *,
        origin: Optional[OriginSpec] = None,
        include_sources: bool = True,
        sources_field_cls: Type[SourcesField] = SourcesField,
    ) -> Tuple[SourcesField, OriginSpec]:
        sources_field = sources_field_cls(
            sources.source_files if include_sources else [],
            address=Address.parse(f"{sources.source_root}:lib"),
        )
        self.create_files(path=sources.source_root, files=sources.source_files)
        if origin is None:
            origin = SiblingAddresses(sources.source_root)
        return sources_field, origin

    def get_all_source_files(
        self,
        sources_fields_with_origins: Iterable[Tuple[SourcesField, OriginSpec]],
        *,
        strip_source_roots: bool = False,
    ) -> List[str]:
        request = AllSourceFilesRequest(
            (
                sources_field_with_origin[0]
                for sources_field_with_origin in sources_fields_with_origins
            ),
            strip_source_roots=strip_source_roots,
        )
        result = self.request_single_product(
            SourceFiles, Params(request, create_options_bootstrapper())
        )
        return sorted(result.snapshot.files)

    def get_specified_source_files(
        self,
        sources_fields_with_origins: Iterable[Tuple[SourcesField, OriginSpec]],
        *,
        strip_source_roots: bool = False,
    ) -> List[str]:
        request = SpecifiedSourceFilesRequest(
            sources_fields_with_origins, strip_source_roots=strip_source_roots,
        )
        result = self.request_single_product(
            SourceFiles, Params(request, create_options_bootstrapper())
        )
        return sorted(result.snapshot.files)

    def test_address_specs(self) -> None:
        sources_field1 = self.mock_sources_field_with_origin(
            SOURCES1, origin=SingleAddress(directory=SOURCES1.source_root, name="lib")
        )
        sources_field2 = self.mock_sources_field_with_origin(
            SOURCES2, origin=SiblingAddresses(SOURCES2.source_root)
        )
        sources_field3 = self.mock_sources_field_with_origin(
            SOURCES3, origin=DescendantAddresses(SOURCES3.source_root)
        )
        sources_field4 = self.mock_sources_field_with_origin(
            SOURCES1, origin=AscendantAddresses(SOURCES1.source_root)
        )

        def assert_all_source_files_resolved(
            sources_field_with_origin: Tuple[SourcesField, OriginSpec], sources: TargetSources
        ) -> None:
            expected = sources.source_file_absolute_paths
            assert self.get_all_source_files([sources_field_with_origin]) == expected
            assert self.get_specified_source_files([sources_field_with_origin]) == expected

        assert_all_source_files_resolved(sources_field1, SOURCES1)
        assert_all_source_files_resolved(sources_field2, SOURCES2)
        assert_all_source_files_resolved(sources_field3, SOURCES3)
        assert_all_source_files_resolved(sources_field4, SOURCES1)
        # NB: sources_field1 and sources_field3 refer to the same files. We should be able to
        # handle this gracefully.
        combined_sources_fields = [sources_field1, sources_field2, sources_field3, sources_field4]
        combined_expected = sorted(
            [
                *SOURCES1.source_file_absolute_paths,
                *SOURCES2.source_file_absolute_paths,
                *SOURCES3.source_file_absolute_paths,
            ]
        )
        assert self.get_all_source_files(combined_sources_fields) == combined_expected
        assert self.get_specified_source_files(combined_sources_fields) == combined_expected

    def test_filesystem_specs(self) -> None:
        # Literal file arg.
        sources_field1_all_sources = SOURCES1.source_file_absolute_paths
        sources_field1_slice = slice(0, 1)
        sources_field1 = self.mock_sources_field_with_origin(
            SOURCES1, origin=FilesystemLiteralSpec(sources_field1_all_sources[0])
        )

        # Glob file arg that matches the entire `sources`.
        sources_field2_all_sources = SOURCES2.source_file_absolute_paths
        sources_field2_slice = slice(0, len(sources_field2_all_sources))
        sources_field2_origin = FilesystemResolvedGlobSpec(
            f"{SOURCES2.source_root}/*.py", files=tuple(sources_field2_all_sources)
        )
        sources_field2 = self.mock_sources_field_with_origin(SOURCES2, origin=sources_field2_origin)

        # Glob file arg that only matches a subset of the `sources` _and_ includes resolved
        # files not owned by the target.
        sources_field3_all_sources = SOURCES3.source_file_absolute_paths
        sources_field3_slice = slice(0, 1)
        sources_field3_origin = FilesystemResolvedGlobSpec(
            f"{SOURCES3.source_root}/*.java",
            files=tuple(
                PurePath(SOURCES3.source_root, name).as_posix()
                for name in [SOURCES3.source_files[0], "other_target.java", "j.tmp.java"]
            ),
        )
        sources_field3 = self.mock_sources_field_with_origin(SOURCES3, origin=sources_field3_origin)

        def assert_file_args_resolved(
            sources_field_with_origin: Tuple[SourcesField, OriginSpec],
            all_sources: List[str],
            expected_slice: slice,
        ) -> None:
            assert self.get_all_source_files([sources_field_with_origin]) == all_sources
            assert (
                self.get_specified_source_files([sources_field_with_origin])
                == all_sources[expected_slice]
            )

        assert_file_args_resolved(sources_field1, sources_field1_all_sources, sources_field1_slice)
        assert_file_args_resolved(sources_field2, sources_field2_all_sources, sources_field2_slice)
        assert_file_args_resolved(sources_field3, sources_field3_all_sources, sources_field3_slice)

        combined_sources_fields = [sources_field1, sources_field2, sources_field3]
        assert self.get_all_source_files(combined_sources_fields) == sorted(
            [*sources_field1_all_sources, *sources_field2_all_sources, *sources_field3_all_sources]
        )
        assert self.get_specified_source_files(combined_sources_fields) == sorted(
            [
                *sources_field1_all_sources[sources_field1_slice],
                *sources_field2_all_sources[sources_field2_slice],
                *sources_field3_all_sources[sources_field3_slice],
            ]
        )

    def test_strip_source_roots(self) -> None:
        sources_field1 = self.mock_sources_field_with_origin(SOURCES1)
        sources_field2 = self.mock_sources_field_with_origin(SOURCES2)
        sources_field3 = self.mock_sources_field_with_origin(SOURCES3)

        def assert_source_roots_stripped(
            sources_field_with_origin: Tuple[SourcesField, OriginSpec], sources: TargetSources
        ) -> None:
            expected = sources.source_files
            assert (
                self.get_all_source_files([sources_field_with_origin], strip_source_roots=True)
                == expected
            )
            assert (
                self.get_specified_source_files(
                    [sources_field_with_origin], strip_source_roots=True
                )
                == expected
            )

        assert_source_roots_stripped(sources_field1, SOURCES1)
        assert_source_roots_stripped(sources_field2, SOURCES2)
        assert_source_roots_stripped(sources_field3, SOURCES3)

        # We must be careful to not strip source roots for `FilesSources`.
        files_sources_field = self.mock_sources_field_with_origin(
            SOURCES1, sources_field_cls=FilesSources
        )
        files_expected = SOURCES1.source_file_absolute_paths

        assert (
            self.get_all_source_files([files_sources_field], strip_source_roots=True)
            == files_expected
        )
        assert (
            self.get_specified_source_files([files_sources_field], strip_source_roots=True)
            == files_expected
        )

        combined_sources_fields = [
            sources_field1,
            sources_field2,
            sources_field3,
            files_sources_field,
        ]
        combined_expected = sorted(
            [
                *SOURCES1.source_files,
                *SOURCES2.source_files,
                *SOURCES3.source_files,
                *files_expected,
            ],
        )
        assert (
            self.get_all_source_files(combined_sources_fields, strip_source_roots=True)
            == combined_expected
        )
        assert (
            self.get_specified_source_files(combined_sources_fields, strip_source_roots=True)
            == combined_expected
        )

    def test_gracefully_handle_no_sources(self) -> None:
        sources_field = self.mock_sources_field_with_origin(SOURCES1, include_sources=False)
        assert self.get_all_source_files([sources_field]) == []
        assert self.get_specified_source_files([sources_field]) == []
        assert self.get_all_source_files([sources_field], strip_source_roots=True) == []
        assert self.get_specified_source_files([sources_field], strip_source_roots=True) == []


class LegacyDetermineSourceFilesTest(TestBase):
    @classmethod
    def rules(cls):
        return (
            *super().rules(),
            *determine_source_files_rules(),
            *strip_source_roots_rules(),
            *target_rules(),
        )

    def mock_target(
        self,
        sources: TargetSources,
        *,
        include_sources: bool = True,
        type_alias: Optional[str] = None,
    ) -> TargetAdaptor:
        sources_field = Mock()
        sources_field.snapshot = self.make_snapshot_of_empty_files(
            sources.source_file_absolute_paths if include_sources else []
        )
        return TargetAdaptor(
            address=Address.parse(f"{sources.source_root}:lib"),
            type_alias=type_alias,
            sources=sources_field,
        )

    def get_all_source_files(
        self, adaptors: Iterable[TargetAdaptor], *, strip_source_roots: bool = False,
    ) -> List[str]:
        request = LegacyAllSourceFilesRequest(
            (adaptor for adaptor in adaptors), strip_source_roots=strip_source_roots
        )
        result = self.request_single_product(
            SourceFiles, Params(request, create_options_bootstrapper())
        )
        return sorted(result.snapshot.files)

    def test_address_specs(self) -> None:
        target1 = self.mock_target(SOURCES1)
        target2 = self.mock_target(SOURCES2)
        target3 = self.mock_target(SOURCES3)
        target4 = self.mock_target(SOURCES1)

        def assert_all_source_files_resolved(target: TargetAdaptor, sources: TargetSources) -> None:
            expected = sources.source_file_absolute_paths
            assert self.get_all_source_files([target]) == expected

        assert_all_source_files_resolved(target1, SOURCES1)
        assert_all_source_files_resolved(target2, SOURCES2)
        assert_all_source_files_resolved(target3, SOURCES3)
        assert_all_source_files_resolved(target4, SOURCES1)
        # NB: target1 and target4 refer to the same files. We should be able to handle this
        # gracefully.
        combined_targets = [target1, target2, target3, target4]
        combined_expected = sorted(
            [
                *SOURCES1.source_file_absolute_paths,
                *SOURCES2.source_file_absolute_paths,
                *SOURCES3.source_file_absolute_paths,
            ]
        )
        assert self.get_all_source_files(combined_targets) == combined_expected

    def test_filesystem_specs(self) -> None:
        target1_all_sources = SOURCES1.source_file_absolute_paths
        target1 = self.mock_target(SOURCES1)

        target2_all_sources = SOURCES2.source_file_absolute_paths
        target2 = self.mock_target(SOURCES2)

        target3_all_sources = SOURCES3.source_file_absolute_paths
        target3 = self.mock_target(SOURCES3)

        def assert_file_args_resolved(target: TargetAdaptor, all_sources: List[str]) -> None:
            assert self.get_all_source_files([target]) == all_sources

        assert_file_args_resolved(target1, target1_all_sources)
        assert_file_args_resolved(target2, target2_all_sources)
        assert_file_args_resolved(target3, target3_all_sources)

        combined_targets = [target1, target2, target3]
        assert self.get_all_source_files(combined_targets) == sorted(
            [*target1_all_sources, *target2_all_sources, *target3_all_sources]
        )

    def test_strip_source_roots(self) -> None:
        target1 = self.mock_target(SOURCES1)
        target2 = self.mock_target(SOURCES2)
        target3 = self.mock_target(SOURCES3)

        # We must be careful to not strip source roots for `files` targets.
        files_target = self.mock_target(SOURCES1, type_alias=Files.alias())
        files_expected = SOURCES1.source_file_absolute_paths

        def assert_source_roots_stripped(target: TargetAdaptor, sources: TargetSources) -> None:
            expected = sources.source_files
            assert self.get_all_source_files([target], strip_source_roots=True) == expected

        assert_source_roots_stripped(target1, SOURCES1)
        assert_source_roots_stripped(target2, SOURCES2)
        assert_source_roots_stripped(target3, SOURCES3)

        assert self.get_all_source_files([files_target], strip_source_roots=True) == files_expected

        combined_targets = [target1, target2, target3, files_target]
        combined_expected = sorted(
            [
                *SOURCES1.source_files,
                *SOURCES2.source_files,
                *SOURCES3.source_files,
                *files_expected,
            ],
        )
        assert (
            self.get_all_source_files(combined_targets, strip_source_roots=True)
            == combined_expected
        )

    def test_gracefully_handle_no_sources(self) -> None:
        target = self.mock_target(SOURCES1, include_sources=False)
        assert self.get_all_source_files([target]) == []
        assert self.get_all_source_files([target], strip_source_roots=True) == []