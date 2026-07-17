"""Integration tests for ``deprecated_module()`` — module-level deprecation via ``__class__`` reassignment.

Tests cover all three operation modes, audit discoverability, reload survival, and warning
stack-level correctness.  Fixture modules live in ``tests/collection_modules/``.
"""

from __future__ import annotations

import importlib
import sys
import types
import warnings
from collections.abc import Iterator
from typing import Any, Callable

import pytest

import tests.collection_modules.new_utils as new_utils
import tests.collection_modules.old_math as old_math
import tests.collection_modules.old_utils as old_utils
from deprecate import (
    TargetMode,
    find_deprecation_wrappers,
    validate_deprecation_expiry,
    validate_deprecation_wrapper,
)
from deprecate._types import DeprecationConfig
from deprecate.audit import _check_deprecated_wrapper_expiry, _format_report_symbol
from deprecate.module import deprecated_module

# Shared version kwargs for module deprecation call sites (see AGENTS.md Unification pattern).
_DEPRS_CASE_MOD_ARGS: dict[str, Any] = {"deprecated_in": "1.0", "remove_in": "2.0"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tmp_module(name: str) -> types.ModuleType:
    """Create and register a fresh throwaway module in ``sys.modules``."""
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _remove_tmp_module(name: str) -> None:
    """Remove a throwaway module from ``sys.modules`` if present."""
    sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# Mode 1: in-place warn
# ---------------------------------------------------------------------------


class TestMode1InPlaceWarn:
    """``deprecated_module()`` with no target and no attrs_mapping emits warning on every public attr access."""

    def test_deprecated_attr_is_set(self) -> None:
        """The ``__deprecated__`` attribute must be a ``DeprecationConfig`` on the module.

        When ``deprecated_module()`` runs it writes ``__deprecated__`` directly to the module
        ``__dict__`` so that audit tools can discover the metadata without triggering the
        ``__getattribute__`` warning path.
        """
        dep = getattr(old_math, "__deprecated__", None)
        assert isinstance(dep, DeprecationConfig)
        assert dep.deprecated_in == "1.0"
        assert dep.remove_in == "2.0"
        assert dep.target is TargetMode.NOTIFY

    def test_missing_attr_warns(self) -> None:
        """Accessing a name absent from ``__dict__`` emits exactly one ``FutureWarning``.

        ``_DeprecatedModuleWrapper.__getattribute__`` fires for every public name, real or missing.
        ``nonexistent_attr`` is never defined in ``old_math``, so the warning fires and
        the hook then raises ``AttributeError`` (caught by ``getattr``'s default).
        """
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = getattr(old_math, "nonexistent_attr", None)
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)

    def test_missing_attr_warning_content(self) -> None:
        """The warning message includes ``deprecated``, version strings, and the custom message.

        Users rely on the warning text to understand what has changed and where to migrate.
        The message must include the deprecation version, the removal version, and any
        caller-supplied custom text.
        """
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = getattr(old_math, "nonexistent_attr", None)
        msg = str(w[0].message).lower()
        assert "deprecated" in msg
        assert "1.0" in msg
        assert "2.0" in msg
        assert "new_math" in msg

    def test_real_attr_warns(self) -> None:
        """Accessing a real attribute (one already in ``__dict__``) emits a ``FutureWarning``.

        Because the module is deprecated and will be removed, every public attribute access
        must warn — including ``square``, which is a real function defined in the module body.
        ``_DeprecatedModuleWrapper.__getattribute__`` intercepts all public attribute lookups,
        not just missing ones, so callers always receive the deprecation notice.
        """
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = old_math.square(4)
        assert result == 16
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)

    def test_missing_attr_raises_attribute_error(self) -> None:
        """In-place warn mode re-raises ``AttributeError`` after the warning.

        Mode 1 has no forwarding target, so after emitting the warning the hook raises
        ``AttributeError`` — the same error Python would raise for a missing attribute on a
        plain module.  Callers using ``getattr(mod, name, default)`` receive the default.
        The ``FutureWarning`` must still be emitted even when the lookup ultimately fails.
        """
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with pytest.raises(AttributeError):
                _ = old_math.truly_missing_attr  # type: ignore[attr-defined]
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)

    def test_dir_does_not_warn(self) -> None:
        """``dir(mod)`` lists the module's real attributes without emitting any warning.

        A developer exploring a deprecated module interactively (e.g. in a REPL, via
        ``dir()``, or via IDE autocompletion) should not be bombarded with warnings just
        for introspecting what is available.  ``dir()`` resolves via ``__dir__``/``__dict__``
        machinery, which the wrapper's ``__getattribute__`` override does not intercept, so no
        ``FutureWarning`` fires and ``square`` is still listed.
        """
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            names = dir(old_math)
        assert "square" in names
        assert len(w) == 0

    def test_repr_does_not_warn(self) -> None:
        """``repr(mod)`` renders the standard module repr without emitting any warning.

        ``repr()`` on a module resolves via the type's ``__repr__``, not through attribute
        lookup on public names, so the deprecation wrapper's ``__getattribute__`` override
        never fires and no ``FutureWarning`` is emitted.
        """
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            text = repr(old_math)
        assert "old_math" in text
        assert len(w) == 0

    def test_vars_does_not_warn(self) -> None:
        """``vars(mod)`` returns the module's ``__dict__`` without emitting any warning.

        Audit tooling and ``find_deprecation_wrappers`` rely on reading ``__deprecated__`` via
        ``vars()``/``__dict__`` access specifically so that metadata introspection does not
        trigger the deprecation warning machinery.  ``vars()`` must therefore stay silent.
        """
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            mapping = vars(old_math)
        assert "square" in mapping
        assert len(w) == 0

    def test_hasattr_warns_even_on_success(self) -> None:
        """``hasattr(mod, "square")`` for a real attribute still emits a ``FutureWarning``.

        ``hasattr()`` is implemented in terms of ``getattr()``, which goes through
        ``_DeprecatedModuleWrapper.__getattribute__``.  That hook emits the warning
        unconditionally for every public name *before* checking whether the name resolves,
        so a successful probe still warns exactly once.
        """
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = hasattr(old_math, "square")
        assert result is True
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)

    def test_hasattr_warns_even_on_failed_probe(self) -> None:
        """``hasattr(mod, "missing_name")`` for a nonexistent attribute still emits a ``FutureWarning``.

        This is the documented, intentional asymmetry versus ``deprecated_instance()``: because
        ``_DeprecatedModuleWrapper.__getattribute__`` warns unconditionally before resolving the
        name, a *failed* ``hasattr()`` probe still triggers the warning even though it ultimately
        returns ``False``.  This is expected behavior for the module-wrapper design, not a bug.
        """
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = hasattr(old_math, "definitely_not_a_real_attr")
        assert result is False
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)


