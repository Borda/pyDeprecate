"""Shared helpers and operation cases for the deprecation proxy unit tests."""

import asyncio
import contextlib
import itertools
import warnings
from typing import Any, cast

import pytest

from deprecate import DeprecationProxy
from tests.collections.api_deprecated.core import DeprecatedColorEnum


class _P8Source:
    """Plain source class used as the deprecated ``obj`` of a target-forwarding proxy (fixture)."""


class _P8Target:
    """Plain replacement class used as the ``target`` a proxy actually serves (fixture)."""


class _P9Config:
    """Active class exposing ``new_attr`` so an ``attrs_mapping`` redirect target validates (fixture)."""

    new_attr = 1


def _future_warning_count(caught: list[warnings.WarningMessage]) -> int:
    """Count the deprecation ``FutureWarning`` records captured by ``catch_warnings(record=True)``."""
    return sum(issubclass(record.category, FutureWarning) for record in caught)


def _drive_getitem(proxy: Any) -> object:  # noqa: ANN401
    """Subscript the proxy."""
    return proxy[0]


def _drive_iter(proxy: Any) -> object:  # noqa: ANN401
    """Start iterating the proxy."""
    return iter(proxy)


def _drive_neg(proxy: Any) -> object:  # noqa: ANN401
    """Negate the proxy (a unary forward)."""
    return -proxy


def _drive_round(proxy: Any) -> object:  # noqa: ANN401
    """Round the proxy."""
    return round(proxy)


def _drive_aiter(proxy: Any) -> object:  # noqa: ANN401
    """Start async iteration over the proxy."""
    return proxy.__aiter__()


def _drive_next(proxy: Any) -> object:  # noqa: ANN401
    """Advance the proxy as an iterator."""
    return next(proxy)


def _drive_enter(proxy: Any) -> object:  # noqa: ANN401
    """Enter and leave the proxy as a context manager."""
    with proxy as entered:
        return entered


def _drive_iadd(proxy: Any) -> object:  # noqa: ANN401
    """Apply an in-place addition through the proxy."""
    proxy += 3
    return proxy


async def _enter_async(proxy: Any) -> object:  # noqa: ANN401
    """Enter and leave the proxy as an async context manager."""
    async with proxy as entered:
        return entered


def _drive_aenter(proxy: Any) -> object:  # noqa: ANN401
    """Run ``async with proxy`` on a fresh event loop."""
    return asyncio.run(_enter_async(proxy))


def _drive_anext(proxy: Any) -> object:  # noqa: ANN401
    """Request the next async item from the proxy and await it on a fresh event loop."""
    return asyncio.run(proxy.__anext__())


async def _await_proxy(proxy: Any) -> object:  # noqa: ANN401
    """Await the proxy."""
    return await proxy


def _drive_await(proxy: Any) -> object:  # noqa: ANN401
    """Await the proxy on a fresh event loop."""
    return asyncio.run(_await_proxy(proxy))


#: Operations that only read the wrapped object: fatal gate first, then compute, then warn.
_PURE_PROXY_OPS = [
    pytest.param("__getitem__", _drive_getitem, id="getitem"),
    pytest.param("__iter__", _drive_iter, id="iter"),
    pytest.param("__neg__", _drive_neg, id="unary-neg"),
    pytest.param("__round__", _drive_round, id="round"),
    pytest.param("__aiter__", _drive_aiter, id="aiter"),
]
#: Operations with side effects on the wrapped object: fatal gate first, then run, then warn.
_SIDE_EFFECTING_PROXY_OPS = [
    pytest.param("__enter__", _drive_enter, id="enter"),
    pytest.param("__iadd__", _drive_iadd, id="inplace-add"),
    pytest.param("__aenter__", _drive_aenter, id="aenter"),
    pytest.param("__anext__", _drive_anext, id="anext"),
    pytest.param("__await__", _drive_await, id="await"),
]
#: Operations that consume what they read: fatal gate first, then warn, then run.
_WARN_FIRST_PROXY_OPS = [pytest.param("__next__", _drive_next, id="next")]
#: Every operation that refuses a fatal deprecation before it touches the wrapped object.
_GATED_PROXY_OPS = [*_PURE_PROXY_OPS, *_SIDE_EFFECTING_PROXY_OPS, *_WARN_FIRST_PROXY_OPS]


# Statically `DeprecatedColorEnum` is still the class statement: mypy does not rebind a decorated
# class to the proxy it becomes at runtime, which is the decorator-form limitation documented on
# `DeprecationProxy`. Cast once so the breadcrumb reads below type-check against the real runtime object.
_ast_class_proxy = cast(DeprecationProxy[Any], DeprecatedColorEnum)


def _drive_getattr(proxy: Any) -> object:  # noqa: ANN401
    """Read a plain attribute through the proxy."""
    return proxy.items


#: Proxy operations that each run exactly one forwarded hook, so ``skip_if`` must be consulted exactly once.
_SINGLE_HOOK_PROXY_OPS = [
    *_PURE_PROXY_OPS,
    pytest.param("__next__", _drive_next, id="next"),
    pytest.param("__iadd__", _drive_iadd, id="inplace-add"),
    pytest.param("__anext__", _drive_anext, id="anext"),
    pytest.param("__await__", _drive_await, id="await"),
    pytest.param("items", _drive_getattr, id="getattr"),
]


def _acquire_anext(proxy: Any) -> object:  # noqa: ANN401
    """Obtain the next awaitable from the proxy without awaiting it."""
    return proxy.__anext__()


def _acquire_await(proxy: Any) -> object:  # noqa: ANN401
    """Obtain the proxy's await iterator without driving it."""
    return proxy.__await__()


async def _collect(stream: Any) -> list[Any]:  # noqa: ANN401
    """Drain an async iterable into a list."""
    return [item async for item in stream]


async def _await_once(awaitable: Any) -> Any:  # noqa: ANN401
    """Await *awaitable* once and return its result."""
    return await awaitable


#: Budget-spending proxy operations raced across threads; each factory builds a fresh source that is safe to share.
_THREADED_BUDGET_OPS = [
    pytest.param(_drive_getitem, lambda: [1, 2], id="getitem"),
    pytest.param(_drive_enter, contextlib.nullcontext, id="enter"),
    pytest.param(_drive_next, itertools.count, id="next"),
]


#: Module-qualified path of :class:`tests.collections.api_current.core.SourcePathTarget`, as a proxy template renders
#: it.
_SOURCE_PATH_CLASS = "tests.collections.api_current.core.SourcePathTarget"
