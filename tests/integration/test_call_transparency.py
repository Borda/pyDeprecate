"""Characterization tests: the call shape a ``@deprecated`` wrapper hands to its source or target.

A deprecation wrapper sits between every caller and the deprecated code, so how it forwards positional and keyword
arguments decides whether call-shape-sensitive sources keep working.  These tests pin the shapes the dispatch
internals must keep: a call the wrapper does not need to rewrite (``TargetMode.NOTIFY``, or a ``TargetMode.ARGS_REMAP``
the caller does not trigger) reaches the source exactly as made, and a rewritten call keeps today's results.

"""

import re
import warnings
from typing import Any, Callable

import pytest

from tests._helpers import captured_warnings
from tests.collections.api_deprecated.core import loc_stack_notify_over_callable, loc_stack_remap_over_notify
from tests.collections.api_deprecated.forwarding import (
    deprecated_gapped_positional_only_source,
    deprecated_var_positional_forward,
)
from tests.collections.api_deprecated.regressions import (
    shape_notify_area_dispatch,
    shape_notify_positional_only,
    shape_notify_raw_signature,
    shape_notify_two_params,
    shape_notify_wraps_chain,
    shape_remap_area_dispatch,
    shape_remap_old_new,
    shape_remap_var_positional_extra_fills_slot,
    shape_remap_var_positional_rename_fills_slot,
    shape_remap_var_positional_with_extra,
    shape_remap_with_extra,
)

# Results and warnings the two supported stacks produced before stacked layers passed the caller's shape through.
_NOTIFY_OVER_CALLABLE_WARNINGS = [
    "FutureWarning: The `_loc_stack_forward_source` was deprecated since v1.5. It will be removed in v2.0.",
    "FutureWarning: The `_loc_stack_forward_source` was deprecated since v1.0 in favor of"
    " `tests.collections.api_current.core.double_value`. It will be removed in v2.0.",
]
_LIFECYCLE_NOTIFY_WARNING = (
    "FutureWarning: The `_loc_stack_lifecycle_source` was deprecated since v1.0. It will be removed in v2.0."
)
_VAR_POSITIONAL_RENAME_WARNING = (
    "FutureWarning: The `shape_remap_var_positional_with_extra` uses deprecated arguments: `old` -> `new`."
    " They were deprecated since v1.0 and will be removed in v2.0."
)
_LIFECYCLE_RENAME_WARNING = (
    "FutureWarning: The `_loc_stack_lifecycle_source` uses deprecated arguments: `old_x` -> `x`."
    " They were deprecated since v1.0 and will be removed in v2.0."
)


def _result_or_type_error(func: Callable[..., object], *args: object) -> object:
    """Return ``func(*args)``, or the ``TypeError`` it raised, so a broken call shape fails as a plain assertion.

    A source that cannot accept the rewritten keywords raises ``TypeError`` from inside the call; turning that into a
    value lets the test compare it against the expected result.  Every other exception still propagates.

    """
    try:
        return func(*args)
    except TypeError as err:
        return err


def _warning_lines(caught: list[warnings.WarningMessage]) -> list[str]:
    """Render recorded warnings as ``"<Category>: <message>"`` lines in emission order, for whole-list comparison."""
    return [f"{record.category.__name__}: {record.message}" for record in caught]


