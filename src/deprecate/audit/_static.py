"""Import-free discovery: :func:`find_deprecation_wrappers_static` and its frozen :class:`StaticScanResult`.

The scan reads a package's source with :mod:`ast` and imports nothing from it. The reader in :mod:`deprecate._static` is
loaded only when the scan runs, so ``import deprecate.audit`` stays as cheap as before. The expiry, policy and table
validators accept the result in place of a module and check its rows without importing or scanning again.

"""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from deprecate._gaps import _FoundGap
from deprecate.audit._scan import _is_excluded
from deprecate.audit._wrappers import DeprecationWrapperInfo, _format_subject

if TYPE_CHECKING:
    from deprecate._static.discovery import _StaticSource


@dataclass(frozen=True)
class _StaticScan:
    """Keep located source, detected version, rows and structured gaps for static scan clients."""

    source: "_StaticSource"
    version: Optional[str]
    wrappers: list[DeprecationWrapperInfo]
    gaps: list[_FoundGap]


def _scan_static_source(
    path: Union[str, os.PathLike[str]],
    package_dir: Optional[Path],
    *,
    recursive: bool,
    include_members: bool,
    exclude: Optional[Sequence[str]],
    version_name: Optional[str] = None,
) -> _StaticScan:
    """Locate and read source with its import-free version, raising ValueError for location errors.

    *version_name* is the module name the version lookup starts from; ``None`` uses the located source's name. The CLI
    passes the importable name it derives from the path, so a package directory nested in another package resolves its
    version as the import scan does.

    """
    # Source analysis is paid only by static callers, never by runtime decorators or import-based audits.
    from deprecate._static.discovery import _locate_static
    from deprecate._static.rows import _read_static
    from deprecate.audit._version import _resolve_package_version

    path_text = os.fspath(path)
    source = _locate_static(path_text, package_dir)
    if isinstance(source, str):
        raise ValueError(source)
    existing = path_text if Path(path_text).exists() else None
    version = _resolve_package_version(
        version_name or source.name, path=existing, allow_import=False, import_root=source.import_root
    )
    wrappers, gaps = _read_static(source, recursive=recursive, include_members=include_members, exclude=exclude or ())
    return _StaticScan(source, version, wrappers, gaps)


@dataclass(frozen=True)
class StaticScanResult:
    r"""Keep the rows, gaps, and version of an import-free source scan together.

    Rows are approximations read from source and carry stand-in targets that raise ``TypeError`` when called.
    Callers must read ``gaps``: empty ``wrappers`` with non-empty ``gaps`` is not a clean result.

    Attributes:
        wrappers: Static wrapper rows after module exclusions.
        gaps: Structural gaps in encounter order, then distinct unresolved-setting details for each row.
        version: Package version resolved without importing, or ``None`` when unavailable.

    Examples:
        >>> from tempfile import TemporaryDirectory
        >>> with TemporaryDirectory() as root:
        ...     _ = Path(root, "module.py").write_text("value = 1\n")
        ...     scan = find_deprecation_wrappers_static(root)
        ...     scan.wrappers, scan.gaps
        ((), ())

    """

    wrappers: tuple[DeprecationWrapperInfo, ...]
    gaps: tuple[str, ...]
    version: Optional[str]


def find_deprecation_wrappers_static(
    path: Union[str, os.PathLike[str]],
    recursive: bool = True,
    include_members: bool = True,
    *,
    exclude: Optional[Sequence[str]] = None,
) -> StaticScanResult:
    r"""Read deprecated wrappers from Python source without importing or executing the scanned package.

    Args:
        path: Package directory, module file, plain directory, or top-level module name located without importing it.
            Dotted module names are refused because locating them would import their parents.
        recursive: Read package submodules recursively; plain directories include only top-level Python files.
        include_members: Include deprecated class members.
        exclude: Glob patterns over dotted module names, as in
            :func:`~deprecate.audit.find_deprecation_wrappers`; ``None`` excludes nothing.

    Returns:
        Source-derived rows, ordered gaps, and the version resolved without imports. Always inspect ``gaps`` before
        treating an empty ``wrappers`` tuple as clean. Targets are stand-ins, not executable replacements.

    Raises:
        ValueError: If the location has no Python source or requires importing parent packages to locate it.

    Examples:
        >>> from tempfile import TemporaryDirectory
        >>> with TemporaryDirectory() as root:
        ...     package = Path(root, "sample")
        ...     package.mkdir()
        ...     _ = (package / "__init__.py").write_text(
        ...         "from deprecate import deprecated\n"
        ...         "@deprecated(deprecated_in='1.0', remove_in='2.0')\ndef old(): pass\n"
        ...     )
        ...     result = find_deprecation_wrappers_static(package)
        ...     [row.function for row in result.wrappers], result.gaps
        (['old'], ())

    """
    from deprecate._static.checks import _unresolved

    scan = _scan_static_source(path, None, recursive=recursive, include_members=include_members, exclude=exclude)
    gaps = [f"{gap.kind.value}: {gap.detail}" for gap in scan.gaps]
    for info in scan.wrappers:
        gaps.extend(
            f"not statically resolvable: {detail} ({_format_subject(info)} not checked)"
            for detail in dict.fromkeys(_unresolved(info).values())
        )
    return StaticScanResult(tuple(scan.wrappers), tuple(gaps), scan.version)


def _static_result_rows(
    result: StaticScanResult, *, recursive: bool, include_members: bool, exclude: Optional[Sequence[str]]
) -> list[DeprecationWrapperInfo]:
    """Reject changed discovery options and apply module exclusions to an already completed scan."""
    if not recursive or not include_members:
        raise ValueError("recursive and include_members were fixed when the static scan ran; pass them to the scan.")
    return [info for info in result.wrappers if not _is_excluded(info.module, exclude or ())]