# ---------------------------------------------------------------------------
# Star-import (from ... import *)
# ---------------------------------------------------------------------------


class TestStarImport:
    """``from old_math import *`` still triggers the deprecation warning for each pulled name."""

    def test_star_import_warns(self) -> None:
        """A star-import of a deprecated module emits a ``FutureWarning`` for each public name pulled in.

        ``from tests.collection_modules.old_math import *`` binds every public name from the module's
        namespace (here, ``deprecate`` and ``square``) into the importing scope.  CPython implements the
        star-import via the ``IMPORT_STAR`` bytecode, which calls ``getattr(module, name)`` for each name
        being copied — routing through ``_DeprecatedModuleWrapper.__getattribute__`` exactly like any other
        attribute access — so the warning still fires, once per pulled-in public name.  This is the shipped,
        intended behavior for this design, not an omission.  The statement is executed via ``exec()`` into a
        throwaway namespace (rather than a literal ``import *`` in this function body) so it stays compliant
        with the project's "no local imports inside test functions" rule while still exercising the real
        ``IMPORT_STAR`` bytecode path.
        """
        namespace: dict[str, object] = {}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            exec("from tests.collection_modules.old_math import *", namespace)  # noqa: S102 -- star-import under test, not arbitrary code execution
        future_warns = [x for x in w if issubclass(x.category, FutureWarning)]
        assert len(future_warns) >= 1
        square_fn: Any = namespace["square"]
        assert square_fn(4) == 16


# ---------------------------------------------------------------------------
# Mode 2: redirect
# ---------------------------------------------------------------------------


class TestMode2Redirect:
    """``deprecated_module()`` with a target module forwards attribute lookups to that module."""

    def test_add_warns_and_returns(self) -> None:
        """``old_utils.add`` warns and returns the result forwarded from ``new_utils.add``.

        The deprecated module emits a ``FutureWarning`` and then transparently delegates the
        attribute lookup to ``new_utils``, so ``old_utils.add(2, 3)`` must return ``5``.
        """
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = old_utils.add(2, 3)  # type: ignore[attr-defined]
        assert result == 5
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)

    def test_multiply_warns_and_returns(self) -> None:
        """``old_utils.multiply`` warns and delegates to ``new_utils.multiply``.

        Every attribute access on the deprecated redirect module emits a warning, regardless
        of which attribute is requested.  The forwarded call must produce the correct result.
        """
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = old_utils.multiply(3, 4)  # type: ignore[attr-defined]
        assert result == 12
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)

    def test_missing_on_target_raises(self) -> None:
        """Accessing a name absent from the target module raises ``AttributeError``.

        ``new_utils`` defines only ``add`` and ``multiply``.  Any other name should raise
        ``AttributeError`` after emitting the warning.  The ``FutureWarning`` must be issued
        before the ``AttributeError`` propagates.
        """
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with pytest.raises(AttributeError):
                _ = old_utils.not_on_new_utils  # type: ignore[attr-defined]
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)

    def test_target_stored_as_module_object(self) -> None:
        """``DeprecationConfig.target`` is the redirect module object when one is provided.

        Mode 2 stores the actual ``types.ModuleType`` in ``__deprecated__.target`` so that
        audit tools and report generators can render the redirect destination by name directly
        from the metadata.
        """
        dep = getattr(old_utils, "__deprecated__", None)
        assert isinstance(dep, DeprecationConfig)
        assert dep.target is new_utils


# ---------------------------------------------------------------------------
# Mode 2 variant — per-attribute mapping
# ---------------------------------------------------------------------------


class TestAttrsMapping:
    """``deprecated_module()`` with ``attrs_mapping`` redirects listed names selectively."""

    def setup_method(self) -> None:
        """Register a fresh temporary module before each test."""
        self._mod_name = "_test_attrs_map_tmp"
        mod = _make_tmp_module(self._mod_name)
        mod.new_fn = lambda x: x * 10  # type: ignore[attr-defined]
        deprecated_module(self._mod_name, attrs_mapping={"old_fn": "new_fn"}, **_DEPRS_CASE_MOD_ARGS)

    def teardown_method(self) -> None:
        """Clean up the temporary module after each test."""
        _remove_tmp_module(self._mod_name)

    def test_mapped_attr_warns_and_returns(self) -> None:
        """Accessing a mapped attribute emits a ``FutureWarning`` and returns the redirected value.

        ``attrs_mapping={"old_fn": "new_fn"}`` means ``mod.old_fn`` should warn and then
        return whatever ``mod.new_fn`` is.  The value ``new_fn(5)`` should therefore be ``50``.
        """
        mod = sys.modules[self._mod_name]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            fn = mod.old_fn  # type: ignore[attr-defined]
        result = fn(5)
        assert result == 50
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)

    def test_unmapped_attr_raises(self) -> None:
        """Accessing an attribute not in ``attrs_mapping`` raises ``AttributeError``.

        Per-attribute mapping mode only forwards names explicitly listed in the mapping.
        All other names are treated as missing and raise ``AttributeError`` after warning.
        The ``FutureWarning`` must be issued even when the lookup ultimately fails.
        """
        mod = sys.modules[self._mod_name]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with pytest.raises(AttributeError):
                _ = mod.not_mapped  # type: ignore[attr-defined]
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)

    def test_none_value_in_mapping_raises(self, make_tmp_module: Callable[[str], types.ModuleType]) -> None:
        """A ``None`` value in ``attrs_mapping`` signals "warn but do not redirect".

        When the mapping value is ``None`` the hook emits the warning and then raises
        ``AttributeError``, giving the caller no forwarded object.
        """
        mod_name = "_test_none_val_tmp"
        make_tmp_module(mod_name)
        deprecated_module(mod_name, attrs_mapping={"gone_fn": None}, **_DEPRS_CASE_MOD_ARGS)
        mod = sys.modules[mod_name]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with pytest.raises(AttributeError):
                _ = mod.gone_fn  # type: ignore[attr-defined]
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)


