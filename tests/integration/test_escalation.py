"""Integration tests for Ft-2 (staged warning escalation) across all four decoration shapes.

``escalate=True`` requests a message suffix that ramps as the installed package version nears (or
passes) ``remove_in``; the warning category is never changed (floored at the configured ``stream``,
default :class:`FutureWarning` — see ``.plans/active/plan_future-directions.md`` § Ft-2 for the
grill-locked rationale). Version detection happens once at decoration time, so every test here
monkeypatches :func:`deprecate._version._detect_current_version` *before* applying the decorator —
the escalation note is baked in at that point, not recomputed per call/access.

"""

import sys
import types
import warnings
from collections.abc import Iterator
from typing import Callable, cast

import pytest

from deprecate import TargetMode, deprecated, deprecated_callable, deprecated_class, deprecated_instance
from deprecate import deprecated_module as _deprecated_module
from deprecate._types import _DeprecatedCallable, get_deprecation_config
from deprecate.audit import validate_deprecation_policy
from tests.collection_targets import NewCls, double_value

#: Factory fixture type: pins the "current installed version" that decoration-time escalation detects.
VersionPinner = Callable[[str], None]


@pytest.fixture
def fixed_current_version(monkeypatch: pytest.MonkeyPatch) -> VersionPinner:
    """Factory fixture: pin the detected "current installed version" for the wrapper built inside a test."""

    def _pin(version: str) -> None:
        monkeypatch.setattr("deprecate._version._detect_current_version", lambda _module_name: version)

    return _pin


