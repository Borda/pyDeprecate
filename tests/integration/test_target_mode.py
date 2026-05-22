"""Demo / contract tests for TargetMode enum and misconfiguration guards.

These tests were written BEFORE the implementation to crystallise the API contract.
They define the expected behaviour for:
- TargetMode.NOTIFY  (replaces target=None)
- TargetMode.ARGS_REMAP (replaces target=True)
- Construction-time UserWarning for misconfigurations
- FutureWarning for legacy target=None / target=True sentinels
"""

import warnings
from typing import Callable, cast

import pytest

from deprecate import TargetMode, deprecated
from deprecate import TargetMode as top_level_TargetMode
from deprecate._types import _DeprecatedCallable
from tests.collection_depr_legacy import (
    depr_target_mode_args_only_remaps_kwargs as legacy_remaps_kwargs,
)
from tests.collection_depr_legacy import (
    depr_target_mode_args_only_silent_when_new_arg_passed as legacy_silent_when_new_arg_passed,
)
from tests.collection_depr_legacy import (
    depr_target_mode_args_only_warns_when_old_arg_passed as legacy_warns_when_old_arg_passed,
)
from tests.collection_depr_legacy import (
    depr_target_mode_args_only_with_args_extra_injects_kwargs as depr_legacy_args_extra,
)
from tests.collection_depr_legacy import (
    depr_target_mode_whole_executes_original_body as legacy_executes_original_body,
)
from tests.collection_depr_legacy import (
    depr_target_mode_whole_warns_on_every_call as legacy_warns_on_every_call,
)
from tests.collection_deprecate import (
    depr_target_mode_args_only_remaps_kwargs,
    depr_target_mode_args_only_silent_when_new_arg_passed,
    depr_target_mode_args_only_warns_when_old_arg_passed,
    depr_target_mode_args_only_with_args_extra_injects_kwargs,
    depr_target_mode_whole_executes_original_body,
    depr_target_mode_whole_warns_on_every_call,
    make_callable_target_no_versions_warns,
    make_default_target_no_versions_warns,
    make_default_target_with_versions,
    make_explicit_notify_no_versions_warns,
    make_partial_version_no_guard_warn,
    make_target_mode_args_only_without_args_mapping_warns,
    make_target_mode_whole_with_args_extra_warns,
    make_target_mode_whole_with_args_mapping_warns,
)
from tests.collection_targets import tracked_identity_calls


class TestTargetModeContract:
    """TargetMode enum public contract — membership and importability."""

    def test_members_exist(self) -> None:
        """TargetMode exposes exactly NOTIFY and ARGS_REMAP members."""
        assert hasattr(TargetMode, "NOTIFY")
        assert hasattr(TargetMode, "ARGS_REMAP")
        assert len(list(TargetMode)) == 2

    def test_importable_from_top_level(self) -> None:
        """TargetMode is part of the public API surface."""
        assert top_level_TargetMode is TargetMode


