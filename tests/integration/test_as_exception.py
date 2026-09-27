"""Tests for fatal deprecations — `as_exception` and the global `AS_EXCEPTIONS` switch.

Covers the callable path (`@deprecated` / `deprecated_callable`) plus the shared resolution rules: monotonic
precedence between a wrapper's own flag and the process-wide switch, the warn-budget bypass that makes a fatal
deprecation raise on every call rather than once, and the interaction with `skip_if` and `stream=None`.

"""

import logging
from typing import Any

import pytest

import deprecate
from deprecate import (
    AS_EXCEPTIONS,
    DeprecatedError,
    TargetMode,
    assert_no_warnings,
    deprecated,
    find_deprecation_wrappers,
)
from tests.collection_deprecate import (
    FatalLegacyWidget,
    decorated_args_fatal,
    decorated_async_fatal,
    decorated_remap_fatal,
    decorated_sum_fatal,
    decorated_sum_fatal_no_stream,
    decorated_sum_fatal_notify,
    decorated_sum_fatal_opt_out,
    decorated_sum_fatal_skipped,
    fatal_legacy_settings,
    wrapped_sum,
)
from tests.collection_targets import base_sum_kwargs, double_value, identity_value

_VERSION_ARGS: dict[str, Any] = {"deprecated_in": "1.0", "remove_in": "2.0"}


