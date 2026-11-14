"""Public import-free discovery and validators consuming its frozen result."""

import sys
from collections.abc import Callable, Sequence
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import pytest

from deprecate._static.discovery import _StaticSource
from deprecate._static.rows import _read_static
from deprecate.audit import (
    DeprecationWrapperInfo,
    StaticScanResult,
    find_deprecation_wrappers_static,
    generate_deprecation_table,
    validate_deprecation_expiry,
    validate_deprecation_policy,
)
from deprecate.audit._report import _format_report_target
from tests.collections import policy, static_forms

_COLLECTIONS = Path(policy.__file__).parent
_SOURCE = (
    "from deprecate import deprecated\n"
    "def replacement(): pass\n"
    "@deprecated(target=replacement, deprecated_in='1.0', remove_in='2.0')\n"
    "def old(): pass\n"
)


def _row_values(rows: Sequence[DeprecationWrapperInfo]) -> list[Any]:
    """Compare every row field while comparing synthetic targets by their displayed source name."""
    return [
        (
            replace(info, deprecated_info=replace(info.deprecated_info, target=None)),
            _format_report_target(info.deprecated_info.target),
            info.source_file,
            info.source_line,
        )
        for info in rows
    ]


@pytest.fixture
def static_package(tmp_path: Path) -> Path:
    """Create an unimportable package with a literal project version and one deprecated callable."""
    package = tmp_path / "static_api_probe"
    package.mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname="static-api-probe"\nversion="3.0"\n')
    (package / "__init__.py").write_text("raise RuntimeError('must not import')\n" + _SOURCE)
    return package


def test_collection_rows_and_ordered_gaps() -> None:
    """Return the reader's rows and structural gaps followed by each distinct unresolved setting.

    Agent tooling reads all settings in one scan, so a shared unresolved splat is reported once per row and guidance
    gaps remain visible even when no expiry check is requested.
    """
    path = _COLLECTIONS / "static_forms.py"
    rows, structural = _read_static(
        _StaticSource("module", "static_forms", path, path.parent),
        recursive=True,
        include_members=True,
        exclude=[],
    )
    result = find_deprecation_wrappers_static(path)
    assert _row_values(result.wrappers) == _row_values(rows)
    assert result.gaps[: len(structural)] == tuple(f"{gap.kind.value}: {gap.detail}" for gap in structural)
    assert len(result.gaps) == len(structural) + 4
    assert [gap.split(" (Callable `")[-1] for gap in result.gaps[len(structural) :]] == [
        "static_forms.ambiguous_removal` not checked)",
        "static_forms.called_splat` not checked)",
        "static_forms.formatted_removal` not checked)",
        "static_forms.looked_up_target` not checked)",
    ]
    assert "`remove_in` is `REMOVAL`, not a literal" in result.gaps[1]
    assert "arguments come from `**dict(deprecated_in='1.0', remove_in='2.0')`" in result.gaps[2]
    assert "`target` is `_REPLACEMENTS['double']`, not a literal or a name" in result.gaps[4]


@pytest.mark.parametrize("by_name", [False, True])
def test_scan_never_imports_package(static_package: Path, monkeypatch: pytest.MonkeyPatch, by_name: bool) -> None:
    """Read wrappers and a literal version without executing a raising package initializer.

    A docs build can locate source by path or top-level module name even when importing the package would fail.
    The version lookup must respect that same boundary.
    """
    monkeypatch.syspath_prepend(str(static_package.parent))
    result = find_deprecation_wrappers_static(static_package.name if by_name else static_package)
    assert [row.function for row in result.wrappers] == ["old"]
    assert result.version == (None if by_name else "3.0")
    assert result.gaps == ()
    assert static_package.name not in sys.modules
    with pytest.raises(TypeError, match="cannot be called"):
        result.wrappers[0].deprecated_info.target()
    with pytest.raises(FrozenInstanceError):
        result.version = "9.0"  # type: ignore[misc]


@pytest.mark.parametrize("location", ["missing.child", "sys"])
def test_location_error(location: str) -> None:
    """Raise a usage error as ValueError instead of printing or importing parents.

    Library callers need a catchable error for dotted names and modules with no Python source.
    """
    with pytest.raises(ValueError, match="parent packages|no Python source"):
        find_deprecation_wrappers_static(location)


