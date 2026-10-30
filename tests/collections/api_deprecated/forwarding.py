"""Deprecated wrappers for argument forwarding, proxy mappings, descriptors and async callables."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Generator, Iterator
from typing import Any, Callable, Optional
from warnings import catch_warnings, simplefilter

import typing_extensions

from deprecate import TargetMode, deprecated, deprecated_class, void
from tests.collections.api_current.core import (
    AutoExpandInitFalseDC,
    AutoExpandOverriddenInitDC,
    AutoExpandReqDC,
    CombinedAttrsArgsSource,
    CombinedAttrsArgsTarget,
    LegacyBoolAttrsSource,
    Palette,
    PaletteEnum,
    PaletteOld,
    Pep702ProxyTarget,
    PositionalOnlyTarget,
    SelfDeprecatedModel,
    SelfOnlyPositionalOnlyTarget,
    SomeTargetClass,
    StackedAttrTarget,
    StackingArgsAttrsBase,
    UncomparableDefault,
    WithInjected,
    _DelOnlyPropTarget,
    async_non_cycle_double,
    async_positional_only_target,
    async_suspending_double,
    async_target,
    async_var_positional_target,
    both_old_new_target,
    del_only_prop_fdel,
    fib_recursive,
    fn_kept_name_target,
    fn_remap_with_extra_body,
    fn_shared_default_target,
    fn_with_default,
    gapped_positional_only_target,
    identity_value,
    non_cycle_double,
    pep702_target,
    positional_only_target,
    positional_only_two_params_target,
    return_default_value,
    single_positional_target,
    tracked_identity,
    trio_positional_target,
    uncomparable_default,
    var_positional_remap_body,
    var_positional_target,
)
from tests.collections.api_current.forwarding import (
    DataBatch,
    ask_once,
    async_fetch_page,
    async_gen_target,
    async_parse_rows,
    async_running_total,
    countdown_stream,
    flush_pending_jobs,
    gen_target,
    generator_based_double,
    load_batches,
    open_cursor,
    parse_rows,
    running_total,
    squares_list,
    sync_stopping_stream,
    walk_down,
)
from tests.collections.api_deprecated.core import (
    _DEPRS_CASE_FATAL_ARGS,
    _DEPRS_CASE_OUTER_ARGS,
    _DEPRS_CASE_STD_ARGS,
    _DEPRS_CASE_STD_INF_ARGS,
    _DEPRS_CASE_TGT_MODE_ARGS,
    _DEPRS_CASE_TGT_MODE_INF_ARGS,
    _class_deprecation_notify_only_callable_target,
    async_source_notify,
)

# ========== Proxy args_mapping behaviour fixtures ==========


# Proxy: auto ARGS_REMAP via args_mapping only (no explicit target)
ProxyArgsRemapAuto = deprecated_class(**_DEPRS_CASE_TGT_MODE_ARGS, args_mapping={"old_key": "new_key"})(SomeTargetClass)

# Proxy: callable target + args_mapping
ProxyCallableWithArgsMapping = deprecated_class(
    **_DEPRS_CASE_TGT_MODE_ARGS, target=SomeTargetClass, args_mapping={"old_key": "new_key"}
)(SomeTargetClass)


# Proxy: args_mapping value "" keeps the old argument name (here it is also the target's own name)
ProxyArgsMappedToEmpty = deprecated_class(**_DEPRS_CASE_TGT_MODE_INF_ARGS, args_mapping={"new_key": ""})(
    SomeTargetClass
)


# ========== Fix-set fixtures: misconfig and feature parity additions ==========


def make_class_target_notify_with_args() -> type:
    """Build a class with an explicit @deprecated(target=TargetMode.NOTIFY) + args_mapping + args_extra.

    Explicit ``NOTIFY`` combined with a mapping is contradictory — ``NOTIFY`` means "warn on every access"
    while the mapping selects per-argument deprecation, and pyDeprecate cannot judge which side the caller
    misconfigured. The explicit choice is therefore never overridden: the proxy stays in ``NOTIFY`` mode,
    the mapping and extras are inert at call time, and decoration emits the "ignores `args_mapping`" /
    "ignores `args_extra`" ``UserWarning`` pair with ``misconfigured=True`` in audit metadata.

    """

    @deprecated(
        target=TargetMode.NOTIFY,
        **_DEPRS_CASE_TGT_MODE_INF_ARGS,
        args_mapping={"old_key": "new_key"},
        args_extra={"injected": "x"},
    )
    class NotifyExplicitContradictionClass:
        """Source class — deprecated_class delegates to this body unchanged."""

        def __init__(self, new_key: int = 0, injected: str = "") -> None:
            self.new_key = new_key
            self.injected = injected

    return NotifyExplicitContradictionClass


def make_class_omitted_target_with_args() -> type:
    """Build a class with @deprecated + args_mapping + args_extra and NO explicit ``target=``.

    The front door's ``TargetMode.AUTO`` default means "infer the mode": the mapping present auto-resolves
    to ``TargetMode.ARGS_REMAP``, so the old constructor keyword warns and renames, and ``args_extra`` is
    merged into the forwarded call after remap — the handy zero-ceremony form of per-argument deprecation.
    ``injected`` is a real constructor parameter so tests can exercise the full round trip.

    """

    @deprecated(
        **_DEPRS_CASE_TGT_MODE_INF_ARGS,
        args_mapping={"old_key": "new_key"},
        args_extra={"injected": "x"},
    )
    class OmittedTargetAutoResolvedClass:
        """Source class — deprecated_class delegates to this body unchanged."""

        def __init__(self, new_key: int = 0, injected: str = "") -> None:
            self.new_key = new_key
            self.injected = injected

    return OmittedTargetAutoResolvedClass


def make_class_target_args_remap() -> type:
    """Build a class with @deprecated(target=TargetMode.ARGS_REMAP, args_mapping=...) (Fix 1 fixture).

    Verifies that callable TargetMode is forwarded correctly through the class branch of @deprecated rather than being
    collapsed to None.

    """

    @deprecated(target=TargetMode.ARGS_REMAP, **_DEPRS_CASE_TGT_MODE_INF_ARGS, args_mapping={"old_key": "new_key"})
    class ArgsRemapClass:
        """Source class with old/new keyword for ARGS_REMAP forwarding."""

        def __init__(self, new_key: int = 0) -> None:
            self.new_key = new_key

    return ArgsRemapClass


# Proxy: deprecated_class(target=NewCls, args_extra={...}) (Fix 3 fixture)
ProxyClassWithArgsExtra = deprecated_class(
    **_DEPRS_CASE_TGT_MODE_INF_ARGS, target=WithInjected, args_extra={"injected": "from-extra"}
)(SomeTargetClass)


# Proxy: deprecated_class(target=ARGS_REMAP, args_mapping=...) for per-arg warning template (Fix 4 fixture)
ProxyArgsRemapForArgWarnMessage = deprecated_class(
    **_DEPRS_CASE_TGT_MODE_INF_ARGS, target=TargetMode.ARGS_REMAP, args_mapping={"old_key": "new_key"}
)(SomeTargetClass)


# Function with both old and new params + args_mapping (Fix 5 fixture)
@deprecated(target=both_old_new_target, **_DEPRS_CASE_TGT_MODE_ARGS, args_mapping={"old": "new"})
def depr_collision_old_new(old: int = 0, new: int = 0) -> int:
    """Source has both old and new params; collision triggered when caller passes old=X.

    Without the fix, the source default ``new=0`` would be merged before the rename of ``old → new``, overwriting the
    renamed value.

    """
    return void(old, new)


@deprecated(
    target=both_old_new_target, **_DEPRS_CASE_TGT_MODE_ARGS, args_mapping={"old": "new"}, args_extra={"new": 99}
)
def depr_collision_old_new_extra_wins(old: int = 0, new: int = 0) -> int:
    """Source with args_extra={"new": 99}; args_extra always wins over explicit new-name kwarg.

    Caller-supplied ``new`` is overwritten by ``args_extra`` merge that runs after the
    ``explicit_new`` restore, so the target always receives ``new=99`` regardless of what
    the caller passed.
    """
    return void(old, new)


@deprecated(target=fn_kept_name_target, **_DEPRS_CASE_TGT_MODE_INF_ARGS, args_mapping={"level": ""})
def depr_args_mapping_empty_keeps_name(level: int = 0) -> int:
    """Source whose ``args_mapping`` value ``""`` keeps the argument name ``level`` when forwarding.

    A caller still passing ``level=...`` is warned, and the value must reach the target under the same name.

    """
    return void(level)


# ========== Regression fixtures: Fix 1 + Fix 2 ==========


@deprecated(target=fn_with_default, args_mapping={"old_arg": "new_arg"}, **_DEPRS_CASE_STD_ARGS)
def fn_old_default(old_arg: int = 1, new_arg: int = 99) -> int:
    """Source whose stale default for the deprecated arg shadows the target's default.

    Regression for Fix 1: when the caller supplies neither name, the source's
    ``old_arg=1`` default would otherwise be renamed to ``new_arg=1`` and forwarded,
    silently overriding the target's own ``new_arg=99`` default. The fix drops the
    stale source default in that case so the target's default is used.

    """
    return void(old_arg, new_arg)


@deprecated(target=fn_shared_default_target, **_DEPRS_CASE_STD_ARGS)
def fn_shared_default(x: int, level: int = 1) -> int:
    """Callable-target source sharing the non-renamed parameter ``level`` (source default 1, target default 99).

    Pins the intended forwarding contract for a non-renamed shared parameter with no ``args_mapping``: the
    source signature is the contract the caller migrated from, so the source's ``level=1`` default is forwarded
    and wins over the target's ``level=99`` when the caller supplies neither.  This is deliberately different
    from the *renamed* path (see ``fn_old_default``), where a stale old-arg default is dropped because that
    argument is being removed.  (The proposal to drop the non-renamed default too was assessed as a
    misdiagnosis — the behaviour is intended and enforced by ``test_functions.py::test_default``.)

    """
    return void(x, level)


@deprecated(target=return_default_value, **_DEPRS_CASE_STD_ARGS)
def depr_uncomparable_default(value: UncomparableDefault = uncomparable_default) -> UncomparableDefault:
    """Forward a default whose equality operators raise instead of returning a boolean."""
    return void(value)


@deprecated(
    target=TargetMode.ARGS_REMAP,
    args_mapping={"old_arg": "new_arg"},
    args_extra={"injected": 100},
    **_DEPRS_CASE_STD_ARGS,
)
def fn_remap_with_extra(old_arg: int = 0, new_arg: int = 0, injected: int = 0) -> int:
    """ARGS_REMAP source body using both a remapped arg and an injected extra arg.

    Regression for Fix 2: when the caller uses the new argument name, the early
    return short-circuit would skip the ``args_extra`` merge before the original
    fix. The body delegates to the target so the test asserts that ``injected=100``
    reaches the body regardless of whether the caller passes the old or new name.

    """
    return fn_remap_with_extra_body(new_arg=new_arg, injected=injected)


# ========== Default-target fixtures (target omitted → TargetMode.NOTIFY) ==========


def make_default_target_with_versions() -> Callable[[int], int]:
    """Build a @deprecated(deprecated_in='1.0', remove_in='2.0') wrapper with no explicit target.

    No FutureWarning should be emitted at decoration time (unlike target=None). Source body must execute on call and a
    FutureWarning must be emitted.

    """

    @deprecated(**_DEPRS_CASE_STD_ARGS)
    def fn(x: int) -> int:
        return tracked_identity(x)

    return fn


def make_default_target_no_versions_warns() -> Callable[[int], int]:
    """Build a @deprecated() wrapper with no versions and no target.

    An empty-version FutureWarning must be emitted at decoration time because the resulting deprecation notice would
    contain empty version strings.

    """

    @deprecated()
    def fn(x: int) -> int:
        return identity_value(x)

    return fn


def make_partial_version_no_guard_warn() -> Callable[[int], int]:
    """Build @deprecated(deprecated_in='1.0') with only deprecated_in set.

    Guard must NOT fire — requires both version parameters to be empty.

    """

    @deprecated(deprecated_in="1.0")
    def fn(x: int) -> int:
        return identity_value(x)

    return fn


def make_callable_target_no_versions_warns() -> Callable[[int], int]:
    """Build @deprecated(target=identity_value) with no version strings.

    Guard MUST fire — F1b removed the NOTIFY-only restriction; all target shapes warn.

    """

    @deprecated(target=identity_value)
    def fn(x: int) -> int:
        return identity_value(x)

    return fn


def make_explicit_notify_no_versions_warns() -> Callable[[int], int]:
    """Build @deprecated(target=TargetMode.NOTIFY) with no version strings.

    Guard MUST fire — explicit NOTIFY + both versions empty triggers the warning.

    """

    @deprecated(target=TargetMode.NOTIFY)
    def fn(x: int) -> int:
        return identity_value(x)

    return fn


# ========== PEP 702 stacking regression fixture (B1a) ==========
# ``typing_extensions.deprecated`` stacked OUTSIDE pyDeprecate's ``@deprecated``
# overwrites the inner wrapper's ``__deprecated__`` attribute with a plain string.
# Pre-fix, ``wrapped_fn`` re-read ``__deprecated__`` from itself at call time and
# crashed with ``AttributeError: 'str' object has no attribute 'misconfigured'``.
# Capturing ``DeprecationConfig`` in a closure variable inside ``packing()`` keeps
# the metadata reachable even after PEP 702 overwrites the attribute.
#
# Both intermediate bindings are underscore-prefixed so ``find_deprecation_wrappers``
# skips them: the inner pyDeprecate wrapper is exposed through PEP 702, which sets a
# plain string ``__deprecated__`` and would otherwise crash ``validate_deprecation_wrapper``.
# The public ``pep702_stacked`` re-export is a thunk function with no ``__deprecated__``
# attribute, so the audit walker steps over it without probing.
pep702_inner = deprecated(target=pep702_target, deprecated_in="0.8", remove_in="1.0")(lambda x: x)
_pep702_stacked = typing_extensions.deprecated("use `pep702_target`")(pep702_inner)


def pep702_stacked(x: int) -> int:
    """Tiny re-export thunk for the PEP 702 + pyDeprecate stacked wrapper.

    Defined as a function (not a direct module-level binding to ``_pep702_stacked``) so ``find_deprecation_wrappers``
    does not treat the PEP 702 wrapper — whose ``__deprecated__`` is a plain string — as a pyDeprecate audit target.
    Forwards the call (and all warnings) to the stacked wrapper.

    """
    return _pep702_stacked(x)


# ========== PEP 702 stacking regression fixture (B1b — deprecated_class proxy) ==========
# ``typing_extensions.deprecated`` stacked OUTSIDE pyDeprecate's ``deprecated_class``
# proxy wrapper.  The inner ``deprecated_class(...)`` returns a ``_DeprecatedProxy``
# instance whose configuration lives on ``__deprecation_config__``.
#
# PEP 702's outer wrapper assigns ``arg.__deprecated__ = msg`` on the proxy.  The
# proxy's ``__setattr__`` stores that message on the proxy itself — never forwarding it
# to ``Pep702ProxyTarget`` — so the configuration survives and the wrapped class is
# not marked deprecated.
#
# This fixture is the B1b regression guard against re-introducing a clobber or leak path.
# Both intermediate bindings are underscore-prefixed so audit walkers do not probe
# the PEP 702-wrapped plain function as a pyDeprecate target.
pep702_proxy_inner = deprecated_class(deprecated_in="0.8", remove_in="1.0")(Pep702ProxyTarget)
_pep702_proxy_stacked = typing_extensions.deprecated("use `Pep702ProxyTarget`")(pep702_proxy_inner)


def pep702_proxy_stacked() -> Pep702ProxyTarget:
    """Tiny re-export thunk for the PEP 702 + ``deprecated_class`` stacked proxy (B1b).

    Returns a fresh instance produced by invoking the stacked wrapper.  Defined as a function (not a module-level
    binding) so audit walkers skip the PEP 702 wrapper whose ``__deprecated__`` is a plain string.

    """
    return _pep702_proxy_stacked()


# ========== generator callable kind fixtures ==========
# Three wrappers exercise the ``@deprecated`` generator path (``inspect.isgeneratorfunction``
# branch in ``packing()``).  Each pairs a generator source/target combination with a different
# ``TargetMode`` so the integration tests can confirm warning timing (on the first ``next()``, not
# at call time) and full round-trip yield equivalence.


def gen_source_remap(old_x: int = 0, x: int = 0) -> Iterator[int]:
    """Self-deprecation source for ``gen_args_remap``: accepts the legacy arg ``old_x`` (mapped to ``x``).

    Source body executes under :attr:`~deprecate.TargetMode.ARGS_REMAP` so the generator must be defined here, not
    inlined into a lambda (lambdas cannot be generators).  ``old_x`` is the legacy parameter name preserved for the
    ARGS_REMAP mapping; only ``x`` is read in the body.

    """
    for i in range(1, 4):
        yield x * i


def gen_source_callable(x: int = 0) -> Iterator[int]:
    """Placeholder generator source for ``gen_callable``: body never executes under callable-target mode.

    Declared as a generator function (``yield`` in body) so :func:`inspect.isgeneratorfunction` returns ``True`` and the
    generator wrapper engages.  The body is unreachable because ``target=gen_target`` forwards every call.

    """
    if False:  # pragma: no cover - unreachable; declares this as a generator
        yield x


#: NOTIFY mode — source body runs unchanged (warning-only).  ``gen_target`` is itself the source.
#: Uses default ``num_warns=1``; the warning fires once, via ``_build_call_plan`` on the first ``next()``.
#: The conftest reset clears ``warned_calls`` before every test, so each parametrize case sees a fresh budget.
gen_notify = deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)(gen_target)
#: ARGS_REMAP mode — self-deprecation; legacy arg ``old_x`` mapped to ``x`` before source body executes.
gen_args_remap = deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_x": "x"}, **_DEPRS_CASE_STD_ARGS)(
    gen_source_remap
)
#: Callable-target mode — call forwarded to ``gen_target``; source body never executes.
gen_callable = deprecated(target=gen_target, **_DEPRS_CASE_STD_ARGS)(gen_source_callable)
#: NOTIFY mode with ``num_warns=-1`` (warn on every call); used to test unlimited-warning behaviour.
gen_notify_unlimited = deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_INF_ARGS)(gen_target)


# ========== async callable kind fixtures ==========
# Three wrappers exercise the ``@deprecated`` async path (``inspect.iscoroutinefunction`` branch in ``packing()``).
# Each pairs an ``async def`` source with one of the three ``TargetMode`` variants so the integration tests can
# confirm: (a) round-trip awaited result, (b) warning timing for async wrappers — fired when the returned coroutine
# is awaited, before the wrapped async body/result completes — and (c) stacklevel pointing back to the user's call
# site rather than to ``deprecation.py``.


async def async_source_remap(old_x: int = 0, x: int = 0) -> int:
    """Self-deprecation async source for ``async_args_remap``: accepts legacy arg ``old_x`` (mapped to ``x``).

    Body executes under :attr:`~deprecate.TargetMode.ARGS_REMAP` so it must be a real ``async def``; only the
    remapped ``x`` is read in the body.

    """
    return x * 2


async def async_source_callable(x: int = 0) -> int:
    """Placeholder async source for ``async_callable``: body never executes under callable-target mode.

    Declared as ``async def`` so :func:`inspect.iscoroutinefunction` returns ``True`` and the async wrapper engages.
    The body is unreachable because ``target=async_target`` forwards every call.

    """
    return x  # pragma: no cover - unreachable; target forwards every call


#: NOTIFY mode — source body runs unchanged (warning-only). Uses default ``num_warns=1``;
#: warning fires once per call. Tests reset ``warned_calls`` between parametrize cases.
async_notify = deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)(async_source_notify)
#: ARGS_REMAP mode — self-deprecation; legacy arg ``old_x`` mapped to ``x`` before source body executes.
async_args_remap = deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_x": "x"}, **_DEPRS_CASE_STD_ARGS)(
    async_source_remap
)
#: Callable-target mode — call forwarded to ``async_target``; source body never executes.
async_callable = deprecated(target=async_target, **_DEPRS_CASE_STD_ARGS)(async_source_callable)


# ========== async generator callable kind fixtures ==========
# Three wrappers exercise the ``@deprecated`` async generator path (``inspect.isasyncgenfunction``
# selects the async generator wrapper in ``packing()``).  Each pairs an async generator source with one of
# the three ``TargetMode`` variants so the integration tests can confirm: (a) round-trip async iteration
# yields ``[1, 2, 3]``, (b) warning fires on the first ``async for`` step, not at call time, and
# (c) stacklevel points to the user's call site.


async def async_gen_source_notify(x: int) -> AsyncIterator[int]:
    """NOTIFY-mode async generator source: body runs unchanged after warning fires.

    Yields the same multiples as :func:`async_gen_target` so the integration test can assert end-to-end
    equivalence without referencing the target directly.

    """
    for i in range(1, 4):
        yield x * i


async def async_gen_source_remap(old_x: int = 0, x: int = 0) -> AsyncIterator[int]:
    """Self-deprecation async generator source for ``async_gen_args_remap``.

    Accepts the legacy arg ``old_x`` (mapped to ``x`` before the body runs).  Body executes under
    :attr:`~deprecate.TargetMode.ARGS_REMAP` so it must be a real ``async def`` + ``yield`` — only the
    remapped ``x`` is read inside the loop.

    """
    for i in range(1, 4):
        yield x * i


async def async_gen_source_callable(x: int = 0) -> AsyncIterator[int]:
    """Placeholder async generator source for ``async_gen_callable``: body never executes.

    Declared as ``async def`` containing ``yield`` so :func:`inspect.isasyncgenfunction` returns ``True``.
    The body is unreachable because ``target=async_gen_target`` forwards every call.

    """
    if False:  # pragma: no cover - unreachable; target forwards every call
        yield x


#: NOTIFY mode — source body runs unchanged (warning-only).  Uses default ``num_warns=1``; warning fires once
#: per call.  The conftest reset clears ``warned_calls`` before every test, so each parametrize case starts fresh.
async_gen_notify = deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)(async_gen_source_notify)
#: ARGS_REMAP mode — self-deprecation; legacy arg ``old_x`` mapped to ``x`` before source body executes.
async_gen_args_remap = deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_x": "x"}, **_DEPRS_CASE_STD_ARGS)(
    async_gen_source_remap
)
#: Callable-target mode — call forwarded to ``async_gen_target``; source body never executes.
async_gen_callable = deprecated(target=async_gen_target, **_DEPRS_CASE_STD_ARGS)(async_gen_source_callable)


# ========== generator protocol fixtures ==========
# Wrappers for the generator-wrapper contract: the wrapper of a generator (async generator) function is itself one, so
# the warning fires on the first ``next()`` (``async for`` step), and the protocol forwards to the inner generator.
# The protocol wrappers forward to callable targets, so they also run under the target-chain cycle guard.


def running_total_source(start: int, log: list[str]) -> Generator[int, Optional[int], None]:
    """Placeholder generator source for ``deprecated_running_total``: body never executes under callable-target mode."""
    if False:  # pragma: no cover - unreachable; declares this as a generator
        yield start


async def async_running_total_source(start: int, log: list[str]) -> AsyncGenerator[int, Optional[int]]:
    """Placeholder async generator source for ``deprecated_async_running_total``; the target forwards every call."""
    if False:  # pragma: no cover - unreachable; declares this as an async generator
        yield start


async def cursor_source(log: list[Any]) -> AsyncGenerator[int, Optional[int]]:
    """Placeholder async generator source for ``deprecated_cursor``; its target returns a non-native async generator."""
    if False:  # pragma: no cover - unreachable; declares this as an async generator
        yield len(log)


def walk_source(depth: int, recurse: Callable[[int, Any], Iterator[int]]) -> Iterator[int]:
    """Placeholder generator source for ``deprecated_walk``, whose target recurses through the ``recurse`` callable."""
    if False:  # pragma: no cover - unreachable; declares this as a generator
        yield depth


def squares_source(n: int) -> Generator[int, None, None]:
    """Placeholder generator source for ``deprecated_squares``, whose target returns a list instead of a generator."""
    if False:  # pragma: no cover - unreachable; declares this as a generator
        yield n


def flush_jobs_source(queue: str) -> Iterator[str]:
    """Placeholder generator source for ``deprecated_flush_jobs``, whose target returns before its first ``yield``."""
    if False:  # pragma: no cover - unreachable; declares this as a generator
        yield queue


def parse_rows_source(lines: list[str]) -> Iterator[int]:
    """Placeholder generator source for ``deprecated_parse_rows``, whose target can raise before its first ``yield``."""
    if False:  # pragma: no cover - unreachable; declares this as a generator
        yield len(lines)


async def async_parse_rows_source(lines: list[str]) -> AsyncIterator[int]:
    """Placeholder async generator source for ``deprecated_async_parse_rows``; the target forwards every call."""
    if False:  # pragma: no cover - unreachable; declares this as an async generator
        yield len(lines)


async def fetch_page_source(page: int) -> AsyncIterator[int]:
    """Placeholder async generator source for ``deprecated_fetch_page``, whose target suspends before its first item."""
    if False:  # pragma: no cover - unreachable; declares this as an async generator
        yield page


def ask_once_source(question: str) -> Generator[str, Optional[str], None]:
    """Placeholder generator source for ``deprecated_ask_once``, whose target returns on the next resumption."""
    if False:  # pragma: no cover - unreachable; declares this as a generator
        yield question


async def countdown_source(start: int) -> AsyncGenerator[int, None]:
    """Placeholder async generator source for ``deprecated_countdown``, whose target returns a plain async iterator."""
    if False:  # pragma: no cover - unreachable; declares this as an async generator
        yield start


async def sync_stopping_source(count: int) -> AsyncGenerator[int, None]:
    """Placeholder async generator source for ``deprecated_sync_stopping``; its target ends iteration synchronously."""
    if False:  # pragma: no cover - unreachable; declares this as an async generator
        yield count


def batches_source(count: int) -> Iterator[DataBatch]:
    """Placeholder generator source for ``deprecated_load_batches``; the target forwards every call."""
    if False:  # pragma: no cover - unreachable; declares this as a generator
        yield DataBatch(count)


async def async_squares_source(n: int) -> AsyncGenerator[int, None]:
    """Placeholder async generator source for ``deprecated_async_squares``, whose target returns a plain list."""
    if False:  # pragma: no cover - unreachable; declares this as an async generator
        yield n


async def async_gen_coroutine_source(x: int) -> AsyncGenerator[int, None]:
    """Placeholder async generator source for ``deprecated_async_gen_to_coroutine``; its target is a coroutine."""
    if False:  # pragma: no cover - unreachable; declares this as an async generator
        yield x


def gen_source_stacked(x: int = 0) -> Iterator[int]:
    """Placeholder generator source for the inner layer of ``gen_stacked``; the target forwards every call."""
    if False:  # pragma: no cover - unreachable; declares this as a generator
        yield x


async def async_gen_source_stacked(x: int = 0) -> AsyncIterator[int]:
    """Placeholder async generator source for the inner layer of ``async_gen_stacked``; the target forwards."""
    if False:  # pragma: no cover - unreachable; declares this as an async generator
        yield x


#: Callable target ``running_total``: ``send``/``throw``/``close`` and the return value reach the target generator.
deprecated_running_total = deprecated(target=running_total, **_DEPRS_CASE_STD_INF_ARGS)(running_total_source)
#: Callable target ``async_running_total``: ``asend``/``athrow``/``aclose`` reach the target async generator.
deprecated_async_running_total = deprecated(target=async_running_total, **_DEPRS_CASE_STD_INF_ARGS)(
    async_running_total_source
)
#: Async generator source forwarded to ``open_cursor``: ``asend``/``athrow``/``aclose`` reach a non-native cursor.
deprecated_cursor = deprecated(target=open_cursor, **_DEPRS_CASE_STD_INF_ARGS)(cursor_source)
#: Generator source forwarded to ``walk_down``; passing ``deprecated_walk`` as ``recurse`` recurses through it.
deprecated_walk = deprecated(target=walk_down, **_DEPRS_CASE_STD_INF_ARGS)(walk_source)
#: Generator source forwarded to ``squares_list``: the wrapper yields the items of the returned list.
deprecated_squares = deprecated(target=squares_list, **_DEPRS_CASE_STD_INF_ARGS)(squares_source)
#: Generator source forwarded to ``flush_pending_jobs``, which returns a value before its first ``yield``.
deprecated_flush_jobs = deprecated(target=flush_pending_jobs, **_DEPRS_CASE_STD_INF_ARGS)(flush_jobs_source)
#: Generator source forwarded to ``parse_rows``, whose first step raises ``ValueError`` on a malformed line.
deprecated_parse_rows = deprecated(target=parse_rows, **_DEPRS_CASE_STD_INF_ARGS)(parse_rows_source)
#: Async twin of ``deprecated_parse_rows``, forwarded to ``async_parse_rows``.
deprecated_async_parse_rows = deprecated(target=async_parse_rows, **_DEPRS_CASE_STD_INF_ARGS)(async_parse_rows_source)
#: Async generator source forwarded to ``async_fetch_page``, whose first step suspends under the cycle guard.
deprecated_fetch_page = deprecated(target=async_fetch_page, **_DEPRS_CASE_STD_INF_ARGS)(fetch_page_source)
#: Generator source forwarded to ``ask_once``, which returns on a sent value or a thrown ``ValueError``.
deprecated_ask_once = deprecated(target=ask_once, **_DEPRS_CASE_STD_INF_ARGS)(ask_once_source)
#: Async generator source forwarded to ``countdown_stream``: the wrapper iterates the plain async iterator it returns.
deprecated_countdown = deprecated(target=countdown_stream, **_DEPRS_CASE_STD_INF_ARGS)(countdown_source)
#: Async generator source forwarded to ``sync_stopping_stream``, whose plain ``def`` steps end the iteration directly.
deprecated_sync_stopping = deprecated(target=sync_stopping_stream, **_DEPRS_CASE_STD_INF_ARGS)(sync_stopping_source)
#: Generator source forwarded to ``load_batches``: each batch must be freed once the consumer has moved past it.
deprecated_load_batches = deprecated(target=load_batches, **_DEPRS_CASE_STD_INF_ARGS)(batches_source)
#: Misconfigured: an async generator source forwarded to ``squares_list``, which returns a list, not an async iterable.
deprecated_async_squares = deprecated(target=squares_list, **_DEPRS_CASE_STD_INF_ARGS)(async_squares_source)
#: Misconfigured: an async generator source forwarded to the coroutine function ``async_target``.
deprecated_async_gen_to_coroutine = deprecated(target=async_target, **_DEPRS_CASE_STD_INF_ARGS)(
    async_gen_coroutine_source
)
#: NOTIFY over a ``@types.coroutine`` generator: it keeps the plain wrapper, so the call warns and stays awaitable.
deprecated_generator_based_double = deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_INF_ARGS)(
    generator_based_double
)
#: ``skip_if=True``: the wrapper stays a generator but never warns and runs ``gen_target`` unchanged.
gen_notify_skipped = deprecated(target=TargetMode.NOTIFY, skip_if=True, **_DEPRS_CASE_STD_INF_ARGS)(gen_target)
#: NOTIFY outer over a callable-target inner wrapper (a supported stack): both layers warn on the first step.
gen_stacked = deprecated(target=TargetMode.NOTIFY, num_warns=-1, **_DEPRS_CASE_OUTER_ARGS)(
    deprecated(target=gen_target, **_DEPRS_CASE_STD_INF_ARGS)(gen_source_stacked)
)
#: Async twin of ``gen_stacked``.
async_gen_stacked = deprecated(target=TargetMode.NOTIFY, num_warns=-1, **_DEPRS_CASE_OUTER_ARGS)(
    deprecated(target=async_gen_target, **_DEPRS_CASE_STD_INF_ARGS)(async_gen_source_stacked)
)
#: ``as_exception=True`` over ``running_total``: the first step raises ``DeprecatedError`` before the target starts.
deprecated_running_total_fatal = deprecated(target=running_total, **_DEPRS_CASE_FATAL_ARGS)(running_total_source)


def make_gen_skip_if_flag() -> tuple[Callable[..., Iterator[int]], dict[str, bool]]:
    """Wrap a generator with a flag-driven ``skip_if`` callable so tests can flip it after creating the generator.

    Returns the wrapper (forwarding to ``gen_target``) together with the mutable flag dict (``{"skip": True}``) its
    ``skip_if`` lambda reads. When skipped, the placeholder source runs and yields nothing, so the items tell which
    path the first step took.

    """
    flag = {"skip": True}
    wrapper = deprecated(target=gen_target, skip_if=lambda: flag["skip"], **_DEPRS_CASE_STD_INF_ARGS)(
        gen_source_callable
    )
    return wrapper, flag


class MultiplesReader:
    """Reader whose generator and async generator methods were renamed; the old names forward to the new ones.

    ``@deprecated`` accepts a method target only on the source's own class (cross-class method forwarding is rejected
    at decoration, because ``self`` would carry the wrong type), so the replacement methods live on this class and
    delegate to the shared ``gen_target`` / ``async_gen_target`` generators.

    """

    def __init__(self, base: int) -> None:
        """Store the ``base`` whose multiples the generator methods yield."""
        self.base = base

    def iter_multiples(self) -> Iterator[int]:
        """Replacement generator method: yield the multiples of ``base`` produced by ``gen_target``."""
        yield from gen_target(self.base)

    async def aiter_multiples(self) -> AsyncIterator[int]:
        """Replacement async generator method: yield the multiples of ``base`` produced by ``async_gen_target``."""
        async for item in async_gen_target(self.base):
            yield item

    @deprecated(target=iter_multiples, **_DEPRS_CASE_STD_INF_ARGS)
    def read_multiples(self) -> Iterator[int]:
        """Deprecated generator method forwarding to :meth:`iter_multiples`; the body never executes."""
        if False:  # pragma: no cover - unreachable; declares this as a generator
            yield self.base

    @deprecated(target=aiter_multiples, **_DEPRS_CASE_STD_INF_ARGS)
    async def aread_multiples(self) -> AsyncIterator[int]:
        """Deprecated async generator method forwarding to :meth:`aiter_multiples`; the body never executes."""
        if False:  # pragma: no cover - unreachable; declares this as an async generator
            yield self.base


# ========== deprecated pytest yield-fixture bodies ==========
# A test suite that deprecates a shared ``yield`` fixture keeps registering it with ``pytest.fixture`` (or
# ``pytest_asyncio.fixture``). pytest picks the fixture runner from ``inspect.isgeneratorfunction`` /
# ``inspect.isasyncgenfunction``, so the wrapper must keep those flags for setup, value and teardown to work.
# The integration test registers these bodies as fixtures in a ``pytester`` project.


@deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_INF_ARGS)
def legacy_db_session() -> Iterator[str]:
    """Deprecated yield-fixture body: yields a session handle, then prints a marker line when torn down."""
    yield "session"
    print("legacy db session closed")


@deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_INF_ARGS)
async def legacy_async_db_session() -> AsyncIterator[str]:
    """Async twin of ``legacy_db_session``, registered with ``pytest_asyncio.fixture``."""
    yield "async session"
    print("legacy async db session closed")


# ========== fdel-only deprecated property regression fixture (H3) ==========
# ``deprecated(...)(property(None, None, fdel))`` — the only accessor is the delete accessor.
# Wrapping must still emit ``FutureWarning`` on ``del obj.prop`` and the body must execute.


class DelOnlyDeprecatedPropCls(_DelOnlyPropTarget):
    """Deprecated fdel-only property (H3 fixture).

    Constructed via explicit ``property(None, None, fdel)`` so the only deprecation-wrapped
    accessor is the delete accessor.  Used by both the deprecation-warning test and the audit
    discovery test to guarantee :func:`deprecate.find_deprecation_wrappers` traverses
    fdel-only properties.

    """

    delete_only: property = deprecated(**_DEPRS_CASE_STD_ARGS)(  # type: ignore[assignment]
        property(None, None, del_only_prop_fdel)  # type: ignore[arg-type]
    )


# ========== attrs_mapping fixtures (selective per-attribute deprecation) ==========
# Wrap ``Palette`` and ``PaletteEnum`` to expose deprecated attribute aliases
# (``color``→``colour``, ``txt``→``text``, etc.).  ``stream=None`` is used on most fixtures
# so reads/writes/deletes do not emit warnings during fixture import and can be controlled
# per-test via the WithStream variant below.


# Read-redirect: ``color``→``colour`` and ``txt``→``text``.  Used by tests that exercise
# per-attribute warning budgets and silent passthrough of non-listed attribute names.
DeprecatedAttrsPalette = deprecated_class(
    attrs_mapping={"color": "colour", "txt": "text"}, **_DEPRS_CASE_STD_ARGS, stream=None
)(Palette)


# Warn-only: ``size`` is registered with redirect target ``None``, so reads warn but the
# canonical attribute name is unchanged (no rename).
DeprecatedAttrsNotifyOnly = deprecated_class(attrs_mapping={"size": None}, **_DEPRS_CASE_STD_ARGS, stream=None)(Palette)


# Enum variant: deprecated alias ``COLOR`` redirects to canonical enum member ``COLOUR``.
DeprecatedAttrsPaletteEnum = deprecated_class(attrs_mapping={"COLOR": "COLOUR"}, **_DEPRS_CASE_STD_ARGS, stream=None)(
    PaletteEnum
)


# Stream-enabled variant used by warning-message content tests: this fixture must NOT use
# ``stream=None`` because the tests assert the FutureWarning text emitted on access.
DeprecatedAttrsPaletteWithStream = deprecated_class(attrs_mapping={"color": "colour"}, **_DEPRS_CASE_STD_ARGS)(Palette)


# H4 fixture: callable target + attrs_mapping.  Wraps ``PaletteOld`` with
# ``target=Palette``.  Listed attr redirects resolve against ``Palette``; unlisted attrs
# and calls still use normal target-forwarding behaviour.
DeprecatedAttrsPaletteCallableTarget = deprecated_class(
    target=Palette, attrs_mapping={"color": "colour"}, **_DEPRS_CASE_STD_ARGS, stream=None
)(PaletteOld)


@_class_deprecation_notify_only_callable_target
class DeprecatedAttrsNotifyOnlyCallableTargetDecorated:
    """Decorator-form: replacement target owns the warn-only attribute."""

    color: str = "source_red"
    colour: str = "source_colour"


DeprecatedAttrsNotifyOnlyCallableTargetWrapped = _class_deprecation_notify_only_callable_target(PaletteOld)


# ========== Combination fixtures: deprecated_class() config matrix ==========
# These fixtures cover the valid combinations for ATTRS_REMAP (additive alias added in v0.10):
# - C1: Explicit ``target=TargetMode.ATTRS_REMAP`` + ``attrs_mapping`` (equivalent to implicit auto-resolve)
# - C3: Callable target + ``attrs_mapping`` + ``args_mapping`` (three disjoint surfaces active)
# - Nested: ``deprecated_class`` wrapping another proxy (blanket-warn outer, ATTRS_REMAP inner)

# C1: Explicit TargetMode.ATTRS_REMAP form — semantically identical to the implicit
# ``DeprecatedAttrsPalette`` fixture above. Used to verify both forms are equivalent.
DeprecatedAttrsExplicitMode = deprecated_class(
    target=TargetMode.ATTRS_REMAP, attrs_mapping={"color": "colour"}, **_DEPRS_CASE_STD_ARGS, stream=None
)(Palette)


# Legacy bool sentinel regression: before validation normalisation, ``target=True`` must
# validate ``attrs_mapping`` against the wrapped source class instead of the bool object.
with catch_warnings():
    simplefilter("ignore", UserWarning)
    DeprecatedAttrsLegacyTrue = deprecated_class(
        target=True, attrs_mapping={"color": "colour"}, **_DEPRS_CASE_STD_ARGS, stream=None
    )(LegacyBoolAttrsSource)


# C3: Callable target + attrs_mapping + args_mapping — three independent surfaces.
# ``attrs_mapping`` handles ``__getattr__``/``__setattr__``/``__delattr__`` for ``colour`` redirects;
# ``args_mapping`` handles ``__call__`` (``old_arg`` -> ``new_arg`` kwarg rename); the callable
# target ``CombinedAttrsArgsTarget`` receives the forwarded construction call.
DeprecatedAttrsPaletteAllThree = deprecated_class(
    target=CombinedAttrsArgsTarget,
    attrs_mapping={"color": "colour"},
    args_mapping={"old_arg": "new_arg"},
    **_DEPRS_CASE_STD_ARGS,
    stream=None,
)(CombinedAttrsArgsSource)


# Nested fixture: ``deprecated_class`` (blanket-warn outer) wraps another ``deprecated_class``
# proxy (ATTRS_REMAP inner). Used to verify nested proxy semantics: the outer blanket proxy
# warns on every attribute access; the inner selective proxy forwards silently because its
# selective set is consumed by the outer's __getattr__ before it reaches the inner.
# ``DeprecatedAttrsPalette`` is a ``_DeprecatedProxy`` instance — the annotation now accepts
# Union[type, _DeprecatedProxy] so no cast is needed.
DeprecatedAttrsPaletteNested = deprecated_class(**_DEPRS_CASE_STD_ARGS, stream=None)(DeprecatedAttrsPalette)


# ========== ATTRS_REMAP stacking fixture ==========
# Two-layer ATTRS_REMAP stacked proxy: each layer carries a distinct version pair.
# Outer proxy maps old_attr -> new_attr (deprecated_in="1.0").
# Inner proxy maps older_attr -> newer_attr (deprecated_in="0.9").
StackedAttrProxy = deprecated_class(
    attrs_mapping={"old_attr": "new_attr"},
    **_DEPRS_CASE_STD_ARGS,
    stream=None,
)(
    deprecated_class(
        attrs_mapping={"older_attr": "newer_attr"},
        deprecated_in="0.9",
        remove_in="1.0",
        stream=None,
    )(StackedAttrTarget)
)

# ========== Dataclass dual-surface auto-expand fixtures ==========

# Required-field variant of ``api_deprecated.DepAutoExpandDC``.
DepAutoExpandReqDC = deprecated_class(attrs_mapping={"old_field": "new_field"}, **_DEPRS_CASE_STD_INF_ARGS)(
    AutoExpandReqDC
)

# Mix of a normal field and an init=False field.  Only the normal field (new_field) must
# appear in args_mapping_auto_expanded; computed_field (init=False) must be excluded because
# it is not a valid __init__ kwarg and passing it to the constructor would raise TypeError.
DepAutoExpandInitFalseDC = deprecated_class(
    attrs_mapping={"old_field": "new_field", "old_computed": "computed_field"},
    **_DEPRS_CASE_STD_INF_ARGS,
)(AutoExpandInitFalseDC)

# Dataclass with overridden __init__: new_field is in the overridden signature, skipped_field
# is a dataclass field but absent from __init__.  Only new_field must be auto-expanded.
DepAutoExpandOverriddenInitDC = deprecated_class(
    attrs_mapping={"old_field": "new_field", "old_skipped": "skipped_field"},
    **_DEPRS_CASE_STD_INF_ARGS,
)(AutoExpandOverriddenInitDC)


# ========== Single-call combined attrs_mapping + args_mapping fixture ==========
# Single deprecated_class() call with both attrs_mapping (class-level attribute redirect)
# and args_mapping (constructor kwarg rename) on the same proxy — verifies the two surfaces
# operate independently without interference when configured together in one call.
DepCombinedSingleCall = deprecated_class(
    target=StackingArgsAttrsBase,
    attrs_mapping={"old_attr": "new_attr"},
    args_mapping={"old_arg": "new_arg"},
    **_DEPRS_CASE_STD_INF_ARGS,
)(StackingArgsAttrsBase)


# ========== Two-decorator no-target self-deprecation fixture ==========
# Two deprecated_class() calls stacked with no callable target on either layer.
# Inner proxy: args_mapping={"n_layers": "num_layers"} auto-resolves to ARGS_REMAP —
# renames constructor kwarg in-place without forwarding to a different class.
# Outer proxy: attrs_mapping={"cuda": None, "gpu": "device"} auto-resolves to ATTRS_REMAP —
# deprecates class-level attributes in-place; call delegation falls through to the inner proxy
# (ATTRS_REMAP + inner-_DeprecatedProxy path — no global callable warning fired by outer).
DepSelfCombinedTwoLayer = deprecated_class(
    attrs_mapping={"cuda": None, "gpu": "device"},
    **_DEPRS_CASE_STD_INF_ARGS,
)(
    deprecated_class(
        args_mapping={"n_layers": "num_layers"},
        **_DEPRS_CASE_STD_INF_ARGS,
    )(SelfDeprecatedModel)
)


# ========== Function-decorator POSITIONAL_ONLY target fixture ==========

# positional_only_target declares `x` as POSITIONAL_ONLY — calling it with
# x=value as a kwarg raises TypeError.  After the fix, @deprecated detects this
# at decoration time (UserWarning) and splits the dispatch at call time.
# Suppress the decoration-time UserWarning here so importing this module does
# not pollute test output.
with catch_warnings():
    simplefilter("ignore", UserWarning)

    @deprecated(target=positional_only_target, **_DEPRS_CASE_STD_ARGS)
    def deprecated_positional_only_source(x: int, y: int = 0) -> int:
        """Deprecated wrapper forwarding to a target with a POSITIONAL_ONLY parameter."""
        return 0


# ========== Async POSITIONAL_ONLY fixture ==========

with catch_warnings():
    simplefilter("ignore", UserWarning)

    @deprecated(target=async_positional_only_target, **_DEPRS_CASE_STD_ARGS)
    async def deprecated_async_positional_only_source(x: int, y: int = 0) -> int:
        """Deprecated async wrapper forwarding to an async target with a POSITIONAL_ONLY parameter."""
        return 0


# ========== Two POSITIONAL_ONLY params ordering fixture ==========

with catch_warnings():
    simplefilter("ignore", UserWarning)

    @deprecated(target=positional_only_two_params_target, **_DEPRS_CASE_STD_ARGS)
    def deprecated_positional_only_two_params_source(a: int, b: int, c: int = 0) -> int:
        """Deprecated wrapper forwarding to a target with two POSITIONAL_ONLY parameters."""
        return 0


# ========== args_mapping + POSITIONAL_ONLY fixture ==========

with catch_warnings():
    simplefilter("ignore", UserWarning)

    @deprecated(
        target=positional_only_target,
        **_DEPRS_CASE_STD_ARGS,
        args_mapping={"old_x": "x"},
    )
    def deprecated_positional_only_with_args_mapping_source(old_x: int, y: int = 0) -> int:
        """Deprecated wrapper using args_mapping to rename 'old_x' to 'x' before positional-only split."""
        return 0


# ========== Constructor-forwarding POSITIONAL_ONLY fixture ==========

# @deprecated applied to OldPositionalOnlyClass.__init__ with target=PositionalOnlyTarget
# (a class).  _normalize_target() maps this to PositionalOnlyTarget.__init__ (unbound),
# whose param_order includes "self".  _split_positional_only_kwargs() must pop "self" into
# pos_args before "new_val" so the unbound __init__ call succeeds.
with catch_warnings():
    simplefilter("ignore", UserWarning)

    class OldPositionalOnlyClass:
        """Deprecated class whose constructor forwards to PositionalOnlyTarget."""

        @deprecated(target=PositionalOnlyTarget, **_DEPRS_CASE_STD_ARGS)
        def __init__(self, new_val: int = 0) -> None:
            """Constructor deprecated in favour of PositionalOnlyTarget."""
            self.new_val = new_val


# ========== self-only POSITIONAL_ONLY constructor fixture ==========

# @deprecated on OldSelfOnlyClass.__init__ with target=SelfOnlyPositionalOnlyTarget.
# _normalize_target() maps the class target to SelfOnlyPositionalOnlyTarget.__init__
# (unbound), whose only POSITIONAL_ONLY param is self.  Before the fix, self was
# excluded from target_positional_only, leaving the set empty → split gate never fired
# → target_func(**{'self': instance}) → TypeError.
with catch_warnings():
    simplefilter("ignore", UserWarning)

    class OldSelfOnlyClass:
        """Deprecated class forwarding to SelfOnlyPositionalOnlyTarget."""

        @deprecated(target=SelfOnlyPositionalOnlyTarget, **_DEPRS_CASE_STD_ARGS)
        def __init__(self) -> None:
            """Constructor deprecated in favour of SelfOnlyPositionalOnlyTarget."""


# ========== stream=None POSITIONAL_ONLY fixture ==========

# stream=None suppresses both the decoration-time UserWarning (via the
# `stream is not None` guard in packing()) and the call-time FutureWarning,
# so no catch_warnings block is needed here.


@deprecated(target=positional_only_target, **_DEPRS_CASE_STD_ARGS, stream=None)
def deprecated_positional_only_stream_none(x: int, y: int = 0) -> int:
    """Deprecated wrapper forwarding to positional_only_target with stream=None (no warnings emitted)."""
    return 0


# ========== num_warns=1 POSITIONAL_ONLY fixture ==========

# Factory — not a singleton — because num_warns=1 gives the wrapper a stateful quota
# counter.  A module-level singleton would have the quota depleted after the first test
# call, causing subsequent test runs to fail on `pytest.warns(FutureWarning)`.
# Each test that needs a fresh quota calls `make_deprecated_positional_only_num_warns_one()`
# to get a new wrapper with warn_count reset to 0.


def make_deprecated_positional_only_num_warns_one() -> "Callable[..., int]":
    """Return a fresh deprecated wrapper for positional_only_target with num_warns=1.

    The wrapper emits ``FutureWarning`` on the first call only.  A fresh wrapper is
    returned each call so the quota counter starts at zero regardless of prior test
    activity — a module-level singleton would be depleted after the first test run.

    Returns:
        A new ``@deprecated`` wrapper around :func:`~tests.collections.api_current.core.positional_only_target`
        configured with ``num_warns=1`` and the decoration-time ``UserWarning`` suppressed.
    """
    with catch_warnings():
        simplefilter("ignore", UserWarning)

        @deprecated(target=positional_only_target, deprecated_in="1.0", remove_in="2.0", num_warns=1)
        def _deprecated_positional_only_num_warns_one(x: int, y: int = 0) -> int:
            """Deprecated wrapper forwarding to positional_only_target; FutureWarning emitted once only."""
            return 0

    return _deprecated_positional_only_num_warns_one


# ========== POSITIONAL_ONLY *source* fixtures (NOTIFY / ARGS_REMAP) ==========

# The decorated source itself declares a `/` in its signature.  NOTIFY and ARGS_REMAP
# execute the source body, so the dispatcher must split the positional-only params back
# out of resolved kwargs before invoking `source` — no decoration-time warning applies
# (the source signature is fully supported).


@deprecated(**_DEPRS_CASE_STD_INF_ARGS)
def deprecated_notify_positional_only_source(a: int, /, b: int = 2) -> int:
    """Warn-only deprecated function whose own signature declares a POSITIONAL_ONLY param."""
    return a + b


@deprecated(**_DEPRS_CASE_STD_INF_ARGS)
async def deprecated_async_notify_positional_only_source(a: int, /, b: int = 2) -> int:
    """Async warn-only deprecated function with a POSITIONAL_ONLY param in its own signature."""
    return a + b


@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_flag": "new_flag"}, **_DEPRS_CASE_STD_INF_ARGS)
def deprecated_args_remap_positional_only_source(a: int, /, old_flag: int = 0, new_flag: int = 0) -> int:
    """ARGS_REMAP self-deprecation on a source that also declares a POSITIONAL_ONLY param."""
    return a + new_flag


@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_flag": "new_flag"}, **_DEPRS_CASE_STD_INF_ARGS)
async def deprecated_async_args_remap_positional_only_source(a: int, /, old_flag: int = 0, new_flag: int = 0) -> int:
    """Async ARGS_REMAP self-deprecation on a source with a POSITIONAL_ONLY param."""
    return a + new_flag


# ========== Var-positional surplus forwarding fixtures ==========

# Sources declaring `*args` forwarding to a callable target: the surplus positional tail
# (everything past the named positionals) must be forwarded to the target, not dropped.


@deprecated(target=var_positional_target, **_DEPRS_CASE_STD_INF_ARGS)
def deprecated_var_positional_forward(a: int, *extras: int) -> int:
    """Deprecated ``*args`` source forwarding to a ``*args``-accepting target."""
    return 0


@deprecated(target=async_var_positional_target, **_DEPRS_CASE_STD_INF_ARGS)
async def deprecated_async_var_positional_forward(a: int, *extras: int) -> int:
    """Async deprecated ``*args`` source forwarding to an async ``*args``-accepting target."""
    return 0


@deprecated(target=trio_positional_target, **_DEPRS_CASE_STD_INF_ARGS)
def deprecated_var_positional_to_fixed(a: int, *extras: int) -> int:
    """Deprecated ``*args`` source whose surplus tail fills the target's fixed positional slots."""
    return 0


