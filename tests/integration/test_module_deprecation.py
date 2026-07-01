"""Integration tests for ``deprecated_module()`` — module-level PEP 562 deprecation.

Tests cover all three operation modes, audit discoverability, reload survival, and warning
stack-level correctness.  Fixture modules live in ``tests/collection_modules/``.
"""

from __future__ import annotations

import importlib
import sys
import types
import warnings

import pytest

import tests.collection_modules.new_utils as new_utils
import tests.collection_modules.old_math as old_math
import tests.collection_modules.old_utils as old_utils
from deprecate import TargetMode, find_deprecation_wrappers, validate_deprecation_wrapper
from deprecate._types import DeprecationConfig
from deprecate.module import deprecated_module

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
        ``__getattr__`` warning hook.
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
        audit tools and report generators can render the redirect destination by name without
        re-inspecting the installed ``__getattr__`` hook.
        """
        dep = getattr(old_utils, "__deprecated__", None)
        assert isinstance(dep, DeprecationConfig)
        assert dep.target is new_utils


# ---------------------------------------------------------------------------
# Mode 3: per-attribute mapping
# ---------------------------------------------------------------------------


class TestAttrsMapping:
    """``deprecated_module()`` with ``attrs_mapping`` redirects listed names selectively."""

    def setup_method(self) -> None:
        """Register a fresh temporary module before each test."""
        self._mod_name = "_test_attrs_map_tmp"
        mod = _make_tmp_module(self._mod_name)
        mod.new_fn = lambda x: x * 10  # type: ignore[attr-defined]
        deprecated_module(self._mod_name, attrs_mapping={"old_fn": "new_fn"}, deprecated_in="1.0", remove_in="2.0")

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

    def test_none_value_in_mapping_raises(self, make_tmp_module: pytest.FixtureRequest) -> None:
        """A ``None`` value in ``attrs_mapping`` signals "warn but do not redirect".

        When the mapping value is ``None`` the hook emits the warning and then raises
        ``AttributeError``, giving the caller no forwarded object.
        """
        mod_name = "_test_none_val_tmp"
        make_tmp_module(mod_name)
        deprecated_module(mod_name, attrs_mapping={"gone_fn": None}, deprecated_in="1.0", remove_in="2.0")
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


# ---------------------------------------------------------------------------
# Reload survival
# ---------------------------------------------------------------------------


class TestReloadSurvival:
    """Reloading a deprecated module must re-install ``__deprecated__`` and ``__getattr__``."""

    def test_deprecated_survives_reload(self) -> None:
        """After ``importlib.reload()``, the module still has ``__deprecated__``.

        ``importlib.reload()`` reuses the same module object; ``__deprecated__`` and ``__class__``
        survive unchanged because ``deprecated_module(__name__, ...)`` is called again at the bottom
        of the module body.  The idempotency guard short-circuits that second call — config is NOT
        re-installed.  Note: editing ``deprecated_in``/``remove_in``/``message`` and reloading silently
        keeps the stale config because the guard exits before overwriting it.
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
            deprecated_module("_definitely_not_registered_xyz", deprecated_in="1.0", remove_in="2.0")


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
        deprecated_module(
            self._mod_name, deprecated_in="1.0", remove_in="2.0", stream=lambda msg, **_kw: self._calls.append(msg)
        )

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

    def test_stream_fallback_when_no_stacklevel(self, make_tmp_module: pytest.FixtureRequest) -> None:
        """A ``stream`` callable that does not accept ``stacklevel`` does not crash.

        The hook first tries ``stream(msg, stacklevel=2)``.  If the callable raises
        ``TypeError`` (e.g. a zero-kwargs ``lambda msg: ...``), the hook retries with
        ``stream(msg)`` only.  The warning must still reach the stream.
        """
        mod_name = "_test_stream_no_sl_tmp"
        make_tmp_module(mod_name)
        calls: list[str] = []
        deprecated_module(mod_name, deprecated_in="1.0", remove_in="2.0", stream=lambda msg: calls.append(msg))
        getattr(sys.modules[mod_name], "some_attr", None)
        assert len(calls) == 1
        assert isinstance(calls[0], str)


# ---------------------------------------------------------------------------
# Pytest fixtures for ephemeral module lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture
def make_tmp_module() -> pytest.FixtureRequest:
    """Factory fixture: create named temp modules; auto-removes each after the test."""
    created: list[str] = []

    def _factory(name: str) -> types.ModuleType:
        mod = _make_tmp_module(name)
        created.append(name)
        return mod

    yield _factory  # type: ignore[misc]
    for name in created:
        _remove_tmp_module(name)


# ---------------------------------------------------------------------------
# Idempotency guard
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Second call to ``deprecated_module()`` on an already-deprecated module is a silent no-op."""

    def test_double_call_is_no_op(self, make_tmp_module: pytest.FixtureRequest) -> None:
        """Calling ``deprecated_module()`` twice on the same module leaves config unchanged.

        The idempotency guard checks for an existing ``DeprecationConfig`` on ``__deprecated__``
        before installing.  A second call with different version strings must not overwrite the
        first config — the ``is`` identity of the ``DeprecationConfig`` object must be preserved.
        This ensures that ``importlib.reload()`` and accidental double-calls both behave safely.
        """
        mod_name = "_test_idempotency_tmp"
        make_tmp_module(mod_name)
        deprecated_module(mod_name, deprecated_in="1.0", remove_in="2.0")
        config_before = vars(sys.modules[mod_name]).get("__deprecated__")
        deprecated_module(mod_name, deprecated_in="9.9", remove_in="10.0")
        config_after = vars(sys.modules[mod_name]).get("__deprecated__")
        assert config_before is config_after
        assert config_after is not None
        assert config_after.deprecated_in == "1.0"


# ---------------------------------------------------------------------------
# PEP 562 __getattr__ chaining
# ---------------------------------------------------------------------------


class TestGetAttrChaining:
    """Pre-existing ``__getattr__`` on the module is preserved and chained after deprecation."""

    def test_install_emits_user_warning(self, make_tmp_module: pytest.FixtureRequest) -> None:
        """Installing on a module that already has ``__getattr__`` emits a ``UserWarning``.

        When ``deprecated_module()`` detects a pre-existing ``__getattr__`` in the module
        ``__dict__``, it chains it by storing the original under ``__deprecated_existing_getattr__``
        and emits a one-time ``UserWarning`` to signal the chaining so the developer is aware.
        """
        mod_name = "_test_chain_warn_tmp"
        mod = make_tmp_module(mod_name)
        mod.__getattr__ = lambda name: f"dynamic_{name}"  # type: ignore[attr-defined]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            deprecated_module(mod_name, deprecated_in="1.0", remove_in="2.0")
        user_warns = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(user_warns) == 1
        assert "chaining" in str(user_warns[0].message).lower()

    def test_chained_getattr_resolves_value(self, make_tmp_module: pytest.FixtureRequest) -> None:
        """Accessing a name resolved by the chained ``__getattr__`` returns the correct value.

        After chaining, missing-attribute lookups that are not handled by ``attrs_mapping`` or
        a redirect ``target`` fall through to the preserved ``__deprecated_existing_getattr__``.
        The ``FutureWarning`` must still fire, and the returned value must come from the original hook.
        """
        mod_name = "_test_chain_resolve_tmp"
        mod = make_tmp_module(mod_name)
        mod.__getattr__ = lambda name: f"resolved_{name}"  # type: ignore[attr-defined]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            deprecated_module(mod_name, deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = sys.modules[mod_name].dynamic_key  # type: ignore[attr-defined]
        assert result == "resolved_dynamic_key"
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