# ---------------------------------------------------------------------------
# Audit discoverability
# ---------------------------------------------------------------------------


class TestAuditDiscoversModule:
    """``find_deprecation_wrappers()`` must detect a deprecated module."""

    def test_finds_one_result(self) -> None:
        """Scanning a deprecated module with ``recursive=False`` returns exactly one result.

        ``find_deprecation_wrappers`` should discover the ``__deprecated__`` attribute set by
        ``deprecated_module()`` and return a single ``DeprecationWrapperInfo`` for the module
        itself, without scanning callable members.
        """
        results = find_deprecation_wrappers(old_math, recursive=False)
        assert len(results) == 1

    def test_result_deprecated_in(self) -> None:
        """The discovered result carries the correct ``deprecated_in`` version string."""
        results = find_deprecation_wrappers(old_math, recursive=False)
        assert results[0].deprecated_info.deprecated_in == "1.0"

    def test_result_remove_in(self) -> None:
        """The discovered result carries the correct ``remove_in`` version string."""
        results = find_deprecation_wrappers(old_math, recursive=False)
        assert results[0].deprecated_info.remove_in == "2.0"

    def test_result_api_type(self) -> None:
        """The discovered result has ``api_type == "module"`` so report tools can identify it."""
        results = find_deprecation_wrappers(old_math, recursive=False)
        assert results[0].api_type == "module"

    def test_result_target_is_notify_for_inplace(self) -> None:
        """``DeprecationConfig.target`` is ``TargetMode.NOTIFY`` when no redirect module is given."""
        results = find_deprecation_wrappers(old_math, recursive=False)
        assert results[0].deprecated_info.target is TargetMode.NOTIFY

    def test_tmp_module_discovered(self) -> None:
        """A dynamically created deprecated module is also discovered by the audit scanner."""
        mod_name = "_test_audit_discover_tmp"
        mod = _make_tmp_module(mod_name)
        try:
            deprecated_module(mod_name, deprecated_in="2.0", remove_in="3.0")
            results = find_deprecation_wrappers(mod, recursive=False)
            assert len(results) == 1
            assert results[0].deprecated_info.deprecated_in == "2.0"
        finally:
            _remove_tmp_module(mod_name)


class TestModuleReportLabel:
    """A deprecated module renders as its bare module name — never a ``(module)`` sentinel.

    Regression guard for the audit rendering bug where ``_scan_module_meta`` stored the sentinel
    ``function="(module)"``.  Because ``_format_report_symbol`` concatenates ``module.function`` and
    the expiry error formatters inlined ``Callable `{info.function}``, a deprecated module surfaced as
    the malformed ``tests.collection_modules.old_math.(module)`` in report rows and as
    ``Callable `(module)``` in expiry messages.  A real CI audit gate that reports an expired module
    must name the module cleanly so a maintainer can find and delete it, and must call it a *Module*
    rather than a *Callable*.  These tests pin the fixture module ``old_math``
    (``deprecated_in="1.0"``, ``remove_in="2.0"``) to the clean fully-qualified rendering across the
    report symbol, the generated report table, and both the batch and single-wrapper expiry paths.
    """

    _MODULE_LABEL = "tests.collection_modules.old_math"

    def test_report_symbol_is_bare_module_name(self) -> None:
        """``_format_report_symbol`` renders the module name with no ``(module)`` suffix or trailing dot."""
        info = find_deprecation_wrappers(old_math, recursive=False)[0]
        assert _format_report_symbol(info) == self._MODULE_LABEL

    def test_wrapper_function_field_is_empty(self) -> None:
        """The module's ``function`` field is the empty sentinel, so nothing can concatenate ``(module)``."""
        info = find_deprecation_wrappers(old_math, recursive=False)[0]
        assert info.function == ""

    def test_batch_expiry_message_names_module(self) -> None:
        """``validate_deprecation_expiry`` reports the expired module as ``Module `<name>```.

        Running the batch CI gate at a version at or past ``remove_in="2.0"`` must flag the module and
        phrase the message with the ``Module`` noun and the clean fully-qualified name — never the
        ``Callable `(module)``` form that the sentinel produced before the fix.
        """
        expired = validate_deprecation_expiry(old_math, "2.0", recursive=False)
        assert expired == [
            f"Module `{self._MODULE_LABEL}` was scheduled for removal in version 2.0"
            " but still exists in version 2.0. Please delete this deprecated code."
        ]

    def test_single_expiry_message_names_module(self) -> None:
        """``_check_deprecated_wrapper_expiry`` raises an ``AssertionError`` naming the module, not ``(module)``.

        The single-wrapper expiry path shares the same subject formatter as the batch path; passing the
        deprecated module directly at its removal version must raise with ``Module `<name>``` so the
        malformed ``Callable `(module)``` label can never reach the error a maintainer reads.
        """
        with pytest.raises(AssertionError, match=r"Module `tests\.collection_modules\.old_math`"):
            _check_deprecated_wrapper_expiry(old_math, "2.0")


