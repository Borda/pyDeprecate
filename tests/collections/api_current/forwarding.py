"""Generator, async and protocol-forwarding targets for deprecation testing.

Iterators, async iterators, awaitables and context managers that deprecated wrappers in
:mod:`tests.collections.api_deprecated.forwarding` and :mod:`tests.collections.api_deprecated.core` forward to.
"""

import asyncio
import types
from collections.abc import AsyncGenerator, AsyncIterator, Coroutine, Generator, Iterator
from typing import Any, Callable, Optional


def gen_target(x: int) -> Iterator[int]:
    """Generator that yields multiples of x (1×, 2×, 3×).

    Used by the generator wrapper integration tests for the ``@deprecated`` decorator.

    """
    for i in range(1, 4):
        yield x * i


async def async_gen_target(x: int) -> AsyncIterator[int]:
    """Async generator that yields multiples of x (1×, 2×, 3×).

    Used by the async generator wrapper integration tests for the ``@deprecated`` decorator.  Mirrors
    :func:`gen_target` (sync generator) and :func:`async_target` (async coroutine) so all three callable kinds
    share the same input → output contract (``x=1`` → ``[1, 2, 3]``).

    Examples:
        >>> import asyncio
        >>>
        >>> async def _collect() -> list[int]:
        ...     return [item async for item in async_gen_target(x=2)]
        >>> asyncio.run(_collect())
        [2, 4, 6]

    """
    for i in range(1, 4):
        yield x * i


def running_total(start: int, log: list[str]) -> Generator[int, Optional[int], int]:
    """Yield a running total that grows by every value sent in, and record in ``log`` when the generator finishes.

    Driving it covers the whole generator protocol: ``send(n)`` adds ``n`` and yields the new total,
    ``throw(ValueError())`` resets the total to ``0`` and yields it, a plain ``next()`` ends the generator with the
    total as its return value, and ``close()`` or the end appends ``"closed"`` to ``log``.

    Examples:
        >>> log: list[str] = []
        >>> gen = running_total(1, log)
        >>> next(gen), gen.send(2), gen.throw(ValueError())
        (1, 3, 0)
        >>> gen.close()
        >>> log
        ['closed']

    """
    total = start
    try:
        while True:
            try:
                sent = yield total
            except ValueError:
                total = 0
                continue
            if sent is None:
                return total
            total += sent
    finally:
        log.append("closed")


async def async_running_total(start: int, log: list[str]) -> AsyncGenerator[int, Optional[int]]:
    """Async twin of :func:`running_total`: ``asend`` adds, ``athrow(ValueError())`` resets, ``aclose`` is logged.

    A plain ``__anext__()`` (``asend(None)``) ends the generator; an async generator cannot return a value.

    Examples:
        >>> import asyncio
        >>> log: list[str] = []
        >>>
        >>> async def _drive() -> list[int]:
        ...     agen = async_running_total(1, log)
        ...     seen = [await agen.__anext__(), await agen.asend(2), await agen.athrow(ValueError())]
        ...     await agen.aclose()
        ...     return seen
        >>> asyncio.run(_drive()), log
        ([1, 3, 0], ['closed'])

    """
    total = start
    try:
        while True:
            try:
                sent = yield total
            except ValueError:
                total = 0
                continue
            if sent is None:
                return
            total += sent
    finally:
        log.append("closed")


class CountdownAsyncIterator:
    """Async iterator (not an async generator) counting down from ``start`` to ``1``."""

    def __init__(self, start: int) -> None:
        """Start the countdown at ``start``."""
        self._next = start

    def __aiter__(self) -> "CountdownAsyncIterator":
        """Return the iterator itself, as the async iterator protocol requires."""
        return self

    async def __anext__(self) -> int:
        """Return the next count, or end the iteration after ``1``."""
        if self._next < 1:
            raise StopAsyncIteration
        self._next -= 1
        return self._next + 1


class LoggingAsyncCursor:
    """Async iterator with ``asend``/``athrow``/``aclose`` that is not a native async generator; logs every call."""

    def __init__(self, log: list[Any]) -> None:
        """Record calls in ``log`` and start the running total at zero."""
        self._log = log
        self._total = 0

    def __aiter__(self) -> "LoggingAsyncCursor":
        """Return the cursor itself, as the async iterator protocol requires."""
        return self

    async def __anext__(self) -> int:
        """Advance without a value, the same as ``asend(None)``."""
        return await self.asend(None)

    async def asend(self, value: Optional[int]) -> int:
        """Add ``value`` to the running total and return it."""
        self._log.append(("send", value))
        self._total += value or 0
        return self._total

    async def athrow(self, exc: BaseException) -> int:
        """Reset the running total on any thrown exception and return it."""
        self._log.append(("throw", type(exc).__name__))
        self._total = 0
        return self._total

    async def aclose(self) -> None:
        """Record that the cursor was closed."""
        self._log.append("close")


