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
import inspect

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
            try:
                runtime_mod = importlib.import_module(mod.name)
            except (ImportError, ModuleNotFoundError):
                return

            for name, obj in mod.members.items():
                self._update_obj(obj, runtime_mod, name)

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
            # inspect.cleandoc normalises leading indentation so Griffe receives
            # clean text rather than a 4-space-indented block that would render
            # as a Markdown code block.
            griffe_obj.docstring.value = inspect.cleandoc(runtime_doc)