# ---------------------------------------------------------------------------
# Reload survival
# ---------------------------------------------------------------------------


class TestReloadSurvival:
    """Reloading a deprecated module must preserve ``__deprecated__`` and the ``__class__``-based wrapper."""

    def test_deprecated_survives_reload(self) -> None:
        """After ``importlib.reload()``, the module still has ``__deprecated__``.

        ``importlib.reload()`` reuses the same module object; ``__deprecated__`` and ``__class__``
        survive unchanged because ``deprecated_module(__name__, ...)`` is called again at the bottom
        of the module body.  The idempotency guard short-circuits that second call — config is NOT
        re-installed.  Note: editing ``deprecated_in``/``remove_in``/``message`` and reloading keeps the
        stale config because the guard exits before overwriting it, but a mismatched second call now
        emits a ``UserWarning`` rather than dropping the reconfiguration silently.
        """
        importlib.reload(old_math)
        assert isinstance(getattr(old_math, "__deprecated__", None), DeprecationConfig)

    def test_attr_access_survives_reload(self) -> None:
        """After ``importlib.reload()``, any public attribute access still emits ``FutureWarning``."""
        importlib.reload(old_math)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = getattr(old_math, "nonexistent_after_reload", None)
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)


# ---------------------------------------------------------------------------
# Stack-level correctness
# ---------------------------------------------------------------------------


class TestStacklevel:
    """The warning ``filename`` must point at the call site, not at ``module.py`` internals."""

    def test_warning_filename_points_to_test(self) -> None:
        """``w[0].filename`` should reference this test file, not ``deprecate/module.py``.

        When a user accesses any attribute on a deprecated module the warning location displayed
        by Python must be the user's source line — not a line inside pyDeprecate's implementation.
        ``stacklevel=3`` inside ``_emit_module_warning`` accounts for the extra helper-function
        frame between ``__getattribute__`` and ``warnings.warn``.
        """
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = getattr(old_math, "stacklevel_check_attr", None)
        assert len(w) == 1
        # The filename must be this test file, not the deprecate implementation module.
        assert "test_module_deprecation" in w[0].filename
        assert "module.py" not in w[0].filename


# ---------------------------------------------------------------------------
# Guard: module not in sys.modules
# ---------------------------------------------------------------------------


class TestGuard:
    """``deprecated_module()`` raises ``ValueError`` for unknown module names."""

    def test_raises_for_unknown_module(self) -> None:
        """Passing a name not in ``sys.modules`` raises ``ValueError`` immediately.

        This guard prevents silent misuse where a typo in the module name would install the
        hook on nothing, leaving the real module undeprecated.
        """
        with pytest.raises(ValueError, match="not in `sys.modules`"):
            deprecated_module("_definitely_not_registered_xyz", **_DEPRS_CASE_MOD_ARGS)

    def test_raises_for_self_target(self, make_tmp_module: Callable[[str], types.ModuleType]) -> None:
        """Passing the module itself as ``target`` raises ``ValueError`` before any wrapper is installed.

        A caller may mistakenly pass ``target=sys.modules[__name__]`` (for example after copy-pasting
        a redirect recipe).  A self-redirect would make every missing-attribute lookup forward back
        into the same wrapper, recursing until the interpreter hits its recursion limit.  The guard
        must reject this up front with a ``ValueError`` naming the offending module, so the mistake
        surfaces at decoration time rather than as an obscure runtime ``RecursionError``.
        """
        mod_name = "_test_self_target_tmp"
        mod = make_tmp_module(mod_name)
        with pytest.raises(ValueError, match=rf"`target`.*{mod_name}.*itself"):
            deprecated_module(mod_name, target=mod, **_DEPRS_CASE_MOD_ARGS)


# ---------------------------------------------------------------------------
# Guard: __slots__ incompatible with __class__ reassignment
# ---------------------------------------------------------------------------


class TestSlotsGuard:
    """``deprecated_module()`` raises ``TypeError`` when the module's type declares ``__slots__``."""

    def test_raises_type_error_for_slotted_module_type(self) -> None:
        """A module whose ``__class__`` declares ``__slots__`` raises ``TypeError`` on deprecation.

        A maintainer might wrap a module in a custom ``types.ModuleType`` subclass — for example a
        lazy-loader or a memory-optimized module shim — that declares ``__slots__`` for a smaller
        instance layout.  ``deprecated_module()`` deprecates by reassigning ``mod.__class__`` to
        :class:`~deprecate.module._DeprecatedModuleWrapper`, which CPython only permits when the old
        and new types share the same C-level instance layout.  A ``__slots__`` type has a different
        layout than the plain ``_DeprecatedModuleWrapper`` (which has no ``__slots__``), so the
        assignment must fail with ``TypeError`` — exactly as documented in ``deprecated_module``'s
        ``Raises:`` section — rather than silently corrupting the module object or crashing later.
        """
        mod_name = "_test_slots_guard_tmp"

        class _SlottedModuleType(types.ModuleType):
            """Custom module subclass with an incompatible ``__slots__`` layout."""

            __slots__ = ("extra_slot",)

        mod = _SlottedModuleType(mod_name)
        sys.modules[mod_name] = mod
        try:
            with warnings.catch_warnings():
                # A UserWarning fires first because `type(mod) is not types.ModuleType`;
                # this test asserts only the documented TypeError, not that warning.
                warnings.simplefilter("ignore")
                with pytest.raises(TypeError, match="__class__ assignment"):
                    deprecated_module(mod_name, **_DEPRS_CASE_MOD_ARGS)
        finally:
            _remove_tmp_module(mod_name)

    def test_failed_install_leaves_no_metadata_and_retry_succeeds(self) -> None:
        """A ``TypeError`` during the ``__class__`` swap rolls back cleanly and a later retry still works.

        A maintainer deprecates a module whose subclass declares ``__slots__``; the ``__class__``
        reassignment raises ``TypeError``.  Because the install is atomic — the class swap happens before any
        ``__deprecated__`` metadata is attached — the module must be left pristine.  If instead the metadata
        lingered, the idempotency guard would treat the module as already deprecated and silently swallow the
        retry that follows the documented fix (wrap in a plain ``types.ModuleType``), leaving the module
        flagged deprecated for audit tools yet never emitting a runtime warning: the worst kind of silent
        half-deprecation.  This test pins both halves: no metadata residue, and a retry that installs the
        wrapper.
        """
        mod_name = "_test_slots_rollback_tmp"

        class _SlottedModuleType(types.ModuleType):
            """Custom module subclass with an incompatible ``__slots__`` layout."""

            __slots__ = ("extra_slot",)

        slotted = _SlottedModuleType(mod_name)
        sys.modules[mod_name] = slotted
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with pytest.raises(TypeError):
                    deprecated_module(mod_name, **_DEPRS_CASE_MOD_ARGS)
            assert "__deprecated__" not in vars(slotted)
            assert "__deprecated_stream__" not in vars(slotted)
            plain = _make_tmp_module(mod_name)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                deprecated_module(mod_name, **_DEPRS_CASE_MOD_ARGS)
            assert type(plain).__name__ == "_DeprecatedModuleWrapper"
        finally:
            _remove_tmp_module(mod_name)


