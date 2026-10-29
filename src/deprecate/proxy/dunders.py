"""Register the optional protocol forwards of the deprecation proxy classes.

Context and async acquisition methods share their cleanup helpers here; generated arithmetic, comparison, and conversion
methods follow them in the original installation order. Importing this module registers the methods with
:func:`deprecate.proxy.core._register_protocol_forward`, preserving their public names and serialization identities;
each proxy class built by :mod:`deprecate.proxy.core` then carries only the forwards its wrapped object supports.

"""

import gc
import math
import operator
import os
import sys
import types
from typing import Any, Callable

from deprecate.proxy.core import _DeprecatedProxy, _register_protocol_forward

__all__: list[str] = []


async def _sample_coroutine() -> None:
    """Do nothing; one instance is created and closed at import to look up the coroutine await-iterator type."""


def _coroutine_wrapper_type() -> type:
    """Return the type of ``coroutine.__await__()`` (CPython's ``coroutine_wrapper``), which :mod:`types` does not name.

    The sample coroutine is closed before it starts, so creating it runs no code and leaves no unawaited coroutine.

    """
    sample = _sample_coroutine()
    try:
        return type(sample.__await__())
    finally:
        sample.close()


#: Type of the iterator returned by ``coroutine.__await__()``.
_COROUTINE_WRAPPER_TYPE = _coroutine_wrapper_type()


def _exit_acquired(context: Any, *exc_info: Any) -> None:  # noqa: ANN401
    """Unwind a context the proxy entered, handing its ``__exit__`` the error that aborted the entry.

    Whatever ``__exit__`` returns is ignored: the caller re-raises that error, so a suppressing context cannot hide it.

    """
    context.__exit__(*exc_info)


def _held_by(owner: Any) -> list[Any]:  # noqa: ANN401
    """List *owner* and the objects it references directly, looking one level into an instance ``__dict__``.

    Depending on the Python version, :func:`gc.get_referents` reports an instance's attribute values directly or through
    its ``__dict__``, so both shapes are expanded. A deprecation proxy is followed down its ``__wrapped__`` chain, so
    the object at the bottom of any stack of proxies counts as held by the outermost one.

    """
    held: list[Any] = []
    seen: set[int] = set()
    while id(owner) not in seen:
        seen.add(id(owner))
        held.append(owner)
        for ref in gc.get_referents(owner):
            held.append(ref)
            if type(ref) is dict:
                held.extend(ref.values())
        if not isinstance(owner, _DeprecatedProxy):
            break
        # Read the slot directly: attribute access through the proxy would warn.
        owner = object.__getattribute__(owner, "__wrapped__")
    return held


def _close_abandoned(acquired: Any, owner: Any, *exc_info: Any) -> None:  # noqa: ANN401
    """Close a coroutine that an acquiring call created, so it is not left behind unawaited.

    When warning delivery fails after ``__anext__`` or ``__await__`` returned, the caller never awaits what it got, and
    the garbage collector would report ``coroutine ... was never awaited`` on top of the real error. An unstarted
    coroutine closes without running its body, so closing is safe for what the call created fresh: a coroutine (from
    ``__anext__``) or the await iterator of one (``__await__`` returning ``helper().__await__()``).

    The result type alone does not prove the call created it: ``__await__`` can return the await iterator of a
    coroutine the object stores, a synchronous ``__anext__`` can return a cached coroutine, and a stacked proxy acquires
    from the inner proxy, which holds the wrapped coroutine. Closing a borrowed coroutine breaks the retry with
    ``cannot reuse already awaited coroutine``, so the coroutine is left open whenever *owner* is it or references it,
    directly or through its instance ``__dict__``, looking through any stack of deprecation proxies. A coroutine stored
    deeper inside the owner (in a list attribute or on a helper object it holds) is not detected and is closed.

    Everything else is left untouched, because it can be a view of the wrapped object itself: an async generator's
    ``asend`` awaitable (on CPython 3.13 and later closing it finishes the generator, so later iteration would end
    with no items). On CPython 3.13 and later an abandoned ``asend`` still reports that it was never awaited; keeping
    the stream usable wins. Other awaitables (futures, plain generators) need no cleanup.

    Args:
        acquired: The awaitable or await iterator the wrapped object returned.
        owner: The object it was acquired from.
        exc_info: The delivery error's ``sys.exc_info()``, accepted for the release-callback signature and unused.

    """
    kind = type(acquired)
    if kind is types.CoroutineType:
        coroutine = acquired
    elif kind is _COROUTINE_WRAPPER_TYPE:
        coroutine = next((ref for ref in gc.get_referents(acquired) if type(ref) is types.CoroutineType), None)
    else:
        return
    if coroutine is None or any(ref is coroutine for ref in _held_by(owner)):
        return
    acquired.close()


