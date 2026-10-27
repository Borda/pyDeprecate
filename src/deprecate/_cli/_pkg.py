"""Package and filesystem helpers for pyDeprecate's CLI.

Internal utilities that read the ``[tool.pydeprecate]`` configuration, locate the package directory a path names and
turn a path into an importable module name. Version resolution, which the audit API shares, lives in
:mod:`deprecate.audit._version`.

This module is private (no ``__all__``); its surface may change without notice.

"""

from pathlib import Path
from typing import Any, Optional

from deprecate.audit._version import _is_package_dir, _iter_pyproject_paths, _load_toml

#: Dotted key of the ``pyproject.toml`` table that carries the CLI's project-level settings.
_CONFIG_TABLE = ("tool", "pydeprecate")


class _ConfigReadError(Exception):
    """A ``pyproject.toml`` within reach of the scanned path could not be read or parsed.

    Raised by :func:`_read_pydeprecate_config` instead of skipping the file: a broken ``pyproject.toml`` must not
    silently fall back to the built-in defaults, and the parser's own error names no file, so the path travels
    here for the CLI's usage error. The underlying :class:`OSError` or :class:`ValueError` is chained as
    ``__cause__``.

    Attributes:
        path: The ``pyproject.toml`` that failed.

    Examples:
        >>> str(_ConfigReadError("/repo/pyproject.toml", ValueError("Expected '=' after a key (at line 3)")))
        "Cannot read /repo/pyproject.toml: Expected '=' after a key (at line 3)"

    """

    def __init__(self, path: str, cause: Exception) -> None:
        super().__init__(f"Cannot read {path}: {cause}")
        self.path = path


def _read_pydeprecate_config(path: str) -> tuple[dict[str, Any], Optional[str]]:
    r"""Return the ``[tool.pydeprecate]`` table from the nearest ``pyproject.toml`` above *path*.

    Walks the same directory + 2 parents as :func:`deprecate.audit._version._read_pyproject_version` and stops at
    the first file that declares the table, so a nested package can override its parent project's settings. A
    ``pyproject.toml`` without the table is skipped, not treated as an empty configuration; one that cannot be read
    or parsed is a hard error rather than a skip, or a syntax slip in the nearest file would silently hand the run to
    a parent's settings or the built-in defaults.

    Args:
        path: File-system path to start the upward search from.

    Returns:
        Tuple of the table contents (kebab-case keys, raw TOML values, nested ``policy`` sub-table included) and
        the path of the file it came from, or ``({}, None)`` when no file within reach declares it (or the TOML
        parser is unavailable).

    Raises:
        _ConfigReadError: If a ``pyproject.toml`` within reach cannot be opened or is not valid TOML.

    Examples:
        >>> import os, tempfile
        >>> with tempfile.TemporaryDirectory() as root:
        ...     with open(os.path.join(root, "pyproject.toml"), "w") as fh:
        ...         _ = fh.write('[tool.pydeprecate]\nexclude = ["pkg.tests"]\npolicy.min-grace = { major = 1 }\n')
        ...     table, found = _read_pydeprecate_config(root)
        >>> table
        {'exclude': ['pkg.tests'], 'policy': {'min-grace': {'major': 1}}}
        >>> with tempfile.TemporaryDirectory() as empty:
        ...     _read_pydeprecate_config(empty)
        ({}, None)

    """
    for toml_path in _iter_pyproject_paths(path):
        try:
            node: Any = _load_toml(toml_path, strict=True)
        except (OSError, ValueError) as err:
            raise _ConfigReadError(toml_path, err) from err
        for key in _CONFIG_TABLE:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            return dict(node), toml_path
    return {}, None


def _find_child_packages(pth: Path) -> list[Path]:
    """Return package sub-directories of *pth*, checking ``src/`` when none found directly.

    Resolution order:
    1. Immediate child directories of ``pth/src/`` that contain ``__init__.py``
       (standard ``src/``-layout projects take precedence).
    2. If none, immediate child directories of *pth* that contain ``__init__.py``.

    Args:
        pth: Directory to search.

    Returns:
        List of :class:`~pathlib.Path` objects for each package directory found.

    """
    src_dir = pth / "src"
    if src_dir.is_dir():
        in_src = [c for c in src_dir.iterdir() if c.is_dir() and _is_package_dir(c)]
        if in_src:
            return in_src
    return [c for c in pth.iterdir() if c.is_dir() and _is_package_dir(c)]


def _select_child_package(path: str) -> Optional[Path]:
    """Return the single package directory under *path*, or ``None`` when it holds no package.

    Two or more packages are ambiguous — a flat-layout root such as ``mypkg/`` plus ``tests/`` — and raise instead of
    guessing, so a caller never audits one package, or nothing, while the user believes the whole root was checked.

    Args:
        path: Directory to search, as the user passed it (quoted in the error message).

    Returns:
        The package directory, or ``None`` when neither ``src/`` nor *path* directly contains one.

    Raises:
        ValueError: If *path* contains two or more package directories.

    """
    child_pkgs = _find_child_packages(Path(path))
    if len(child_pkgs) > 1:
        names = ", ".join(sorted(c.name for c in child_pkgs))
        raise ValueError(
            f"Directory {path!r} contains multiple packages ({names}). Pass the specific package sub-directory instead."
        )
    return child_pkgs[0] if child_pkgs else None


def _resolve_module_name(path: str) -> str:
    """Convert a filesystem path to an importable module name.

    Accepts a package directory (with ``__init__.py``) or an importable module
    name string. A plain directory and an individual ``.py`` file have no single
    importable name; the CLI scans a plain directory module by module instead.

    Args:
        path: Package directory path or importable module name string.

    Returns:
        Importable module name string.

    Raises:
        ValueError: If path is a plain directory without ``__init__.py``, a ``.py``
            file, or cannot be resolved to an importable module name.

    """
    pth = Path(path)
    if pth.is_file():
        raise ValueError(
            f"File paths are not supported: {path!r}. Pass an importable module/package name or a directory instead."
        )
    if pth.is_dir():
        if _is_package_dir(pth):
            return pth.resolve().name
        # Flat src-layout or project root: find package in direct children or src/ subdir.
        child_pkg = _select_child_package(path)
        if child_pkg is not None:
            return child_pkg.name
        raise ValueError(
            f"Plain directory without '__init__.py' has no importable module name: {path!r}. "
            "Use an importable package layout with '__init__.py', or pass an importable module name instead."
        )
    return path


def _safe_module_name(path: str) -> str:
    """Return the importable module name for *path*, falling back to *path* itself."""
    try:
        return _resolve_module_name(path)
    except ValueError:
        return path