def open_cursor(log: list[Any]) -> LoggingAsyncCursor:
    """Return a logging cursor: a replacement for a deprecated async generator with the full async generator API.

    Examples:
        >>> import asyncio
        >>>
        >>> async def _drive() -> list[int]:
        ...     cursor = open_cursor([])
        ...     return [await cursor.__anext__(), await cursor.asend(5)]
        >>> asyncio.run(_drive())
        [0, 5]

    """
    return LoggingAsyncCursor(log)


def walk_down(depth: int, recurse: Callable[[int, Any], Iterator[int]]) -> Iterator[int]:
    """Yield ``depth`` down to ``0``, reaching the lower levels through ``recurse`` before the first ``yield``.

    Examples:
        >>> def _direct(depth: int, recurse: Any) -> Iterator[int]:
        ...     return walk_down(depth, recurse)
        >>> list(walk_down(2, _direct))
        [0, 1, 2]

    """
    if depth > 0:
        yield from recurse(depth - 1, recurse)
    yield depth


def countdown_stream(start: int) -> CountdownAsyncIterator:
    """Return a countdown async iterator: a replacement for a deprecated async generator that is not one itself.

    Examples:
        >>> import asyncio
        >>>
        >>> async def _collect() -> list[int]:
        ...     return [item async for item in countdown_stream(3)]
        >>> asyncio.run(_collect())
        [3, 2, 1]

    """
    return CountdownAsyncIterator(start)


async def _resolved(value: int) -> int:
    """Return ``value``: the awaitable that :class:`SyncStoppingAsyncIterator` hands out for each item."""
    return value


class SyncStoppingAsyncIterator:
    """Async iterator whose plain ``def`` steps end the iteration synchronously instead of from an awaitable.

    ``__anext__`` returns an awaitable for each of the counts ``1`` to ``count``, but raises ``StopAsyncIteration``
    directly once they run out, and ``athrow`` ends the iteration the same way. Native ``async for`` treats a
    ``StopAsyncIteration`` raised by the call itself as a normal end, so this is still a valid async iterator.

    """

    def __init__(self, count: int) -> None:
        """Hold the counts ``1`` to ``count`` still to be handed out."""
        self._remaining = list(range(1, count + 1))

    def __aiter__(self) -> "SyncStoppingAsyncIterator":
        """Return the iterator itself, as the async iterator protocol requires."""
        return self

    def __anext__(self) -> Coroutine[Any, Any, int]:
        """Return an awaitable for the next count, or raise ``StopAsyncIteration`` at once when none is left."""
        if not self._remaining:
            raise StopAsyncIteration
        return _resolved(self._remaining.pop(0))

    def athrow(self, exc: BaseException) -> Coroutine[Any, Any, int]:
        """Drop the remaining counts on any thrown exception and end the iteration before returning an awaitable."""
        self._remaining.clear()
        raise StopAsyncIteration from exc


def sync_stopping_stream(count: int) -> SyncStoppingAsyncIterator:
    """Return an async iterator that ends synchronously: a replacement for a deprecated async generator.

    Examples:
        >>> import asyncio
        >>>
        >>> async def _collect() -> list[int]:
        ...     return [item async for item in sync_stopping_stream(2)]
        >>> asyncio.run(_collect())
        [1, 2]

    """
    return SyncStoppingAsyncIterator(count)


@types.coroutine
def generator_based_double(x: int) -> Generator[None, None, int]:
    """Return ``x * 2`` from a generator-based coroutine: a generator function that ``await`` accepts.

    ``@types.coroutine`` flags the code object with ``CO_ITERABLE_COROUTINE``, so ``await generator_based_double(3)``
    works although the function is a plain generator function, not an ``async def``.

    Examples:
        >>> import asyncio
        >>>
        >>> async def _run() -> int:
        ...     return await generator_based_double(3)
        >>> asyncio.run(_run())
        6

    """
    if False:  # pragma: no cover - unreachable; declares this as a generator
        yield
    return x * 2


class DataBatch:
    """One batch handed out by :func:`load_batches`; a plain class, so a test can hold a weak reference to it."""

    def __init__(self, index: int) -> None:
        """Record the position of the batch in its stream."""
        self.index = index


def load_batches(count: int) -> Iterator[DataBatch]:
    """Yield ``count`` fresh batches, keeping no reference to a batch once it has been handed out.

    Examples:
        >>> [batch.index for batch in load_batches(3)]
        [0, 1, 2]

    """
    for index in range(count):
        yield DataBatch(index)


