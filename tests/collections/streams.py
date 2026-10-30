"""Stand-ins for the ``stream=`` callables users pass to the deprecation decorators.

Plain functions cover the signature shapes ``_stream_accepts_stacklevel`` must classify; recording classes
count every invocation, so a test can prove the stream ran exactly once.
"""

import functools
import logging
import time
import warnings
from typing import Any, Callable, Optional


def stream_message_only(msg: str) -> None:
    """Stream that takes only the message, like a ``print``-style sink with no ``stacklevel`` support."""


@functools.wraps(logging.warning)
def stream_wraps_logging_warning(msg: str) -> None:
    """Message-only stream copying ``logging.warning``'s metadata, so ``__wrapped__`` has a wider signature."""


def stream_with_stacklevel(msg: str, stacklevel: int = 1) -> None:
    """Stream that accepts ``stacklevel`` as an ordinary keyword-capable parameter."""


def stream_positional_only_message(msg: str, /, stacklevel: int = 1) -> None:
    """Stream whose message is positional-only while ``stacklevel`` can still be passed by keyword."""


def stream_positional_only_stacklevel(msg: str, stacklevel: int = 1, /) -> None:
    """Stream whose ``stacklevel`` is positional-only, so ``stacklevel=`` by keyword is rejected."""


def stream_keyword_only_stacklevel(msg: str, *, stacklevel: int = 1) -> None:
    """Stream that accepts ``stacklevel`` only as a keyword-only parameter."""


def stream_var_keyword(msg: str, **kwargs: Any) -> None:  # noqa: ANN401
    """Stream that soaks up any keyword, ``stacklevel`` included, through ``**kwargs``."""


def stream_var_positional_named_stacklevel(msg: str, *stacklevel: int) -> None:
    """Stream with a ``*stacklevel`` catch-all — the name matches but no keyword binding is possible."""


class RecordingStream:
    """Stream that records every call's message and keyword arguments and accepts ``stacklevel``.

    A logging handler or metrics sink plugged in as ``stream=`` has side effects, so how many times the deprecation
    machinery invokes it is observable behaviour.  ``calls`` holds one ``(msg, kwargs)`` pair per invocation.
    """

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, msg: str, stacklevel: int = 1) -> None:
        """Record the message together with the ``stacklevel`` it was handed."""
        self.calls.append((msg, {"stacklevel": stacklevel}))


class MessageOnlyRecordingStream:
    """Stream that records every message and has no ``stacklevel`` parameter at all."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[str] = []

    def __call__(self, msg: str) -> None:
        """Record the message."""
        self.calls.append(msg)


class KeywordTypeErrorStream(RecordingStream):
    """Stream that accepts ``stacklevel`` but fails inside its own body with a ``TypeError`` mentioning "keyword".

    Models a handler whose internal bug raises ``TypeError("unexpected keyword in handler")`` — text that a
    message-sniffing retry would mistake for a rejected ``stacklevel`` keyword and answer by invoking the stream a
    second time.
    """

    def __call__(self, msg: str, stacklevel: int = 1) -> None:
        """Record the call, then raise the handler's own ``TypeError``."""
        super().__call__(msg, stacklevel=stacklevel)
        raise TypeError("unexpected keyword in handler")


class FlakyTypeErrorStream(RecordingStream):
    """Stream that accepts ``stacklevel`` and raises an unrelated ``TypeError`` on its first call only.

    Models a sink that fails once (a lazily configured handler, say) and works afterwards; the first failure must
    reach the caller instead of being swallowed and silently retried.
    """

    def __call__(self, msg: str, stacklevel: int = 1) -> None:
        """Record the call and raise on the first one."""
        super().__call__(msg, stacklevel=stacklevel)
        if len(self.calls) == 1:
            raise TypeError("unsupported operand type(s) for +: 'int' and 'str'")