class TestNotifyMode:
    """TargetMode.NOTIFY — warns every call; executes original body."""

    @pytest.fixture(autouse=True)
    def _reset_deprecation_state(self) -> None:
        """Reset warning state before each test."""
        for func in (
            depr_target_mode_whole_warns_on_every_call,
            depr_target_mode_whole_executes_original_body,
            legacy_warns_on_every_call,
            legacy_executes_original_body,
        ):
            state = cast(_DeprecatedCallable, func)._state
            state.warned_calls = 0
            state.warned_args.clear()

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(depr_target_mode_whole_warns_on_every_call, id="modern"),
            pytest.param(legacy_warns_on_every_call, id="legacy"),
        ],
    )
    def test_warns_on_every_call(self, func: Callable[..., int]) -> None:
        """NOTIFY mode emits a FutureWarning on each of 3 consecutive calls (num_warns=-1)."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", FutureWarning)
            func(1)
            func(2)
            func(3)

        future_warns = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert len(future_warns) == 3
        assert all("deprecated" in str(w.message).lower() for w in future_warns)

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(depr_target_mode_whole_executes_original_body, id="modern"),
            pytest.param(legacy_executes_original_body, id="legacy"),
        ],
    )
    def test_executes_original_body(self, func: Callable[..., int]) -> None:
        """NOTIFY mode executes the original function body unchanged."""
        tracked_identity_calls.clear()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = func(42)

        assert result == 42
        assert tracked_identity_calls == [42]

    def test_construction_warns_with_args_mapping(self) -> None:
        """NOTIFY ignores args_mapping — should warn at decoration time."""
        with pytest.warns(UserWarning, match="args_mapping"):
            make_target_mode_whole_with_args_mapping_warns()

    def test_construction_warns_with_args_extra(self) -> None:
        """NOTIFY ignores args_extra — should warn at decoration time."""
        with pytest.warns(UserWarning, match="args_extra"):
            make_target_mode_whole_with_args_extra_warns()

    def test_args_mapping_is_runtime_noop(self) -> None:
        """NOTIFY + args_mapping: function body executes correctly at runtime despite misconfiguration."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            fn = make_target_mode_whole_with_args_mapping_warns()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = fn(7)  # positional — Callable[[int], int] doesn't expose param name
        assert result == 7

    def test_args_extra_is_runtime_noop(self) -> None:
        """NOTIFY + args_extra: function body executes correctly at runtime despite misconfiguration."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            fn = make_target_mode_whole_with_args_extra_warns()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = fn(3)  # positional — Callable[[int], int] doesn't expose param name
        assert result == 3


class TestArgsRemapMode:
    """TargetMode.ARGS_REMAP — warns only when old arg names passed; remaps kwargs."""

    @pytest.fixture(autouse=True)
    def _reset_deprecation_state(self) -> None:
        """Reset warning state before each test."""
        for func in (
            depr_target_mode_args_only_warns_when_old_arg_passed,
            depr_target_mode_args_only_silent_when_new_arg_passed,
            depr_target_mode_args_only_remaps_kwargs,
            legacy_warns_when_old_arg_passed,
            legacy_silent_when_new_arg_passed,
            legacy_remaps_kwargs,
        ):
            state = cast(_DeprecatedCallable, func)._state
            state.warned_calls = 0
            state.warned_args.clear()

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(depr_target_mode_args_only_warns_when_old_arg_passed, id="modern"),
            pytest.param(legacy_warns_when_old_arg_passed, id="legacy"),
        ],
    )
    def test_warns_when_old_arg_passed(self, func: Callable[..., int]) -> None:
        """ARGS_REMAP emits FutureWarning only when deprecated arg name is used."""
        with pytest.warns(FutureWarning):
            result = func(old_x=5)

        assert result == 6

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(depr_target_mode_args_only_silent_when_new_arg_passed, id="modern"),
            pytest.param(legacy_silent_when_new_arg_passed, id="legacy"),
        ],
    )
    def test_silent_when_new_arg_passed(self, func: Callable[..., int]) -> None:
        """ARGS_REMAP does NOT warn when caller uses the new argument name."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning would raise
            result = func(x=5)

        assert result == 6

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(depr_target_mode_args_only_remaps_kwargs, id="modern"),
            pytest.param(legacy_remaps_kwargs, id="legacy"),
        ],
    )
    def test_remaps_kwargs(self, func: Callable[..., float]) -> None:
        """ARGS_REMAP renames old arg to new name before calling the function body."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = func(2.0, coef=3.0)

        assert result == 8.0

    def test_with_args_extra_injects_kwargs(self) -> None:
        """ARGS_REMAP with args_extra merges extra kwargs after remapping."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = depr_target_mode_args_only_with_args_extra_injects_kwargs(old_x=5)

        assert result == 15

    def test_construction_warns_without_args_mapping(self) -> None:
        """ARGS_REMAP without args_mapping has zero effect — should warn at decoration time."""
        with pytest.warns(UserWarning, match="args_mapping"):
            make_target_mode_args_only_without_args_mapping_warns()

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(depr_target_mode_args_only_remaps_kwargs, id="modern"),
            pytest.param(legacy_remaps_kwargs, id="legacy"),
        ],
    )
    def test_positional_passthrough(self, func: Callable[..., float]) -> None:
        """ARGS_REMAP with all-positional args bypasses mapping and emits no warning."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            result = func(2.0, 3.0)
        assert result == 8.0

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(depr_target_mode_args_only_with_args_extra_injects_kwargs, id="modern"),
            pytest.param(depr_legacy_args_extra, id="legacy"),
        ],
    )
    @pytest.mark.parametrize(("old_x", "expected"), [(5, 15), (0, 10), (-3, 7)])
    def test_args_extra_equivalence_with_legacy(self, func: Callable[..., int], old_x: int, expected: int) -> None:
        """args_extra injects kwargs identically for TargetMode.ARGS_REMAP and legacy target=True."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = func(old_x=old_x)
        assert result == expected