def __enter__(self: _DeprecatedProxy) -> Any:  # noqa: ANN401, N807
    """Enter the active context, unwinding it if warning delivery after acquisition fails."""
    active, live = self._gate()
    entered = active.__enter__()
    # Python will not invoke __exit__ when entry raises. Unwind the context we acquired,
    # even if the stream changed skip state, and never suppress the emission failure.
    self._deliver_or_release(live, _exit_acquired, active)
    return entered


def __exit__(self: _DeprecatedProxy, *exc_info: Any) -> Any:  # noqa: ANN401, N807
    """Exit the active object's context without emitting a warning (cleanup half of the pair)."""
    return self._get_active().__exit__(*exc_info)


async def __aenter__(self: _DeprecatedProxy) -> Any:  # noqa: ANN401, N807
    """Async-enter the active context, unwinding it if warning delivery after acquisition fails."""
    active, live = self._gate()
    entered = await active.__aenter__()
    try:
        self._deliver(live)
    except BaseException:
        # Entry has acquired the resource, but Python will not call __aexit__ on its failure.
        # Release the acquired context via __aexit__, then re-raise the delivery error unchanged, whatever
        # __aexit__ returns: its return value is ignored, so a suppressing context cannot swallow the error.
        await active.__aexit__(*sys.exc_info())
        raise
    return entered


async def __aexit__(self: _DeprecatedProxy, *exc_info: Any) -> Any:  # noqa: ANN401, N807
    """Async-exit the active object's context without emitting a warning (cleanup half of the pair)."""
    return await self._get_active().__aexit__(*exc_info)


def __aiter__(self: _DeprecatedProxy) -> Any:  # noqa: ANN401, N807
    """Return the active object's async iterator, emitting a deprecation warning once it is obtained."""
    active, live = self._gate()
    iterator = active.__aiter__()
    self._deliver(live)
    return iterator


def __anext__(self: _DeprecatedProxy) -> Any:  # noqa: ANN401, N807
    """Return the active object's next awaitable, emitting a deprecation warning once it is obtained.

    If delivering the warning raises, a coroutine the wrapped ``__anext__`` created is closed unstarted before the error
    propagates (see :func:`_close_abandoned`).

    """
    active, live = self._gate()
    awaitable = active.__anext__()
    self._deliver_or_release(live, _close_abandoned, awaitable, active)
    return awaitable


def __await__(self: _DeprecatedProxy) -> Any:  # noqa: ANN401, N807
    """Await the active object, emitting a deprecation warning once its await iterator is obtained.

    If delivering the warning raises, the await iterator of a coroutine the wrapped ``__await__`` created is closed
    unstarted before the error propagates (see :func:`_close_abandoned`).

    """
    active, live = self._gate()
    iterator = active.__await__()
    self._deliver_or_release(live, _close_abandoned, iterator, active)
    return iterator


# Register the handwritten acquisition protocols before the generated operators, preserving their class identity.
for _method in (__enter__, __exit__, __aenter__, __aexit__, __aiter__, __anext__, __await__):
    _method.__module__ = "deprecate.proxy"
    _method.__qualname__ = f"_DeprecatedProxy.{_method.__name__}"
    _register_protocol_forward(_method)


# ----------------------------------------------------------------------
# Operator / protocol dunder forwarding (see the "Protocol forwarding" section of the
# _DeprecatedProxy docstring for the warn-policy contract).
#
# Implicit special-method lookup bypasses ``__getattr__`` — Python resolves dunders on the *type*,
# not the instance — so every operator, conversion, and protocol dunder must live on the class
# itself for the proxy to stay transparent. These are generated from a table (rather than ~60
# hand-written defs) and registered below; each proxy class carries the ones its wrapped object supports.
# ----------------------------------------------------------------------