def squares_list(n: int) -> list[int]:
    """Return the first ``n`` squares as a list: a non-generator replacement for a deprecated generator function.

    Examples:
        >>> squares_list(3)
        [1, 4, 9]

    """
    return [i * i for i in range(1, n + 1)]


def flush_pending_jobs(queue: str) -> Generator[str, None, str]:
    """Return a summary for ``queue`` without yielding a job: a generator replacement that ends on its first step.

    Examples:
        >>> next(flush_pending_jobs("emails"))
        Traceback (most recent call last):
        ...
        StopIteration: emails: no pending jobs

    """
    if False:  # pragma: no cover - unreachable; declares this as a generator
        yield queue
    return f"{queue}: no pending jobs"


def parse_rows(lines: list[str]) -> Iterator[int]:
    """Yield each line as an integer, after checking every line before the first ``yield``.

    A malformed line therefore fails the first step with ``ValueError``, before any row is handed out.

    Examples:
        >>> list(parse_rows(["1", "2"]))
        [1, 2]
        >>> next(parse_rows(["1", "x"]))
        Traceback (most recent call last):
        ...
        ValueError: line 'x' is not an integer

    """
    for line in lines:
        if not line.isdigit():
            raise ValueError(f"line {line!r} is not an integer")
    for line in lines:
        yield int(line)


async def async_parse_rows(lines: list[str]) -> AsyncIterator[int]:
    """Async twin of :func:`parse_rows`: a malformed line fails the first step before any row is handed out.

    Examples:
        >>> import asyncio
        >>>
        >>> async def _collect() -> list[int]:
        ...     return [row async for row in async_parse_rows(["3", "4"])]
        >>> asyncio.run(_collect())
        [3, 4]

    """
    for line in lines:
        if not line.isdigit():
            raise ValueError(f"line {line!r} is not an integer")
    for line in lines:
        yield int(line)


async def async_fetch_page(page: int) -> AsyncIterator[int]:
    """Yield the two record ids of ``page`` after one await, as a network fetch suspends before its first ``yield``.

    Examples:
        >>> import asyncio
        >>>
        >>> async def _collect() -> list[int]:
        ...     return [record async for record in async_fetch_page(3)]
        >>> asyncio.run(_collect())
        [30, 31]

    """
    await asyncio.sleep(0)
    for offset in range(2):
        yield page * 10 + offset


def ask_once(question: str) -> Generator[str, Optional[str], str]:
    """Yield ``question`` once, then return the reply sent in, or ``"cancelled"`` when a ``ValueError`` is thrown in.

    Examples:
        >>> prompt = ask_once("Proceed?")
        >>> next(prompt)
        'Proceed?'
        >>> prompt.send("yes")
        Traceback (most recent call last):
        ...
        StopIteration: yes

    """
    try:
        reply = yield question
    except ValueError:
        return "cancelled"
    return reply or ""


class SyncCallableObject:
    """Callable object with a plain synchronous ``__call__`` — a callable instance a scope may safely decorate."""

    def __call__(self, x: int) -> int:
        """Return ``x`` unchanged."""
        return x


class AsyncCallableObject:
    """Callable object whose ``__call__`` is a coroutine function, so calling an instance only builds a coroutine."""

    async def __call__(self, x: int) -> int:
        """Return ``x`` unchanged once awaited."""
        return x


class GeneratorCallableObject:
    """Callable object whose ``__call__`` is a generator function, so calling an instance only builds a generator."""

    def __call__(self, x: int) -> Iterator[int]:
        """Yield ``x`` once iterated."""
        yield x


# --- Protocol-forwarding targets (appended for the proxy medium-findings fixes) ---


class ManagedResource:
    """Context-manager target for proxy ``__enter__``/``__exit__`` forwarding tests.

    A migration may wrap a legacy resource handle (a database session, a file-like object) in
    ``deprecated_instance`` while callers still use it in a ``with`` block.  The proxy must forward the
    context-manager protocol so ``with proxy:`` enters and exits the underlying resource, recording both
    transitions here for assertions.
    """

    def __init__(self) -> None:
        """Track whether the context was entered and exited."""
        self.entered = False
        self.exited = False

    def __enter__(self) -> "ManagedResource":
        """Mark the resource as entered and return it."""
        self.entered = True
        return self

    def __exit__(self, *exc_info: Any) -> None:  # noqa: ANN401
        """Mark the resource as exited; never suppress exceptions."""
        self.exited = True


