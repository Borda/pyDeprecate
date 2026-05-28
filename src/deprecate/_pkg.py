"""Package-metadata helpers for pyDeprecate's CLI.

Internal utilities that resolve a package's version from either a local ``pyproject.toml`` (development checkout) or
installed distribution metadata.

TOML parsing uses ``tomlkit`` (available via ``pip install 'pyDeprecate[audit]'``). When not available the helpers
return ``None`` and callers fall back to ``importlib.metadata``.

This module is private (no ``__all__``); its surface may change without notice.

"""

import sys
from typing import Any, Optional


def _load_toml(path: str) -> dict[str, Any]:
    r"""Load a TOML file using ``tomlkit``.

    Returns an empty dict on any failure (missing library, parse error, IO).

    Examples:
        >>> import tempfile, os
        >>> with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
        ...     _ = f.write('[project]\nname = "mypkg"\nversion = "1.2.3"\n')
        ...     name = f.name
        >>> _load_toml(name).get("project", {}).get("version")
        '1.2.3'
        >>> os.unlink(name)

    """
    try:
        import tomlkit

        with open(path, encoding="utf-8") as fh:
            return dict(tomlkit.load(fh))
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
    import importlib
    import os

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
    import os

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
    import os

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
        import importlib.metadata

        return importlib.metadata.version(module_name)
    except Exception:
        return None