class TestFatalCallable:
    """A callable deprecation configured with `as_exception=True`."""

    def test_raises_instead_of_warning(self) -> None:
        """Calling a fatal deprecated function raises `DeprecatedError` and emits no warning.

        This is the whole point of the feature: an author past the polite-warning stage wants the call to fail
        the build, and a caller who filtered warnings away must not be able to keep using the symbol quietly.
        """
        with assert_no_warnings(), pytest.raises(DeprecatedError):
            decorated_sum_fatal(2, 3)

    def test_message_matches_the_warning_text(self) -> None:
        """The raised message is byte-for-byte the text the warning would have carried.

        Authors write one migration message naming the replacement; a fatal deprecation must not degrade it to
        a bare "deprecated" string, or the caller loses the only pointer to the new API.
        """
        warning_form = deprecated(target=double_value, **_VERSION_ARGS)(identity_value)
        fatal_form = deprecated(target=double_value, **_VERSION_ARGS, as_exception=True)(identity_value)
        with pytest.warns(FutureWarning) as warned:
            warning_form(2)
        with pytest.raises(DeprecatedError) as fatal:
            fatal_form(2)
        assert str(fatal.value) == str(warned[0].message)

    def test_raises_on_every_call_not_just_the_first(self) -> None:
        """A fatal deprecation ignores the warn budget — call 2 and call 3 raise exactly like call 1.

        The budget gate sits ahead of the warning emitter, so a hand-rolled raising `stream` raises once with
        the default `num_warns=1` and then silently forwards forever. A caller that catches `DeprecatedError`
        in a retry loop would otherwise slip straight through the gate on its second attempt.
        """
        for _ in range(3):
            with pytest.raises(DeprecatedError):
                decorated_sum_fatal(2, 3)

    def test_target_is_never_invoked(self) -> None:
        """The replacement target does not run when the deprecation is fatal.

        A fatal deprecation is a closed door, not a warning plus a redirect: if the target still ran, a strict
        CI run would mutate state (write a file, call an API) before failing the build.
        """
        calls: list[tuple] = []

        def _spy(a: int = 0, b: int = 3) -> int:
            calls.append((a, b))
            return a + b

        fatal = deprecated(target=_spy, **_VERSION_ARGS, as_exception=True)(base_sum_kwargs)
        with pytest.raises(DeprecatedError):
            fatal(1, b=2)
        assert calls == []

    def test_notify_mode_does_not_execute_the_source_body(self) -> None:
        """A fatal `TargetMode.NOTIFY` deprecation raises instead of falling through to the body.

        NOTIFY normally warns and then runs the body, which is the mode most likely to surprise: an author
        flipping it to fatal expects the call to stop, not to warn and continue.
        """
        with pytest.raises(DeprecatedError):
            decorated_sum_fatal_notify(2, 3)

    def test_args_remap_does_not_execute_the_source_body(self) -> None:
        """A fatal `TargetMode.ARGS_REMAP` deprecation raises instead of running the remapped body.

        ARGS_REMAP is self-deprecation — source and target are the same function — so raising before the body
        is the only way to stop the call, which a caller relying on the return value must observe.
        """
        with pytest.raises(DeprecatedError):
            decorated_remap_fatal(old_x=4)

    def test_renamed_argument_reason_also_raises(self) -> None:
        """Passing a deprecated *argument* name to a fatal wrapper raises, not only a deprecated callable.

        Argument renames run through a separate emitter with its own per-argument budget; a gate covering only
        the callable reason would let every renamed-kwarg call through untouched.
        """
        with pytest.raises(DeprecatedError):
            decorated_args_fatal(old_a=1)

    def test_raises_even_when_the_stream_is_silenced(self) -> None:
        """`stream=None` silences the message but does not disable the gate.

        `stream=None` means "say nothing"; fatal mode is a lifecycle state, not an output channel. Letting the
        silencer switch off the raise would make a strict CI run depend on an unrelated cosmetic setting.
        """
        with assert_no_warnings(), pytest.raises(DeprecatedError):
            decorated_sum_fatal_no_stream(2, 3)

    def test_skip_if_suppresses_the_raise(self) -> None:
        """`skip_if=True` skips the deprecation entirely, fatal or not, and the body still returns.

        A wrapper skipped by configuration (a feature flag, a compatibility shim) is not a deprecation at that
        moment, so a strict run must not fail on it.
        """
        with assert_no_warnings():
            assert decorated_sum_fatal_skipped(2, 3) == 5

    def test_error_is_an_ordinary_exception_not_a_warning(self) -> None:
        """`DeprecatedError` is caught by `except Exception` and is not a `Warning` subclass.

        Downstream error handling already brackets calls with `except Exception`; a fatal deprecation should
        land there rather than in warning-filter machinery, and a `Warning` subclass would confuse both.
        """
        assert issubclass(DeprecatedError, RuntimeError)
        assert not issubclass(DeprecatedError, Warning)
        with pytest.raises(Exception, match="decorated_sum_fatal"):
            decorated_sum_fatal(2, 3)

    @pytest.mark.asyncio
    async def test_async_source_raises_on_await(self) -> None:
        """An `async def` source with `as_exception=True` raises when awaited.

        The async wrapper shares the call-plan engine with the sync one, so the gate should apply for free —
        but "should apply for free" is exactly the assumption that silently rots, and an unguarded async path
        would let every coroutine-shaped deprecation through a strict run.
        """
        with pytest.raises(DeprecatedError):
            await decorated_async_fatal(3)

    def test_audit_still_discovers_a_fatal_module(self) -> None:
        """`find_deprecation_wrappers` enumerates a fatally deprecated module without triggering it.

        The audit scanner is itself an attribute-walker, so a naive `getattr` walk would make the tool that
        lists deprecations blow up on the very deprecations it is meant to report. It reads `__dict__`, so the
        fatal module is discovered like any other — this locks that in.
        """
        from tests import collection_modules

        found = {info.module for info in find_deprecation_wrappers(collection_modules, recursive=True)}
        assert "tests.collection_modules.fatal_utils" in found

    def test_non_warnings_stream_still_raises(self) -> None:
        """A wrapper routing through `logging.warning` raises when fatal — no warnings filter can do this.

        This is one of the two capabilities `-W error::FutureWarning` cannot provide: a `logging` stream never
        reaches the warnings machinery, so without `as_exception` such a wrapper has no fail-fast path at all.
        """
        fatal = deprecated(target=TargetMode.NOTIFY, **_VERSION_ARGS, as_exception=True, stream=logging.warning)(
            base_sum_kwargs
        )
        with pytest.raises(DeprecatedError):
            fatal(1)


