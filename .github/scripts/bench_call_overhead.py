"""Measure the per-call overhead of deprecated callables against a plain function call.

Stdlib only, not a CI gate (timing is noisy). Run it on ``main`` and on a branch with the same
interpreter on the same machine, and compare the two Markdown tables::

    python .github/scripts/bench_call_overhead.py
    python .github/scripts/bench_call_overhead.py --number 20000 --repeat 5

Each scenario is timed with ``timeit.repeat``; the table reports the minimum per-call time in ns and its
ratio to the plain function measured in the same process. Warning budgets are spent before timing, so the
"budget spent" rows measure the steady state a long-running caller sees after the first warning.

"""

from __future__ import annotations

import argparse
import asyncio
import platform
import sys
import timeit
import warnings
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from deprecate import TargetMode, __version__, deprecated


def plain(x: int, y: int = 1) -> int:
    """Undecorated reference callable."""
    return x + y


@deprecated(target=TargetMode.NOTIFY, deprecated_in="0.1", remove_in="9.0", stream=None)
def notify_silent(x: int, y: int = 1) -> int:
    """NOTIFY with ``stream=None``: no warning is ever emitted."""
    return x + y


@deprecated(target=TargetMode.NOTIFY, deprecated_in="0.1", remove_in="9.0")
def notify_spent(x: int, y: int = 1) -> int:
    """NOTIFY with the default stream; the one-warning budget is spent before timing."""
    return x + y


@deprecated(target=plain, deprecated_in="0.1", remove_in="9.0")
def forward_spent(x: int, y: int = 1) -> int:
    """Callable target; the one-warning budget is spent before timing."""
    return x + y


@deprecated(target=TargetMode.ARGS_REMAP, deprecated_in="0.1", remove_in="9.0", args_mapping={"old_y": "y"})
def remap_migrated(x: int, y: int = 1) -> int:
    """ARGS_REMAP called with the new argument name, so no remapping and no warning happen."""
    return x + y


async def plain_async(x: int, y: int = 1) -> int:
    """Undecorated reference coroutine."""
    return x + y


@deprecated(target=TargetMode.NOTIFY, deprecated_in="0.1", remove_in="9.0")
async def notify_async_spent(x: int, y: int = 1) -> int:
    """Async NOTIFY; the one-warning budget is spent before timing."""
    return x + y


def _sync_case(func: Callable[..., int]) -> Callable[[], int]:
    return lambda: func(1, y=2)


def _async_case(func: Callable[..., object], number: int) -> Callable[[], None]:
    async def _loop() -> None:
        for _ in range(number):
            await func(1, y=2)  # type: ignore[misc]

    return lambda: asyncio.run(_loop())


def _best_ns(stmt: Callable[[], object], number: int, repeat: int, calls_per_run: int) -> float:
    runs = timeit.repeat(stmt, number=number, repeat=repeat)
    return min(runs) / (number * calls_per_run) * 1e9


def main() -> None:
    """Spend the warning budgets, time every scenario, print a Markdown table."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--number", type=int, default=100_000, help="calls per timing run")
    parser.add_argument("--repeat", type=int, default=7, help="timing runs per scenario; the minimum is reported")
    args = parser.parse_args()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        notify_spent(1)
        forward_spent(1)
        asyncio.run(notify_async_spent(1))

    sync_cases = {
        "plain function": plain,
        "NOTIFY stream=None": notify_silent,
        "NOTIFY default stream, budget spent": notify_spent,
        "callable forward, budget spent": forward_spent,
        "ARGS_REMAP migrated caller": remap_migrated,
    }
    rows = [(name, _best_ns(_sync_case(func), args.number, args.repeat, 1)) for name, func in sync_cases.items()]
    # one asyncio.run per timing run, so the event-loop start-up is amortised over `number` awaits
    async_cases = {"plain coroutine": plain_async, "async NOTIFY, budget spent": notify_async_spent}
    async_rows = [
        (name, _best_ns(_async_case(func, args.number), 1, args.repeat, args.number))
        for name, func in async_cases.items()
    ]

    interpreter = f"{platform.python_implementation()} {platform.python_version()}"
    print(f"pyDeprecate {__version__} · {interpreter} · {platform.system()} {platform.machine()}")
    print(f"timeit.repeat(number={args.number:_}, repeat={args.repeat}), minimum per call\n")
    print("| Scenario | ns/call | × plain |")
    print("| --- | ---: | ---: |")
    for group in (rows, async_rows):
        base = group[0][1]
        for name, ns in group:
            print(f"| {name} | {ns:,.0f} | {ns / base:.1f} |")


if __name__ == "__main__":
    main()