@deprecated(target=single_positional_target, **_DEPRS_CASE_STD_INF_ARGS)
def deprecated_var_positional_overflow(a: int, *extras: int) -> int:
    """Deprecated ``*args`` source whose surplus tail cannot fit the single-slot target."""
    return 0


@deprecated(target=TargetMode.ARGS_REMAP, **_DEPRS_CASE_TGT_MODE_ARGS, args_mapping={"old_kwarg": "new_kwarg"})
def deprecated_var_positional_remap(a: int, *extras: int, old_kwarg: str = "", new_kwarg: str = "") -> tuple:
    """ARGS_REMAP ``*args`` source — ``old_kwarg`` renamed to ``new_kwarg``.

    Regression fixture for the double-pass bug: without the fix, calling with ``old_kwarg``
    raised ``TypeError: got multiple values for argument 'a'`` because ``resolved_kwargs``
    included positional-to-keyword conversions already present in ``*extras``.

    Wrapped body is :func:`tests.collections.api_current.core.var_positional_remap_body`.
    """
    return var_positional_remap_body(a, *extras, old_kwarg=old_kwarg, new_kwarg=new_kwarg)


# ========== Gapped POSITIONAL_ONLY forwarding fixtures ==========

# gapped_positional_only_target declares `a` and `b` positional-only, both defaulted.
# A source supplying only `b` leaves a gap at `a` — positional binding would slide `b`'s
# value into `a`'s slot, so the split dispatch must raise TypeError instead.
with catch_warnings():
    simplefilter("ignore", UserWarning)

    @deprecated(target=gapped_positional_only_target, **_DEPRS_CASE_STD_INF_ARGS)
    def deprecated_gapped_positional_only_source(b: int = 20) -> dict[str, int]:
        """Source supplying only the *second* POSITIONAL_ONLY target param — gap at ``a``."""
        return {}

    @deprecated(target=gapped_positional_only_target, **_DEPRS_CASE_STD_INF_ARGS)
    async def deprecated_async_gapped_positional_only_source(b: int = 20) -> dict[str, int]:
        """Async source supplying only the *second* POSITIONAL_ONLY target param — gap at ``a``."""
        return {}

    @deprecated(target=gapped_positional_only_target, **_DEPRS_CASE_STD_INF_ARGS)
    def deprecated_gapped_positional_only_full_source(a: int = 1, b: int = 2) -> dict[str, int]:
        """Source supplying both POSITIONAL_ONLY target params — no gap, forwards in order."""
        return {}

    @deprecated(target=gapped_positional_only_target, **_DEPRS_CASE_STD_INF_ARGS)
    def deprecated_gapped_positional_only_first_source(a: int = 9) -> dict[str, int]:
        """Source supplying only the *first* POSITIONAL_ONLY target param — trailing gap at ``b`` is safe."""
        return {}