class UnintrospectableStream(RecordingStream):
    """Stream whose ``__signature__`` raises ``ValueError``, so ``inspect.signature`` cannot describe it.

    Stands in for C-level callables without signature metadata: the deprecation machinery cannot tell whether it
    accepts ``stacklevel``, so it must call it with the message alone and exactly once.
    """

    @property
    def __signature__(self) -> Any:  # noqa: ANN401
        """Always raise ``ValueError``, as ``inspect.signature`` does for an unsupported builtin."""
        raise ValueError("no signature found for this stream")

    def __call__(self, msg: str, stacklevel: Optional[int] = None) -> None:
        """Record the message and whether ``stacklevel`` was passed (``None`` means it was not)."""
        self.calls.append((msg, {} if stacklevel is None else {"stacklevel": stacklevel}))


class UnhashableStream(RecordingStream):
    """Stream that cannot be hashed, so any memoised per-stream decision must fall back to an uncached check.

    Defining ``__eq__`` without ``__hash__`` is the everyday way a user-written callable class ends up unhashable.
    """

    def __eq__(self, other: object) -> bool:
        """Compare by call log, which is what makes the instance unhashable."""
        return isinstance(other, UnhashableStream) and other.calls == self.calls

    __hash__ = None  # type: ignore[assignment]


class RuntimeSignatureStream(UnintrospectableStream):
    """Represent a working stream whose metadata descriptor fails unexpectedly."""

    def __init__(self, *, fail: bool = False) -> None:
        """Configure a distinct callback failure after recording delivery."""
        super().__init__()
        self.fail = fail

    @property
    def __signature__(self) -> Any:  # noqa: ANN401
        """Raise a descriptor error rather than an unsupported-signature error."""
        raise RuntimeError("signature metadata failed")

    def __call__(self, msg: str, stacklevel: Optional[int] = None) -> None:
        """Record delivery once and optionally raise the callback's own error."""
        super().__call__(msg, stacklevel)
        if self.fail:
            raise RuntimeError("callback failed")


# ========== Stream fixtures: signatures where the positional message slot decides ==========

# ``_stream_accepts_stacklevel`` binds the real call ``stream(msg, stacklevel=...)``, so a parameter named
# ``stacklevel`` only counts when the message does not already occupy it.


def stream_message_named_stacklevel(stacklevel: str) -> None:
    """Stream whose single parameter is called ``stacklevel`` — the positional message lands in it."""


def stream_var_positional_keyword_only_stacklevel(*args: str, stacklevel: int = 1) -> None:
    """Stream that takes the message through ``*args`` and ``stacklevel`` as a keyword-only parameter."""


class MessageNamedStacklevelRecordingStream:
    """Stream recording its single parameter, which happens to be called ``stacklevel``."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[str] = []

    def __call__(self, stacklevel: str) -> None:
        """Record what arrived in the ``stacklevel`` parameter — the deprecation message."""
        self.calls.append(stacklevel)


# ========== Stream fixtures: streams the decision memo must tell apart ==========


class EqualStreamMixin:
    """Make every instance compare and hash equal, as a value-object style callable class might.

    A memo keyed by ``==`` / ``hash`` would hand the first decision made for one such instance to all the others.
    """

    def __eq__(self, other: object) -> bool:
        """Equal to any other instance of this mixin, whatever its ``__call__`` signature."""
        return isinstance(other, EqualStreamMixin)

    def __hash__(self) -> int:
        """Constant hash, so equal instances also collide in any hash-keyed table."""
        return 0


class EqualMessageOnlyStream(EqualStreamMixin, MessageOnlyRecordingStream):
    """Equal-by-value stream that takes only the message."""


class EqualStacklevelStream(EqualStreamMixin, RecordingStream):
    """Equal-by-value stream that also accepts ``stacklevel``."""


class SlottedStream:
    """Stream that cannot be weakly referenced: ``__slots__`` without ``__weakref__`` leaves no slot for it."""

    __slots__ = ()

    def __call__(self, msg: str, stacklevel: int = 1) -> None:
        """Accept the message and ``stacklevel`` without recording anything."""


# ========== Stream fixtures: ``functools.wraps`` pass-throughs and ``**kwargs`` forwarders ==========

# A logging adapter or metrics decorator is typically a bare ``(*args, **kwargs)`` pass-through; what it forwards to
# decides whether ``stacklevel=`` is acceptable, so ``_stream_accepts_stacklevel`` classifies it by the wrapped stream.


@functools.wraps(stream_message_only)
def stream_passthrough_message_only(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
    """Bare pass-through around a message-only stream, as a ``functools.wraps`` decorator produces."""
    stream_message_only(*args, **kwargs)


@functools.wraps(stream_with_stacklevel)
def stream_passthrough_with_stacklevel(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
    """Bare pass-through around a stream that accepts ``stacklevel``."""
    stream_with_stacklevel(*args, **kwargs)


@functools.wraps(stream_passthrough_message_only)
def stream_passthrough_of_passthrough(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
    """Pass-through stacked on a pass-through, so the message-only stream sits two layers down."""
    stream_passthrough_message_only(*args, **kwargs)


@functools.wraps(stream_wraps_logging_warning)
def stream_passthrough_of_narrow_wraps(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
    """Pass-through around a narrow ``(msg)`` stream that itself copied ``logging.warning``'s metadata."""
    stream_wraps_logging_warning(*args, **kwargs)