def test_non_python_file_error(tmp_path: Path) -> None:
    """Reject an existing file without Python source.

    Passing an artifact path must not produce an apparently clean empty scan.
    """
    path = tmp_path / "artifact.txt"
    path.touch()
    with pytest.raises(ValueError, match="no Python source"):
        find_deprecation_wrappers_static(path)


@pytest.mark.parametrize(
    ("module", "expiry_exempt"),
    [
        pytest.param(policy, (), id="policy"),
        pytest.param(
            static_forms,
            ("ambiguous_removal", "formatted_removal", "called_splat", "partial_built"),
            id="static-forms",
        ),
    ],
)
def test_validator_parity(module: Any, expiry_exempt: tuple[str, ...]) -> None:  # noqa: ANN401
    """Preserve the imported validators' messages and order on literal collections.

    Teams can switch discovery mode without rewriting their CI assertions when every setting needed by a rule is
    settled. Use the same expiry exceptions as the existing CLI collection parity tests: three unresolved settings
    and an unsupported factory shape. Preserve the imported module's dotted row names for comparison.
    """
    result = find_deprecation_wrappers_static(Path(module.__file__))
    result = replace(result, wrappers=tuple(replace(row, module=module.__name__) for row in result.wrappers))
    imported_expiry = validate_deprecation_expiry(module, "99.0")
    assert validate_deprecation_expiry(result, "99.0") == [
        message for message in imported_expiry if not any(f".{name}`" in message for name in expiry_exempt)
    ]
    assert validate_deprecation_policy(result) == validate_deprecation_policy(module)


def test_unresolved_settings_are_skipped_per_rule(tmp_path: Path) -> None:
    """Leave unresolved settings out of only the rules that depend on them.

    One source has an unknown removal but no guidance; another has a literal short grace window and an unknown
    target. Each still produces the independently provable policy violation, while expiry skips the unknown date.
    """
    path = tmp_path / "mixed.py"
    path.write_text(
        "from deprecate import deprecated\nVERSION = '2.0'\nVERSION = '3.0'\n"
        "@deprecated(deprecated_in='1.0', remove_in=VERSION)\ndef missing_guidance(): pass\n"
        "@deprecated(target=lookup(), deprecated_in='1.0', remove_in='1.1')\ndef short_grace(): pass\n"
    )
    result = find_deprecation_wrappers_static(path)
    assert len(result.gaps) == 2
    assert validate_deprecation_expiry(result, "9.0") == [
        "Callable `mixed.short_grace` was scheduled for removal in version 1.1 but still exists in version 9.0. "
        "Please delete this deprecated code."
    ]
    violations = validate_deprecation_policy(result)
    assert len(violations) == 2
    assert violations[0].startswith("[message-required] Callable `mixed.missing_guidance`")
    assert violations[1].startswith("[min-grace] Callable `mixed.short_grace`")


def test_version_precedence(static_package: Path) -> None:
    """Use a result's version unless an explicit current version overrides it.

    A release check can compare the same pre-scanned rows against an upcoming version without another scan or import.
    """
    result = find_deprecation_wrappers_static(static_package)
    assert len(validate_deprecation_expiry(result)) == 1
    assert validate_deprecation_expiry(result, "1.0") == []
    assert generate_deprecation_table(result).startswith("<!-- Current version: 3.0 -->")
    assert generate_deprecation_table(result, "1.0").startswith("<!-- Current version: 1.0 -->")


def test_missing_version(static_package: Path) -> None:
    """Raise for expiry and render unknown table status when the result has no version.

    Pre-scanned rows cannot fall back to importing a package to obtain missing metadata; policy does not need the
    current version and continues to run.
    """
    result = replace(find_deprecation_wrappers_static(static_package), version=None)
    with pytest.raises(ImportError, match="[Cc]ould not determine version"):
        validate_deprecation_expiry(result)
    assert "Status Unknown" in generate_deprecation_table(result)
    assert validate_deprecation_policy(result) == []