class TestFatalProxyAndModule:
    """Fatal deprecations on the other three entry points: class, instance, and whole-module wrappers."""

    def test_class_instantiation_raises(self) -> None:
        """Instantiating a fatally deprecated class raises instead of warning and constructing.

        A class alias kept alive only for backwards compatibility is the classic warn/raise/delete candidate:
        the author wants the constructor call to fail before any object exists.
        """
        with assert_no_warnings(), pytest.raises(DeprecatedError):
            FatalLegacyWidget(2)

    def test_class_attribute_access_raises_every_time(self) -> None:
        """Reading an attribute off the fatal class proxy raises on each access, not only the first.

        The proxy keeps its own warn counter, separate from the callable path's, so the budget bypass has to be
        proven here too or a second access would silently succeed.
        """
        for _ in range(2):
            with pytest.raises(DeprecatedError):
                _ = FatalLegacyWidget.DEFAULT_SIZE

    def test_instance_item_access_raises(self) -> None:
        """Reading a key from a fatally deprecated instance proxy raises.

        `deprecated_instance` most often wraps a module-level config dict or constant, where the access is a
        subscript rather than a call — that path routes through the proxy's item dunders.
        """
        with assert_no_warnings(), pytest.raises(DeprecatedError):
            _ = fatal_legacy_settings["threshold"]

    def test_structural_probes_stay_silent(self) -> None:
        """`isinstance`, `repr`, and equality never raise on a fatal proxy — they never warned either.

        The proxy deliberately keeps structural probes warning-free so duck-typing, debuggers, and pickling
        stay transparent; promoting those to exceptions would break `repr()` in a debugger session.
        """
        with assert_no_warnings():
            assert repr(FatalLegacyWidget)
            assert not isinstance(object(), FatalLegacyWidget)
            assert fatal_legacy_settings == fatal_legacy_settings

    def test_module_attribute_access_raises(self) -> None:
        """Touching any public attribute of a fatally deprecated module raises.

        Whole-module deprecation is normally `num_warns=-1` (warn on every access); fatal mode turns the same
        surface into a hard stop, which is how an author retires a module before deleting the file.
        """
        from tests.collection_modules import fatal_utils

        with assert_no_warnings(), pytest.raises(DeprecatedError):
            _ = fatal_utils.WIDGET_LIMIT


class TestGlobalSwitch:
    """The process-wide `deprecate.AS_EXCEPTIONS` switch and its precedence against a wrapper's own flag."""

    def test_default_is_off(self) -> None:
        """Out of the box nothing is fatal — the import-time value is `False` with the env var unset.

        Fatal deprecations must be opt-in: a release that silently turned existing warnings into exceptions
        would break every downstream caller on upgrade.
        """
        assert AS_EXCEPTIONS is False
        with pytest.warns(FutureWarning):
            wrapped_sum(2, 3)

    def test_promotes_an_unconfigured_wrapper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With the global on, a wrapper that never mentions `as_exception` raises.

        This is the CI use case: a consumer flips one environment variable to find every deprecated call in
        their own code, without patching the library that declared the deprecations.
        """
        monkeypatch.setattr(deprecate, "AS_EXCEPTIONS", True)
        with pytest.raises(DeprecatedError):
            wrapped_sum(2, 3)

    def test_wrapper_false_yields_to_the_global(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An explicit `as_exception=False` still raises while the global is on — precedence is monotonic.

        Otherwise a consumer's strict run could be escaped by an upstream author's per-wrapper opt-out, which
        is exactly the deprecation they most need to find.
        """
        monkeypatch.setattr(deprecate, "AS_EXCEPTIONS", True)
        with pytest.raises(DeprecatedError):
            decorated_sum_fatal_opt_out(2, 3)

    def test_wrapper_false_warns_while_the_global_is_off(self) -> None:
        """With the global off, `as_exception=False` behaves exactly like the default — it warns.

        `False` means "not fatal by default", so it must not read as a third state that changes the ordinary
        warning behaviour of a wrapper.
        """
        with pytest.warns(FutureWarning):
            assert decorated_sum_fatal_opt_out(2, 3) == 5

    def test_wrapper_true_raises_while_the_global_is_off(self) -> None:
        """A wrapper's own `as_exception=True` is fatal without any global or environment setting.

        An author promoting one symbol ahead of the rest — the warn/raise/delete lifecycle step — must not need
        their consumers to set an environment variable for it to take effect.
        """
        assert deprecate.AS_EXCEPTIONS is False
        with pytest.raises(DeprecatedError):
            decorated_sum_fatal(2, 3)

    def test_switch_is_read_per_call_not_captured_at_decoration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Flipping the global after decoration changes the outcome of the next call on the same wrapper.

        Applications enable strict mode during bootstrap and test suites toggle it per case, both of which
        happen long after module import decorated the wrappers.
        """
        warn_only = deprecated(target=double_value, **_VERSION_ARGS, num_warns=-1)(identity_value)
        with pytest.warns(FutureWarning):
            warn_only(2)
        monkeypatch.setattr(deprecate, "AS_EXCEPTIONS", True)
        with pytest.raises(DeprecatedError):
            warn_only(2)