class TestDefaultTarget:
    """Omitting `target` defaults to TargetMode.NOTIFY — warn-only, no forwarding."""

    def test_default_target_resolves_to_notify(self) -> None:
        """Omitting target stores TargetMode.NOTIFY in __deprecated__.target."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fn = make_default_target_with_versions()
        assert cast(_DeprecatedCallable, fn).__deprecated__.target is TargetMode.NOTIFY

    def test_default_target_no_decoration_time_future_warning(self) -> None:
        """Omitting target emits no FutureWarning at decoration time (unlike target=None)."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            make_default_target_with_versions()
        future_warns = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert future_warns == []

    def test_default_target_warns_on_call(self) -> None:
        """Omitting target still emits FutureWarning when the decorated function is called."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fn = make_default_target_with_versions()
        with pytest.warns(FutureWarning):
            fn(1)

    def test_default_target_executes_body(self) -> None:
        """Omitting target executes the original function body and returns its value."""
        tracked_identity_calls.clear()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fn = make_default_target_with_versions()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = fn(42)
        assert result == 42
        assert tracked_identity_calls == [42]

    def test_empty_versions_warns_at_decoration(self) -> None:
        """@deprecated() with no versions emits UserWarning at decoration time."""
        with pytest.warns(UserWarning, match=r"has no `deprecated_in` set"):
            make_default_target_no_versions_warns()

    def test_partial_version_does_not_warn_at_decoration(self) -> None:
        """@deprecated(deprecated_in='1.0') with only one version set must not warn."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            make_partial_version_no_guard_warn()
        assert not any(
            issubclass(w.category, UserWarning) and "no `deprecated_in` set" in str(w.message) for w in caught
        )

    def test_callable_target_no_versions_warns_at_decoration(self) -> None:
        """@deprecated(target=callable) with empty versions emits UserWarning — guard applies to all targets."""
        with pytest.warns(UserWarning, match=r"has no `deprecated_in` set"):
            make_callable_target_no_versions_warns()

    def test_explicit_notify_no_versions_warns_at_decoration(self) -> None:
        """@deprecated(target=TargetMode.NOTIFY) with empty versions must warn at decoration time."""
        with pytest.warns(UserWarning, match=r"has no `deprecated_in` set"):
            make_explicit_notify_no_versions_warns()