# ========== Non-cycling callable-target fixtures ==========


@deprecated(target=non_cycle_double, **_DEPRS_CASE_STD_ARGS)
def dep_non_cycle_old_fn(x: int) -> int:
    """Deprecated wrapper forwarding to non_cycle_double — never forms a cycle."""
    return x


@deprecated(target=async_non_cycle_double, **_DEPRS_CASE_STD_ARGS)
async def dep_async_non_cycle_old_fn(x: int) -> int:
    """Async deprecated wrapper forwarding to async_non_cycle_double — never forms a cycle."""
    return x


# ========== Tasks spawned inside a guarded call ==========


@deprecated(target=async_suspending_double, **_DEPRS_CASE_STD_ARGS)
async def dep_async_suspending_old_fn(x: int) -> int:
    """Async deprecated wrapper whose target stays in flight across a task switch."""
    return x


async def _async_sibling_pair_target(x: int) -> list[int]:
    """Target of ``dep_async_sibling_pair_old_fn``: runs two sibling calls of one deprecated wrapper concurrently."""
    return list(await asyncio.gather(dep_async_suspending_old_fn(x), dep_async_suspending_old_fn(x + 1)))


@deprecated(target=_async_sibling_pair_target, **_DEPRS_CASE_STD_ARGS)
async def dep_async_sibling_pair_old_fn(x: int) -> list[int]:
    """Async deprecated wrapper whose target gathers two sibling calls of another deprecated wrapper."""
    return [x]


