"""Package and filesystem helpers for pyDeprecate's CLI.

Internal utilities that resolve a package's version, locate package directories, and manage ``sys.path`` for scanning.

Version resolution uses a local ``pyproject.toml`` (development checkout) when available, falling back to installed
distribution metadata via ``importlib.metadata``.

TOML parsing uses ``tomllib`` (stdlib on Python 3.11+) or ``tomli`` (backport, via ``pip install 'pyDeprecate[audit]'``
on Python 3.10). When not available the helpers return ``None`` and callers fall back to ``importlib.metadata``.

This module is private (no ``__all__``); its surface may change without notice.

"""

import importlib
import importlib.metadata
import os
import sys
import warnings
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional


def _load_toml(path: str) -> dict[str, Any]:
    r"""Load a TOML file using ``tomllib`` (Python 3.11+) or ``tomli`` (Python 3.10 backport).

    Returns an empty dict on any failure (missing library, parse error, IO).

    Examples:
        >>> import os, tempfile
        >>> with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
        ...     _ = f.write('[project]\nname = "mypkg"\nversion = "1.2.3"\n')
        ...     name = f.name
        >>> _load_toml(name).get("project", {}).get("version")
        '1.2.3'
        >>> os.unlink(name)

    """
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # Python 3.10 backport

        with open(path, "rb") as fh:
            return dict(tomllib.load(fh))
    except Exception:
        return {}


def _version_from_dynamic(pkg_name: str, scan_path: str, pyproject_dir: str) -> Optional[str]:
    """Import *pkg_name* and return its ``__version__``, or ``None`` on any failure.

    Adds candidate import roots (scan dir, its parent, pyproject dir, ``src/``
    sub-dir) to ``sys.path`` before importing, then restores the original
    ``sys.path`` unconditionally via ``finally``.

    Args:
        pkg_name: Importable package name declared in ``[project].name``.
        scan_path: File-system path originally passed to the CLI (used to
            locate the package root).
        pyproject_dir: Directory containing the ``pyproject.toml`` file.

    Returns:
        Version string from ``pkg.__version__`` or ``None``.

    """
    scan_root = os.path.abspath(scan_path) if os.path.isdir(scan_path) else os.path.dirname(os.path.abspath(scan_path))
    original = list(sys.path)
    added: list[str] = []
    for root in (scan_root, os.path.dirname(scan_root), pyproject_dir, os.path.join(pyproject_dir, "src")):
        if root and os.path.isdir(root) and root not in added:
            added.append(root)
            sys.path.insert(0, root)
    try:
        module = importlib.import_module(pkg_name)
        version = getattr(module, "__version__", None)
        return version if isinstance(version, str) else None
    except Exception:
        return None
    finally:
        sys.path[:] = original


def _version_from_toml(toml_path: str, scan_path: str) -> Optional[str]:
    """Extract ``[project].version`` from *toml_path*, or ``None``.

    Falls back to importing the package for ``dynamic = ["version"]`` projects.

    Args:
        toml_path: Absolute path to a ``pyproject.toml`` file.
        scan_path: Original file-system path passed to the CLI (forwarded to
            :func:`_version_from_dynamic` for import-root resolution).

    Returns:
        Version string or ``None`` when not resolvable.

    """
    data = _load_toml(toml_path)
    project = data.get("project", {})
    if not project:
        return None

    version = project.get("version")
    if isinstance(version, str):
        return version

    if "version" in project.get("dynamic", []):
        pkg_name = project.get("name")
        if isinstance(pkg_name, str) and pkg_name:
            return _version_from_dynamic(pkg_name, scan_path, os.path.dirname(toml_path))

    return None