class TestLegacySentinels:
    """Backward-compat: legacy sentinels emit FutureWarning at decoration time."""

    def test_none_emits_future_warning(self) -> None:
        """target=None triggers FutureWarning at decoration time; use TargetMode.NOTIFY."""
        with pytest.warns(FutureWarning, match="TargetMode\\.NOTIFY"):

            @deprecated(target=None, deprecated_in="0.1", remove_in="0.5")
            def _fn(x: int) -> int:
                return x

    def test_true_emits_future_warning(self) -> None:
        """target=True triggers FutureWarning at decoration time; use TargetMode.ARGS_REMAP."""
        with pytest.warns(FutureWarning, match="TargetMode.ARGS_REMAP"):

            @deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={"old_x": "x"})
            def _fn(x: int) -> int:
                return x

    def test_false_emits_user_warning(self) -> None:
        """target=False is not valid — should warn at decoration time."""
        with pytest.warns(UserWarning, match=r"target=False.*is not a valid deprecation mode"):

            @deprecated(target=False, deprecated_in="0.1", remove_in="0.5")
            def _fn(x: int) -> int:
                return x

    def test_false_stores_notify_enum_in_deprecated_config(self) -> None:
        """target=False is normalised to TargetMode.NOTIFY in __deprecated__.target."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)

            @deprecated(target=False, deprecated_in="0.1", remove_in="0.5")
            def _fn(x: int) -> int:
                return x

        assert cast(_DeprecatedCallable, _fn).__deprecated__.target is TargetMode.NOTIFY

    def test_true_stores_args_remap_enum_in_deprecated_config(self) -> None:
        """target=True is normalised to TargetMode.ARGS_REMAP in __deprecated__.target."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)

            @deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={"old_x": "x"})
            def _fn(x: int) -> int:
                return x

        assert cast(_DeprecatedCallable, _fn).__deprecated__.target is TargetMode.ARGS_REMAP

    def test_none_stores_notify_enum_in_deprecated_config(self) -> None:
        """target=None is normalised to TargetMode.NOTIFY in __deprecated__.target."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)

            @deprecated(target=None, deprecated_in="0.1", remove_in="0.5")
            def _fn(x: int) -> int:
                return x

        assert cast(_DeprecatedCallable, _fn).__deprecated__.target is TargetMode.NOTIFY


class TestFromLegacyErrors:
    """TargetMode._from_legacy TypeError for non-sentinel inputs."""

    @pytest.mark.parametrize(
        "bad_value",
        [
            pytest.param(42, id="int"),
            pytest.param("notify", id="str"),
            pytest.param(object(), id="object"),
            pytest.param(TargetMode.NOTIFY, id="TargetMode_member"),
        ],
    )
    def test_raises_type_error_for_non_sentinel(self, bad_value: object) -> None:
        """_from_legacy raises TypeError for any value that is not None, True, or False."""
        with pytest.raises(TypeError, match="_from_legacy` accepts only None, True, or False"):
            TargetMode._from_legacy(cast(bool, bad_value), stacklevel=None)


class TestTargetModeValidate:
    """TargetMode._validate — direct unit tests for all misconfig combinations."""

    def test_args_remap_without_mapping_returns_true(self) -> None:
        """ARGS_REMAP with no args_mapping is misconfigured; returns True."""
        assert TargetMode._validate(TargetMode.ARGS_REMAP, "fn", args_mapping=None, stacklevel=None) is True

    def test_notify_with_args_mapping_returns_true(self) -> None:
        """NOTIFY with args_mapping is misconfigured; returns True."""
        assert TargetMode._validate(TargetMode.NOTIFY, "fn", args_mapping={"old": "new"}, stacklevel=None) is True

    def test_notify_with_args_extra_returns_true(self) -> None:
        """NOTIFY with args_extra is misconfigured; returns True."""
        assert TargetMode._validate(TargetMode.NOTIFY, "fn", args_extra={"bias": 1}, stacklevel=None) is True

    def test_valid_notify_returns_false(self) -> None:
        """NOTIFY with no extra kwargs is valid; returns False."""
        assert TargetMode._validate(TargetMode.NOTIFY, "fn", stacklevel=None) is False

    def test_valid_args_remap_returns_false(self) -> None:
        """ARGS_REMAP with args_mapping is valid; returns False."""
        assert TargetMode._validate(TargetMode.ARGS_REMAP, "fn", args_mapping={"old": "new"}, stacklevel=None) is False

    def test_misconfig_emits_user_warning(self) -> None:
        """_validate emits UserWarning when misconfigured (stacklevel != None)."""
        with pytest.warns(UserWarning, match="args_mapping"):
            TargetMode._validate(TargetMode.NOTIFY, "my_fn", args_mapping={"old": "new"}, stacklevel=2)

    def test_valid_emits_no_warning(self) -> None:
        """_validate does not emit any warning when configuration is valid."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            TargetMode._validate(TargetMode.NOTIFY, "my_fn", stacklevel=2)


class TestNotifyLegacyMessageParity:
    """NOTIFY mode: modern TargetMode.NOTIFY and legacy target=None emit identical call-time warnings."""

    @pytest.fixture(autouse=True)
    def _reset_state(self) -> None:
        """Reset warned_calls before each test."""
        for func in (
            depr_target_mode_whole_warns_on_every_call,
            legacy_warns_on_every_call,
        ):
            state = cast(_DeprecatedCallable, func)._state
            state.warned_calls = 0
            state.warned_args.clear()

    def test_modern_and_legacy_emit_identical_warning_text(self) -> None:
        """TargetMode.NOTIFY and target=None produce the same FutureWarning text at call time."""
        with warnings.catch_warnings(record=True) as modern_caught:
            warnings.simplefilter("always", FutureWarning)
            depr_target_mode_whole_warns_on_every_call(1)
        with warnings.catch_warnings(record=True) as legacy_caught:
            warnings.simplefilter("always", FutureWarning)
            legacy_warns_on_every_call(1)

        modern_msgs = [str(w.message) for w in modern_caught if issubclass(w.category, FutureWarning)]
        legacy_msgs = [str(w.message) for w in legacy_caught if issubclass(w.category, FutureWarning)]
        assert modern_msgs == legacy_msgs
