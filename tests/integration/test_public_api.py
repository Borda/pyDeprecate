"""Pin the declared public surface (``__all__``) of every pyDeprecate module."""

import importlib
import pkgutil

import pytest

import deprecate

#: Expected ``__all__`` of each public module whose surface is not already pinned elsewhere.
_EXPECTED_ALL: dict[str, set[str]] = {
    "deprecate.deprecation": {"deprecated"},
    "deprecate.routine": {"deprecated_callable"},
    "deprecate.proxy": {"deprecated_class", "deprecated_instance"},
    "deprecate.proxy.factories": {"deprecated_class", "deprecated_instance"},
    "deprecate.proxy.core": set(),
    "deprecate.proxy.resolve": set(),
    "deprecate.proxy.dunders": set(),
    "deprecate.module": {"deprecated_module"},
    "deprecate.messaging": {
        "TEMPLATE_ARGUMENT_MAPPING",
        "TEMPLATE_WARNING_ARGUMENTS",
        "TEMPLATE_WARNING_CALLABLE",
        "TEMPLATE_WARNING_NO_TARGET",
        "deprecation_warning",
    },
    "deprecate.utils": {"assert_no_warnings", "no_warning_call", "void"},
    "deprecate.docstring": set(),
    "deprecate.docstring.inject": set(),
    "deprecate.docstring.sphinx_ext": {"setup"},
    "deprecate.audit": {
        "ChainType",
        "DeprecatedCallableInfo",
        "DeprecationStatus",
        "DeprecationWrapperInfo",
        "GraceWindow",
        "GraceWindowSpec",
        "PolicyRule",
        "TableStyle",
        "VersionBump",
        "find_deprecated_callables",
        "find_deprecation_wrappers",
        "generate_deprecation_table",
        "validate_deprecated_callable",
        "validate_deprecation_chains",
        "validate_deprecation_expiry",
        "validate_deprecation_policy",
        "validate_deprecation_wrapper",
        "validate_mapping_compatibility",
    },
}

#: Public modules deliberately left out of ``_EXPECTED_ALL``, each with the reason it is pinned elsewhere.
_UNPINNED_PUBLIC_MODULES: dict[str, str] = {
    "deprecate.__about__": "package metadata; its dunder names are the exports, not API names",
    "deprecate.__main__": "``python -m deprecate`` entry point; defines no exports",
    "deprecate.docstring.griffe_ext": "export is conditional on griffe; pinned by its own test below",
}

_ALL_MODULES = sorted(info.name for info in pkgutil.walk_packages(deprecate.__path__, prefix="deprecate."))
_PRIVATE_MODULES = [
    name for name in _ALL_MODULES if any(part.startswith("_") and not part.startswith("__") for part in name.split("."))
]
_PUBLIC_MODULES = [name for name in _ALL_MODULES if name not in _PRIVATE_MODULES]


class TestModuleAll:
    """Each public module declares exactly the names it means to export."""

    @pytest.mark.parametrize(("module_name", "expected"), sorted(_EXPECTED_ALL.items()))
    def test_matches_expected_names(self, module_name: str, expected: set[str]) -> None:
        """A public module's ``__all__`` lists exactly its documented public names.

        Without ``__all__``, every un-underscored helper and imported standard-library module of a public module
        looks like API to documentation tools, IDE completion and ``from module import *``. The declared list is
        the contract; a name added or dropped from it changes the public surface and must be deliberate.
        """
        assert set(importlib.import_module(module_name).__all__) == expected

    def test_every_public_module_is_classified(self) -> None:
        """Every public module is either pinned in ``_EXPECTED_ALL`` or listed with a reason in the exemptions.

        A new public module added without an ``__all__`` pin would silently escape this whole class; the check
        also fails on a pinned or exempt name that no longer exists, so renames cannot leave stale entries.
        """
        classified = set(_EXPECTED_ALL) | set(_UNPINNED_PUBLIC_MODULES)
        assert (sorted(set(_PUBLIC_MODULES) - classified), sorted(classified - set(_PUBLIC_MODULES))) == ([], [])

    def test_pinned_and_exempt_modules_do_not_overlap(self) -> None:
        """No module is both pinned in ``_EXPECTED_ALL`` and listed as exempt from pinning."""
        assert sorted(set(_EXPECTED_ALL) & set(_UNPINNED_PUBLIC_MODULES)) == []

    @pytest.mark.parametrize("module_name", sorted(_EXPECTED_ALL))
    def test_lists_no_duplicates(self, module_name: str) -> None:
        """No name appears twice in ``__all__``.

        The pinned comparison uses sets, which would hide a name pasted in twice; a duplicate is harmless at
        runtime but signals a botched merge of the export list.
        """
        exported = importlib.import_module(module_name).__all__
        assert len(exported) == len(set(exported))

    @pytest.mark.parametrize("module_name", sorted(_EXPECTED_ALL))
    def test_every_name_resolves(self, module_name: str) -> None:
        """Every name in a module's ``__all__`` is an attribute of that module.

        A stale entry left behind after a rename makes ``from module import *`` raise ``AttributeError`` for every
        caller of the module, which is worse than not declaring ``__all__`` at all.
        """
        module = importlib.import_module(module_name)
        assert set(module.__all__) <= set(vars(module))

    @pytest.mark.parametrize("module_name", sorted(_EXPECTED_ALL))
    def test_lists_no_private_names(self, module_name: str) -> None:
        """No ``__all__`` entry starts with an underscore.

        Exporting a private helper through ``__all__`` would advertise it as API while its name says it may
        change without notice.
        """
        assert [name for name in importlib.import_module(module_name).__all__ if name.startswith("_")] == []

    @pytest.mark.parametrize("module_name", _PRIVATE_MODULES)
    def test_private_module_declares_none(self, module_name: str) -> None:
        """A private module (an underscored path component) defines no ``__all__``.

        The leading underscore already marks the whole module internal; an ``__all__`` there would fake a public
        surface on something that has none.
        """
        assert not hasattr(importlib.import_module(module_name), "__all__")

    def test_griffe_extension_is_exported_when_griffe_imports(self) -> None:
        """``deprecate.docstring.griffe_ext`` exports ``RuntimeDocstrings`` exactly when the class exists.

        The extension class is defined only when ``griffe`` is importable; listing it unconditionally would make
        a star import fail on installs without ``griffe``.
        """
        module = importlib.import_module("deprecate.docstring.griffe_ext")
        assert module.__all__ == (["RuntimeDocstrings"] if hasattr(module, "RuntimeDocstrings") else [])

    def test_star_import_binds_exactly_all(self) -> None:
        """``from deprecate.utils import *`` binds the declared names and no helpers.

        Before ``__all__`` existed, a star import also pulled internal helpers and the standard-library modules
        ``utils`` imports into the caller's namespace.
        """
        namespace: dict[str, object] = {}
        exec("from deprecate.utils import *", namespace)  # noqa: S102 — the only way to exercise a star import here
        assert set(namespace) - {"__builtins__"} == {"assert_no_warnings", "no_warning_call", "void"}