class TestCallableEscalation:
    """``deprecated_callable(escalate=True)`` ramps the emitted warning message."""

    def test_default_escalate_false_leaves_message_unchanged(self) -> None:
        """Omitting ``escalate`` produces the exact same message as before Ft-2 existed.

        The default must be a true no-op: every existing ``@deprecated`` call site in the wild sees
        byte-identical warning text after upgrading to a version carrying Ft-2.
        """
        wrapped = deprecated_callable(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")(double_value)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            wrapped(3)
        assert str(caught[0].message) == "The `double_value` was deprecated since v1.0. It will be removed in v2.0."

    def test_mid_window_appends_no_suffix(self, fixed_current_version: VersionPinner) -> None:
        """``escalate=True`` mid-window (current version well below ``remove_in``) adds nothing yet."""
        fixed_current_version("1.2")
        wrapped = deprecated_callable(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0", escalate=True)(
            double_value
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            wrapped(3)
        assert str(caught[0].message) == "The `double_value` was deprecated since v1.0. It will be removed in v2.0."

    def test_overdue_appends_past_removal_suffix(self, fixed_current_version: VersionPinner) -> None:
        """Once the installed version reaches ``remove_in``, the message ramps to "past removal"."""
        fixed_current_version("2.0")
        wrapped = deprecated_callable(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0", escalate=True)(
            double_value
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            wrapped(3)
        msg = str(caught[0].message)
        assert msg.startswith("The `double_value` was deprecated since v1.0. It was due to be removed in v2.0.")
        assert "planned removal" in msg

    def test_imminent_keeps_future_tense_base_message(self, fixed_current_version: VersionPinner) -> None:
        """At a pre-release of ``remove_in`` the base clause still reads "will be removed".

        The removal has not happened yet at this point — the release carrying it is only in its RC — so
        the promise is still accurate and only the past-removal tier has cause to flip it.
        """
        fixed_current_version("2.0rc1")
        wrapped = deprecated_callable(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0", escalate=True)(
            double_value
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            wrapped(3)
        msg = str(caught[0].message)
        assert msg.startswith("The `double_value` was deprecated since v1.0. It will be removed in v2.0.")
        assert "imminent" in msg

    def test_overdue_leaves_custom_message_template_untouched(self, fixed_current_version: VersionPinner) -> None:
        """A caller-supplied ``message_template`` keeps its own wording even when the removal is overdue.

        The past-tense swap exists to stop the library contradicting itself in text the library wrote.
        Text the caller wrote is theirs: it may deliberately use a different tense, voice, or language,
        so the ramp appends its note and changes nothing else.
        """
        fixed_current_version("2.0")
        wrapped = deprecated_callable(
            target=TargetMode.NOTIFY,
            deprecated_in="1.0",
            remove_in="2.0",
            escalate=True,
            message_template="`%(source_name)s` goes away in v%(remove_in)s.",
        )(double_value)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            wrapped(3)
        msg = str(caught[0].message)
        assert msg.startswith("`double_value` goes away in v2.0.")
        assert "planned removal" in msg

    def test_category_stays_future_warning_even_when_overdue(self, fixed_current_version: VersionPinner) -> None:
        """The escalation ramp never changes the warning category — only the message text.

        Grill-locked design decision (§ Ft-2): a Pending->Deprecation->Future category ladder would
        *downgrade* visibility below today's default ``FutureWarning`` for most of the window, since
        Python's default filters ignore ``PendingDeprecationWarning``/``DeprecationWarning`` outside
        ``__main__``. Escalation is message urgency only.
        """
        fixed_current_version("2.0")
        wrapped = deprecated_callable(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0", escalate=True)(
            double_value
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            wrapped(3)
        assert issubclass(caught[0].category, FutureWarning)

    def test_escalation_note_is_precomputed_at_decoration_time(self, fixed_current_version: VersionPinner) -> None:
        """The note is frozen into ``__deprecation_config__`` once, not recomputed on every call.

        Changing what :func:`_detect_current_version` returns *after* decoration must not affect an
        already-built wrapper — the installed package version cannot change mid-process, so
        recomputing per call would only add cost with no behavioural benefit.
        """
        fixed_current_version("2.0")
        wrapped = deprecated_callable(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0", escalate=True)(
            double_value
        )
        cfg = get_deprecation_config(cast(_DeprecatedCallable, wrapped))
        assert cfg is not None
        assert "planned removal" in cfg.escalation_note


class TestFrontDoorEscalation:
    """The ``deprecated()`` front door forwards ``escalate`` to both its callable and class dispatch arms."""

    def test_forwards_to_callable_arm(self, fixed_current_version: VersionPinner) -> None:
        """``@deprecated(escalate=True)`` on a function reaches the same ramp as ``deprecated_callable``."""
        fixed_current_version("2.0")
        wrapped = deprecated(deprecated_in="1.0", remove_in="2.0", escalate=True)(double_value)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            wrapped(3)
        assert "planned removal" in str(caught[0].message)

    def test_forwards_to_class_arm(self, fixed_current_version: VersionPinner) -> None:
        """``@deprecated(escalate=True)`` on a class reaches the same ramp as ``deprecated_class``."""
        fixed_current_version("2.0")
        proxy = deprecated(deprecated_in="1.0", remove_in="2.0", escalate=True, stream=warnings.warn)(NewCls)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy(1.0)
        messages = [str(item.message) for item in caught]
        assert any("planned removal" in msg for msg in messages)


class TestClassProxyEscalation:
    """``deprecated_class(escalate=True)`` ramps the message emitted on proxy access."""

    def test_overdue_appends_past_removal_suffix(self, fixed_current_version: VersionPinner) -> None:
        """A class proxy accessed after ``remove_in`` has passed gets the same ramp as a callable."""
        fixed_current_version("2.0")
        proxy = deprecated_class(deprecated_in="1.0", remove_in="2.0", escalate=True, stream=warnings.warn)(NewCls)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy(1.0)
        assert "planned removal" in str(caught[0].message)

    def test_default_escalate_false_leaves_message_unchanged(self) -> None:
        """Omitting ``escalate`` on ``deprecated_class`` leaves the pre-Ft-2 message untouched."""
        proxy = deprecated_class(deprecated_in="1.0", remove_in="2.0", stream=warnings.warn)(NewCls)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy(1.0)
        assert "planned removal" not in str(caught[0].message)


class TestInstanceProxyEscalation:
    """``deprecated_instance(escalate=True)`` ramps the message emitted on proxy access."""

    def test_overdue_appends_past_removal_suffix(self, fixed_current_version: VersionPinner) -> None:
        """An object proxy accessed after ``remove_in`` has passed gets the same ramp as a callable."""
        fixed_current_version("2.0")
        proxy = deprecated_instance(
            {"a": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", escalate=True, stream=warnings.warn
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy["a"]
        assert "planned removal" in str(caught[0].message)

    def test_version_detection_uses_callers_module_not_builtins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``deprecated_instance({"a": 1}, escalate=True)`` detects the CALLER's package version.

        A dict/list/str constant — the documented primary ``deprecated_instance`` use case — has no
        useful ``__module__`` of its own (``type({}).__module__ == "builtins"``); detecting "builtins"'
        version would make escalation permanently inert for exactly this common case. The caller's own
        module (this test file, whose top-level package is ``tests``) is what must be probed instead.
        """
        seen_module_names: list[str] = []

        def _record_and_return_none(module_name: str) -> None:
            seen_module_names.append(module_name)
            return

        monkeypatch.setattr("deprecate._version._detect_current_version", _record_and_return_none)
        deprecated_instance({"a": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", escalate=True)
        assert seen_module_names == ["tests.integration.test_escalation"]


class TestModuleEscalation:
    """``deprecated_module(escalate=True)`` ramps the message emitted on attribute access."""

    @pytest.fixture
    def tmp_module(self) -> Iterator[types.ModuleType]:
        """A throwaway module registered in ``sys.modules``, removed after the test."""
        mod = types.ModuleType("_test_escalation_tmp_module")
        sys.modules[mod.__name__] = mod
        yield mod
        sys.modules.pop(mod.__name__, None)

    def test_overdue_appends_past_removal_suffix(
        self, tmp_module: types.ModuleType, fixed_current_version: VersionPinner
    ) -> None:
        """A deprecated module accessed after ``remove_in`` has passed gets the same ramp as a callable.

        The module path stores its message fully rendered rather than as a template, so the past-tense
        swap has to happen on the rendered suffix — this test is what proves the two paths still agree.
        """
        fixed_current_version("2.0")
        _deprecated_module(tmp_module.__name__, deprecated_in="1.0", remove_in="2.0", escalate=True)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            getattr(tmp_module, "anything", None)
        msg = str(caught[0].message)
        assert "planned removal" in msg
        assert "It was due to be removed in v2.0." in msg
        assert "will be removed" not in msg

    def test_default_escalate_false_leaves_message_unchanged(self, tmp_module: types.ModuleType) -> None:
        """Omitting ``escalate`` on ``deprecated_module`` leaves the pre-Ft-2 message untouched."""
        _deprecated_module(tmp_module.__name__, deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            getattr(tmp_module, "anything", None)
        assert "planned removal" not in str(caught[0].message)

    def test_static_dunder_deprecated_stays_phase_less(
        self, tmp_module: types.ModuleType, fixed_current_version: VersionPinner
    ) -> None:
        """``__deprecated__`` (the PEP-702-style static message) never carries the escalation ramp.

        It is a decoration-time snapshot rendered once (R-1's design), shared by the callable/proxy
        paths too — only the message actually emitted at each call/access carries the ramp. A prior
        draft of this feature accidentally baked the ramp into the module's ``message_template``, which
        both this attribute and the emitted warning read from, making ``__deprecated__`` wrongly
        escalation-aware for modules only.
        """
        fixed_current_version("2.0")
        _deprecated_module(tmp_module.__name__, deprecated_in="1.0", remove_in="2.0", escalate=True)
        assert "planned removal" not in tmp_module.__deprecated__  # type: ignore[attr-defined]
        assert "It will be removed in v2.0." in tmp_module.__deprecated__  # type: ignore[attr-defined]

    def test_message_required_policy_still_flags_escalate_only_module(
        self, tmp_module: types.ModuleType, fixed_current_version: VersionPinner
    ) -> None:
        """The escalation ramp text does not count as ``message_required`` migration guidance.

        ``validate_deprecation_policy``'s ``message_required`` rule detects an auto-rendered,
        non-custom ``message_template`` by recomputing the built-in template and comparing it for
        equality against the stored one. If the escalation note were baked into ``message_template``
        (a prior draft did this), that comparison would mismatch and the rule would wrongly treat the
        ramp as caller-supplied guidance, silently passing a module with no real migration guidance.
        """
        fixed_current_version("2.0")
        _deprecated_module(tmp_module.__name__, deprecated_in="1.0", remove_in="2.0", escalate=True)
        violations = validate_deprecation_policy(tmp_module, recursive=False, min_grace=None, message_required=True)
        assert any("[message-required]" in v for v in violations)
