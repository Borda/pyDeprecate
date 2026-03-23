"""Griffe extension: replace static docstrings with their runtime values.

pyDeprecate's ``@deprecated(update_docstring=True)`` modifies a function's
``__doc__`` attribute at decoration time.  Griffe, however, reads docstrings
from the source AST, so the injected deprecation notice is invisible.

This extension imports each module after Griffe has visited it and updates
the docstring value for every callable whose ``__doc__`` was changed by a
decorator (detected via the ``__deprecated__`` marker set by pyDeprecate).
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

import griffe


class RuntimeDocstrings(griffe.Extension):
    """Update Griffe docstrings to reflect decorator-modified ``__doc__``."""

    def on_module(self, *, mod: griffe.Module, loader: griffe.GriffeLoader, **kwargs: object) -> None:
        """Run after a module is fully loaded by Griffe.

        For each function or method in *mod* that carries a ``__deprecated__``
        attribute (set by pyDeprecate), replace the Griffe docstring with the
        runtime value so that mkdocstrings renders the injected notice.
        """
        # Add the module's parent directory to sys.path so importlib can find it.
        if mod.filepath:
            mod_dir = str(Path(mod.filepath).parent)
            if mod_dir not in sys.path:
                sys.path.insert(0, mod_dir)

        try:
            runtime_mod = importlib.import_module(mod.name)
        except (ImportError, ModuleNotFoundError):
            return

        for path, obj in mod.members.items():
            self._update_obj(obj, runtime_mod, path)

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
        # Apply inspect.cleandoc so griffe receives normalized text (no leading
        # 4-space indent that would otherwise render as a markdown code block).
        griffe_obj.docstring.value = inspect.cleandoc(runtime_doc)