class AsyncManagedResource:
    """Async-protocol target for proxy async-dunder forwarding tests.

    An async migration may wrap a legacy async resource (an aiohttp session, an async DB connection) in
    ``deprecated_instance`` while callers still use ``async with``, ``async for``, or ``await`` on it. The proxy
    must forward every async protocol dunder so those call sites keep working; each transition is recorded here
    for assertions.
    """

    def __init__(self, items: Optional[list[int]] = None) -> None:
        """Track context transitions and hold the items produced by async iteration."""
        self.entered = False
        self.exited = False
        self._items = [1, 2, 3] if items is None else list(items)
        self._cursor = 0

    async def __aenter__(self) -> "AsyncManagedResource":
        """Mark the resource entered and return it."""
        self.entered = True
        return self

    async def __aexit__(self, *exc_info: Any) -> None:  # noqa: ANN401
        """Mark the resource exited; never suppress exceptions."""
        self.exited = True

    def __aiter__(self) -> "AsyncManagedResource":
        """Reset the cursor and return self as the async iterator."""
        self._cursor = 0
        return self

    async def __anext__(self) -> int:
        """Yield the next item, raising ``StopAsyncIteration`` once exhausted."""
        if self._cursor >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._cursor]
        self._cursor += 1
        return item

    def __await__(self) -> Generator[Any, None, str]:
        """Make the resource awaitable; ``await resource`` yields a sentinel string."""
        return self._awaited().__await__()

    async def _awaited(self) -> str:
        """Coroutine body returned by :meth:`__await__`."""
        return "awaited"


class SubclassableBase:
    """Plain base-class target for PEP 560 subclassing tests through ``deprecated_class``.

    During a migration window a public base class is often renamed and wrapped in ``deprecated_class``;
    downstream code still writes ``class Child(OldName): ...``.  This concrete class stands in for the
    replacement so a subclass built off the deprecated alias inherits real, callable behaviour.
    """

    marker: str = "base"

    def greet(self) -> str:
        """Return a fixed greeting so subclasses can prove inherited behaviour works."""
        return "hello"


class RecordingEntryContext:
    """Record context acquisition and cleanup, including errors supplied to cleanup."""

    def __init__(self, *, fail: bool = False, suppress: bool = False) -> None:
        """Configure entry failure and cleanup suppression independently."""
        self.fail = fail
        self.suppress = suppress
        self.entries = 0
        self.exits: list[tuple[Any, ...]] = []

    def __enter__(self) -> "RecordingEntryContext":
        """Acquire once, or raise before acquisition completes."""
        self.entries += 1
        if self.fail:
            raise RuntimeError("entry failed")
        return self

    def __exit__(self, *exc_info: object) -> bool:
        """Record the exact exception information and optionally suppress it."""
        self.exits.append(exc_info)
        return self.suppress

    async def __aenter__(self) -> "RecordingEntryContext":
        """Acquire through the asynchronous protocol."""
        return self.__enter__()

    async def __aexit__(self, *exc_info: object) -> bool:
        """Record asynchronous cleanup and optionally suppress its exception."""
        return self.__exit__(*exc_info)


class DeferredFailureAwaitable:
    """Fail while driving an acquired await iterator, rather than during acquisition."""

    def __await__(self) -> Generator[Any, None, int]:
        """Acquire an iterator whose first advancement raises."""
        return self._fail().__await__()

    async def _fail(self) -> int:
        """Raise after the await iterator has already been obtained."""
        raise RuntimeError("deferred await failed")


async def legacy_tick_stream() -> AsyncIterator[int]:
    """Async generator yielding ``1`` then ``2``: a deprecated async data stream whose items must outlive a failure."""
    yield 1
    yield 2


async def legacy_snapshot() -> list[int]:
    """Coroutine returning ``[1, 2]``; its coroutine object is wrapped directly as a deprecated awaitable."""
    return [1, 2]


class PendingSnapshotBox:
    """Awaitable whose ``__await__`` hands out the await iterator of a coroutine it created once and keeps."""

    def __init__(self) -> None:
        """Create the one coroutine every ``await`` of this box delegates to."""
        self.pending = legacy_snapshot()

    def __await__(self) -> Generator[Any, None, list[int]]:
        """Delegate to the stored coroutine, so awaiting the box twice reuses one coroutine object."""
        return self.pending.__await__()


class CachedStepIterator:
    """Async iterator whose synchronous ``__anext__`` returns a coroutine it keeps until that step is awaited."""

    def __init__(self) -> None:
        """Create the coroutine the next step hands out."""
        self.cached = legacy_snapshot()

    def __aiter__(self) -> "CachedStepIterator":
        """Return the iterator itself."""
        return self

    def __anext__(self) -> Coroutine[Any, Any, list[int]]:
        """Return the stored coroutine instead of creating a new one per step."""
        return self.cached