# ---------------------------------------------------------------------------
# attrs_mapping + target combination
# ---------------------------------------------------------------------------


class TestAttrsMappingWithTarget:
    """``attrs_mapping`` plus ``target`` — listed names redirect via mapping; unlisted fall to target."""

    def setup_method(self) -> None:
        """Register fresh temporary modules before each test."""
        self._mod_name = "_test_attrs_target_tmp"
        self._target_name = "_test_attrs_target_new_tmp"
        target = _make_tmp_module(self._target_name)
        target.unmapped_fn = lambda x: x * 2  # type: ignore[attr-defined]
        target.mapped_fn = lambda x: x * 3  # type: ignore[attr-defined]
        _make_tmp_module(self._mod_name)
        deprecated_module(
            self._mod_name,
            target=target,
            attrs_mapping={"old_mapped": "mapped_fn"},
            deprecated_in="1.0",
            remove_in="2.0",
        )

    def teardown_method(self) -> None:
        """Clean up both temporary modules after each test."""
        _remove_tmp_module(self._mod_name)
        _remove_tmp_module(self._target_name)

    def test_unmapped_falls_through_to_target(self) -> None:
        """Names absent from ``attrs_mapping`` fall through to ``target`` after warning.

        When both ``target`` and ``attrs_mapping`` are supplied, the hook first checks the
        mapping dict.  An attribute NOT present in the mapping is forwarded directly to the
        target module, so the call produces the correct result from the target.
        """
        mod = sys.modules[self._mod_name]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            fn = mod.unmapped_fn  # type: ignore[attr-defined]
        assert fn(5) == 10
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)

    def test_mapped_attr_uses_mapping_key(self) -> None:
        """Attributes in ``attrs_mapping`` are redirected via the mapped name, not the original.

        ``old_mapped`` maps to ``"mapped_fn"`` so accessing ``mod.old_mapped`` should return
        ``target.mapped_fn``, which multiplies by 3.
        """
        mod = sys.modules[self._mod_name]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            fn = mod.old_mapped  # type: ignore[attr-defined]
        assert fn(5) == 15
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)

    def test_mapped_value_missing_from_both_raises(self, make_tmp_module: Callable[[str], types.ModuleType]) -> None:
        """A mapped name whose value is absent from both the deprecated module and ``target`` raises.

        A maintainer may write ``attrs_mapping={"old_fn": "renamed_fn"}`` and then rename or remove
        ``renamed_fn`` on the replacement module without updating the mapping — a stale-migration
        mistake.  ``_resolve_mapped`` (module.py:149-165) does not fall back to the deprecated
        module's own ``__dict__`` when a ``target`` is supplied; it only calls
        ``getattr(target, mapped)``.  When the mapped name exists on neither side, that lookup must
        propagate a plain ``AttributeError`` naming the *target* module (not the deprecated source
        module), distinct from ``test_unmapped_falls_through_to_target`` (name absent from the
        mapping, successfully falls through) and ``test_mapped_attr_uses_mapping_key`` (mapped name
        present on the target, resolves successfully).  The ``FutureWarning`` must still fire before
        the ``AttributeError`` propagates.
        """
        target_name = "_test_attrs_missing_both_target_tmp"
        mod_name = "_test_attrs_missing_both_mod_tmp"
        make_tmp_module(target_name)  # target has no attributes at all
        make_tmp_module(mod_name)
        deprecated_module(
            mod_name,
            target=sys.modules[target_name],
            attrs_mapping={"old_fn": "missing_on_both"},
            deprecated_in="1.0",
            remove_in="2.0",
        )
        mod = sys.modules[mod_name]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with pytest.raises(AttributeError, match=target_name):
                _ = mod.old_fn  # type: ignore[attr-defined]
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)


# ---------------------------------------------------------------------------
# Custom stream callable
# ---------------------------------------------------------------------------