def _unwrap_operand(value: Any) -> Any:  # noqa: ANN401
    """Return the active object behind a proxy operand, leaving non-proxy operands untouched."""
    if isinstance(value, _DeprecatedProxy):
        return _DeprecatedProxy._get_static_attr_owner(value)
    return value


def _make_binary_forward(op_name: str, *, warn: bool) -> Callable[..., Any]:
    """Build a binary-operator dunder that forwards to the active object.

    Looks the operator up on the *type* of the active object and returns :data:`NotImplemented` when it is absent, so
    Python can try the reflected operand and ultimately raise its normal :class:`TypeError` — the proxy never masks an
    unsupported-operand error. Proxy operands are unwrapped to their active object first. A warning operator resolves
    first: one ``skip_if`` decision selects the operand and the warning, and a fatal deprecation is refused only once
    the operator has produced a result.

    """

    def _binary_dunder(self: "_DeprecatedProxy", other: Any, *extra: Any) -> Any:  # noqa: ANN401
        active, live = self._activation()
        impl = getattr(type(active), op_name, None)
        if impl is None:
            return NotImplemented
        result = impl(active, _unwrap_operand(other), *(_unwrap_operand(e) for e in extra))
        if result is NotImplemented:
            return NotImplemented
        if warn and live:
            self._refuse_fatal()
            self._deliver(live)
        return result

    _binary_dunder.__module__ = "deprecate.proxy"
    _binary_dunder.__name__ = op_name
    _binary_dunder.__qualname__ = f"_DeprecatedProxy.{op_name}"
    return _binary_dunder


def _make_inplace_forward(op_name: str) -> Callable[..., Any]:
    """Build an in-place operator dunder that enforces ``read_only`` before forwarding.

    In-place operators mutate the active object, so they must honour the ``read_only=True`` contract.  This is a
    separate factory from :func:`_make_binary_forward` so that the check is not paid by the much more common
    arithmetic/reflected paths.  The mutation is a side effect, so one :meth:`~deprecate.proxy._DeprecatedProxy._gate`
    call checks ``read_only`` and refuses a fatal deprecation before the operator is looked up — a read-only proxy over
    a value without the in-place hook (an ``int``) still raises instead of letting Python fall back to the binary
    operator. The warning follows only a result other than :data:`NotImplemented` — a declined hook makes Python retry
    the binary operator, which warns on its own. The mutation has already happened when the warning is delivered, so it
    stays if that delivery raises.

    """
    read_only_op = f"In-place operation '{op_name}'"

    def _inplace_dunder(self: "_DeprecatedProxy", other: Any) -> Any:  # noqa: ANN401
        active, live = self._gate(read_only_op=read_only_op)
        impl = getattr(type(active), op_name, None)
        if impl is None:
            return NotImplemented
        result = impl(active, _unwrap_operand(other))
        if result is NotImplemented:
            return NotImplemented
        self._deliver(live)
        return result

    _inplace_dunder.__module__ = "deprecate.proxy"
    _inplace_dunder.__name__ = op_name
    _inplace_dunder.__qualname__ = f"_DeprecatedProxy.{op_name}"
    return _inplace_dunder


def _make_unary_forward(op_name: str, func: Callable[[Any], Any]) -> Callable[..., Any]:
    """Build a unary/conversion dunder that forwards through *func* (a builtin such as ``int`` or ``abs``).

    Delegating through the builtin preserves Python's own fallbacks (e.g. ``int()`` falling back to ``__index__``) and
    lets the natural :class:`TypeError` surface when the active object does not support the protocol.  The warning
    follows a successful call only, so a failing one keeps the warn budget; every forwarded hook is a read without a
    lasting effect, which is why ``__next__`` (it consumes an item) is hand-written to warn first instead.  Every
    forward first passes :meth:`~deprecate.proxy._DeprecatedProxy._gate`, so a fatal deprecation never runs the wrapped
    object's hook (``__index__`` or ``__fspath__`` may run user code).

    """

    def _unary_dunder(self: "_DeprecatedProxy") -> Any:  # noqa: ANN401
        active, live = self._gate()
        result = func(active)
        self._deliver(live)
        return result

    _unary_dunder.__module__ = "deprecate.proxy"
    _unary_dunder.__name__ = op_name
    _unary_dunder.__qualname__ = f"_DeprecatedProxy.{op_name}"
    return _unary_dunder


