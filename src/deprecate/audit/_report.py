"""Markdown report generation: :func:`~deprecate.audit.generate_deprecation_table`.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import enum
import inspect
from collections.abc import Callable, Sequence
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
from deprecate.audit._wrappers import DeprecationWrapperInfo, _ApiType, _format_report_symbol
from deprecate.messaging import _callable_display_name
from deprecate.proxy import _DeprecatedProxy
from deprecate.utils import _partial_chain


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
        return _format_callable_target(target)
    # A plain data replacement (``deprecated_instance(target=...)``): its type, never its contents, which may span
    # lines, contain ``|`` and carry configuration values.
    return type(target).__name__


def _format_callable_target(target: Callable[..., Any]) -> str:
    """Name a callable replacement target by its dotted path, never by a memory-address ``repr``.

    A ``functools.partial`` is named by the callable it wraps, so ``partial(pkg.new, b=7)`` reports ``pkg.new`` like the
    runtime warning does. ``__qualname__`` is preferred so a method keeps its class (``pkg.Cls.meth``); an object with
    no string ``__qualname__``/``__name__`` of its own, such as a callable instance, is named the way the runtime
    warning names it (:func:`~deprecate.messaging._callable_display_name`).

    """
    chain = _partial_chain(target)
    base = chain[-1].func if chain else target
    name = getattr(base, "__qualname__", None)
    if not isinstance(name, str):
        name = getattr(base, "__name__", None)
    if not isinstance(name, str):
        return _callable_display_name(base)[1]
    module = getattr(base, "__module__", None)
    return f"{module}.{name}" if isinstance(module, str) and module else name


def _format_report_api_type(info: DeprecationWrapperInfo) -> str:
    """Return api_type with backward-compatible fallback."""
    if info.api_type:
        return info.api_type
    return (_ApiType.ARGS if info.deprecated_info.args_mapping else _ApiType.CALLABLE).value


def _report_row_sort_key(info: DeprecationWrapperInfo) -> tuple[str, str, str, bool, str]:
    """Sort report rows by module and symbol family, keeping args-variants adjacent."""
    function = info.function or ""
    top_level = function.split(".", maxsplit=1)[0] if function else ""
    api_type = _format_report_api_type(info)
    return (info.module or "", top_level, function, api_type.endswith(" args"), api_type)


def _matrix_columns(wrappers: Sequence[DeprecationWrapperInfo]) -> tuple[list[str], dict[str, int]]:
    """Return the matrix version headers and the column of every raw version string, one column per release.

    Spellings of one release (``1.0``, ``v1.0``, ``1.0.0``) share a column: versions are keyed by their parsed
    ``Version``, or by their ``v``-prefixed text when they do not parse (or ``packaging`` is missing, in which case only
    spellings that differ by the ``v`` prefix merge). Parsed releases come first in release order, then the rest by
    text. A merged column is headed by its shortest spelling, so the header does not depend on row order.

    """
    # many wrappers share the same few releases: parse each distinct raw string once, not once per occurrence
    raw_versions = {
        raw for info in wrappers for raw in (info.deprecated_info.deprecated_in, info.deprecated_info.remove_in) if raw
    }
    spellings: dict[Union[Version, str], set[str]] = {}
    for raw in raw_versions:
        parsed = _safe_parse_version(raw)
        spellings.setdefault(parsed if parsed is not None else _format_version(raw), set()).add(raw)
    # the first item tells parsed releases from unparsed text, so a Version is never compared with a str
    keys = sorted(spellings, key=lambda key: (isinstance(key, str), key))
    headers = [_format_version(min(spellings[key], key=lambda raw: (len(raw), raw))) for key in keys]
    col_idx = {raw: idx for idx, key in enumerate(keys) for raw in spellings[key]}
    return headers, col_idx


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
) -> str:
    """Generate a markdown table summarizing deprecated wrappers.

    The table is derived from ``__deprecation_config__`` metadata and includes both
    top-level wrappers and deprecated class members (methods/constructors).

    Args:
        module: Imported module/package object or string module path to scan.
        current_version: Optional current package version for lifecycle status
            evaluation in compact style. If ``None``, the version is auto-detected
            like ``validate_deprecation_expiry`` does; status falls back to
            ``"⚪ Status Unknown"`` when it stays unresolved or ``packaging`` is not
            installed.
        recursive: If True (default), include submodules in the scan.
        style: Table format — ``"compact"`` or ``"matrix"``.
            - ``"compact"``: ``Original API | API Type | New API | Deprecated | Remove | Current Status``
            - ``"matrix"``: ``Original API | API Type | New API | <all versions...>``, with markers
              ``D`` (deprecated) and ``R`` (remove) in version columns; ``D/R`` marks a release that is both
              deprecated and removed. There is one column per release: spellings such as ``1.0``, ``v1.0`` and
              ``1.0.0`` merge into one column when ``packaging`` is installed, and without it only the ``v``
              prefix is ignored.
        include_members: If True (default), include deprecated class members (methods, constructors).
        exclude: Glob patterns over full dotted module names to leave out of the scan, as in
            :func:`~deprecate.audit.find_deprecation_wrappers` (e.g. ``["my_package.tests"]``); ``None`` excludes
            nothing.

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
        >>> from tests.collections.api_deprecated import core as pkg
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

    resolved_version, parsed_version = _resolve_table_version(module, current_version=current_version)
    wrappers = find_deprecation_wrappers(module, recursive=recursive, include_members=include_members, exclude=exclude)
    return _render_deprecation_table(wrappers, resolved_version, parsed_version, style)


def _render_deprecation_table(
    wrappers: Sequence[DeprecationWrapperInfo],
    resolved_version: Optional[str],
    parsed_version: Optional["Version"],
    style: TableStyle,
) -> str:
    """Render the markdown deprecation table of already-scanned wrappers.

    The rendering half of :func:`generate_deprecation_table`, which the CLI calls on the wrappers of its own scan
    with a version it resolved through :func:`_resolve_table_version`.

    Args:
        wrappers: Scanned wrappers, in any order; rows are sorted by module and symbol family.
        resolved_version: Version string named in the leading ``<!-- Current version: X -->`` comment, or ``None``
            to leave the comment out.
        parsed_version: *resolved_version* parsed, for the compact style's status column; ``None`` when it is
            unresolved, unparsable or ``packaging`` is missing.
        style: Table layout.

    Returns:
        The markdown table, as documented on :func:`generate_deprecation_table`.

    """
    wrappers = sorted(wrappers, key=_report_row_sort_key)

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
        version_headers, col_idx = _matrix_columns(wrappers)
        header_row = "| Original API | API Type | New API | " + " | ".join(version_headers) + " |"
        divider_row = "| :--- | :--- | :--- | " + " | ".join(":---:" for _ in version_headers) + " |"
        n_versions = len(version_headers)
        rows = [header_row, divider_row]

        for info in wrappers:
            rows.append(_format_matrix_row(info, col_idx, n_versions))

    if resolved_version is not None:
        rows.insert(0, f"<!-- Current version: {resolved_version} -->")

    return "\n".join(rows)