class TestStream:
    """Custom ``stream`` callable receives the warning message string."""

    def setup_method(self) -> None:
        """Register a deprecated module that uses a custom stream callable."""
        self._mod_name = "_test_stream_tmp"
        self._calls: list[str] = []
        _make_tmp_module(self._mod_name)
        deprecated_module(self._mod_name, **_DEPRS_CASE_MOD_ARGS, stream=lambda msg, **_kw: self._calls.append(msg))

    def teardown_method(self) -> None:
        """Remove the temporary module after each test."""
        _remove_tmp_module(self._mod_name)

    def test_stream_called_on_attr_access(self) -> None:
        """Accessing a missing attribute invokes the ``stream`` callable instead of ``warnings.warn``.

        When a ``stream`` callable is provided, the hook delegates warning emission to it
        rather than calling ``warnings.warn``.  The callable must be invoked exactly once
        per attribute access, and the first positional argument is the formatted warning
        message string (matching the contract of ``deprecation.py``'s ``_raise_warn``).
        """
        mod = sys.modules[self._mod_name]
        getattr(mod, "some_attr", None)
        assert len(self._calls) == 1
        assert isinstance(self._calls[0], str)

    def test_stream_fallback_when_no_stacklevel(self, make_tmp_module: Callable[[str], types.ModuleType]) -> None:
        """A ``stream`` callable that does not accept ``stacklevel`` does not crash.

        The hook first tries ``stream(msg, stacklevel=3)``.  If the callable raises
        ``TypeError`` (e.g. a zero-kwargs ``lambda msg: ...``), the hook retries with
        ``stream(msg)`` only.  The warning must still reach the stream.
        """
        mod_name = "_test_stream_no_sl_tmp"
        make_tmp_module(mod_name)
        calls: list[str] = []
        deprecated_module(mod_name, **_DEPRS_CASE_MOD_ARGS, stream=lambda msg: calls.append(msg))
        getattr(sys.modules[mod_name], "some_attr", None)
        assert len(calls) == 1
        assert isinstance(calls[0], str)


# ---------------------------------------------------------------------------
# Pytest fixtures for ephemeral module lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture
def make_tmp_module() -> Iterator[Callable[[str], types.ModuleType]]:
    """Factory fixture: create named temp modules; auto-removes each after the test."""
    created: list[str] = []

    def _factory(name: str) -> types.ModuleType:
        mod = _make_tmp_module(name)
        created.append(name)
        return mod

    yield _factory
    for name in created:
        _remove_tmp_module(name)