class TestNotifyCallShape:
    """``TargetMode.NOTIFY`` warns and then runs the source body; the source should see the caller's own arguments."""

    def test_singledispatch_source_dispatches_on_positional(self) -> None:
        """A ``functools.singledispatch`` source still dispatches on the type of its first positional argument.

        Libraries deprecate generic functions built with ``singledispatch``; the dispatcher needs the first argument
        positionally.  Today the wrapper converts ``(3,)`` into ``shape=3`` and ``singledispatch`` raises
        ``TypeError`` because it received no positional argument at all.

        """
        with pytest.warns(FutureWarning, match="`area_dispatch` was deprecated"):
            result = _result_or_type_error(shape_notify_area_dispatch, 3)

        assert result == "int:3"

    def test_signature_patched_source_receives_positionals(self) -> None:
        """A ``__signature__``-patched ``(*args, **kwargs)`` source receives the caller's positional arguments.

        C-accelerated wrappers and argument-clinic shims advertise a signature that differs from their real call
        shape.  The source returns what it received, so the result shows the forwarded shape directly; today it is
        ``((), {"x": 1})`` instead of the caller's ``((1,), {})``.

        """
        with pytest.warns(FutureWarning, match="`raw_signature_patched` was deprecated"):
            result = shape_notify_raw_signature(1)

        assert result == ((1,), {})

    def test_wraps_forwarder_receives_positionals(self) -> None:
        """A ``functools.wraps`` forwarder that accepts positionals only still receives the caller's positional value.

        User code often stacks a hand-written ``functools.wraps`` decorator under the deprecation; ``inspect.signature``
        follows ``__wrapped__`` and reports named parameters, so today the wrapper passes ``value=2`` as a keyword and
        the forwarder raises ``TypeError``.

        """
        with pytest.warns(FutureWarning, match="`wraps_chain_outer` was deprecated"):
            result = _result_or_type_error(shape_notify_wraps_chain, 2)

        assert result == 4

    def test_duplicate_argument_raises_like_undecorated(self) -> None:
        """A value passed both positionally and by keyword raises Python's own ``TypeError`` after the notice.

        The source receives the caller's arguments as made, so ``old(1, a=2)`` fails exactly as the undecorated
        function would instead of silently dropping one of the two values.  The deprecation notice is emitted once,
        before the source is called.

        """
        with captured_warnings() as caught, pytest.raises(TypeError, match="multiple values for argument 'a'"):
            shape_notify_two_params(1, a=2)  # type: ignore[misc]

        warned_calls = shape_notify_two_params._state.warned_calls  # type: ignore[attr-defined]
        assert ([record.category for record in caught], warned_calls) == ([FutureWarning], 1)

    def test_positional_only_by_keyword_raises_like_undecorated(self) -> None:
        """A positional-only parameter passed by keyword raises Python's own ``TypeError`` after the notice.

        ``positional_only_target(x, /, y=0)`` rejects ``x=1`` when undecorated.  The warn-only wrapper forwards the
        call as made, so the caller gets the same ``TypeError`` instead of a call silently rebound to the positional
        slot.  The deprecation notice is emitted once, before the source is called.

        """
        with (
            captured_warnings() as caught,
            pytest.raises(TypeError, match="positional-only arguments passed as keyword"),
        ):
            shape_notify_positional_only(x=1)  # type: ignore[call-arg]

        warned_calls = shape_notify_positional_only._state.warned_calls  # type: ignore[attr-defined]
        assert ([record.category for record in caught], warned_calls) == ([FutureWarning], 1)


