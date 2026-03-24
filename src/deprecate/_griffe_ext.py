"""Griffe extension: expose runtime-modified docstrings to mkdocstrings.

``@deprecated(update_docstring=True)`` writes the deprecation notice into
``fn.__doc__`` at decoration time.  Griffe — the engine used by mkdocstrings —
reads docstrings from the source AST, so it never sees that runtime change.

This extension bridges the gap: after Griffe has visited a module it imports
the module at runtime and replaces every AST docstring whose callable carries
a ``__deprecated__`` attribute (set by pyDeprecate) with the live ``__doc__``.

Usage in ``mkdocs.yml``::

    plugins:
      - mkdocstrings:
          handlers:
            python:
              extensions:
                - deprecate._griffe_ext:RuntimeDocstrings

Requirements:
    ``griffe`` is a dependency of ``mkdocstrings[python]`` and will always be
    present in an MkDocs environment that uses mkdocstrings.  Importing this
    module without ``griffe`` installed is safe — ``RuntimeDocstrings`` simply
    will not be defined.
"""

from __future__ import annotations

import importlib
import sys

try:
    import griffe
except ImportError:
    griffe = None


if griffe is not None:

    class RuntimeDocstrings(griffe.Extension):
        """Update Griffe docstrings to reflect decorator-modified ``__doc__``."""

        def on_module(self, *, mod: griffe.Module, loader: griffe.GriffeLoader, **kwargs: object) -> None:
            """Run after a module is fully loaded by Griffe.

            For each function or method in *mod* that carries a ``__deprecated__``
            attribute (set by pyDeprecate), replace the Griffe docstring with the
            runtime value so that mkdocstrings renders the injected notice.
            """
            runtime_mod = self._import_module(mod)
            if runtime_mod is None:
                return

            for name, obj in mod.members.items():
                self._update_obj(obj, runtime_mod, name)

        @staticmethod
        def _import_module(mod: griffe.Module) -> object:
            """Import *mod* at runtime, adding its package root to sys.path if needed.

            griffe loads modules via its own search_paths which are not on sys.path,
            so a plain ``importlib.import_module(mod.name)`` will fail for modules
            that live outside the installed packages (e.g. a local ``demo.py``).
            We derive the package root from ``mod.filepath`` — walking up one level
            per dotted name component so that both single-file modules (``demo``) and
            package sub-modules (``pkg.submod``) resolve correctly — and temporarily
            add it to sys.path so the import can succeed.
            """
            # Fast path: module is already importable (installed package, etc.)
            try:
                return importlib.import_module(mod.name)
            except (ImportError, ModuleNotFoundError):
                pass

            # Slow path: add the package root derived from mod.filepath.
            filepath = getattr(mod, "filepath", None)
            if filepath is None:
                return None

            import pathlib

            # Walk up one level per name component so sys.path contains the
            # *root* of the package tree, not a sub-directory.
            # Examples (depth before __init__ adjustment):
            #   "demo"     /project/demo.py           → depth=0, parents[0]=/project ✓
            #   "pkg.sub"  /project/pkg/sub.py        → depth=1, parents[1]=/project ✓
            # For packages the filepath points to __init__.py, adding one more
            # level:
            #   "pkg"      /project/pkg/__init__.py   → depth=0+1=1, parents[1]=/project ✓
            #   "pkg.sub"  /project/pkg/sub/__init__.py → depth=1+1=2, parents[2]=/project ✓
            p = pathlib.Path(filepath)
            depth = len(mod.name.split(".")) - 1
            if p.name == "__init__.py":
                depth += 1
            source_dir = str(p.parents[depth])
            added = source_dir not in sys.path
            if added:
                sys.path.insert(0, source_dir)
            try:
                return importlib.import_module(mod.name)
            except (ImportError, ModuleNotFoundError):
                return None
            finally:
                if added:
                    sys.path.remove(source_dir)

        def _update_obj(self, obj: griffe.Object, parent: object, name: str) -> None:
            runtime_obj = getattr(parent, name, None)
            if runtime_obj is None:
                return

            if isinstance(obj, griffe.Function):
                self._replace_docstring(obj, runtime_obj)
            elif isinstance(obj, griffe.Class):
                self._replace_docstring(obj, runtime_obj)
                for member_name, member in obj.members.items():
                    self._update_obj(member, runtime_obj, member_name)

        @staticmethod
        def _replace_docstring(griffe_obj: griffe.Object, runtime_obj: object) -> None:
            """Overwrite the Griffe docstring when the runtime ``__doc__`` differs."""
            if not getattr(runtime_obj, "__deprecated__", None):
                return
            runtime_doc = getattr(runtime_obj, "__doc__", None)
            if not runtime_doc:
                return
            if griffe_obj.docstring is None:
                return
            griffe_obj.docstring.value = runtime_doc
            # Griffe caches parsed sections in docstring.__dict__["parsed"].
            # If the cache was populated from the static source before this
            # extension ran, it must be cleared so the next access re-parses
            # from the updated value.
            griffe_obj.docstring.__dict__.pop("parsed", None)