# ---------------------------------------------------------------------------
# Idempotency guard
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Second call to ``deprecated_module()`` with the SAME configuration is a silent no-op."""

    def test_double_call_same_config_is_silent_no_op(self, make_tmp_module: Callable[[str], types.ModuleType]) -> None:
        """Re-deprecating a module with identical arguments leaves config unchanged and warns nothing.

        This is the ``importlib.reload()`` case: the module body re-runs ``deprecated_module(...)`` with
        exactly the same arguments.  The idempotency guard finds an existing ``DeprecationConfig`` whose
        user-facing identity matches the incoming one, so it short-circuits without re-installing a second
        wrapper and without emitting any warning.  The ``is`` identity of the original ``DeprecationConfig``
        object must be preserved so that repeated reloads stay cheap and side-effect free.
        """
        mod_name = "_test_idempotency_same_tmp"
        make_tmp_module(mod_name)
        deprecated_module(mod_name, **_DEPRS_CASE_MOD_ARGS)
        config_before = vars(sys.modules[mod_name]).get("__deprecated__")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            deprecated_module(mod_name, **_DEPRS_CASE_MOD_ARGS)
        config_after = vars(sys.modules[mod_name]).get("__deprecated__")
        assert config_before is config_after
        assert config_after is not None
        assert config_after.deprecated_in == "1.0"
        assert [x for x in w if issubclass(x.category, UserWarning)] == []


class TestReconfigurationWarns:
    """Second call with a DIFFERENT configuration warns and keeps the original config."""

    def test_different_mode_warns_and_keeps_original(self, make_tmp_module: Callable[[str], types.ModuleType]) -> None:
        """Switching from Mode 1 (in-place warn) to Mode 2 (redirect) on a second call is reported, not dropped.

        A maintainer first marks a module deprecated in-place, then later edits the call to add a redirect
        ``target`` (Mode 1 -> Mode 2) but leaves the original ``deprecated_module`` call in place — a common
        copy-paste-and-tweak mistake, or two conflicting registrations landing in one process.  The
        idempotency guard must not silently swallow this reconfiguration the way it safely swallows an
        identical reload: the second call's redirect would never take effect and the maintainer would have
        no signal.  The guard therefore emits a ``UserWarning`` naming the module and stating the second call
        was ignored, while retaining the ORIGINAL config so behaviour stays deterministic — the redirect is
        NOT installed (``target`` remains ``TargetMode.NOTIFY``), so no ``_DeprecatedModuleWrapper`` change is
        applied for the second call.
        """
        target = make_tmp_module("_test_reconfig_target_tmp")
        target.add = lambda a, b: a + b  # type: ignore[attr-defined]
        mod_name = "_test_reconfig_tmp"
        make_tmp_module(mod_name)
        deprecated_module(mod_name, **_DEPRS_CASE_MOD_ARGS)
        config_before = vars(sys.modules[mod_name]).get("__deprecated__")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            deprecated_module(mod_name, target=target, **_DEPRS_CASE_MOD_ARGS)
        config_after = vars(sys.modules[mod_name]).get("__deprecated__")
        user_warns = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(user_warns) == 1
        assert "different configuration" in str(user_warns[0].message)
        assert "second call ignored" in str(user_warns[0].message)
        # Original config retained: same object identity, redirect NOT installed.
        assert config_after is config_before
        assert config_after is not None
        assert config_after.target is TargetMode.NOTIFY


# ---------------------------------------------------------------------------
# PEP 562 __getattr__ chaining
# ---------------------------------------------------------------------------


class TestGetAttrChaining:
    """Pre-existing ``__getattr__`` on the module is preserved and chained after deprecation."""

    def test_install_emits_user_warning(self, make_tmp_module: Callable[[str], types.ModuleType]) -> None:
        """Installing on a module that already has ``__getattr__`` emits a ``UserWarning``.

        When ``deprecated_module()`` detects a pre-existing ``__getattr__`` in the module
        ``__dict__``, it chains it by storing the original under ``__deprecated_existing_getattr__``
        and emits a one-time ``UserWarning`` to signal the chaining so the developer is aware.
        """
        mod_name = "_test_chain_warn_tmp"
        mod = make_tmp_module(mod_name)
        mod.__getattr__ = lambda name: f"dynamic_{name}"  # type: ignore[attr-defined,method-assign]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            deprecated_module(mod_name, **_DEPRS_CASE_MOD_ARGS)
        user_warns = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(user_warns) == 1
        assert "chaining" in str(user_warns[0].message).lower()

    def test_chained_getattr_resolves_value(self, make_tmp_module: Callable[[str], types.ModuleType]) -> None:
        """Accessing a name resolved by the chained ``__getattr__`` returns the correct value.

        After chaining, missing-attribute lookups that are not handled by ``attrs_mapping`` or
        a redirect ``target`` fall through to the preserved ``__deprecated_existing_getattr__``.
        The ``FutureWarning`` must still fire, and the returned value must come from the original hook.
        """
        mod_name = "_test_chain_resolve_tmp"
        mod = make_tmp_module(mod_name)
        mod.__getattr__ = lambda name: f"resolved_{name}"  # type: ignore[attr-defined,method-assign]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            deprecated_module(mod_name, **_DEPRS_CASE_MOD_ARGS)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = sys.modules[mod_name].dynamic_key  # type: ignore[attr-defined]
        assert result == "resolved_dynamic_key"
        future_warns = [x for x in w if issubclass(x.category, FutureWarning)]
        assert len(future_warns) == 1

    def test_target_miss_falls_back_to_chained_getattr(
        self, make_tmp_module: Callable[[str], types.ModuleType]
    ) -> None:
        """A name absent from the redirect ``target`` falls back to the preserved ``__getattr__``.

        Redirect mode and a bespoke PEP 562 ``__getattr__`` can coexist: names living on the
        replacement module resolve via the target, while dynamically computed names still route
        through the module's own hook.  When ``getattr(target, name)`` raises ``AttributeError``
        the resolver must consult the preserved ``__deprecated_existing_getattr__`` before giving
        up, rather than short-circuiting to ``AttributeError``.  The ``FutureWarning`` must still fire.
        """
        target = make_tmp_module("_test_target_miss_new_tmp")
        target.on_target = lambda: "from_target"  # type: ignore[attr-defined]
        mod_name = "_test_target_miss_tmp"
        mod = make_tmp_module(mod_name)
        mod.__getattr__ = lambda name: f"fallback_{name}"  # type: ignore[attr-defined,method-assign]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            deprecated_module(mod_name, target=target, **_DEPRS_CASE_MOD_ARGS)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = sys.modules[mod_name].only_dynamic  # type: ignore[attr-defined]
        assert result == "fallback_only_dynamic"
        future_warns = [x for x in w if issubclass(x.category, FutureWarning)]
        assert len(future_warns) == 1


# ---------------------------------------------------------------------------
# validate_deprecation_wrapper — module mode
# ---------------------------------------------------------------------------


class TestValidateWrapper:
    """``validate_deprecation_wrapper()`` handles module objects correctly."""

    def test_returns_wrapper_info_for_deprecated_module(self) -> None:
        """Passing a deprecated module returns ``DeprecationWrapperInfo`` with ``api_type="module"``.

        ``validate_deprecation_wrapper`` dispatches on ``inspect.ismodule`` and calls
        ``_scan_module_meta``, which returns a ``DeprecationWrapperInfo`` with safe defaults
        (no invalid args, no misconfig) and ``api_type="module"``.
        """
        info = validate_deprecation_wrapper(old_math)
        assert info.api_type == "module"
        assert info.no_effect is False
        assert info.invalid_args == []

    def test_deprecated_in_propagated(self) -> None:
        """The returned info carries the correct ``deprecated_in`` from the module's config."""
        info = validate_deprecation_wrapper(old_math)
        assert info.deprecated_info.deprecated_in == "1.0"

    def test_raises_for_plain_module(self) -> None:
        """Passing a plain module without ``__deprecated__`` raises ``ValueError``.

        ``validate_deprecation_wrapper`` must not silently return a default result for an
        unconfigured module — it must raise to force the caller to fix the setup.
        """
        plain_mod = types.ModuleType("_plain_test_mod")
        with pytest.raises(ValueError, match="missing or invalid"):
            validate_deprecation_wrapper(plain_mod)


# ---------------------------------------------------------------------------
# attrs_mapping precedence when the mapped name still lives in __dict__
# ---------------------------------------------------------------------------