def _read_pyproject_version(path: str) -> Optional[str]:
    """Return ``[project].version`` from the nearest ``pyproject.toml`` up to 2 levels above *path*.

    Searches the directory itself, its parent, and its grandparent — stops
    as soon as a resolvable version is found or the limit is reached.

    Args:
        path: File-system path to start the upward search from.

    Returns:
        Version string or ``None`` when not found within 2 levels.

    """
    candidate = os.path.abspath(path)
    if not os.path.isdir(candidate):
        candidate = os.path.dirname(candidate)
    for _ in range(3):  # current dir + 2 levels up
        toml_path = os.path.join(candidate, "pyproject.toml")
        if os.path.isfile(toml_path):
            version = _version_from_toml(toml_path, path)
            if version is not None:
                return version
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    return None


def _auto_detect_version(module_name: str, path: Optional[str] = None) -> Optional[str]:
    """Return the version for *module_name*, or ``None`` on any failure.

    Priority order:

    1. ``[project].version`` in the nearest ``pyproject.toml`` above *path* (reflects
       local development checkouts whose version may differ from the installed dist).
    2. ``importlib.metadata.version(module_name)`` — the installed distribution.

    Args:
        module_name: Importable package name whose installed version to look up.
        path: Optional file-system path used to locate a local ``pyproject.toml``.
            When provided, the local file takes precedence over installed metadata.

    Returns:
        Version string (e.g. ``"1.2.3"``) or ``None`` when the package is not
        installed or the version cannot be determined.

    """
    if path is not None:
        local_ver = _read_pyproject_version(path)
        if local_ver is not None:
            return local_ver
    try:
        return importlib.metadata.version(module_name)
    except Exception:
        return None


def _is_package_dir(pth: Path) -> bool:
    """Return True if *pth* is an importable package directory (contains ``__init__.py``)."""
    return (pth / "__init__.py").exists()


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


@contextmanager
def _managed_sys_path(path: str) -> Generator[None, None, None]:
    """Context manager that prepends the import root to ``sys.path`` and restores it after scanning.

    For package directories (containing ``__init__.py``), inserts the parent directory so the package name resolves as
    an importable module. For plain directories, inserts the directory itself. Importable module name strings are passed
    through unchanged.

    Warning:
        Not thread-safe. ``sys.path`` is a process-global list; concurrent use from multiple threads will corrupt
        the restored state. Each scan should run in a dedicated process or be serialized via a lock.

    """
    abs_path: Path = Path(path).resolve()
    original = list(sys.path)
    if abs_path.is_dir():
        if _is_package_dir(abs_path):
            import_root = str(abs_path.parent)
        else:
            src_dir = abs_path / "src"
            in_src = src_dir.is_dir() and any(c.is_dir() and _is_package_dir(c) for c in src_dir.iterdir())
            import_root = str(src_dir) if in_src else str(abs_path)
        sys.path.insert(0, import_root)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning, module="deprecate.*")
            warnings.filterwarnings("ignore", category=FutureWarning, module="deprecate.*")
            yield
    finally:
        sys.path[:] = original


def _resolve_module_name(path: str) -> str:
    """Convert a filesystem path to an importable module name.

    Accepts a package directory (with ``__init__.py``) or an importable module
    name string. Plain directories and individual ``.py`` files are not supported
    because ``validate_deprecation_expiry`` and ``validate_deprecation_chains``
    require an importable module name, not a filesystem path.

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
        child_pkgs = _find_child_packages(pth)
        if len(child_pkgs) == 1:
            return child_pkgs[0].name
        if len(child_pkgs) > 1:
            names = ", ".join(sorted(c.name for c in child_pkgs))
            raise ValueError(
                f"Directory {path!r} contains multiple packages ({names}). "
                "Pass the specific package sub-directory instead."
            )
        raise ValueError(
            f"Plain directories without '__init__.py' are not supported for expiry or chain checks: {path!r}. "
            "Use an importable package layout with '__init__.py', or pass an importable module name instead."
        )
    return path


def _safe_module_name(path: str) -> str:
    """Return the importable module name for *path*, falling back to *path* itself."""
    try:
        return _resolve_module_name(path)
    except ValueError:
        return path
