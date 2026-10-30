"""Unit tests for proxy operators, context/async protocols, and acquisition cleanup."""

import asyncio
import gc
import math
import os
import threading
import warnings
from collections.abc import Callable
from typing import Any

import pytest

from deprecate.proxy import deprecated_instance
from tests._helpers import captured_warnings
from tests.collections.api_current.core import FlakyProtocolBox
from tests.collections.api_current.forwarding import (
    AsyncManagedResource,
    CachedStepIterator,
    DeferredFailureAwaitable,
    ManagedResource,
    PendingSnapshotBox,
    RecordingEntryContext,
    legacy_snapshot,
    legacy_tick_stream,
)
from tests.collections.api_deprecated.core import _DEPRS_CASE_STD_ARGS
from tests.collections.api_deprecated.regressions import (
    make_read_only_inplace_proxy,
    make_silent_proxy_stack,
    make_switching_entry_proxy,
    make_warn_after_success_proxy,
)
from tests.collections.streams import KeywordTypeErrorStream
from tests.unittests.proxy._helpers import (
    _acquire_anext,
    _acquire_await,
    _await_once,
    _collect,
    _drive_aenter,
    _drive_anext,
    _drive_await,
    _drive_enter,
)


class TestOperatorForwarding:
    """Type-level operator, conversion, and context-manager dunders forward to the active object."""

    @pytest.mark.parametrize(
        ("op", "expected"),
        [
            pytest.param(lambda p: p + 2, 5, id="add"),
            pytest.param(lambda p: p - 1, 2, id="sub"),
            pytest.param(lambda p: p * 3, 9, id="mul"),
            pytest.param(lambda p: p // 2, 1, id="floordiv"),
            pytest.param(lambda p: p % 2, 1, id="mod"),
            pytest.param(lambda p: p**2, 9, id="pow"),
            pytest.param(lambda p: p << 1, 6, id="lshift"),
            pytest.param(lambda p: p & 1, 1, id="and"),
            pytest.param(lambda p: 10 + p, 13, id="radd"),
            pytest.param(lambda p: 10 - p, 7, id="rsub"),
            pytest.param(lambda p: -p, -3, id="neg"),
            pytest.param(abs, 3, id="abs"),
            pytest.param(lambda p: ~p, -4, id="invert"),
        ],
    )
    def test_arithmetic_forwards_to_active(self, op: Callable[[Any], Any], expected: Any) -> None:  # noqa: ANN401
        """Arithmetic, reflected, and unary operators compute against the wrapped value.

        A project deprecating a module-level numeric constant wraps it in ``deprecated_instance``; downstream
        expressions such as ``THRESHOLD + 2`` must keep working during the migration window rather than raising
        ``TypeError`` because the operand is a proxy.
        """
        proxy = deprecated_instance(3, name="n", **_DEPRS_CASE_STD_ARGS, stream=None)
        assert op(proxy) == expected

    @pytest.mark.parametrize(
        ("op", "expected"),
        [
            pytest.param(lambda p: p < 5, True, id="lt"),
            pytest.param(lambda p: p <= 3, True, id="le"),
            pytest.param(lambda p: p > 5, False, id="gt"),
            pytest.param(lambda p: p >= 3, True, id="ge"),
        ],
    )
    def test_ordering_forwards_to_active(self, op: Callable[[Any], Any], expected: bool) -> None:
        """Ordering comparisons delegate to the wrapped value so sorting and thresholding stay transparent.

        Code that keeps a proxied legacy value in a sorted container or gates on ``value >= limit`` must observe the
        underlying object's ordering, not a proxy-identity fallback.
        """
        proxy = deprecated_instance(3, name="n", **_DEPRS_CASE_STD_ARGS, stream=None)
        assert op(proxy) is expected

    def test_unsupported_binary_op_raises_type_error(self) -> None:
        """An operator the wrapped object lacks raises the normal ``TypeError`` instead of being masked.

        Because the forwarding dunder returns ``NotImplemented`` when the active type has no implementation, Python's
        own operand-resolution path runs and produces the same ``TypeError`` a user would see without the proxy —
        adding a dict and an int here.
        """
        proxy: Any = deprecated_instance({"a": 1}, name="d", **_DEPRS_CASE_STD_ARGS, stream=None)
        with pytest.raises(TypeError):
            _ = proxy + 1

    def test_unsupported_binary_op_does_not_warn(self) -> None:
        """When the wrapped type has no implementation for an operator the proxy returns ``NotImplemented`` silently.

        A proxy wrapping a ``dict`` has no ``__add__``; Python first tries ``dict.__add__(proxy, 1)`` (the
        forwarding dunder returns ``NotImplemented``), then tries ``int.__radd__(1, proxy)`` (also
        ``NotImplemented``), and finally raises ``TypeError``. The warning must not fire during the
        ``NotImplemented`` phase because the operation did not succeed.
        """
        proxy: Any = deprecated_instance({"a": 1}, name="d", **_DEPRS_CASE_STD_ARGS)
        with captured_warnings() as caught, pytest.raises(TypeError):
            _ = proxy + 1
        assert not [w for w in caught if issubclass(w.category, FutureWarning)]

    def test_three_arg_pow_unwraps_modulus_proxy(self) -> None:
        """``pow(proxy_base, exp, proxy_mod)`` unwraps both proxy operands before calling ``int.__pow__``.

        ``pow(base, exp, mod)`` calls ``__pow__(base, exp, mod)`` with three arguments; when ``mod`` is itself
        a proxy its raw form must reach the underlying ``int.__pow__`` implementation.  Without unwrapping,
        ``int.__pow__`` receives a ``_DeprecatedProxy`` as the modulus and raises ``TypeError``.
        """
        proxy_base: Any = deprecated_instance(7, name="base", **_DEPRS_CASE_STD_ARGS, stream=None)
        proxy_mod: Any = deprecated_instance(3, name="mod", **_DEPRS_CASE_STD_ARGS, stream=None)
        assert pow(proxy_base, 2, proxy_mod) == 1  # 7**2 % 3 == 49 % 3 == 1

    def test_inplace_add_on_immutable_rebinds_to_result(self) -> None:
        """``+=`` on a proxied immutable rebinds the name to the computed result (an int), not a re-wrapped proxy."""
        proxy: Any = deprecated_instance(3, name="n", **_DEPRS_CASE_STD_ARGS, stream=None)
        proxy += 2
        assert (proxy, isinstance(proxy, int)) == (5, True)

    def test_inplace_add_on_mutable_mutates_and_returns_active(self) -> None:
        """``+=`` on a proxied list extends the wrapped list in place and yields the active list result."""
        proxy: Any = deprecated_instance([1, 2], name="lst", **_DEPRS_CASE_STD_ARGS, stream=None)
        proxy += [3]
        assert proxy == [1, 2, 3]

    def test_numeric_and_path_conversions_forward(self) -> None:
        """``int()``, ``float()``, ``__index__`` (via ``bin``) forward to the wrapped number.

        Legacy numeric handles are frequently passed to APIs that coerce with ``int()``/``float()`` or use the value
        as an index; all of these must transparently reach the wrapped object.
        """
        proxy = deprecated_instance(3, name="n", **_DEPRS_CASE_STD_ARGS, stream=None)
        assert (int(proxy), float(proxy), bin(proxy)) == (3, 3.0, "0b11")

    def test_reversed_forwards(self) -> None:
        """``reversed()`` iterates the wrapped sequence in reverse."""
        proxy = deprecated_instance([1, 2, 3], name="lst", **_DEPRS_CASE_STD_ARGS, stream=None)
        assert list(reversed(proxy)) == [3, 2, 1]

    def test_fspath_forwards(self) -> None:
        """``os.fspath()`` returns the wrapped path string so proxies work with filesystem APIs."""
        proxy = deprecated_instance("data/legacy.txt", name="p", **_DEPRS_CASE_STD_ARGS, stream=None)
        assert os.fspath(proxy) == "data/legacy.txt"

    def test_context_manager_protocol_forwards(self) -> None:
        """A proxied context manager forwards ``__enter__``/``__exit__`` so ``with proxy:`` drives the resource.

        Wrapping a legacy resource handle (session, file-like object) in ``deprecated_instance`` must not break
        callers that still use it in a ``with`` block; both transitions reach the underlying resource.
        """
        resource = ManagedResource()
        proxy = deprecated_instance(resource, name="res", **_DEPRS_CASE_STD_ARGS, stream=None)
        with proxy as entered:
            pass
        assert (resource.entered, resource.exited, entered is resource) == (True, True, True)

    def test_arithmetic_warns_once(self) -> None:
        """Using the proxy in an arithmetic expression emits one deprecation warning (a data-producing use)."""
        proxy: Any = deprecated_instance(3, name="n", **_DEPRS_CASE_STD_ARGS)
        with pytest.warns(FutureWarning, match=r"The `n` was deprecated since v1\.0"):
            _ = proxy + 1

    def test_ordering_comparison_is_silent(self) -> None:
        """Ordering comparisons are cheap probes and emit no warning, mirroring ``__eq__`` and ``__len__``."""
        proxy: Any = deprecated_instance(3, name="n", **_DEPRS_CASE_STD_ARGS)
        with captured_warnings() as caught:
            _ = proxy < 5
        assert not [w for w in caught if issubclass(w.category, FutureWarning)]

    def test_context_entry_warns_once_exit_silent(self) -> None:
        """Context entry warns once and exit stays silent, so a ``with`` block yields exactly one warning."""
        resource = ManagedResource()
        proxy = deprecated_instance(resource, name="res", **_DEPRS_CASE_STD_ARGS)
        with captured_warnings() as caught, proxy:
            pass
        assert len([w for w in caught if issubclass(w.category, FutureWarning)]) == 1

    def test_round_no_ndigits_forwards_and_warns(self) -> None:
        """``round(proxy)`` routes through ``__round__`` (branch 1: no ndigits) and emits FutureWarning.

        A project deprecating a legacy float constant must still be usable in ``round(LEGACY_CONST)``
        calls; and the round operation counts as data-producing use, so a warning is expected.
        """
        proxy = deprecated_instance(3.7, name="n", **_DEPRS_CASE_STD_ARGS)
        with pytest.warns(FutureWarning, match=r"The `n` was deprecated since v1\.0"):
            result = round(proxy)
        assert result == 4

    def test_round_with_ndigits_forwards_and_warns(self) -> None:
        """``round(proxy, 2)`` routes through ``__round__`` (branch 2: ndigits given) and emits FutureWarning.

        The two-argument form is the most common rounding call in numerical code; both the forwarding
        and the warning policy must hold for this signature.
        """
        proxy = deprecated_instance(3.789, name="n", **_DEPRS_CASE_STD_ARGS)
        with pytest.warns(FutureWarning, match=r"The `n` was deprecated since v1\.0"):
            result = round(proxy, 2)
        assert result == 3.79

    @pytest.mark.parametrize(
        ("func", "expected"),
        [
            pytest.param(math.trunc, 3, id="trunc"),
            pytest.param(math.floor, 3, id="floor"),
            pytest.param(math.ceil, 4, id="ceil"),
        ],
    )
    def test_math_rounding_functions_forward_and_warn(self, func: Callable[[Any], Any], expected: int) -> None:
        """``math.trunc/floor/ceil`` on a proxy forward to the wrapped value and each emit FutureWarning.

        Libraries and ORMs that coerce legacy float handles via ``math.floor`` or ``math.ceil`` must
        still receive the correct integer result; because these are numeric reads, each is a data-use
        operation that consumes the warn budget.
        """
        proxy = deprecated_instance(3.7, name="n", **_DEPRS_CASE_STD_ARGS)
        with pytest.warns(FutureWarning, match=r"The `n` was deprecated since v1\.0"):
            result = func(proxy)
        assert result == expected

    def test_next_forwards_value_and_warns(self) -> None:
        """``next(proxy)`` on a proxied iterator forwards to the active iterator and emits FutureWarning.

        Wrapping a legacy iterator handle (e.g. a generator or file pointer) in ``deprecated_instance``
        must not break code that advances it with ``next()``; the call is a data-producing read, so the
        warning fires.
        """
        proxy = deprecated_instance(iter([10, 20]), name="it", **_DEPRS_CASE_STD_ARGS)
        with pytest.warns(FutureWarning, match=r"The `it` was deprecated since v1\.0"):
            result = next(proxy)
        assert result == 10


class TestAsyncProtocolForwarding:
    """Async protocol dunders forward to the active object so ``async with`` / ``async for`` / ``await`` keep working.

    The proxy hand-forwards ``__aenter__``/``__aexit__``/``__aiter__``/``__anext__``/``__await__`` to the wrapped
    async resource. These exercise the real asyncio runtime paths, not just structural presence of the
    methods. Warning policy mirrors the sync analogues: data-driving entry/iteration/await warn once; the
    ``__aexit__`` cleanup half stays silent.
    """

    @pytest.mark.asyncio
    async def test_async_context_manager_forwards(self) -> None:
        """A proxied async context manager drives ``async with proxy:`` through to the wrapped resource.

        A migration wraps a legacy async session in ``deprecated_instance``; callers that still write
        ``async with session:`` must reach the underlying ``__aenter__``/``__aexit__`` and receive the real
        resource as the bound value.
        """
        resource = AsyncManagedResource()
        proxy = deprecated_instance(resource, name="res", **_DEPRS_CASE_STD_ARGS, stream=None)
        async with proxy as entered:
            pass
        assert (resource.entered, resource.exited, entered is resource) == (True, True, True)

    @pytest.mark.asyncio
    async def test_async_context_entry_warns_once_exit_silent(self) -> None:
        """Async context entry warns exactly once; the async exit half emits no warning."""
        resource = AsyncManagedResource()
        proxy = deprecated_instance(resource, name="res", **_DEPRS_CASE_STD_ARGS)
        with captured_warnings() as caught:
            async with proxy:
                pass
        assert len([w for w in caught if issubclass(w.category, FutureWarning)]) == 1

    @pytest.mark.asyncio
    async def test_async_iteration_forwards(self) -> None:
        """``async for`` over a proxied async iterator yields every item from the wrapped resource.

        A deprecated async stream handle wrapped in ``deprecated_instance`` must remain iterable so
        ``async for chunk in stream:`` keeps producing the underlying sequence during the migration window.
        """
        resource = AsyncManagedResource([1, 2, 3])
        proxy = deprecated_instance(resource, name="stream", **_DEPRS_CASE_STD_ARGS, stream=None)
        collected = [item async for item in proxy]
        assert collected == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_async_iteration_warns_on_aiter(self) -> None:
        """Entering async iteration emits a deprecation warning (a data-producing use, like sync ``__iter__``)."""
        resource = AsyncManagedResource([1, 2])
        proxy = deprecated_instance(resource, name="stream", **_DEPRS_CASE_STD_ARGS)
        with pytest.warns(FutureWarning, match=r"The `stream` was deprecated since v1\.0"):
            _ = [item async for item in proxy]

    @pytest.mark.asyncio
    async def test_anext_forwards_to_active(self) -> None:
        """Calling ``__anext__`` on the proxy advances the wrapped async iterator directly.

        ``async for`` binds iteration to the object returned by ``__aiter__`` (the resource itself), so the proxy's
        own ``__anext__`` is only reached by an explicit ``await proxy.__anext__()`` — this pins that forwarding path.
        """
        resource = AsyncManagedResource([10, 20])
        proxy = deprecated_instance(resource, name="stream", **_DEPRS_CASE_STD_ARGS, stream=None)
        proxy.__aiter__()
        assert await proxy.__anext__() == 10

    @pytest.mark.asyncio
    async def test_await_forwards_to_active(self) -> None:
        """``await proxy`` awaits the wrapped awaitable and returns its result.

        Wrapping an awaitable handle in ``deprecated_instance`` must keep ``result = await handle`` working, routing
        through the proxy's ``__await__`` to the resource's coroutine.
        """
        resource = AsyncManagedResource()
        proxy = deprecated_instance(resource, name="awaitable", **_DEPRS_CASE_STD_ARGS, stream=None)
        assert await proxy == "awaited"


class TestEntryWarningCleanup:
    """Failed warning delivery must unwind resources acquired by proxy entry."""

    @pytest.mark.parametrize("drive", [pytest.param(_drive_enter, id="sync"), pytest.param(_drive_aenter, id="async")])
    @pytest.mark.parametrize("suppress", [False, True])
    @pytest.mark.parametrize("raising_stream", [False, True])
    def test_emission_failure_unwinds_acquired_context(
        self, drive: Callable[[Any], object], suppress: bool, raising_stream: bool
    ) -> None:
        """Clean up acquired contexts even if their exit hook would suppress errors.

        Applications treating warnings as errors, or using a failing logging sink,
        never reach the with body. The acquired resource must still receive the exact
        emission exception once and that exception must remain visible to the caller.
        """
        resource = RecordingEntryContext(suppress=suppress)
        stream = KeywordTypeErrorStream()
        proxy = make_warn_after_success_proxy(resource, **({"stream": stream} if raising_stream else {}))
        expected = TypeError if raising_stream else FutureWarning
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(expected) as raised:
                drive(proxy)
        assert resource.entries == 1
        assert len(resource.exits) == 1
        exc_type, exc, traceback = resource.exits[0]
        assert exc_type is expected
        assert exc is raised.value
        assert traceback is not None
        if raising_stream:
            assert len(stream.calls) == 1

    @pytest.mark.parametrize("drive", [pytest.param(_drive_enter, id="sync"), pytest.param(_drive_aenter, id="async")])
    def test_cleanup_uses_acquired_context_after_skip_changes(self, drive: Callable[[Any], object]) -> None:
        """Unwind the acquired target even if the failing stream changes skip state.

        A logging callback can activate a feature flag before it raises. Cleanup must
        use the context already acquired, although future proxy operations serve source.
        """
        source = RecordingEntryContext()
        target = RecordingEntryContext()
        proxy = make_switching_entry_proxy(source, target)
        with pytest.raises(RuntimeError, match="^delivery failed$") as raised:
            drive(proxy)
        assert (source.entries, source.exits, target.entries) == (0, [], 1)
        assert len(target.exits) == 1
        assert target.exits[0][1] is raised.value

    def test_warning_error_releases_real_lock(self) -> None:
        """Release a real lock when entry fails because a warning becomes an error.

        A subsequent nonblocking acquisition proves the warning path does not leave
        shared application state locked after Python declines to enter the with body.
        """
        lock = threading.Lock()
        proxy = make_warn_after_success_proxy(lock)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", FutureWarning)
                with pytest.raises(FutureWarning):
                    _drive_enter(proxy)
            assert not lock.locked()
            assert lock.acquire(blocking=False)
        finally:
            if lock.locked():
                lock.release()

    @pytest.mark.parametrize("drive", [pytest.param(_drive_enter, id="sync"), pytest.param(_drive_aenter, id="async")])
    @pytest.mark.parametrize("fail", [False, True])
    def test_underlying_entry_controls_cleanup(self, drive: Callable[[Any], object], fail: bool) -> None:
        """Clean up exactly once after successful entry and never after failed entry.

        A context whose own acquisition raises has nothing to unwind. Normal entry
        must still warn once, return the acquired resource and exit without an error.
        """
        resource = RecordingEntryContext(fail=fail)
        proxy = make_warn_after_success_proxy(resource)
        if fail:
            with captured_warnings() as caught, pytest.raises(RuntimeError, match="^entry failed$"):
                drive(proxy)
            assert not caught
            assert resource.exits == []
        else:
            with pytest.warns(FutureWarning) as caught:
                assert drive(proxy) is resource
            assert len(caught) == 1
            assert resource.exits == [(None, None, None)]
        assert resource.entries == 1


class TestSkippedInplaceReadOnly:
    """Read-only in-place operators honor the same dynamic skip as other writes."""

    def test_skip_bypasses_guards_then_rearms_without_mutation(self) -> None:
        """Permit skipped mutation and reject the same operation once skip is off.

        A feature flag disables a fatal read-only deprecation during a transition.
        The source list may mutate while skipped, but reactivation must reject the
        in-place operation before it changes the list or emits a warning.
        """
        source = [1, 2]
        flag = {"skip": True}
        proxy = make_read_only_inplace_proxy(source, flag)
        with captured_warnings() as caught:
            result = proxy.__iadd__([3])
            flag["skip"] = False
            with pytest.raises(AttributeError, match="read-only"):
                proxy.__iadd__([4])
        assert result is source
        assert source == [1, 2, 3]
        assert not caught


class TestDeferredAsyncWarningTiming:
    """Acquiring an awaitable spends the budget before deferred execution fails."""

    def test_exhaustion_after_acquisition_spends_budget(self) -> None:
        """Warn before an async iterator reports exhaustion during awaiting.

        An async iterator obtains its coroutine successfully but has no items left.
        The acquisition warning consumes the single notice before StopAsyncIteration.
        """
        source = FlakyProtocolBox(items=())
        proxy = make_warn_after_success_proxy(source)
        with pytest.warns(FutureWarning) as caught:
            pending = proxy.__anext__()
        assert len(caught) == 1
        with pytest.raises(StopAsyncIteration):
            asyncio.run(pending)
        source.items.append(7)
        with captured_warnings() as later:
            assert _drive_anext(proxy) == 7
        assert not later

    def test_await_failure_after_acquisition_spends_budget(self) -> None:
        """Warn before driving an await iterator that subsequently raises.

        A lazy awaitable acquires its iterator normally and fails only when awaited.
        Retrying the await still fails but cannot issue another default-budget notice.
        """
        proxy = make_warn_after_success_proxy(DeferredFailureAwaitable())
        with pytest.warns(FutureWarning) as caught, pytest.raises(RuntimeError, match="^deferred await failed$"):
            _drive_await(proxy)
        assert len(caught) == 1
        with captured_warnings() as later, pytest.raises(RuntimeError, match="^deferred await failed$"):
            _drive_await(proxy)
        assert not later


class TestAsyncAcquisitionCleanup:
    """A failed warning delivery after ``__anext__``/``__await__`` must not abandon what the acquisition created."""

    @pytest.mark.parametrize(
        "acquire", [pytest.param(_acquire_anext, id="anext"), pytest.param(_acquire_await, id="await")]
    )
    @pytest.mark.parametrize("raising_stream", [False, True])
    def test_failed_delivery_leaves_no_unawaited_coroutine(
        self, acquire: Callable[[Any], object], raising_stream: bool
    ) -> None:
        """Closing the acquired coroutine leaves no ``coroutine ... was never awaited`` warning behind.

        ``async for`` and ``await`` over a deprecated object fail on their first step when ``FutureWarning`` is an
        error or the logging sink raises. The coroutine the wrapped ``__anext__``/``__await__`` created never runs, so
        it must be closed rather than left for the garbage collector to report as a second, misleading warning.
        """
        stream = KeywordTypeErrorStream()
        proxy = make_warn_after_success_proxy(FlakyProtocolBox(), **({"stream": stream} if raising_stream else {}))
        with captured_warnings() as caught:
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(TypeError if raising_stream else FutureWarning):
                acquire(proxy)
            gc.collect()
        assert [str(record.message) for record in caught if issubclass(record.category, RuntimeWarning)] == []

    def test_failed_delivery_keeps_async_generator_open(self) -> None:
        """A failed ``__anext__`` delivery over an async generator leaves every item for the next iteration.

        The awaitable an async generator hands out is a view of the generator itself; on CPython 3.13+ closing it
        finishes the generator, so a later ``async for`` would end silently with no items. The proxy must not close it.
        """
        source = legacy_tick_stream()
        proxy = make_warn_after_success_proxy(source)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(FutureWarning):
                _acquire_anext(proxy)
            gc.collect()
        assert asyncio.run(_collect(source)) == [1, 2]

    def test_failed_delivery_keeps_wrapped_coroutine_awaitable(self) -> None:
        """A failed ``await`` delivery over a wrapped coroutine object leaves that coroutine usable.

        When the deprecated object is itself a coroutine, its await iterator belongs to it; closing that iterator would
        close the wrapped coroutine, and awaiting it again would raise ``cannot reuse already awaited coroutine``.
        """
        source = legacy_snapshot()
        proxy = make_warn_after_success_proxy(source)
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(FutureWarning):
                _acquire_await(proxy)
        assert asyncio.run(source) == [1, 2]

    def test_failed_delivery_keeps_stored_coroutine_awaitable(self) -> None:
        """A failed ``await`` delivery leaves usable a coroutine that the awaitable stores and reuses.

        A custom awaitable creates its coroutine once and returns that coroutine's await iterator from every
        ``__await__``. The iterator has the same type as one created fresh, but closing it would close the stored
        coroutine, so the caller's retry would raise ``cannot reuse already awaited coroutine``.
        """
        box = PendingSnapshotBox()
        proxy = make_warn_after_success_proxy(box)
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(FutureWarning):
                _acquire_await(proxy)
        assert asyncio.run(_await_once(box)) == [1, 2]

    def test_failed_delivery_keeps_cached_step_awaitable(self) -> None:
        """A failed ``__anext__`` delivery leaves usable a coroutine that the iterator caches for its next step.

        A synchronous ``__anext__`` can return a coroutine the iterator keeps instead of creating one per step. That
        coroutine is a ``coroutine`` like a fresh one, so the proxy must see that the iterator still holds it.
        """
        source = CachedStepIterator()
        proxy = make_warn_after_success_proxy(source)
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(FutureWarning):
                _acquire_anext(proxy)
        assert asyncio.run(_await_once(source.__anext__())) == [1, 2]

    @pytest.mark.parametrize("silent_layers", [1, 2])
    def test_failed_delivery_through_stacked_proxy_keeps_wrapped_coroutine(self, silent_layers: int) -> None:
        """A failed ``await`` delivery on an outer proxy leaves usable the coroutine inner proxies wrap.

        The outer proxy acquires the await iterator from the next proxy down, not from the coroutine, so the owner is a
        proxy. With two inner layers that owner wraps another proxy, so the coroutine sits at the bottom of the stack;
        it is still the wrapped object and must not be closed.
        """
        source = legacy_snapshot()
        inner = make_silent_proxy_stack(source, silent_layers)
        proxy = make_warn_after_success_proxy(inner)
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(FutureWarning):
                _acquire_await(proxy)
        assert asyncio.run(_await_once(source)) == [1, 2]


class _RefusesAddition:
    """Instance whose ``__add__`` declines every operand by returning ``NotImplemented`` (fixture)."""

    def __add__(self, other: object) -> object:
        """Decline the operation so Python tries the reflected operand."""
        return NotImplemented


class _ReflectedAddition:
    """Operand that answers ``something + self`` through ``__radd__`` (fixture)."""

    def __radd__(self, other: object) -> str:
        """Answer the reflected addition."""
        return "reflected"


class TestFormatAndDeclinedOperators:
    """``format()`` and binary operators on a proxy defer to the wrapped object like the object itself would."""

    def test_format_applies_spec_to_wrapped_value_without_warning(self) -> None:
        """``format(proxy, spec)`` and f-string specs format the wrapped value, and formatting never warns.

        Formatting a deprecated constant into a log line is a repr-like probe, not a use of the deprecated API, so a
        warning there would be noise.

        """
        proxy = deprecated_instance(3.14159, name="legacy_pi", **_DEPRS_CASE_STD_ARGS)

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert format(proxy, ".2f") == "3.14"
            assert f"{proxy:>8.1f}" == "     3.1"

    def test_operator_declined_by_wrapped_object_raises_type_error(self) -> None:
        """When the wrapped object's operator returns ``NotImplemented``, Python's normal ``TypeError`` surfaces.

        The proxy must not mask an unsupported operand pair by returning a value, and it must not warn for an
        operation that did not happen.

        """
        proxy = deprecated_instance(_RefusesAddition(), name="legacy_obj", **_DEPRS_CASE_STD_ARGS)

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with pytest.raises(TypeError, match=r"unsupported operand type\(s\) for \+"):
                _ = proxy + 1  # type: ignore[operator]

    def test_declined_operator_falls_back_to_reflected_operand(self) -> None:
        """A declined ``proxy + other`` is retried as ``other.__radd__(proxy)``, exactly as for a plain object."""
        proxy = deprecated_instance(_RefusesAddition(), name="legacy_obj", **_DEPRS_CASE_STD_ARGS)

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert proxy + _ReflectedAddition() == "reflected"
