"""Unit tests for private helpers in :mod:`deprecate.audit._scan`."""

import dataclasses
import types
import warnings
from functools import cached_property

import pytest

import tests.collection_deprecate as col
import tests.collection_misconfigured as clean_module
import tests.collection_pep702 as pep702_module
import tests.collection_targets as targets_module
from deprecate import (
    TargetMode,
    deprecated,
    validate_mapping_compatibility,
)
from deprecate._types import DeprecationConfig
from deprecate.audit import (
    DeprecationWrapperInfo,
    find_deprecation_wrappers,
    validate_deprecation_wrapper,
)
from deprecate.audit._scan import _member_has_deprecation_meta, _scan_class
from deprecate.proxy import _DeprecatedProxy, deprecated_class
from tests.collection_targets import PositionalOnlyTarget


class _SideEffectScanModule:
    """Test double that mimics module-level dynamic attribute side effects."""

    def __init__(self, proxy: _DeprecatedProxy) -> None:
        """Store proxy and expose a module-like name."""
        self.__name__ = "fake_scan_mod"
        self.scan_proxy = proxy

    def __dir__(self) -> list[str]:
        """Expose one dynamic name that would trigger __getattr__ under getmembers()."""
        return ["__name__", "scan_proxy", "trigger_side_effect"]

    def __getattr__(self, name: str) -> str:
        """Trigger proxy access when dynamic attr lookup is attempted."""
        if name == "trigger_side_effect":
            self.scan_proxy.get("x")
            return "triggered"
        raise AttributeError(name)


class TestFindDeprecationWrappersWarningBudget:
    """Scanning must not consume proxy warning budgets."""

    def test_find_deprecation_wrappers_does_not_consume_warning_budget(self) -> None:
        """Scanning must avoid dynamic attribute access paths that burn warn budget.

        ``inspect.getmembers()`` triggers ``getattr()`` for names from ``__dir__``, which can execute module-level
        ``__getattr__`` side effects. This fixture reproduces that pattern: a dynamic name touches the proxy during
        lookup. Static inspection must avoid consuming the proxy warning budget.

        """
        proxy = _DeprecatedProxy(obj={}, name="scan_test", deprecated_in="1.0", remove_in="2.0", num_warns=1)
        fake_mod = _SideEffectScanModule(proxy)

        find_deprecation_wrappers(fake_mod, recursive=False)

        # Budget should be untouched — scanning must not consume it
        with pytest.warns(FutureWarning):
            proxy.get("x")  # triggers __getattr__ → _warn() → should still fire