def test_invalid_detected_version(static_package: Path) -> None:
    """Render an invalid detected version as unknown while rejecting an invalid explicit version.

    A source checkout may stamp an arbitrary development label into metadata; tables retain that label without
    treating it as an explicitly requested comparison version.
    """
    result = replace(find_deprecation_wrappers_static(static_package), version="development")
    table = generate_deprecation_table(result)
    assert table.startswith("<!-- Current version: development -->")
    assert "Status Unknown" in table
    with pytest.raises(ValueError, match="Invalid current_version"):
        generate_deprecation_table(result, "development")
    with pytest.raises(ValueError, match="Invalid current_version"):
        validate_deprecation_expiry(result)


def test_consumers_reuse_rows_after_source_removed(static_package: Path) -> None:
    """Consume saved rows even when the original package source is no longer readable.

    A scan result is a snapshot: validators and reporting must not rediscover wrappers or try importing a module
    after discovery has finished.
    """
    result = find_deprecation_wrappers_static(static_package)
    (static_package / "__init__.py").unlink()
    assert len(validate_deprecation_expiry(result)) == 1
    assert validate_deprecation_policy(result) == []
    assert "static_api_probe.old" in generate_deprecation_table(result)
    assert static_package.name not in sys.modules


@pytest.mark.parametrize("style", ["compact", "matrix"])
def test_table_without_import(static_package: Path, style: str) -> None:
    """Render stand-in target names and static rows with the existing table renderer.

    A documentation job must render both table styles despite the package initializer refusing to import.
    """
    result = find_deprecation_wrappers_static(static_package)
    table = generate_deprecation_table(result, style=style)
    assert "static_api_probe.old" in table
    assert "replacement" in table
    assert static_package.name not in sys.modules


@pytest.mark.parametrize(
    "consumer",
    [
        pytest.param(validate_deprecation_expiry, id="expiry"),
        pytest.param(validate_deprecation_policy, id="policy"),
        pytest.param(generate_deprecation_table, id="table"),
    ],
)
@pytest.mark.parametrize("option", ["recursive", "include_members"])
def test_option_conflicts(consumer: Callable[..., Any], option: str, static_package: Path) -> None:
    """Reject discovery options that would silently reinterpret a completed scan.

    Callers must select scan depth and member inclusion when collecting the rows, before passing the result to a
    validator or renderer.
    """
    result = find_deprecation_wrappers_static(static_package)
    with pytest.raises(ValueError, match="fixed|scan"):
        consumer(result, **{option: False})


def test_exclude_filters_scan_and_consumers(static_package: Path) -> None:
    """Apply module exclusion both during discovery and when consuming saved rows.

    A caller can reuse a complete scan for several checks that exclude different internal modules.
    """
    (static_package / "extra.py").write_text(_SOURCE)
    result = find_deprecation_wrappers_static(static_package)
    patterns = ["*.extra"]
    filtered = find_deprecation_wrappers_static(static_package, exclude=patterns)
    assert [row.module for row in filtered.wrappers] == [static_package.name]
    assert validate_deprecation_expiry(result, exclude=patterns) == validate_deprecation_expiry(filtered)
    assert validate_deprecation_policy(result, exclude=patterns) == validate_deprecation_policy(filtered)
    assert generate_deprecation_table(result, exclude=patterns) == generate_deprecation_table(filtered)


def test_scan_options(static_package: Path) -> None:
    """Honor discovery depth and member selection on a package path.

    A caller wanting top-level functions can trim its scan before validators receive the fixed result.
    """
    (static_package / "extra.py").write_text(_SOURCE)
    (static_package / "members.py").write_text(
        "from deprecate import deprecated\nclass API:\n"
        "    @deprecated(deprecated_in='1.0', remove_in='2.0')\n    def old(self): pass\n"
    )
    assert len(find_deprecation_wrappers_static(static_package).wrappers) == 3
    assert len(find_deprecation_wrappers_static(static_package, recursive=False).wrappers) == 1
    assert len(find_deprecation_wrappers_static(static_package, include_members=False).wrappers) == 2


def test_empty_rows_do_not_hide_gaps(tmp_path: Path) -> None:
    """Keep structural gaps visible when no rows can be read.

    An invalid source file is an incomplete audit, even when there are no wrappers to pass to validators.
    """
    (tmp_path / "broken.py").write_text("def broken(:\n")
    result = find_deprecation_wrappers_static(tmp_path)
    assert isinstance(result, StaticScanResult)
    assert result.wrappers == ()
    assert len(result.gaps) == 1
    assert result.gaps[0].startswith("module not audited:")
