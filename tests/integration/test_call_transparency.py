"""Characterization tests: the call shape a ``@deprecated`` wrapper hands to its source or target.

A deprecation wrapper sits between every caller and the deprecated code, so how it forwards positional and keyword
arguments decides whether call-shape-sensitive sources keep working.  These tests pin today's shapes before the
dispatch internals are refactored: the ones the refactor must keep pass, and the ones the audit marked as broken
(finding CORE-02: ``TargetMode.NOTIFY`` rewrites positionals into keywords) are ``xfail(strict=True)`` and assert the
correct, transparent shape so the fix flips them.

"""

import re
import warnings
from typing import Callable

import pytest

from tests.collection_deprecate import (
    deprecated_gapped_positional_only_source,
    deprecated_var_positional_forward,
    shape_notify_area_dispatch,
    shape_notify_raw_signature,
    shape_notify_two_params,
    shape_notify_wraps_chain,
    shape_remap_old_new,
)

_XFAIL_CORE02 = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="CORE-02: NOTIFY rewrites positional arguments into keywords before calling the source",
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


class TestNotifyCallShape:
    """``TargetMode.NOTIFY`` warns and then runs the source body; the source should see the caller's own arguments."""

    @_XFAIL_CORE02
    def test_singledispatch_source_dispatches_on_positional(self) -> None:
        """A ``functools.singledispatch`` source still dispatches on the type of its first positional argument.

        Libraries deprecate generic functions built with ``singledispatch``; the dispatcher needs the first argument
        positionally.  Today the wrapper converts ``(3,)`` into ``shape=3`` and ``singledispatch`` raises
        ``TypeError`` because it received no positional argument at all.

        """
        with pytest.warns(FutureWarning, match="`area_dispatch` was deprecated"):
            result = _result_or_type_error(shape_notify_area_dispatch, 3)

        assert result == "int:3"

    @_XFAIL_CORE02
    def test_signature_patched_source_receives_positionals(self) -> None:
        """A ``__signature__``-patched ``(*args, **kwargs)`` source receives the caller's positional arguments.

        C-accelerated wrappers and argument-clinic shims advertise a signature that differs from their real call
        shape.  The source returns what it received, so the result shows the forwarded shape directly; today it is
        ``((), {"x": 1})`` instead of the caller's ``((1,), {})``.

        """
        with pytest.warns(FutureWarning, match="`raw_signature_patched` was deprecated"):
            result = shape_notify_raw_signature(1)

        assert result == ((1,), {})

    @_XFAIL_CORE02
    def test_wraps_forwarder_receives_positionals(self) -> None:
        """A ``functools.wraps`` forwarder that accepts positionals only still receives the caller's positional value.

        User code often stacks a hand-written ``functools.wraps`` decorator under the deprecation; ``inspect.signature``
        follows ``__wrapped__`` and reports named parameters, so today the wrapper passes ``value=2`` as a keyword and
        the forwarder raises ``TypeError``.

        """
        with pytest.warns(FutureWarning, match="`wraps_chain_outer` was deprecated"):
            result = _result_or_type_error(shape_notify_wraps_chain, 2)

        assert result == 4


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


class TestArityError:
    """Too many positional arguments are rejected before any deprecation machinery runs."""

    def test_raises_curated_type_error_before_warning(self) -> None:
        """Surplus positionals raise the curated ``TypeError``, emit no warning and leave the warn budget unspent.

        A caller passing one argument too many must get the same error the plain function would give, and the one
        ``num_warns=1`` warning must stay available for the first valid call instead of being burned on a call that
        never ran.

        """
        message = "shape_notify_two_params() takes 1 to 2 positional argument(s) but got 3 positional argument(s)"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(TypeError, match=re.escape(message)):
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