_dep_async_task_cycle: dict[str, Any] = {}


async def _async_task_cycle_target(x: int) -> int:
    """Target that re-enters its own deprecated wrapper from a spawned task while the outer call is in flight."""
    return await asyncio.create_task(_dep_async_task_cycle["fn"](x))


@deprecated(target=_async_task_cycle_target, **_DEPRS_CASE_STD_ARGS)
async def dep_async_task_cycle_fn(x: int) -> int:
    """Async deprecated wrapper whose target calls it again from a new task: a cycle across a task boundary."""
    return x


_dep_async_task_cycle["fn"] = dep_async_task_cycle_fn


# ========== Recursive deprecated function fixtures ==========


@deprecated(**_DEPRS_CASE_STD_ARGS)
def dep_fib_notify(n: int) -> int:
    """Deprecated recursive Fibonacci in NOTIFY mode — body recurses through the wrapper.

    Each recursive call re-enters the wrapper; ``num_warns=1`` (default) ensures only the
    outermost call emits a ``FutureWarning`` regardless of recursion depth.
    """
    if n <= 1:
        return n
    return dep_fib_notify(n - 1) + dep_fib_notify(n - 2)


@deprecated(num_warns=0, **_DEPRS_CASE_STD_ARGS)
def dep_fib_silent(n: int) -> int:
    """Deprecated recursive Fibonacci with ``num_warns=0`` — no warning fires regardless of recursion depth."""
    if n <= 1:
        return n
    return dep_fib_silent(n - 1) + dep_fib_silent(n - 2)


