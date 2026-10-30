---
id: async
description: Deprecating Python async functions and async generators — all three TargetModes, warning timing, coroutine introspection, and concurrency caveats.
---

# Async

This page covers deprecation of `async def` functions and async generator functions (`async def` + `yield`). All three TargetModes work with both; the key difference is when the warning fires. For sync functions see [Functions](functions.md); for sync generators see [Advanced](advanced.md).

## Async functions

`@deprecated` works on `async def` functions natively. The wrapper produced is itself `async def`, so `inspect.iscoroutinefunction(wrapper)` returns `True` and callers can `await` it as expected.

All three TargetModes work with async functions. The deprecation warning fires when the coroutine is awaited — not when it is created by calling the wrapper — because the warning logic runs inside the `async def` body. This differs from plain sync wrappers, where the warning fires at call time; generator and async generator wrappers, like coroutines, wait for the first `next()` / `async for` step.

**`TargetMode.NOTIFY` — warn and keep the async body:**

```python
import asyncio
from deprecate import deprecated


@deprecated(deprecated_in="0.9", remove_in="1.0")
async def fetch_data(url: str) -> bytes:
    """Deprecated — no replacement yet; remove call sites."""
    return b""


print(asyncio.run(fetch_data("https://example.com")))
```

<details>
  <summary>Output: <code>asyncio.run(fetch_data("https://example.com"))</code></summary>

```
b''
```

</details>

**`TargetMode.ARGS_REMAP` — rename an argument within the same async function:**

```python
import asyncio
from deprecate import TargetMode, deprecated


@deprecated(
    target=TargetMode.ARGS_REMAP,
    args_mapping={"endpoint": "url"},
    deprecated_in="0.9",
    remove_in="1.0",
)
async def fetch_data(endpoint: str = "", url: str = "") -> bytes:
    """Deprecated argument `endpoint` renamed to `url`."""
    return url.encode()


print(asyncio.run(fetch_data(endpoint="https://example.com")))
```

<details>
  <summary>Output: <code>asyncio.run(fetch_data(endpoint="https://example.com"))</code></summary>

```
b'https://example.com'
```

</details>

**`target=<callable>` — forward to a replacement async function:**

```python
import asyncio
from deprecate import deprecated, void


async def download(url: str) -> bytes:
    """New async API."""
    return url.encode()


@deprecated(target=download, deprecated_in="0.9", remove_in="1.0")
async def fetch(url: str) -> bytes:
    """Deprecated — use download() instead."""
    return void(url)


print(asyncio.run(fetch("https://example.com")))
```

<details>
  <summary>Output: <code>asyncio.run(fetch("https://example.com"))</code></summary>

```
b'https://example.com'
```

</details>

!!! warning "Concurrent coroutines and warning counts"

    Warning-budget reservations are protected by a per-wrapper lock. An async wrapper reserves its callable or per-argument budget before its first `await`, so concurrent coroutines sharing the wrapper cannot race while reserving those counters. `num_warns` is a separate budget for each deprecated argument, so warnings for several deprecated arguments do not share one overall limit.

    The budget controls warning attempts, not the number of visible warning records: Python warning filters can suppress records, and a custom warning stream can fail after a callable wrapper has reserved its budget. The callable wrapper does not restore that reservation after a delivery error. Use `num_warns=-1` only when unlimited warning attempts are intended, not as a concurrency workaround.

## Async generators

`@deprecated` works on async generator functions (`async def` + `yield`) too. The wrapper is itself an **async generator function**: calling it only creates the async generator object, and the deprecation warning fires on the first `async for` step; callers iterate the result with `async for` as before. All three TargetModes — `NOTIFY`, `ARGS_REMAP`, and `target=<callable>` — work the same way they do for sync generators.

**`TargetMode.NOTIFY` — warn and keep the async generator body:**

```python
import asyncio
from collections.abc import AsyncIterator

from deprecate import deprecated


@deprecated(deprecated_in="0.9", remove_in="1.0")
async def stream_lines(start: int = 0) -> AsyncIterator[int]:
    """Deprecated — no replacement yet; remove call sites."""
    for i in range(start, start + 3):
        yield i


async def main() -> list[int]:
    return [item async for item in stream_lines(start=1)]


asyncio.run(main())
```

**`TargetMode.ARGS_REMAP` — rename an argument within the same async generator:**

```python
import asyncio
from collections.abc import AsyncIterator

from deprecate import TargetMode, deprecated


@deprecated(
    target=TargetMode.ARGS_REMAP,
    args_mapping={"begin": "start"},
    deprecated_in="0.9",
    remove_in="1.0",
)
async def stream_lines(begin: int = 0, start: int = 0) -> AsyncIterator[int]:
    """Deprecated argument `begin` renamed to `start`."""
    for i in range(start, start + 3):
        yield i


async def main() -> list[int]:
    return [item async for item in stream_lines(begin=1)]


asyncio.run(main())
```

**`target=<callable>` — forward to a replacement async generator:**

```python
import asyncio
from collections.abc import AsyncIterator

from deprecate import deprecated


async def stream(start: int) -> AsyncIterator[int]:
    """New async generator API."""
    for i in range(start, start + 3):
        yield i


@deprecated(target=stream, deprecated_in="0.9", remove_in="1.0")
async def stream_legacy(start: int) -> AsyncIterator[int]:
    """Deprecated — use stream() instead."""
    if False:  # pragma: no cover — body unreachable; target forwards every call
        yield 0


async def main() -> list[int]:
    return [item async for item in stream_legacy(start=1)]


asyncio.run(main())
```

!!! note "The wrapper is an async generator function"

    `inspect.isasyncgenfunction(wrapper)` returns `True` (and `inspect.iscoroutinefunction(wrapper)` returns `False`, as for the undecorated function), so frameworks that branch on these flags — a pytest-asyncio `yield` fixture, for one — run the deprecated function exactly like the original. Calling `wrapper(...)` runs no code: the deprecation warning, the `num_warns` budget and an `as_exceptions()` error all come on the first `async for` step, and an async generator that is never iterated never warns. `asend()`, `athrow()` and `aclose()` reach the inner async generator.

## See also

- [Use Cases overview](use-cases.md) — start here for a guided tour of all deprecation patterns
- [Functions](functions.md) — sync function deprecation (includes sync generators)
- [Advanced](advanced.md) — sync generators, class/static methods, and testing helpers
- [Audit Tools](audit.md) — enforce removal deadlines in CI
- [Troubleshooting](../troubleshooting.md) — common errors and fixes

______________________________________________________________________

Next: [Advanced](advanced.md) — docstring updates, `args_extra`, testing helpers, class/static methods, and generator functions.
