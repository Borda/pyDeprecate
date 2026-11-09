"""Package version resolution shared by the audit API and the CLI.

:func:`_resolve_package_version` is the one resolution chain behind ``validate_deprecation_expiry``,
``generate_deprecation_table`` and every CLI subcommand: a local ``pyproject.toml`` (development checkout) when
available, then installed distribution metadata via ``importlib.metadata``, and finally the package's ``__version__``,
imported with the scanned path's import root on ``sys.path`` (:func:`_managed_sys_path`).

TOML parsing uses ``tomllib`` (stdlib on Python 3.11+) or ``tomli`` (backport, via ``pip install 'pyDeprecate[audit]'``
on Python 3.9-3.10). When not available the helpers return ``None`` and callers fall back to ``importlib.metadata``.

This module is private (no ``__all__``); its surface may change without notice.

"""

import ast
import importlib
import importlib.metadata
import os
import sys
import warnings
from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager, nullcontext, suppress
from pathlib import Path
from typing import Any, Optional


def _load_toml(path: str, *, strict: bool = False) -> dict[str, Any]:
    r"""Load a TOML file using ``tomllib`` (Python 3.11+) or ``tomli`` (Python 3.10 backport).

    Returns an empty dict when no TOML parser is installed. By default a file that cannot be opened or parsed
    also yields an empty dict, so version auto-detection degrades to installed metadata; with ``strict`` that
    failure is re-raised instead, for a caller whose result must never silently fall back to built-in defaults.

    Args:
        path: File-system path of the TOML file.
        strict: Re-raise read and parse failures instead of returning ``{}``.

    Returns:
        The parsed document, or ``{}`` for a swallowed failure or a missing parser.

    Raises:
        OSError: If ``strict`` and the file cannot be opened or read.
        ValueError: If ``strict`` and the file is not valid TOML (``TOMLDecodeError``) or not valid UTF-8.

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
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # Python 3.10 backport
        except ImportError:
            return {}
    try:
        with open(path, "rb") as fh:
            return dict(tomllib.load(fh))
    except (OSError, ValueError):
        if strict:
            raise
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


#: Modules :func:`_string_from_source` follows through ``from X import ...`` re-exports before giving up.
_SOURCE_HOPS = 3


def _module_source_file(module: str, roots: Sequence[Path]) -> Optional[Path]:
    """Return the source file of dotted *module* under the first import root that has it, without importing anything.

    Examples:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as root:
        ...     pkg = Path(root, "pkg")
        ...     pkg.mkdir()
        ...     _ = (pkg / "__init__.py").write_text("")
        ...     _ = (pkg / "about.py").write_text("")
        ...     found = [_module_source_file(name, [Path(root)]) for name in ("pkg", "pkg.about", "pkg.nope")]
        ...     [path.relative_to(root).as_posix() if path else None for path in found]
        ['pkg/__init__.py', 'pkg/about.py', None]

    """
    parts = module.split(".")
    for root in roots:
        base = root.joinpath(*parts)
        for candidate in (base / "__init__.py", base.with_suffix(".py")):
            if candidate.is_file():
                return candidate
    return None


def _star_exports(tree: ast.Module, name: str) -> bool:
    """Tell whether ``from <module> import *`` binds *name*: listed in a literal ``__all__``, else any public name.

    Examples:
        >>> _star_exports(ast.parse("__all__ = ['__version__']"), "__version__")
        True
        >>> _star_exports(ast.parse("__version__ = '1.0'"), "__version__")
        False

    """
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            return any(isinstance(elt, ast.Constant) and elt.value == name for elt in node.value.elts)
    return not name.startswith("_")


def _absolute_import(module: str, is_package: bool, node: ast.ImportFrom) -> str:
    """Return the absolute module name a ``from X import ...`` statement in *module* reads from.

    Examples:
        >>> _absolute_import("pkg.sub", False, ast.parse("from ._meta import x").body[0])
        'pkg._meta'
        >>> _absolute_import("pkg", True, ast.parse("from ._meta import x").body[0])
        'pkg._meta'
        >>> _absolute_import("pkg.a.b", False, ast.parse("from .. import x").body[0])
        'pkg'

    """
    if not node.level:
        return node.module or ""
    base = module.split(".") if is_package else module.split(".")[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def _string_from_source(
    module: str, name: str, roots: Sequence[Path], *, hops: int = _SOURCE_HOPS, via_star: bool = False
) -> Optional[str]:
    r"""Return the string constant that module-level *name* is bound to in *module*'s source, read with ``ast``.

    Nothing is imported. Follows ``from X import name``, ``from X import other as name`` and ``from X import *``
    re-exports up to *hops* modules deep: the shapes a package uses to keep its ``__version__`` in a separate file
    (``__about__.py``, setuptools-scm's ``_version.py``). The last module-level binding of *name* wins, as at runtime.

    Examples:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as root:
        ...     pkg = Path(root, "pkg")
        ...     pkg.mkdir()
        ...     _ = (pkg / "__init__.py").write_text("from pkg._meta import *\n")
        ...     _ = (pkg / "_meta.py").write_text("__all__ = ['__version__']\n__version__ = '1.4.2'\n")
        ...     _string_from_source("pkg", "__version__", [Path(root)])
        '1.4.2'

    """
    source = _module_source_file(module, roots) if hops >= 0 else None
    if source is None:
        return None
    try:
        tree = ast.parse(source.read_bytes(), filename=str(source))
    except (OSError, SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    if via_star and not _star_exports(tree, name):
        return None
    if any(isinstance(node, ast.Global) and name in node.names for node in ast.walk(tree)):
        return None  # a function may rebind it at runtime
    found: Optional[str] = None
    for node in tree.body:
        found = _rebound_string(
            node, found, name, module=module, is_package=source.name == "__init__.py", roots=roots, hops=hops
        )
    return found


def _rebound_string(
    node: ast.stmt,
    current: Optional[str],
    name: str,
    *,
    module: str,
    is_package: bool,
    roots: Sequence[Path],
    hops: int,
) -> Optional[str]:
    r"""Return the string module-level *name* holds after the statement *node*, given it held *current* before.

    Examples:
        >>> _rebound_string(ast.parse("v = '1.0'").body[0], None, "v", module="m", is_package=False, roots=[], hops=1)
        '1.0'
        >>> _rebound_string(ast.parse("w = '2.0'").body[0], "1.0", "v", module="m", is_package=False, roots=[], hops=1)
        '1.0'
        >>> try_rebind = ast.parse("try:\n    v = '2.0'\nexcept ImportError:\n    pass").body[0]
        >>> _rebound_string(try_rebind, "1.0", "v", module="m", is_package=False, roots=[], hops=1) is None
        True

    """
    if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return current if not _binds(node, name) else None
        value = node.value
        return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None
    if isinstance(node, ast.ImportFrom):
        origin = _absolute_import(module, is_package, node)
        for alias in node.names:
            if alias.name == "*":
                hop = _string_from_source(origin, name, roots, hops=hops - 1, via_star=True)
                current = hop if hop is not None else current
            elif (alias.asname or alias.name) == name:
                current = _string_from_source(origin, alias.name, roots, hops=hops - 1)
        return current
    # any other statement that binds the name (a conditional or ``try`` rebind, an augmented assignment, a loop
    # target) leaves its value to runtime
    return None if _binds(node, name) else current


def _binds(node: ast.AST, name: str) -> bool:
    r"""Tell whether a statement binds *name* anywhere inside it, so its value after the statement depends on runtime.

    Examples:
        >>> _binds(ast.parse("if x:\n    from ._v import v").body[0], "v")
        True
        >>> _binds(ast.parse("if x:\n    w = 1").body[0], "v")
        False

    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == name and isinstance(sub.ctx, (ast.Store, ast.Del)):
            return True
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub.name == name:
            return True
        if isinstance(sub, (ast.Import, ast.ImportFrom)) and any(
            alias.name == "*" or (alias.asname or alias.name.split(".")[0]) == name for alias in sub.names
        ):
            return True
    return False


def _version_from_source(
    module_name: str,
    import_root: Optional[Path],
    *,
    toml_data: Optional[dict[str, Any]] = None,
    toml_dir: Optional[Path] = None,
) -> Optional[str]:
    r"""Read a package's version from its source with ``ast`` — the stand-in for importing it, used by ``--static``.

    Sources, first hit wins: the module path in ``[tool.setuptools.dynamic].version.attr`` of *toml_data*, then the
    top-level package's ``__version__``. Module files are looked up under *import_root* (the directory holding the
    top-level package) and, for the setuptools path, also under *toml_dir* and its ``src/``.

    Examples:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as root:
        ...     pkg = Path(root, "src", "pkg")
        ...     pkg.mkdir(parents=True)
        ...     _ = (pkg / "__init__.py").write_text("raise RuntimeError('never imported')\n")
        ...     _ = (pkg / "about.py").write_text("__version__ = '2.1'\n")
        ...     data = {"tool": {"setuptools": {"dynamic": {"version": {"attr": "pkg.about.__version__"}}}}}
        ...     _version_from_source("pkg", None, toml_data=data, toml_dir=Path(root))
        '2.1'

    """
    roots = [import_root] if import_root is not None else []
    attr_path = _setuptools_version_attr(toml_data or {})
    if attr_path is not None:
        attr_module, _, attr_name = attr_path.rpartition(".")
        attr_roots = [*roots, toml_dir, toml_dir / "src"] if toml_dir is not None else roots
        version = _string_from_source(attr_module, attr_name, attr_roots)
        if version is not None:
            return version
    top_level = module_name.split(".")[0]
    if not roots or not top_level.isidentifier():
        return None
    return _string_from_source(top_level, "__version__", roots)


def _setuptools_version_attr(data: dict[str, Any]) -> Optional[str]:
    """Return the dotted ``attr`` of ``[tool.setuptools.dynamic].version`` in a parsed ``pyproject.toml``, if any."""
    node: Any = data
    for key in ("tool", "setuptools", "dynamic", "version"):
        node = node.get(key) if isinstance(node, dict) else None
    attr = node.get("attr") if isinstance(node, dict) else None
    return attr if isinstance(attr, str) and "." in attr else None


def _version_from_toml(
    toml_path: str,
    scan_path: str,
    *,
    allow_import: bool = True,
    module_name: str = "",
    import_root: Optional[Path] = None,
) -> Optional[str]:
    """Extract ``[project].version`` from *toml_path*, or ``None``.

    A ``dynamic = ["version"]`` project falls back to importing the package; with *allow_import* off, to reading that
    version from source instead (:func:`_version_from_source`).

    Args:
        toml_path: Absolute path to a ``pyproject.toml`` file.
        scan_path: Original file-system path passed to the CLI (forwarded to
            :func:`_version_from_dynamic` for import-root resolution).
        allow_import: Import the package for a dynamic version; ``False`` reads its source instead.
        module_name: Module name being scanned, whose top-level package the source read looks in.
        import_root: Directory holding the top-level package, for the source read.

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
        if not allow_import:
            toml_dir = Path(toml_path).parent
            return _version_from_source(module_name, import_root, toml_data=data, toml_dir=toml_dir)
        pkg_name = project.get("name")
        if isinstance(pkg_name, str) and pkg_name:
            return _version_from_dynamic(pkg_name, scan_path, os.path.dirname(toml_path))

    return None


def _iter_pyproject_paths(path: str) -> Iterator[str]:
    r"""Yield every existing ``pyproject.toml`` from *path*'s directory up to 2 levels above it, nearest first.

    Examples:
        >>> import os, tempfile
        >>> with tempfile.TemporaryDirectory() as root:
        ...     pkg = os.path.join(root, "src", "pkg")
        ...     os.makedirs(pkg)
        ...     with open(os.path.join(root, "pyproject.toml"), "w") as fh:
        ...         _ = fh.write("[project]\n")
        ...     [os.path.relpath(p, root) for p in _iter_pyproject_paths(pkg)]
        ['pyproject.toml']

    """
    candidate = os.path.abspath(path)
    if not os.path.isdir(candidate):
        candidate = os.path.dirname(candidate)
    for _ in range(3):  # current dir + 2 levels up
        toml_path = os.path.join(candidate, "pyproject.toml")
        if os.path.isfile(toml_path):
            yield toml_path
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent


def _read_pyproject_version(
    path: str, *, allow_import: bool = True, module_name: str = "", import_root: Optional[Path] = None
) -> Optional[str]:
    """Return ``[project].version`` from the nearest ``pyproject.toml`` up to 2 levels above *path*.

    Searches the directory itself, its parent, and its grandparent — stops
    as soon as a resolvable version is found or the limit is reached.

    Args:
        path: File-system path to start the upward search from.
        allow_import: Import the package for a dynamic version; ``False`` reads its source instead.
        module_name: Module name being scanned, for the source read of a dynamic version.
        import_root: Directory holding the top-level package, for the source read of a dynamic version.

    Returns:
        Version string or ``None`` when not found within 2 levels.

    """
    for toml_path in _iter_pyproject_paths(path):
        version = _version_from_toml(
            toml_path, path, allow_import=allow_import, module_name=module_name, import_root=import_root
        )
        if version is not None:
            return version
    return None


def _resolve_package_version(
    module_name: str, path: Optional[str] = None, *, allow_import: bool = True, import_root: Optional[Path] = None
) -> Optional[str]:
    """Return the version of the package that provides *module_name*, or ``None`` when no source yields one.

    The single resolution chain behind every CLI subcommand, the JSON report table and the public audit API
    (``validate_deprecation_expiry``, ``generate_deprecation_table``), so the same package never resolves to one
    version in one entry point and to another version — or to none — in a different one. Sources, first hit wins:

    1. ``[project].version`` in the nearest ``pyproject.toml`` above *path* (reflects local development checkouts
       whose version may differ from the installed dist; a ``dynamic`` version imports ``[project].name``).
    2. ``importlib.metadata.version`` of the full module name, then its top-level import package. A dotted
       component can have its own distribution and version, distinct from another package sharing its namespace.
    3. The same lookup for the distribution that provides the top-level import package, as the import name
       (``deprecate``, ``PIL``, ``sklearn``) often differs from the distribution name (``pyDeprecate``,
       ``Pillow``, ``scikit-learn``).
    4. The top-level package's ``__version__`` when it is a string — the only source an uninstalled checkout
       without a static ``pyproject.toml`` version exposes. The package is imported with *path*'s import root
       on ``sys.path``.

    With *allow_import* off (the CLI's ``--static`` mode) nothing is imported: steps 1 and 4 read the version from the
    package's source with ``ast`` instead (:func:`_version_from_source`), and step 1 does so before any metadata, in
    the place of the import it replaces.

    Args:
        module_name: Importable (possibly dotted) module name; matching distribution metadata takes priority
            over the top-level package's version.
            A raw path that is not a dotted name (the CLI's fallback for a plain directory) is never imported.
        path: Optional file-system path used to locate a local ``pyproject.toml`` and the import root.
            Only consulted when it actually exists on the filesystem; an importable module *name* passed
            through here is ignored, because the walk-up from the current working directory would otherwise
            pick up an unrelated project's ``pyproject.toml`` version.
        allow_import: Import the package where a step needs it; ``False`` reads its source with ``ast`` instead.
        import_root: Directory holding the top-level package, for the source reads when *allow_import* is off.

    Returns:
        Version string (e.g. ``"1.2.3"``) or ``None`` when no source yields a version string.

    """
    # Guard: a bare module name (not an existing path) must not trigger the pyproject.toml
    # walk-up — starting from cwd it would grab whatever unrelated project the caller is
    # standing in, flipping expiry gates on a foreign version.
    existing_path = path if path is not None and os.path.exists(path) else None
    if existing_path is not None:
        local_ver = _read_pyproject_version(
            existing_path, allow_import=allow_import, module_name=module_name, import_root=import_root
        )
        if local_ver is not None:
            return local_ver
    top_level = module_name.split(".")[0]
    with suppress(Exception):
        # Python 3.9's metadata reader normalizes hyphens but leaves dots unchanged.
        return importlib.metadata.version(module_name.replace(".", "-"))
    if module_name != top_level:
        with suppress(Exception):
            return importlib.metadata.version(top_level)
    # Mapping enumerates installed distributions, so defer it until direct metadata lookups fail.
    dist_name = _distribution_for_import(top_level)
    if dist_name is not None:
        with suppress(Exception):
            return importlib.metadata.version(dist_name)
    if not all(part.isidentifier() for part in module_name.split(".")):
        return None
    if not allow_import:
        return _version_from_source(module_name, import_root)
    with _managed_sys_path(existing_path) if existing_path is not None else nullcontext():
        try:
            version = getattr(importlib.import_module(top_level), "__version__", None)
        except Exception:
            return None
    # A non-string __version__ (e.g. a tuple) is no version; keep it unresolved so the caller reports a gap.
    return version if isinstance(version, str) else None


def _distribution_for_import(import_name: str) -> Optional[str]:
    """Map a top-level import package name to its installed distribution name.

    ``importlib.metadata.version`` needs the *distribution* name (``pyDeprecate``) while the CLI is
    handed the *import* name (``deprecate``). Uses ``importlib.metadata.packages_distributions`` on
    Python 3.11+ and falls back to scanning each distribution's ``top_level.txt`` on Python <3.11.

    Args:
        import_name: Importable (possibly dotted) package name; only the first component is used.

    Returns:
        The first distribution name that provides the top-level package, or ``None`` when no
        installed distribution exposes it.

    """
    top_level = import_name.split(".")[0]
    packages_distributions = getattr(importlib.metadata, "packages_distributions", None)
    if callable(packages_distributions):
        try:
            distributions = packages_distributions().get(top_level)
        except Exception:
            return None
        return distributions[0] if distributions else None
    # Python <3.11: packages_distributions is unavailable — read top_level.txt from each distribution.
    return _distribution_from_top_level(top_level)


def _distribution_from_top_level(top_level: str) -> Optional[str]:
    """Return the distribution whose ``top_level.txt`` lists *top_level* (Python <3.11 fallback)."""
    try:
        distributions = list(importlib.metadata.distributions())
    except Exception:
        return None
    for dist in distributions:
        try:
            listed = dist.read_text("top_level.txt")
        except Exception:
            listed = None
        if listed and top_level in listed.split():
            try:
                name = dist.metadata["Name"]
            except KeyError:
                continue
            if name:
                return name
    return None


def _is_package_dir(pth: Path) -> bool:
    """Return True if *pth* is an importable package directory (contains ``__init__.py``)."""
    return (pth / "__init__.py").exists()


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