@deprecated(target=fib_recursive, **_DEPRS_CASE_STD_ARGS)
def dep_fib_callable(n: int) -> int:
    """Deprecated callable-target wrapper forwarding to ``fib_recursive``.

    Cycle detection does not false-positive when the target self-recurses: ``id(source)`` is
    added to ``_active`` once and removed in the ``finally`` block.  The target
    (``fib_recursive``) recurses on itself without re-entering this wrapper.
    """
    return n  # dead code — forwarded to fib_recursive (would return n, not fib(n), if bypassed)


@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"n": "x"}, **_DEPRS_CASE_STD_ARGS)
def dep_fib_remap(n: int = 0, x: int = 0) -> int:
    """Deprecated recursive Fibonacci with arg rename — ``n`` remapped to ``x`` on every recursive call."""
    if x <= 1:
        return x
    return dep_fib_remap(n=x - 1) + dep_fib_remap(n=x - 2)


# ========== Async source forwarding to a SYNC target fixtures ==========

# An `async def` wrapper may forward to a *synchronous* callable target: legacy targets are
# not required to be redeclared `async def` for callers to migrate.  These fixtures exercise
# that path (the `not iscoroutinefunction(target)` branch of the async dispatcher) for both
# the var-positional surplus reorder shape and the POSITIONAL_ONLY split shape.


@deprecated(target=var_positional_target, **_DEPRS_CASE_STD_INF_ARGS)
async def deprecated_async_var_positional_to_sync(a: int, *extras: int) -> int:
    """Async ``*args`` source forwarding a surplus tail to a SYNC ``*args``-accepting target."""
    return 0


# positional_only_target declares `x` as POSITIONAL_ONLY, so decoration emits a UserWarning
# that is suppressed here to keep import-time output clean (mirrors the sync fixture above).
with catch_warnings():
    simplefilter("ignore", UserWarning)

    @deprecated(target=positional_only_target, **_DEPRS_CASE_STD_INF_ARGS)
    async def deprecated_async_positional_only_to_sync(x: int, y: int = 0) -> int:
        """Async source forwarding to a SYNC target with a POSITIONAL_ONLY parameter."""
        return 0