@functools.wraps(warnings.warn)
def stream_passthrough_warn(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
    """Bare pass-through around :func:`warnings.warn`, which always accepts ``stacklevel``."""
    warnings.warn(*args, **kwargs)


def make_stream_cycle() -> Callable[..., None]:
    """Build two bare pass-through streams whose ``__wrapped__`` attributes point at each other; return the first.

    A ``__wrapped__`` loop is the one shape ``inspect.unwrap`` refuses with ``ValueError``. The pair is built on demand,
    not at import: doctest collection calls ``inspect.unwrap`` on every module-level function, so a module-level loop
    would break collection of this whole module.
    """

    def stream_cycle_a(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        """Bare pass-through whose ``__wrapped__`` points at ``stream_cycle_b``, which points back here."""

    def stream_cycle_b(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        """Bare pass-through whose ``__wrapped__`` points back at ``stream_cycle_a``."""

    # Assigned after both exist, so each can name the other.
    setattr(stream_cycle_a, "__wrapped__", stream_cycle_b)
    setattr(stream_cycle_b, "__wrapped__", stream_cycle_a)
    return stream_cycle_a


class PassThroughMessageOnlyStream:
    """Bare ``(*args, **kwargs)`` pass-through that records through the message-only stream it wraps.

    Instance counterpart of the ``functools.wraps`` function fixtures: ``__wrapped__`` names the message-only
    recorder, so the stream must be called with the message alone or the recorder rejects the extra keyword.
    """

    def __init__(self) -> None:
        """Wrap a fresh message-only recorder and expose its call log."""
        self.inner = MessageOnlyRecordingStream()
        self.__wrapped__ = self.inner
        self.calls = self.inner.calls

    def __call__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        """Forward everything unchanged to the wrapped recorder."""
        self.inner(*args, **kwargs)


class SlowRecordingStream(RecordingStream):
    """Recording stream that pauses briefly before logging, so concurrent deliveries overlap.

    Stands in for a sink with real latency (a network log handler, say) under multi-threaded use: the pause is a yield
    point that lets other threads reach the warn budget while one delivery is still in progress.
    """

    def __call__(self, msg: str, stacklevel: int = 1) -> None:
        """Pause, then record the call."""
        time.sleep(0.001)
        super().__call__(msg, stacklevel=stacklevel)


@functools.wraps(warnings.warn)
def stream_wraps_warn_forwarding_stacklevel(msg: str, stacklevel: int = 1) -> None:
    """Pure-Python stream copying ``warnings.warn`` metadata; adds its own frame to the forwarded ``stacklevel``."""
    warnings.warn(msg, stacklevel=stacklevel + 1)
