"""Check that every module's ``__all__`` is well formed, whatever names it holds."""

import importlib
import pkgutil

import pytest

import deprecate

_ALL_MODULES = sorted(info.name for info in pkgutil.walk_packages(deprecate.__path__, prefix="deprecate."))
_PRIVATE_MODULES = [
    name for name in _ALL_MODULES if any(part.startswith("_") and not part.startswith("__") for part in name.split("."))
]
_MODULES_WITH_ALL = [
    name for name in _ALL_MODULES if name not in _PRIVATE_MODULES and hasattr(importlib.import_module(name), "__all__")
]


class TestModuleAll:
    """Each public module's ``__all__`` is consistent with the module it describes."""

    @pytest.mark.parametrize("module_name", _MODULES_WITH_ALL)
    def test_lists_valid_names_once(self, module_name: str) -> None:
        """Every ``__all__`` entry is an attribute of its module, listed once.

        A stale entry left behind after a rename makes ``from module import *`` raise ``AttributeError`` for every
        caller of the module.
        """
        module = importlib.import_module(module_name)
        exported = module.__all__
        assert len(exported) == len(set(exported))
        assert set(exported) <= set(vars(module))

    @pytest.mark.parametrize("module_name", _PRIVATE_MODULES)
    def test_private_module_declares_none(self, module_name: str) -> None:
        """A private module (an underscored path component) defines no ``__all__``.

        The leading underscore already marks the whole module internal; an ``__all__`` there would fake a public
        surface on something that has none.
        """
        assert not hasattr(importlib.import_module(module_name), "__all__")

    def test_star_import_binds_exactly_all(self) -> None:
        """``from deprecate.utils import *`` binds the declared names and no helpers.

        Before ``__all__`` existed, a star import also pulled internal helpers and the standard-library modules
        ``utils`` imports into the caller's namespace.
        """
        namespace: dict[str, object] = {}
        exec("from deprecate.utils import *", namespace)  # noqa: S102 — the only way to exercise a star import here
        assert set(namespace) - {"__builtins__"} == set(importlib.import_module("deprecate.utils").__all__)
