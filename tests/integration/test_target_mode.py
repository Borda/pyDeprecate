"""Demo / contract tests for TargetMode enum and misconfiguration guards.

These tests were written BEFORE the implementation to crystallise the API contract.
They define the expected behaviour for:
- TargetMode.WHOLE  (replaces target=None)
- TargetMode.ARGS_ONLY (replaces target=True)
- Construction-time UserWarning for misconfigurations
- FutureWarning for legacy target=None / target=True sentinels
"""

import warnings

import pytest

from deprecate import TargetMode, deprecated

# ---------------------------------------------------------------------------
# TargetMode enum contract
# ---------------------------------------------------------------------------


def test_target_mode_members_exist() -> None:
    """TargetMode exposes exactly WHOLE and ARGS_ONLY members."""
    assert hasattr(TargetMode, "WHOLE")
    assert hasattr(TargetMode, "ARGS_ONLY")
    assert len(list(TargetMode)) == 2


def test_target_mode_importable_from_top_level() -> None:
    """TargetMode is part of the public API surface."""
    import deprecate

    assert hasattr(deprecate, "TargetMode")
    assert deprecate.TargetMode is TargetMode


# ---------------------------------------------------------------------------
# TargetMode.WHOLE — warns every call; exec original body
# ---------------------------------------------------------------------------


def test_whole_warns_on_every_call() -> None:
    """WHOLE mode emits a FutureWarning on every call up to num_warns."""

    @deprecated(target=TargetMode.WHOLE, deprecated_in="0.9", remove_in="1.0")
    def going_away(x: int) -> int:
        return x * 2

    with pytest.warns(FutureWarning):
        result = going_away(3)

    assert result == 6


def test_whole_executes_original_body() -> None:
    """WHOLE mode executes the original function body unchanged."""
    call_log: list[int] = []

    @deprecated(target=TargetMode.WHOLE, deprecated_in="0.9", remove_in="1.0")
    def tracked(x: int) -> int:
        call_log.append(x)
        return x

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        result = tracked(42)

    assert result == 42
    assert call_log == [42]


# ---------------------------------------------------------------------------
# TargetMode.ARGS_ONLY — warns only when old arg names passed; remaps kwargs
# ---------------------------------------------------------------------------


def test_args_only_warns_when_old_arg_passed() -> None:
    """ARGS_ONLY emits FutureWarning only when deprecated arg name is used."""

    @deprecated(
        target=TargetMode.ARGS_ONLY,
        deprecated_in="0.9",
        remove_in="1.0",
        args_mapping={"old_x": "x"},
    )
    def my_fn(x: int) -> int:
        return x + 1

    with pytest.warns(FutureWarning):
        result = my_fn(old_x=5)

    assert result == 6


def test_args_only_silent_when_new_arg_passed() -> None:
    """ARGS_ONLY does NOT warn when caller uses the new argument name."""

    @deprecated(
        target=TargetMode.ARGS_ONLY,
        deprecated_in="0.9",
        remove_in="1.0",
        args_mapping={"old_x": "x"},
    )
    def my_fn(x: int) -> int:
        return x + 1

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning would raise
        result = my_fn(x=5)

    assert result == 6


def test_args_only_remaps_kwargs() -> None:
    """ARGS_ONLY renames old arg to new name before calling the function body."""

    @deprecated(
        target=TargetMode.ARGS_ONLY,
        deprecated_in="0.9",
        remove_in="1.0",
        args_mapping={"coef": "new_coef"},
    )
    def power(base: float, new_coef: float = 1.0) -> float:
        return base**new_coef

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        result = power(2.0, coef=3.0)

    assert result == 8.0


def test_args_only_with_args_extra_injects_kwargs() -> None:
    """ARGS_ONLY with args_extra merges extra kwargs after remapping."""

    @deprecated(
        target=TargetMode.ARGS_ONLY,
        deprecated_in="0.9",
        remove_in="1.0",
        args_mapping={"old_x": "x"},
        args_extra={"y": 10},
    )
    def add(x: int, y: int) -> int:
        return x + y

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        result = add(old_x=5)

    assert result == 15


# ---------------------------------------------------------------------------
# Construction-time UserWarning for misconfigurations
# ---------------------------------------------------------------------------


def test_args_only_without_args_mapping_warns() -> None:
    """ARGS_ONLY without args_mapping has zero effect — should warn at decoration time."""
    with pytest.warns(UserWarning, match="args_mapping"):

        @deprecated(target=TargetMode.ARGS_ONLY, deprecated_in="0.9", remove_in="1.0")
        def noop(x: int) -> int:
            return x


def test_whole_with_args_mapping_warns() -> None:
    """WHOLE ignores args_mapping — should warn at decoration time."""
    with pytest.warns(UserWarning, match="args_mapping"):

        @deprecated(
            target=TargetMode.WHOLE,
            deprecated_in="0.9",
            remove_in="1.0",
            args_mapping={"a": "b"},
        )
        def fn(b: int) -> int:
            return b


def test_whole_with_args_extra_warns() -> None:
    """WHOLE ignores args_extra — should warn at decoration time."""
    with pytest.warns(UserWarning, match="args_extra"):

        @deprecated(
            target=TargetMode.WHOLE,
            deprecated_in="0.9",
            remove_in="1.0",
            args_extra={"z": 1},
        )
        def fn(z: int = 0) -> int:
            return z


def test_target_false_warns() -> None:
    """target=False is not valid — should warn at decoration time."""
    with pytest.warns(UserWarning, match="not valid"):

        @deprecated(target=False, deprecated_in="0.9", remove_in="1.0")
        def fn() -> None:
            pass


# ---------------------------------------------------------------------------
# Backward-compat: legacy sentinels emit FutureWarning at decoration time
# ---------------------------------------------------------------------------


def test_target_none_sentinel_emits_future_warning() -> None:
    """target=None triggers FutureWarning at decoration time; use TargetMode.WHOLE."""
    with pytest.warns(FutureWarning, match="TargetMode.WHOLE"):

        @deprecated(target=None, deprecated_in="0.9", remove_in="1.0")
        def legacy_none() -> None:
            pass


def test_target_true_sentinel_emits_future_warning() -> None:
    """target=True triggers FutureWarning at decoration time; use TargetMode.ARGS_ONLY."""
    with pytest.warns(FutureWarning, match="TargetMode.ARGS_ONLY"):

        @deprecated(
            target=True,
            deprecated_in="0.9",
            remove_in="1.0",
            args_mapping={"old": "new"},
        )
        def legacy_true(new: int = 0) -> int:
            return new