class TestAttrsMappingShadowsDict:
    """A mapped name must honor ``attrs_mapping`` even when the old body still exists in ``__dict__``."""

    def test_none_mapping_raises_even_when_name_still_defined(
        self, make_tmp_module: Callable[[str], types.ModuleType]
    ) -> None:
        """``{"old": None}`` must raise ``AttributeError`` even if ``old`` is still defined locally.

        The realistic case: a maintainer marks ``helper`` for removal with ``attrs_mapping={"helper": None}``
        but has not yet deleted the ``helper`` body from the module (a mid-migration state).  The removal
        marker must still win — ``mod.helper`` has to raise, not quietly return the stale live function —
        otherwise the "this attribute is gone" contract is a silent no-op and callers keep binding to code
        the maintainer believes they blocked.
        """
        mod_name = "_test_none_shadow_tmp"
        mod = make_tmp_module(mod_name)
        mod.helper = lambda: "STALE"  # type: ignore[attr-defined]
        deprecated_module(mod_name, attrs_mapping={"helper": None}, **_DEPRS_CASE_MOD_ARGS)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with pytest.raises(AttributeError):
                _ = sys.modules[mod_name].helper  # type: ignore[attr-defined]
        assert len(w) == 1
        assert issubclass(w[0].category, FutureWarning)

    def test_rename_no_target_returns_new_not_stale_local(
        self, make_tmp_module: Callable[[str], types.ModuleType]
    ) -> None:
        """``{"old": "new"}`` returns the ``new`` value even when both ``old`` and ``new`` exist locally.

        During a rename window both the legacy ``old_fn`` and its replacement ``new_fn`` typically coexist in
        the module body so nothing breaks abruptly.  Accessing ``mod.old_fn`` must resolve through the mapping
        to ``new_fn``'s value — not short-circuit to the stale local ``old_fn`` via the fast path — so alias
        callers and direct ``new_fn`` callers observe identical behavior.  Exactly one warning must fire,
        proving the mapped-read does not re-enter ``__getattribute__``.
        """
        mod_name = "_test_rename_shadow_tmp"
        mod = make_tmp_module(mod_name)
        mod.old_fn = lambda: "STALE_LOCAL"  # type: ignore[attr-defined]
        mod.new_fn = lambda: "NEW_BODY"  # type: ignore[attr-defined]
        deprecated_module(mod_name, attrs_mapping={"old_fn": "new_fn"}, **_DEPRS_CASE_MOD_ARGS)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            fn = sys.modules[mod_name].old_fn  # type: ignore[attr-defined]
        assert fn() == "NEW_BODY"
        assert len(w) == 1

    def test_rename_with_target_returns_target_over_stale_local(
        self, make_tmp_module: Callable[[str], types.ModuleType]
    ) -> None:
        """A mapped name with a redirect ``target`` resolves on the target, not the stale local body.

        The old module keeps ``old_fn`` defined during the deprecation window while the real implementation
        moves to ``new_mod.new_fn``.  ``attrs_mapping={"old_fn": "new_fn"}`` with ``target=new_mod`` must
        forward to the target's ``new_fn`` so bug fixes there reach alias callers — the local stale body must
        never shadow the mapping.
        """
        target_name = "_test_shadow_target_tmp"
        target = make_tmp_module(target_name)
        target.new_fn = lambda: "TARGET_NEW"  # type: ignore[attr-defined]
        mod_name = "_test_shadow_src_tmp"
        mod = make_tmp_module(mod_name)
        mod.old_fn = lambda: "STALE_LOCAL"  # type: ignore[attr-defined]
        deprecated_module(mod_name, target=target, attrs_mapping={"old_fn": "new_fn"}, **_DEPRS_CASE_MOD_ARGS)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fn = sys.modules[mod_name].old_fn  # type: ignore[attr-defined]
        assert fn() == "TARGET_NEW"

    def test_unmapped_real_attr_still_returns_real_value(
        self, make_tmp_module: Callable[[str], types.ModuleType]
    ) -> None:
        """An unmapped real attribute is unaffected — the mapping only diverts names it lists.

        With a mapping present for other names, an unlisted real attribute must still return its own local
        value via the fast path, confirming the precedence fix diverts selectively and does not funnel every
        public access through the redirect machinery.
        """
        mod_name = "_test_unmapped_kept_tmp"
        mod = make_tmp_module(mod_name)
        mod.kept = lambda: "REAL"  # type: ignore[attr-defined]
        mod.other = lambda: "O"  # type: ignore[attr-defined]
        deprecated_module(mod_name, attrs_mapping={"other": "other"}, **_DEPRS_CASE_MOD_ARGS)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fn = sys.modules[mod_name].kept  # type: ignore[attr-defined]
        assert fn() == "REAL"


# ---------------------------------------------------------------------------
# Cyclic redirect guard
# ---------------------------------------------------------------------------


class TestRedirectCycle:
    """Redirect cycles longer than the trivial self-target must fail cleanly, not recurse forever."""

    def test_mutual_redirect_raises_attribute_error(self, make_tmp_module: Callable[[str], types.ModuleType]) -> None:
        """``A`` redirecting to ``B`` and ``B`` back to ``A`` yields ``AttributeError``, not ``RecursionError``.

        Two modules can end up pointing at each other — e.g. a rename that is later reverted, or two teams
        each deprecating toward the other.  A lookup missing from both would otherwise bounce A→B→A→…  until
        the interpreter hits its recursion limit, emitting a warning on every frame.  The resolution-time
        cycle guard must instead surface the documented ``AttributeError`` after a bounded number of hops.
        """
        a = make_tmp_module("_test_cycle_a_tmp")
        b = make_tmp_module("_test_cycle_b_tmp")
        deprecated_module("_test_cycle_a_tmp", target=b, **_DEPRS_CASE_MOD_ARGS)
        deprecated_module("_test_cycle_b_tmp", target=a, **_DEPRS_CASE_MOD_ARGS)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(AttributeError):
                _ = sys.modules["_test_cycle_a_tmp"].ghost  # type: ignore[attr-defined]

    def test_normal_redirect_unaffected_by_cycle_guard(
        self, make_tmp_module: Callable[[str], types.ModuleType]
    ) -> None:
        """A non-cyclic redirect still forwards correctly after the guard clears its thread-local state.

        The cycle guard tracks in-flight ``(module, name)`` resolutions and must discard them once a lookup
        completes, so an ordinary redirect to a live target returns the target's value on every call — the
        guard must not leave stale entries that poison later lookups.
        """
        target = make_tmp_module("_test_cycle_target_tmp")
        target.real = lambda: "REAL"  # type: ignore[attr-defined]
        make_tmp_module("_test_cycle_src_tmp")
        deprecated_module("_test_cycle_src_tmp", target=target, **_DEPRS_CASE_MOD_ARGS)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            first = sys.modules["_test_cycle_src_tmp"].real  # type: ignore[attr-defined]
            second = sys.modules["_test_cycle_src_tmp"].real  # type: ignore[attr-defined]
        assert first() == "REAL"
        assert second() == "REAL"