class TestFindDeprecationWrappersPep702:
    """Opt-in discovery of objects deprecated only with the PEP 702 decorator (``include_pep702=True``)."""

    def test_default_scan_reports_only_pydeprecate_wrappers(self) -> None:
        """Without the opt-in, PEP 702-only objects stay out of the report and every gate built on it.

        A project mixing pyDeprecate and ``warnings.deprecated`` must see no change in ``check``/``expiry``/``policy``
        results: PEP 702-only objects carry no version schedule, so reporting them by default would flip
        ``empty_deprecated_in`` and ``message-required`` gates for code that was never scheduled with pyDeprecate.

        """
        infos = find_deprecation_wrappers(pep702_module, recursive=False)
        assert {info.function for info in infos} == {
            "stacked_callable",
            "StackedAlias",
            "StackedMembers.legacy_method",
            "StackedMembers.legacy_value",
            "StackedMembers.legacy_static",
        }

    def test_opt_in_reports_pep702_only_objects(self) -> None:
        """With ``include_pep702=True`` every PEP 702-only symbol is listed, its ``api_type`` naming its shape.

        A maintainer wants one audit listing of every live deprecation, including symbols that only carry the
        stdlib-style decorator, grouped by shape like any other row (a report groups ``class`` and ``class method``
        rows, whatever decorator produced them); ``pep702_message`` alone marks the mechanism. A subclass that merely
        inherits ``__deprecated__`` through the MRO is not itself deprecated and must not appear.

        """
        infos = find_deprecation_wrappers(pep702_module, recursive=False, include_pep702=True)
        assert {(info.function, info.api_type) for info in infos if info.pep702_message is not None} == {
            ("pep702_only_function", "callable"),
            ("pep702_empty_message", "callable"),
            ("Pep702OnlyClass", "class"),
            ("Pep702DefaultCategoryClass", "class"),
            ("Pep702CallableClass", "class"),
            ("Pep702OnlyMembers.old_method", "class method"),
            ("Pep702OnlyMembers._old_method", "class method"),
            ("Pep702OnlyMembers._old_value", "class method"),
            ("Pep702OnlyMembers.old_value", "class method"),
            ("Pep702OnlyMembers.old_static", "staticmethod"),
        }

    @pytest.mark.parametrize(
        ("class_name", "expected"),
        [
            pytest.param("Pep702DefaultCategoryClass", ["Pep702DefaultCategoryClass"], id="default-category-class"),
            pytest.param("Pep702DefaultCategorySubclass", [], id="default-category-subclass"),
            pytest.param("Pep702LibrarySubclass", [], id="library-base-subclass"),
        ],
    )
    def test_class_reports_only_its_own_markers(self, class_name: str, expected: list[str]) -> None:
        """A class contributes rows only for PEP 702 markers it defines itself.

        The decorator's default category installs ``__new__`` and ``__init_subclass__`` on the class it decorates and
        stamps both with the class's message, so a naive member walk lists one deprecated class three times, and its
        undecorated subclass twice. A project model built on a library base (pydantic's ``BaseModel`` is the real-world
        case) inherits every PEP 702-deprecated method of that base, public and private; those are the library's
        deprecations and must not be repeated once per project subclass.

        """
        infos = find_deprecation_wrappers(pep702_module, recursive=False, include_pep702=True)
        assert [info.function for info in infos if info.function.split(".")[0] == class_name] == expected

    def test_instance_of_deprecated_class_not_reported(self) -> None:
        """A module-level instance of a PEP 702-deprecated callable class is not a deprecation of its own.

        Libraries often expose a ready-made callable object (a default parser, a shared client) built from a class.
        When only the class is deprecated, the instance reaches ``__deprecated__`` through its type alone; reporting it
        would list the same deprecation twice, once under a name nobody decorated.

        """
        infos = find_deprecation_wrappers(pep702_module, recursive=False, include_pep702=True)
        names = [
            info.function for info in infos if info.function in ("Pep702CallableClass", "pep702_callable_instance")
        ]
        assert names == ["Pep702CallableClass"]

    @pytest.mark.parametrize(
        "module_attrs",
        [
            pytest.param({}, id="module"),
            pytest.param({"__path__": []}, id="package-non-recursive"),
        ],
    )
    def test_foreign_package_import_not_reported(self, module_attrs: dict[str, list[str]]) -> None:
        """A PEP 702-only symbol imported from another top-level package is left to that package.

        Importing a deprecated helper from a dependency (``from pydantic.deprecated.tools import parse_obj_as``) is
        routine; a row for it under the importing module is noise the project can neither expire nor remove. The filter
        holds even when re-exports are not being attributed elsewhere (a package scanned with ``recursive=False``), and
        it covers PEP 702 rows only: a pyDeprecate wrapper imported the same way keeps its row, as before.

        """
        consumer = types.ModuleType("consumer_app")
        vars(consumer).update(
            module_attrs,
            pep702_only_function=pep702_module.pep702_only_function,
            Pep702OnlyClass=pep702_module.Pep702OnlyClass,
            stacked_callable=pep702_module.stacked_callable,
        )
        infos = find_deprecation_wrappers(consumer, recursive=False, include_pep702=True)
        assert [(info.module, info.function) for info in infos] == [("consumer_app", "stacked_callable")]

    def test_unknown_defining_module_is_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A PEP 702-only symbol with no known defining module stays in the report of the module exposing it.

        Callables built dynamically (``exec``, some extension modules) can carry ``__module__ = None``. The scanner
        then cannot tell a foreign import from a local definition, so it keeps the row rather than silently drop a live
        deprecation.

        """
        monkeypatch.setattr(pep702_module.pep702_only_function, "__module__", None)
        consumer = types.ModuleType("consumer_app")
        consumer.pep702_only_function = pep702_module.pep702_only_function  # type: ignore[attr-defined]
        infos = find_deprecation_wrappers(consumer, include_pep702=True)
        assert [(info.module, info.function) for info in infos] == [("consumer_app", "pep702_only_function")]

    def test_inherited_markers_reported_on_defining_class(self) -> None:
        """PEP 702 methods of a base class are reported once, on the base, in the module that defines it.

        Moving the rows off the subclass must not lose them: an audit of the library's own module still lists each
        deprecated method, private ones included, under the class that owns it.

        """
        infos = find_deprecation_wrappers(targets_module, recursive=False, include_pep702=True)
        assert sorted(info.function for info in infos if info.pep702_message is not None) == [
            "Pep702LibraryBase._iter_legacy",
            "Pep702LibraryBase.export_legacy",
        ]

    def test_empty_pep702_message_is_reported(self) -> None:
        """An empty PEP 702 message remains a real marker in the audit report.

        A library can use the empty string with the standard decorator. The scanner must retain that row even though
        the message is false in a Boolean context.

        """
        infos = find_deprecation_wrappers(pep702_module, recursive=False, include_pep702=True)
        rows = [info for info in infos if info.function == "pep702_empty_message"]
        assert [(info.api_type, info.pep702_message) for info in rows] == [("callable", "")]

    def test_pep702_row_carries_message_and_no_schedule(self) -> None:
        """A PEP 702 row exposes the decorator's message and reports the missing version schedule.

        The message is the only metadata a PEP 702 decorator records; the empty ``deprecated_in`` tells a CI filter
        that this symbol has no removal plan pyDeprecate could enforce.

        """
        infos = find_deprecation_wrappers(pep702_module, recursive=False, include_pep702=True)
        row = next(info for info in infos if info.function == "pep702_only_function")
        assert (row.module, row.pep702_message, row.empty_deprecated_in) == (
            "tests.collection_pep702",
            "Use `pep702_target` instead.",
            True,
        )

    def test_stacked_wrapper_stays_a_pydeprecate_row(self) -> None:
        """A pyDeprecate wrapper with a PEP 702 decorator stacked on top is reported once, with its full schedule.

        Stacking is the documented static-checker pattern; it must not demote the wrapper to a schedule-less PEP 702
        row or report it twice.

        """
        infos = find_deprecation_wrappers(pep702_module, recursive=False, include_pep702=True)
        rows = [info for info in infos if info.function == "stacked_callable"]
        assert [(row.api_type, row.deprecated_info.remove_in) for row in rows] == [("callable", "2.0")]


class TestFindDeprecationWrappersReexport:
    """Re-exported wrappers are attributed to their defining module and never double-counted."""

    def test_reexport_dropped_in_importing_module(self) -> None:
        """A same-package re-export is skipped in the importing module to avoid double-counting.

        A library commonly surfaces a deprecated shim from a private submodule through its package
        ``__init__``. A recursive audit must attribute that wrapper to the module that defines it, not
        report it once per importing module — inflated counts break any CI gate summing ``len(results)``.

        The attribution filter only fires when the defining module shares the same top-level package
        (``_same_top_package`` guard). Wrappers from a wholly different package (e.g. an external
        library re-exposed) are never skipped — they will not be visited elsewhere and must be reported
        where they appear.
        """
        importer = types.ModuleType("importer_mod")

        @deprecated(deprecated_in="1.0", remove_in="2.0")
        def defined_elsewhere() -> None:
            """Wrapper defined in a sibling submodule of the same package."""

        # Simulate a same-package re-export: __module__ points to a submodule of the same top
        # package so _same_top_package("importer_mod.sub", "importer_mod") → True → skip fires.
        defined_elsewhere.__module__ = "importer_mod.sub"
        importer.defined_elsewhere = defined_elsewhere  # type: ignore[attr-defined]

        results = find_deprecation_wrappers(importer)

        assert [r for r in results if r.function == "defined_elsewhere"] == []

    def test_aliased_object_counted_once(self) -> None:
        """The same wrapper object bound under two names in one module is reported once.

        Mirrors the real ``self_ref_typed = cast(..., self_referencing_deprecation)`` alias in the
        misconfigured collection: two names, one underlying object — id-based dedup must collapse them
        so the wrapper is counted exactly once rather than inflating the scan by every extra binding.
        """
        mod = types.ModuleType("alias_mod")

        @deprecated(deprecated_in="1.0", remove_in="2.0")
        def canonical() -> None:
            """Single wrapper object exposed under two names."""

        canonical.__module__ = mod.__name__
        mod.canonical = canonical  # type: ignore[attr-defined]
        mod.alias = canonical  # type: ignore[attr-defined]  # same object, second binding

        results = find_deprecation_wrappers(mod)

        assert len([r for r in results if r.function in ("canonical", "alias")]) == 1


class TestFindDeprecationWrappersClassScan:
    """find_deprecation_wrappers discovers @deprecated on class members, peeking through descriptors."""

    def test_finds_deprecated_regular_method(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deprecated regular method on a class is discovered by find_deprecation_wrappers."""
        mod = types.ModuleType("test_mod_method")

        @deprecated(deprecated_in="1.0", remove_in="2.0")
        def _new(self: object) -> int:
            return 1

        class OldCls:
            old_method = _new

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_method" in n for n in names)

    def test_finds_deprecated_classmethod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deprecated classmethod (correct @classmethod @deprecated order) is discovered."""
        mod = types.ModuleType("test_mod_cm")

        class OldCls:
            @classmethod
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_cm(cls: type) -> int:
                """Old classmethod."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_cm" in n for n in names)

    def test_finds_deprecated_staticmethod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deprecated staticmethod (correct @staticmethod @deprecated order) is discovered."""
        mod = types.ModuleType("test_mod_sm")

        class OldCls:
            @staticmethod
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_sm() -> int:
                """Old staticmethod."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_sm" in n for n in names)

    def test_finds_deprecated_property(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deprecated property (correct @property @deprecated order) is discovered."""
        mod = types.ModuleType("test_mod_prop")

        class OldCls:
            @property
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_prop(self: object) -> int:
                """Old property."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_prop" in n for n in names)

    def test_finds_deprecated_cached_property(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deprecated cached_property (correct @cached_property @deprecated order) is discovered."""
        mod = types.ModuleType("test_mod_cp")

        class OldCls:
            @cached_property
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_cp(self: object) -> int:
                """Old cached_property."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_cp" in n for n in names)

    def test_finds_outer_deprecated_classmethod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Outer @deprecated @classmethod order: wrapper is discovered by audit scan."""
        mod = types.ModuleType("test_mod_outer_cm")

        class OldCls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[arg-type]
            @classmethod
            def old_cm(cls: type) -> int:
                """Old classmethod."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_cm" in n for n in names)

    def test_finds_outer_deprecated_staticmethod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Outer @deprecated @staticmethod order: wrapper is discovered by audit scan."""
        mod = types.ModuleType("test_mod_outer_sm")

        class OldCls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[arg-type]
            @staticmethod
            def old_sm() -> int:
                """Old staticmethod."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_sm" in n for n in names)

    def test_finds_outer_deprecated_property(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Outer @deprecated @property order: wrapper is discovered by audit scan."""
        mod = types.ModuleType("test_mod_outer_prop")

        class OldCls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @property
            def old_prop(self: object) -> int:
                """Old property."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_prop" in n for n in names)

    def test_finds_outer_deprecated_cached_property(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Outer @deprecated @cached_property order: wrapper is discovered by audit scan."""
        mod = types.ModuleType("test_mod_outer_cp")

        class OldCls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @cached_property
            def old_cp(self: object) -> int:
                """Old cached_property."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_cp" in n for n in names)

    def test_finds_setter_only_property(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit property(fget=None, fset=deprecated_fset) is discovered by audit scan."""
        mod = types.ModuleType("test_mod_setter_only")

        def _fset(self: object, v: int) -> None:
            pass

        class OldCls:
            write_only: property = deprecated(deprecated_in="1.0", remove_in="2.0")(property(None, _fset))  # type: ignore[assignment,arg-type]

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("write_only" in n for n in names)

    def test_finds_explicit_construction_fset_deprecated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit property(plain_fget, deprecated_fset): fset accessor is discovered."""
        mod = types.ModuleType("test_mod_explicit_fset")

        def _plain_fget(self: object) -> int:
            return 1

        def _fset(self: object, v: int) -> None:
            pass

        _deprecated_fset = deprecated(deprecated_in="1.0", remove_in="2.0")(_fset)

        class OldCls:
            rw_prop: property = property(_plain_fget, _deprecated_fset)

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("rw_prop" in n for n in names)

    def test_finds_deleter_only_property(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit property(None, None, deprecated_fdel) is discovered by audit scan.

        Symmetric to :meth:`test_finds_setter_only_property` for the fdel accessor: when the only
        deprecation-wrapped accessor on a property is ``fdel``, :func:`find_deprecation_wrappers`
        must traverse the deleter and surface the wrapper.
        """
        mod = types.ModuleType("test_mod_deleter_only")

        def _fdel(self: object) -> None:
            pass

        _deprecated_fdel = deprecated(deprecated_in="1.0", remove_in="2.0")(_fdel)

        class OldCls:
            delete_only: property = property(None, None, _deprecated_fdel)

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("delete_only" in n for n in names)


class TestValidateMappingCompatibility:
    """``validate_mapping_compatibility`` surfaces positional-only incompatibilities."""

    def test_finds_positional_only_wrapper(self) -> None:
        """``DepPositionalOnly`` appears in ``validate_mapping_compatibility`` results.

        The wrapper remaps ``old_val``→``new_val`` which is POSITIONAL_ONLY on
        ``PositionalOnlyTarget``; the validator must surface it.
        """
        results = validate_mapping_compatibility(col, recursive=False)
        names = [r.function for r in results]
        assert "DepPositionalOnly" in names

    def test_dataclass_auto_expanded_visible_in_audit(self) -> None:
        """``find_deprecation_wrappers`` populates ``args_mapping_auto_expanded`` for ``DepAutoExpandDC``.

        After auto-expand the ``DeprecationConfig`` stores the auto-copied keys; the
        ``DeprecationWrapperInfo`` returned by the audit walk must reflect this.
        """
        results = find_deprecation_wrappers(col, recursive=False)
        dc_results = [r for r in results if r.function == "DepAutoExpandDC"]
        assert dc_results, "DepAutoExpandDC not found by find_deprecation_wrappers"
        assert "old_field" in dc_results[0].args_mapping_auto_expanded

    def test_returns_empty_list_for_module_without_positional_only_wrappers(self) -> None:
        """``validate_mapping_compatibility`` returns [] when no wrapper targets POSITIONAL_ONLY params.

        ``tests.collection_misconfigured`` contains only ``@deprecated``-decorated functions
        (not ``deprecated_class`` proxies with ``args_mapping`` to positional-only constructor
        params), so the validator must return an empty list — no false positives.
        """
        results = validate_mapping_compatibility(clean_module, recursive=False)
        assert results == [], (
            f"Expected no positional-only incompatibilities in collection_misconfigured; got: "
            f"{[r.function for r in results]}"
        )

    def test_none_value_in_args_mapping_is_not_false_positive(self) -> None:
        """A ``deprecated_class`` with ``args_mapping={old: None}`` must NOT appear in results.

        ``args_mapping`` values of ``None`` denote warn-only (drop) entries — the proxy never
        attempts to forward the key as a kwarg, so there is no positional-only incompatibility
        to report.  ``_get_args_mapping_positional_only_keys`` correctly skips ``None`` values;
        this test pins that behaviour so a future refactor cannot introduce a false positive.
        """
        # Construct the proxy with a warn-only (None) mapping to the positional-only param name.
        # Suppress the decoration-time UserWarning that fires when a real remap key is positional-only;
        # here "old_val" maps to None (drop), so no UserWarning fires — but wrap defensively.
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            proxy = deprecated_class(
                args_mapping={"old_val": None},
                deprecated_in="1.0",
                remove_in="2.0",
            )(PositionalOnlyTarget)

        info = validate_deprecation_wrapper(proxy)
        assert info.args_mapping_positional_only == [], (
            f"args_mapping={{old_val: None}} must not produce args_mapping_positional_only; "
            f"got: {info.args_mapping_positional_only}"
        )


class TestInnerOrderPropertyAudit:
    """``find_deprecation_wrappers`` flags inner-order ``@property @deprecated`` definitions.

    Inner-order means ``@property`` sits outermost and ``@deprecated`` closer to ``def``, so only ``fget`` gets
    wrapped.  Any setter or deleter rebound afterwards is built from the plain :class:`property` base class and is
    therefore silently unprotected — writes and deletes never warn.  A library author who adopts the inner order by
    habit (mirroring how ``@property`` is normally placed outermost) creates a silent gap that an audit must surface.
    The ``inner_order_property`` flag lets CI pipelines reject this configuration and steer authors toward the
    canonical outer order ``@deprecated(...) @property``.
    """

    def test_inner_order_property_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A class using inner-order property with setter/deleter is flagged ``inner_order_property=True``.

        The shared ``InnerOrderDeprecatedPropCls`` fixture wraps only ``fget`` while exposing a plain setter and
        deleter; scanning the module that holds it must mark the discovered wrapper so maintainers can see at a
        glance that the write and delete paths are unprotected.
        """
        mod = types.ModuleType("test_mod_inner_order_prop")
        monkeypatch.setattr(col.InnerOrderDeprecatedPropCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.InnerOrderDeprecatedPropCls = col.InnerOrderDeprecatedPropCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        prop_results = [r for r in results if r.function.endswith(".value")]
        assert prop_results, f"property 'value' not discovered; got {[r.function for r in results]}"
        assert all(r.inner_order_property for r in prop_results)

    def test_outer_order_property_not_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An outer-order ``_DeprecatedProperty`` is NOT flagged ``inner_order_property``.

        The canonical order ``@deprecated(...) @property`` produces a :class:`_DeprecatedProperty` whose setter and
        deleter re-wrap every rebound accessor, so all paths warn.  This configuration is correct and the audit flag
        must stay ``False`` to avoid false positives that would punish the recommended usage.
        """
        mod = types.ModuleType("test_mod_outer_order_prop")

        class OldCls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @property
            def value(self) -> int:
                """Outer-order deprecated property."""
                return 42

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        prop_results = [r for r in results if r.function.endswith(".value")]
        assert prop_results, f"property 'value' not discovered; got {[r.function for r in results]}"
        assert not any(r.inner_order_property for r in prop_results)

    def test_getter_only_inner_order_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A getter-only inner-order property is ALSO flagged, not just ones carrying a setter.

        The stance is that outer order is canonical: any plain :class:`property` whose ``fget`` is deprecated was
        almost certainly written with the decorators in the wrong order.  Even without a setter the author has
        signalled intent to deprecate the attribute and should migrate to the order that survives future setter or
        deleter additions, so the flag fires for the getter-only shape too.
        """
        mod = types.ModuleType("test_mod_getter_only_inner")

        class OldCls:
            @property
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def value(self) -> int:
                """Inner-order deprecated getter-only property."""
                return 42

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        prop_results = [r for r in results if r.function.endswith(".value")]
        assert prop_results, f"property 'value' not discovered; got {[r.function for r in results]}"
        assert all(r.inner_order_property for r in prop_results)

    def test_inner_order_flag_survives_dataclass_replace(self) -> None:
        """``dataclasses.replace`` on a flagged info preserves ``inner_order_property``.

        Audit results flow through several ``replace`` calls during scanning and report assembly; a regular field
        (not ``init=False``) must round-trip through ``replace`` unchanged so downstream consumers that copy the
        info to adjust an unrelated field do not silently lose the flag.
        """
        info = DeprecationWrapperInfo(
            function="OldCls.value",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="2.0"),
            inner_order_property=True,
        )
        replaced = dataclasses.replace(info, module="some.module")
        assert replaced.inner_order_property is True
        assert replaced.module == "some.module"


def _aud_new_impl() -> int:
    """Replacement callable used as a deprecation target for the private-member scan fixture."""
    return 1


class _AudPrivateMembers:
    """Fixture class carrying deprecated private members across all descriptor kinds."""

    @deprecated(target=_aud_new_impl, deprecated_in="1.0", remove_in="2.0")
    def _legacy(self) -> int:
        return 0

    @classmethod
    @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
    def _cls_legacy(cls) -> int:
        return 0

    @staticmethod
    @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
    def _static_legacy() -> int:
        return 0

    @cached_property
    @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
    def _cached_legacy(self) -> int:
        return 0


class TestScanClassPrivateDeprecated:
    """Deprecated private/dunder members carry ``__deprecation_config__`` and must be surfaced so they can expire."""

    def test_member_meta_peeks_through_descriptor(self) -> None:
        """The helper detects deprecation metadata stored on a descriptor's underlying callable."""
        assert _member_has_deprecation_meta(_AudPrivateMembers.__dict__["_legacy"]) is True

    def test_member_meta_peeks_through_classmethod_descriptor(self) -> None:
        """The helper detects deprecation metadata stored on a classmethod's underlying ``__func__``.

        ``classmethod`` objects store the wrapped function in ``__func__``; ``_member_has_deprecation_meta``
        must unwrap it to find ``__deprecation_config__`` rather than inspecting the ``classmethod`` itself.
        """
        assert _member_has_deprecation_meta(_AudPrivateMembers.__dict__["_cls_legacy"]) is True

    def test_member_meta_peeks_through_staticmethod_descriptor(self) -> None:
        """The helper detects deprecation metadata stored on a staticmethod's underlying ``__func__``."""
        assert _member_has_deprecation_meta(_AudPrivateMembers.__dict__["_static_legacy"]) is True

    def test_member_meta_peeks_through_cached_property_descriptor(self) -> None:
        """The helper detects deprecation metadata stored on a cached_property's ``.func`` attribute."""
        assert _member_has_deprecation_meta(_AudPrivateMembers.__dict__["_cached_legacy"]) is True

    def test_scan_surfaces_deprecated_private_method(self) -> None:
        """A deprecated ``_legacy`` method is included in the scan even though it starts with an underscore.

        Previously ``_scan_class`` skipped every ``_*`` member except ``__init__``, so a deprecated private or
        dunder member could never be flagged as expired — a zombie that outlived its ``remove_in`` unnoticed.
        """
        results = _scan_class(_AudPrivateMembers, "tests.unittests.audit.test__scan", "_AudPrivateMembers")
        functions = [info.function for info in results]
        assert any("_legacy" in fn for fn in functions)

    def test_scan_surfaces_deprecated_private_classmethod(self) -> None:
        """A deprecated private classmethod is discovered by the scan via the ``classmethod.__func__`` path."""
        results = _scan_class(_AudPrivateMembers, "tests.unittests.audit.test__scan", "_AudPrivateMembers")
        functions = [info.function for info in results]
        assert any("_cls_legacy" in fn for fn in functions)

    def test_scan_surfaces_deprecated_private_staticmethod(self) -> None:
        """A deprecated private staticmethod is discovered by the scan via the ``staticmethod.__func__`` path."""
        results = _scan_class(_AudPrivateMembers, "tests.unittests.audit.test__scan", "_AudPrivateMembers")
        functions = [info.function for info in results]
        assert any("_static_legacy" in fn for fn in functions)

    def test_scan_surfaces_deprecated_private_cached_property(self) -> None:
        """A deprecated private cached_property is discovered by the scan via the ``cached_property.func`` path."""
        results = _scan_class(_AudPrivateMembers, "tests.unittests.audit.test__scan", "_AudPrivateMembers")
        functions = [info.function for info in results]
        assert any("_cached_legacy" in fn for fn in functions)
