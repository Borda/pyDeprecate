"""Markdown report generation: :func:`~deprecate.audit.generate_deprecation_table`.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import enum
import inspect
from collections.abc import Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:
    from packaging.version import Version

from deprecate._types import TargetMode, get_deprecation_config
from deprecate.audit._lifecycle import (
    _format_version,
    _get_deprecation_status,
    _get_package_version,
    _parse_version,
    _safe_parse_version,
)
from deprecate.audit._scan import find_deprecation_wrappers
from deprecate.audit._wrappers import DeprecationWrapperInfo, _format_report_symbol
from deprecate.proxy import _DeprecatedProxy


class TableStyle(str, enum.Enum):
    """Markdown table layout produced by :func:`~deprecate.audit.generate_deprecation_table`."""

    COMPACT = "compact"
    MATRIX = "matrix"


def _resolve_table_version(
    module: Union[Any, str],  # noqa: ANN401
    *,
    current_version: Optional[str],
    version_explicit: bool = True,
) -> tuple[Optional[str], Optional["Version"]]:
    """Resolve report version string and optional parsed version object.

    An unparsable version is fatal only when the caller typed it: *version_explicit* ``False`` marks a
    *current_version* the caller auto-detected on this function's behalf, which degrades to an unparsed
    version string exactly like the auto-detection performed here does.

    """
    module_name = module if isinstance(module, str) else getattr(module, "__name__", None)
    resolved_version = current_version

    if resolved_version is None and module_name:
        with suppress(ImportError):
            resolved_version = _get_package_version(module_name.split(".")[0])

    if resolved_version is None:
        return None, None

    try:
        return resolved_version, _parse_version(resolved_version)
    except ImportError:
        return resolved_version, None
    except ValueError as err:
        if current_version is not None and version_explicit:
            raise ValueError(f"Invalid current_version '{current_version}': {err}") from err
        return resolved_version, None


def _format_report_target(target: Any) -> str:  # noqa: ANN401
    """Format replacement target name for report rows."""
    if target is None or target is TargetMode.NOTIFY:
        return "—"
    if isinstance(target, TargetMode):
        return target.value
    if inspect.ismodule(target):
        return getattr(target, "__name__", str(target))
    if isinstance(target, _DeprecatedProxy):
        # A chained-proxy target may have been created before the v0.13 metadata split. The public
        # accessor checks both layouts without invoking the proxy's dynamic forwarding path.
        config = get_deprecation_config(target)
        return config.name if config is not None else type(target).__name__
    if callable(target):
        target_module = getattr(target, "__module__", "")
        target_name = getattr(target, "__qualname__", getattr(target, "__name__", str(target)))
        return f"{target_module}.{target_name}" if target_module else target_name
    return str(target)


def _format_report_api_type(info: DeprecationWrapperInfo) -> str:
    """Return api_type with backward-compatible fallback."""
    if info.api_type:
        return info.api_type
    return "args" if info.deprecated_info.args_mapping else "callable"


def _report_row_sort_key(info: DeprecationWrapperInfo) -> tuple[str, str, str, bool, str]:
    """Sort report rows by module and symbol family, keeping args-variants adjacent."""
    function = info.function or ""
    top_level = function.split(".", maxsplit=1)[0] if function else ""
    api_type = _format_report_api_type(info)
    return (info.module or "", top_level, function, api_type.endswith(" args"), api_type)


def _format_matrix_row(info: DeprecationWrapperInfo, col_idx: dict[str, int], n_versions: int) -> str:
    """Format one matrix-style table row for a deprecated wrapper."""
    markers: list[str] = [" "] * n_versions
    dep_in = info.deprecated_info.deprecated_in
    rem_in = info.deprecated_info.remove_in
    if dep_in and dep_in in col_idx:
        markers[col_idx[dep_in]] = "D"
    if rem_in and rem_in in col_idx:
        i = col_idx[rem_in]
        markers[i] = "R" if markers[i] == " " else "D/R"
    return (
        "| "
        f"`{_format_report_symbol(info)}` | "
        f"{_format_report_api_type(info)} | "
        f"`{_format_report_target(info.deprecated_info.target)}` | " + " | ".join(markers) + " |"
    )


def generate_deprecation_table(
    module: Union[Any, str],  # noqa: ANN401
    current_version: Optional[str] = None,
    recursive: bool = True,
    style: Union[TableStyle, str] = TableStyle.COMPACT,
    include_members: bool = True,
    *,
    exclude: Optional[Sequence[str]] = None,
    _wrappers: Optional[list["DeprecationWrapperInfo"]] = None,
    _version_explicit: bool = True,
) -> str:
    """Generate a markdown table summarizing deprecated wrappers.

    The table is derived from ``__deprecation_config__`` metadata and includes both
    top-level wrappers and deprecated class members (methods/constructors).

    Args:
        module: Imported module/package object or string module path to scan.
        current_version: Optional current package version for lifecycle status
            evaluation in compact style. If ``None``, auto-detection is attempted
            via the package name; status falls back to ``"⚪ Status Unknown"`` when
            ``packaging`` is not installed.
        recursive: If True (default), include submodules in the scan.
        style: Table format — ``"compact"`` or ``"matrix"``.
            - ``"compact"``: ``Original API | API Type | New API | Deprecated | Remove | Current Status``
            - ``"matrix"``: ``Original API | API Type | New API | <all versions...>``, with markers
              ``D`` (deprecated) and ``R`` (remove) in version columns.
        include_members: If True (default), include deprecated class members (methods, constructors).
        exclude: Glob patterns over full dotted module names to leave out of the scan, as in
            :func:`~deprecate.audit.find_deprecation_wrappers` (e.g. ``["my_package.tests"]``); ``None`` excludes
            nothing.
        _version_explicit: Whether ``current_version`` was typed by the caller rather than auto-detected
            on its behalf. A caller that resolves the version itself (the CLI's single-scan path) passes
            ``False`` so an unparsable one degrades to an unparsed version string — the same fallback this
            function applies to a version it auto-detects — instead of raising. Underscore prefix marks it
            internal; it is not part of the public signature.

    Returns:
        Markdown string containing a formatted table. When a version is
        resolvable (either from current_version or auto-detected), the
        first line is an HTML comment <!-- Current version: X.Y -->
        followed by the header row and alignment row. When no version can be
        resolved, the first line is the header row directly.

    Raises:
        ValueError: If ``style`` is not ``"compact"`` or ``"matrix"``, or if
            ``current_version`` is supplied explicitly but is not a valid PEP 440
            version string and ``packaging`` is installed.

    Example:
        >>> from tests import collection_deprecate as pkg
        >>> report = generate_deprecation_table(pkg, recursive=False)
        >>> report.splitlines()[0]
        '| Original API | API Type | New API | Deprecated | Remove | Current Status |'

    """
    try:
        style = TableStyle(style)
    except ValueError as err:
        raise ValueError(
            f"Invalid style {style!r}. Expected one of: {', '.join(s.value for s in TableStyle)}."
        ) from err

    resolved_version, parsed_version = _resolve_table_version(
        module, current_version=current_version, version_explicit=_version_explicit
    )
    if _wrappers is None:
        _wrappers = find_deprecation_wrappers(
            module, recursive=recursive, include_members=include_members, exclude=exclude
        )
    wrappers = sorted(
        _wrappers,
        key=_report_row_sort_key,
    )

    if style == TableStyle.COMPACT:
        rows = [
            "| Original API | API Type | New API | Deprecated | Remove | Current Status |",
            "| :--- | :--- | :--- | :---: | :---: | :--- |",
        ]

        for info in wrappers:
            rows.append(
                "| "
                f"`{_format_report_symbol(info)}` | "
                f"{_format_report_api_type(info)} | "
                f"`{_format_report_target(info.deprecated_info.target)}` | "
                f"{_format_version(info.deprecated_info.deprecated_in)} | "
                f"{_format_version(info.deprecated_info.remove_in)} | "
                f"{_get_deprecation_status(info, parsed_version).value} |"
            )
    else:
        version_map: dict[str, Optional[Version]] = {}
        for info in wrappers:
            for version in (info.deprecated_info.deprecated_in, info.deprecated_info.remove_in):
                if version and version not in version_map:
                    version_map[version] = _safe_parse_version(version)

        sorted_versions = sorted(
            version_map,
            key=lambda version: (
                version_map[version] is None,
                version_map[version] if version_map[version] is not None else version,
            ),
        )
        version_headers = [_format_version(version) for version in sorted_versions]
        header_row = "| Original API | API Type | New API | " + " | ".join(version_headers) + " |"
        divider_row = "| :--- | :--- | :--- | " + " | ".join(":---:" for _ in version_headers) + " |"
        col_idx = {v: i for i, v in enumerate(sorted_versions)}
        n_versions = len(sorted_versions)
        rows = [header_row, divider_row]

        for info in wrappers:
            rows.append(_format_matrix_row(info, col_idx, n_versions))

    if resolved_version is not None:
        rows.insert(0, f"<!-- Current version: {resolved_version} -->")

    return "\n".join(rows)