class TestArgsRemapCallShape:
    """``TargetMode.ARGS_REMAP`` renames deprecated arguments before running the source body."""

    def test_old_name_passed_positionally_warns_and_remaps(self) -> None:
        """A deprecated argument filled positionally still warns and its value moves to the new name.

        A caller written against the old signature ``f(old=None, new=None)`` calls ``f(1)``: the value lands in the
        deprecated ``old`` slot, so the argument warning fires and the source receives ``new=1``.  The refactor has to
        keep detecting deprecated names in positional slots, not only in keywords.

        """
        with pytest.warns(FutureWarning, match=re.escape("uses deprecated arguments: `old` -> `new`")):
            result = shape_remap_old_new(1)

        assert result == (None, 1)

    def test_migrated_caller_keeps_positional_dispatch(self) -> None:
        """A caller already on the new spelling reaches a ``singledispatch`` source with its positional argument.

        A library renames a keyword of a generic function built with ``functools.singledispatch`` and keeps the old
        spelling working with ``TargetMode.ARGS_REMAP``.  A caller that never used the old name, here ``area(3)``,
        triggers no rename, so the wrapper must hand ``(3,)`` through untouched for the dispatcher to pick the ``int``
        implementation, and no warning is due.

        """
        with captured_warnings() as caught:
            result = _result_or_type_error(shape_remap_area_dispatch, 3)

        assert (result, _warning_lines(caught)) == ("int:3", [])

    def test_args_extra_still_injected_for_migrated_caller(self) -> None:
        """``args_extra`` is still merged into the call when the caller already uses the new argument name.

        ``args_extra`` adds keywords the source must receive on every call, so a wrapper carrying it cannot pass the
        caller's arguments through unchanged even when no deprecated name is used: the source has to see the injected
        ``flag=True`` next to the caller's ``new=1``, without any warning.

        """
        with captured_warnings() as caught:
            result = shape_remap_with_extra(new=1)

        assert (result, _warning_lines(caught)) == ((None, 1, True), [])

    @pytest.mark.parametrize(
        ("kwargs", "expected_warnings"),
        [
            pytest.param({"new": 3}, [], id="migrated-caller"),
            pytest.param({"old": 3}, [_VAR_POSITIONAL_RENAME_WARNING], id="old-name-caller"),
        ],
    )
    def test_args_extra_injected_for_var_positional_source(
        self, kwargs: dict[str, Any], expected_warnings: list[str]
    ) -> None:
        """``args_extra`` reaches a source declaring ``*args`` whether or not the caller uses the deprecated name.

        A library renames ``old`` to ``new`` on a function that also collects a ``*values`` tail, and injects
        ``flag=True`` through ``args_extra``.  The injected keyword belongs to every call, so a caller already on the
        new spelling, ``f(1, 2, new=3)``, must reach the source with ``flag=True`` next to its positional tail exactly
        like a caller still passing ``old=3``; only the latter warns.

        """
        with captured_warnings() as caught:
            result = shape_remap_var_positional_with_extra(1, 2, **kwargs)

        assert (result, _warning_lines(caught)) == (((1, 2), None, 3, True), expected_warnings)

    def test_args_extra_overrides_positionally_filled_name(self) -> None:
        """``args_extra`` wins over a value the caller passed positionally, as it does for a source without ``*args``.

        A library injects ``new=5`` into a function that takes ``new`` first and then collects a ``*values`` tail.
        ``f(1, 2)`` fills ``new`` by position, so the injected value replaces that positional slot instead of being
        passed a second time, which Python rejects with "multiple values for argument".
        """
        with captured_warnings() as caught:
            result = shape_remap_var_positional_extra_fills_slot(1, 2)

        assert (result, _warning_lines(caught)) == ((5, (2,), {}), [])

    def test_positional_value_wins_over_renamed_keyword(self) -> None:
        """A value passed by position beats the deprecated keyword that renames to the same parameter.

        A caller still passing the old keyword next to positional arguments, ``f(8, 9, old=7)``, fills ``new`` twice
        once ``old`` is renamed.  The positional value wins, as the collision warning says ("``old`` is ignored"),
        and the call reaches the source instead of failing with "multiple values for argument".
        """
        with captured_warnings() as caught:
            result = shape_remap_var_positional_rename_fills_slot(8, 9, old=7)

        assert (result, [line.split(":")[0] for line in _warning_lines(caught)]) == (
            (8, (9,), {}),
            ["FutureWarning", "UserWarning"],
        )

    def test_callers_own_duplicate_still_raises(self) -> None:
        """A caller that passes the same parameter both by position and by keyword still gets Python's error.

        Only a duplicate the rename produced is dropped.  ``f(8, 9, old=7, new=3)`` names ``new`` twice on its own, a
        genuine caller mistake that must not be hidden behind a silently chosen value.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(TypeError, match="multiple values for argument 'new'"):
                shape_remap_var_positional_rename_fills_slot(8, 9, old=7, new=3)  # type: ignore[misc]


class TestArityError:
    """Too many positional arguments are rejected before any deprecation machinery runs."""

    def test_raises_curated_type_error_before_warning(self) -> None:
        """Surplus positionals raise the curated ``TypeError``, emit no warning and leave the warn budget unspent.

        A caller passing one argument too many must get the same error the plain function would give, and the one
        ``num_warns=1`` warning must stay available for the first valid call instead of being burned on a call that
        never ran.

        """
        message = "shape_notify_two_params() takes 1 to 2 positional argument(s) but got 3 positional argument(s)"
        with captured_warnings() as caught, pytest.raises(TypeError, match=re.escape(message)):
            shape_notify_two_params(1, 2, 3)  # type: ignore[call-arg]

        assert (caught, shape_notify_two_params._state.warned_calls) == ([], 0)  # type: ignore[attr-defined]


class TestForwardingCallShape:
    """Callable-target forwarding keeps positional surplus and positional-only slots in order."""

    def test_var_positional_surplus_reaches_target(self) -> None:
        """The ``*args`` tail of a deprecated source is forwarded positionally to a ``*args`` target.

        ``deprecated_var_positional_forward(a, *extras)`` forwards to ``var_positional_target(a, *extras)``, which
        sums everything; a result of ``6`` for ``(1, 2, 3)`` proves no surplus value was dropped or turned into a
        keyword on the way.

        """
        with pytest.warns(FutureWarning, match="in favor of"):
            result = deprecated_var_positional_forward(1, 2, 3)

        assert result == 6

    def test_positional_only_gap_raises(self) -> None:
        """Forwarding a later positional-only value while an earlier one is missing raises the curated ``TypeError``.

        The source supplies only ``b`` while the target declares ``(a=1, b=2, /, c=3)``: binding by slot would put
        ``b``'s value into ``a``, so the dispatcher refuses instead of silently misbinding.  The deprecation warning
        is emitted before the forwarding step raises.

        """
        message = "Cannot forward `b` positionally: the earlier positional-only parameter `a` was not supplied"
        with pytest.warns(FutureWarning, match="in favor of"), pytest.raises(TypeError, match=re.escape(message)):
            deprecated_gapped_positional_only_source(b=5)


class TestStackedCallShape:
    """Stacked ``@deprecated`` layers keep their results and warnings when an outer layer passes the call through."""

    @pytest.mark.parametrize(
        ("wrapper", "args", "kwargs", "expected"),
        [
            pytest.param(
                loc_stack_notify_over_callable,
                (1,),
                {},
                (2, _NOTIFY_OVER_CALLABLE_WARNINGS),
                id="notify-over-callable-positional",
            ),
            pytest.param(
                loc_stack_notify_over_callable,
                (),
                {"x": 1},
                (2, _NOTIFY_OVER_CALLABLE_WARNINGS),
                id="notify-over-callable-keyword",
            ),
            pytest.param(
                loc_stack_remap_over_notify,
                (),
                {"old_x": 1},
                (1, [_LIFECYCLE_RENAME_WARNING, _LIFECYCLE_NOTIFY_WARNING]),
                id="remap-over-notify-old-name",
            ),
            pytest.param(
                loc_stack_remap_over_notify,
                (),
                {"x": 1},
                (1, [_LIFECYCLE_NOTIFY_WARNING]),
                id="remap-over-notify-new-name",
            ),
            pytest.param(
                loc_stack_remap_over_notify,
                (1,),
                {},
                (1, [_LIFECYCLE_NOTIFY_WARNING]),
                id="remap-over-notify-positional",
            ),
        ],
    )
    def test_results_and_warnings_unchanged(
        self,
        wrapper: Callable[..., int],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        expected: tuple[int, list[str]],
    ) -> None:
        """Each supported stack returns the same value and emits the same warnings, in the same order, as before.

        An outer ``TargetMode.NOTIFY`` layer over a forwarding layer, and an outer ``TargetMode.ARGS_REMAP`` layer
        over a ``TargetMode.NOTIFY`` layer, are the two stacks the stacking guide supports.  When the outer layer
        stops rewriting positionals into keywords it hands the caller's own shape to the inner wrapper, which must
        still forward, rename and warn exactly as it did when it received keywords.

        """
        with captured_warnings() as caught:
            result = wrapper(*args, **kwargs)

        assert (result, _warning_lines(caught)) == expected