#: Arithmetic, reflected, and in-place binary operators — all warn (a data-producing use).
_ARITHMETIC_OPS: tuple[str, ...] = (
    "__add__", "__sub__", "__mul__", "__matmul__", "__truediv__", "__floordiv__",
    "__mod__", "__divmod__", "__pow__", "__lshift__", "__rshift__", "__and__", "__xor__", "__or__",
)  # fmt: skip
_REFLECTED_OPS: tuple[str, ...] = (
    "__radd__", "__rsub__", "__rmul__", "__rmatmul__", "__rtruediv__", "__rfloordiv__",
    "__rmod__", "__rdivmod__", "__rpow__", "__rlshift__", "__rrshift__", "__rand__", "__rxor__", "__ror__",
)  # fmt: skip
_INPLACE_OPS: tuple[str, ...] = (
    "__iadd__", "__isub__", "__imul__", "__imatmul__", "__itruediv__", "__ifloordiv__",
    "__imod__", "__ipow__", "__ilshift__", "__irshift__", "__iand__", "__ixor__", "__ior__",
)  # fmt: skip
#: Ordering comparisons — silent (cheap probes, mirroring ``__eq__``/``__len__``).
_ORDERING_OPS: tuple[str, ...] = ("__lt__", "__le__", "__gt__", "__ge__")
#: Unary and conversion protocols forwarded through their builtin — all warn (a data-producing use).
_UNARY_OPS: dict[str, Callable[[Any], Any]] = {
    "__neg__": operator.neg,
    "__pos__": operator.pos,
    "__abs__": abs,
    "__invert__": operator.invert,
    "__int__": int,
    "__float__": float,
    "__complex__": complex,
    "__index__": operator.index,
    "__trunc__": math.trunc,
    "__floor__": math.floor,
    "__ceil__": math.ceil,
    "__reversed__": reversed,
    "__fspath__": os.fspath,
}
#: Text types a conversion builtin parses natively (``int("5")``, ``open("data.txt")``) although the type defines no
#: such dunder; a proxy over one keeps the conversion forward so a deprecated text constant still converts.
_NATIVE_TEXT_TYPES: dict[str, tuple[type, ...]] = {
    "__int__": (str, bytes, bytearray),
    "__float__": (str, bytes, bytearray),
    "__complex__": (str,),
    "__fspath__": (str, bytes),
}


def _install_forwarding_dunders() -> None:
    """Register the generated operator/conversion dunders as optional protocol forwards at import time.

    Arithmetic and reflected binary operators are built via :func:`_make_binary_forward`; in-place operators via
    :func:`_make_inplace_forward` (which also enforces ``read_only``); ordering comparisons via
    :func:`_make_binary_forward` (silent); unary and conversion protocols via :func:`_make_unary_forward`. Ordering
    comparisons are silent (structural probes); every other generated dunder warns (data use). In-place operators are
    set on the base class rather than registered: every proxy carries them, so ``+=`` on a ``read_only`` proxy reaches
    the guard even when the wrapped type has neither the in-place nor the binary operator and the right-hand operand
    defines the reflected one. Without the hook the forward declines, so Python falls back exactly as for the wrapped
    object; no structural ABC keys on in-place operators.

    """
    for _op in (*_ARITHMETIC_OPS, *_REFLECTED_OPS):
        _register_protocol_forward(_make_binary_forward(_op, warn=True))
    for _op in _INPLACE_OPS:
        setattr(_DeprecatedProxy, _op, _make_inplace_forward(_op))
    for _op in _ORDERING_OPS:
        _register_protocol_forward(_make_binary_forward(_op, warn=False))
    for _op, _func in _UNARY_OPS.items():
        _register_protocol_forward(_make_unary_forward(_op, _func), native_types=_NATIVE_TEXT_TYPES.get(_op, ()))


_install_forwarding_dunders()
