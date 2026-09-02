---
id: async
description: Deprecating Python async functions and async generators — all three TargetModes, warning timing, coroutine introspection, and concurrency caveats.
---

# Async

This page covers deprecation of `async def` functions and async generator functions (`async def` + `yield`). All three TargetModes work with both; the key difference is when the warning fires. For sync functions see [Functions](functions.md); for sync generators see [Advanced](advanced.md).

## Async functions

`@deprecated` works on `async def` functions natively. The wrapper produced is itself `async def`, so `inspect.iscoroutinefunction(wrapper)` returns `True` and callers can `await` it as expected.

All three TargetModes work with async functions. The deprecation warning fires when the coroutine is awaited — not when it is created by calling the wrapper — because the warning logic runs inside the `async def` body. This differs from sync and generator wrappers where the warning fires eagerly at call time.

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

    `_WrapperState` fields (`called`, `warned_calls`, `warned_args`) are plain dataclass fields — there is no asyncio lock protecting them. If multiple coroutines share one deprecated wrapper and run concurrently, they can race on the warning counter: the same wrapper may emit more or fewer warnings than `num_warns` specifies, depending on scheduling.

    This is an accepted limitation for v0.9. If exact warning counts matter (for example in tests), either run deprecated coroutines sequentially or set `num_warns=-1` to bypass the gate entirely.

## Async generators

`@deprecated` works on async generator functions (`async def` + `yield`) too. The wrapper is a **sync** callable that fires the deprecation warning eagerly at call time and returns the underlying async generator object; callers iterate the result with `async for`. All three TargetModes — `NOTIFY`, `ARGS_REMAP`, and `target=<callable>` — work the same way they do for sync generators.

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

!!! note "The wrapper itself is sync, not an async generator"

    Calling `wrapper(...)` returns the async generator object directly — no `await` is required at call time, and the deprecation warning fires once at that point. Because the wrapper is implemented as a regular function (it never enters an `async def` body), `inspect.iscoroutinefunction(wrapper)` and `inspect.isasyncgenfunction(wrapper)` both return `False`. Frameworks that branch on those introspections (rare in practice — `async for` does not consult them) may need a hand-written passthrough async generator placed between `@deprecated` and the framework.

## See also

- [Use Cases overview](use-cases.md) — start here for a guided tour of all deprecation patterns
- [Functions](functions.md) — sync function deprecation (includes sync generators)
- [Advanced](advanced.md) — sync generators, class/static methods, and testing helpers
- [Audit Tools](audit.md) — enforce removal deadlines in CI
- [Troubleshooting](../troubleshooting.md) — common errors and fixes

______________________________________________________________________

Next: [Advanced](advanced.md) — docstring updates, `args_extra`, testing helpers, class/static methods, and generator functions.
