"""Target functions for deprecation testing.

This module provides base functions that are used as targets for deprecated functions in other test modules.

"""

import functools
import time
import warnings
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


def raise_pow(base: float, coef: float) -> float:
    """Compute base**coef while emitting a UserWarning — used to test assert_no_warnings."""
    warnings.warn("warning you!", UserWarning)
    return base**coef


def raise_pow_future(base: float, coef: float) -> float:
    """Compute base**coef while emitting a FutureWarning — used to test assert_no_warnings."""
    warnings.warn("future warning!", FutureWarning)
    return base**coef


def base_sum_kwargs(a: int = 0, b: int = 3) -> int:
    """Base sum function with keyword arguments."""
    return a + b


def base_pow_args(a: float, b: int) -> float:
    """Base power function with positional arguments."""
    return a**b


tracked_identity_calls: list[int] = []


def double_value(x: int) -> int:
    """Return double the input value for TargetMode smoke tests."""
    return x * 2


def tracked_identity(x: int) -> int:
    """Record calls and return the original value for body-execution tests."""
    tracked_identity_calls.append(x)
    return x


def increment_value(x: int) -> int:
    """Return the input value plus one for args-only deprecation tests."""
    return x + 1


def power_with_new_coef(base: float, new_coef: float = 1.0) -> float:
    """Raise a base to a remapped coefficient for args-only tests."""
    return base**new_coef


def add_values(x: int, y: int) -> int:
    """Add two integers for args-extra injection tests."""
    return x + y


def identity_value(x: int) -> int:
    """Return the original input value."""
    return x


def stacked_chain_identity(base: int) -> int:
    """Return the base input unchanged for stacked TargetMode chain fixtures."""
    return base


def return_b(b: int) -> int:
    """Return the mapped positional argument."""
    return b


def return_z(z: int = 0) -> int:
    """Return the optional keyword argument used in warning tests."""
    return z


def return_none() -> None:
    """Return ``None`` for warning-only sentinel tests."""


def return_new(new: int = 0) -> int:
    """Return the remapped value for the ``target=True`` sentinel test."""
    return new


class NewCls:
    """New class for testing deprecation."""

    def __init__(self, c: float, d: str = "abc", **kwargs: Any) -> None:  # noqa: ANN401
        """Initialize NewCls."""
        self.my_c = c
        self.my_d = d
        self.my_e = kwargs.get("e", 0.2)


class NewEnum(Enum):
    """New enum for forwarding tests."""

    ALPHA = "alpha"
    BETA = "beta"


class NewIntEnum(Enum):
    """New enum with integer values for mapping tests."""

    ALPHA = 1
    BETA = 2


class ColorEnum(Enum):
    """Target enum for deprecated_class forwarding tests."""

    RED = 1
    BLUE = 2


def plain_function_target(x: int) -> int:
    """Plain function without deprecation decorator for testing error handling."""
    return x


def cross_guard_standalone_increment(x: int) -> int:
    """Module-level target used by cross-class guard tests."""
    return x + 1


def call_signature_source(value: str) -> object:
    """Source signature helper for _prepare_target_call tests."""
    raise NotImplementedError


class KeywordCallMeta(type):
    """Metaclass exposing a keyword-only value in __call__ for signature-validation tests."""

    def __call__(cls, *, value: str) -> object:
        """Create a target instance using keyword-only `value`."""
        return super().__call__(raw=value)


class KeywordCallTarget(metaclass=KeywordCallMeta):
    """Target class whose metaclass __call__ differs from __init__."""

    def __init__(self, raw: str) -> None:
        """Store the raw payload passed through metaclass __call__."""
        self.raw = raw


class CrossGuardClassTargetNew:
    """Constructor-forwarding target class used by cross-class guard tests."""

    def __init__(self, x: int) -> None:
        """Store constructor argument for assertions."""
        self.x = x


def sample_function(x: int) -> int:
    """Simple callable used as input to deprecated function-wrapper tests."""
    return x * 2


@dataclass
class NewDataClass:
    """Target dataclass for deprecation forwarding tests."""

    label: str
    total: int = 0


class SomeTargetClass:
    """Simple target class for proxy args_mapping behaviour tests.

    Accepts ``new_key`` as the canonical parameter name; used to verify that ``deprecated_class`` fixtures correctly
    remap ``old_key`` to ``new_key``.

    """

    def __init__(self, new_key: int = 0) -> None:
        """Store the canonical keyword argument."""
        self.new_key = new_key


class WithInjected:
    """Target class accepting an ``injected`` kwarg for ``args_extra`` tests.

    Used by proxy ``args_extra`` fixtures to verify that deprecated_class() merges configured extra kwargs into
    forwarded calls.

    """

    def __init__(self, new_key: int = 0, injected: str = "") -> None:
        """Store both the canonical keyword and the injected extra value."""
        self.new_key = new_key
        self.injected = injected


def both_old_new_target(new: int = 0) -> int:
    """Target callable used by collision-bug fixtures (only ``new`` accepted)."""
    return new


def fn_with_default(new_arg: int = 99) -> int:
    """Target callable carrying its own default for the renamed argument.

    Used by Fix 1 regression tests to verify that the source's stale default for the deprecated argument name does not
    silently override the target's default when the caller supplies neither name.

    """
    return new_arg


def fn_remap_with_extra_body(new_arg: int = 0, injected: int = 0) -> int:
    """Source body for Fix 2 regression tests using TargetMode.ARGS_REMAP.

    The body intentionally combines the remapped argument and the injected extra so the test can assert that
    ``args_extra`` is merged into kwargs even when the caller already uses the new argument name (no remap warning
    fires).

    """
    return new_arg + injected


def pep702_target(x: int) -> int:
    """Target for PEP 702 stacking regression tests.

    Doubles the input value so the wrapping test can confirm the inner pyDeprecate
    @deprecated forwarded the call after PEP 702 ``typing_extensions.deprecated``
    overwrote ``__deprecated__`` on the wrapper.

    """
    return x * 2


class _Pep702ProxyTarget:
    """Target class for PEP 702 stacking on ``deprecated_class`` proxy (B1b).

    Provides a stable ``value()`` method so the stacking test can confirm that
    instantiation and method dispatch survive after PEP 702
    ``typing_extensions.deprecated`` was applied on top of the ``deprecated_class``
    proxy wrapper.

    Underscore-prefixed so :func:`deprecate.find_deprecation_wrappers` skips it: the
    outer PEP 702 wrapper forwards its ``__deprecated__ = msg`` assignment through the
    proxy's ``__setattr__`` onto this wrapped class, leaving a plain string on the
    class attribute that would otherwise crash the audit walker.

    """

    def value(self) -> int:
        """Return a stable sentinel value used by the B1b regression test."""
        return 42


def timing_wrapper(func: Callable) -> Callable:
    """Decorator to measure the execution time of a function."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Wrapper function."""
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        end_time = time.perf_counter()
        print(f"Function {func.__name__!r} took {(end_time - start_time):.4f} seconds to execute.")
        return result

    return wrapper


def stacked_inner_target(x: int) -> int:
    """Inner target for stacked-callable-target guard tests."""
    return x * 3


def stacked_outer_target(x: int) -> int:
    """Outer target for stacked-callable-target guard tests."""
    return x * 5


class TimerDecorator:
    """A class-based decorator to time functions and methods."""

    def __init__(self, func: Callable) -> None:
        """Initialize the timer decorator."""
        functools.update_wrapper(self, func)
        self.func = func
        self.total_time = 0.0
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Call the wrapped function and track timing."""
        start_time = time.perf_counter()
        result = self.func(*args, **kwargs)
        end_time = time.perf_counter()
        execution_time = end_time - start_time
        self.total_time += execution_time
        self.calls += 1
        print(f"'{self.func.__name__}' executed in {execution_time:.4f}s")
        return result


def compute_power(base: float, factor: float = 1, scale: float = 1) -> float:
    """Compute base raised to scale; factor is the legacy parameter name for scale."""
    return base**scale


def gen_target(x: int) -> Iterator[int]:
    """Generator that yields multiples of x (1×, 2×, 3×).

    Used by the generator wrapper integration tests for the ``@deprecated`` decorator.

    """
    for i in range(1, 4):
        yield x * i


async def async_target(x: int) -> int:
    """Replacement async function used by ``async_callable`` deprecation wrapper tests.

    Examples:
        >>> import asyncio
        >>> asyncio.run(async_target(x=3))
        6

    """
    return x * 2


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


class _InnerOrderPropTarget:
    """Target class for inner-order ``@property @deprecated`` regression tests (H2 fixture).

    Inner-order wrapping (``@property`` outermost, ``@deprecated`` closer to ``def``) wraps the
    ``fget`` accessor only.  Setters and deleters added afterwards via ``@value.setter`` /
    ``@value.deleter`` are *not* re-wrapped, so writes and deletes must be silent.

    The class is consumed by :mod:`tests.collection_deprecate` which subclasses it to attach the
    inner-order deprecated property + chain-style setter/deleter.

    """

    def __init__(self) -> None:
        """Initialise the mutable read/write/delete slots."""
        self._value: int = 0
        self._del_value: Optional[int] = 42


class _DelOnlyPropTarget:
    """Target class for fdel-only property regression tests (H3 fixture).

    A ``property(None, None, fdel)`` exposes only the delete accessor.  Wrapping it with
    ``@deprecated`` must still emit a ``FutureWarning`` on ``del obj.prop`` because the
    deprecation surface attaches to the only callable the property carries.

    """

    def __init__(self) -> None:
        """Initialise the slot mutated by the delete accessor."""
        self._value: Optional[int] = 99


def del_only_prop_fdel(self: _DelOnlyPropTarget) -> None:
    """Delete accessor for the fdel-only deprecated property fixture (H3).

    Mutates ``self._value`` to ``None`` so the regression test can assert the body actually ran
    after the deprecation warning fired.

    """
    self._value = None


class Palette:
    """Target class with canonical attribute names for ``attrs_mapping`` tests.

    Carries both the canonical names (``colour``, ``text``, ``size``) and is wrapped by ``deprecated_class`` fixtures in
    :mod:`tests.collection_deprecate` to register deprecated aliases (``color`` → ``colour``, ``txt`` → ``text``).  The
    canonical attributes are mutable instance-style class attributes so that read/write/delete tests can exercise the
    forwarding behaviour without instantiating the class.

    """

    colour: str = "red"
    text: str = "hello"
    size: int = 42


class PaletteEnum(Enum):
    """Enum with canonical member names for the ``attrs_mapping`` enum redirect test.

    Wrapped by ``DeprecatedAttrsPaletteEnum`` in :mod:`tests.collection_deprecate` to register a deprecated alias
    ``COLOR`` → ``COLOUR``.  Used to verify that proxy ``__getattr__`` redirect logic survives the enum metaclass.

    """

    COLOUR = "red"
    TEXT = "hello"


class PaletteOld:
    """Source class for H4 callable-target + attrs_mapping fixture.

    Has both a deprecated ``color`` attribute and canonical ``colour`` attribute.  Wrapped by ``deprecated_class`` in
    :mod:`tests.collection_deprecate` with ``target=Palette`` and ``attrs_mapping={"color": "colour"}``
    to verify that mapped attributes resolve against the target class when a callable target is configured.

    """

    color: str = "source_red"
    colour: str = "source_colour"


class LegacyBoolAttrsSource:
    """Source class for ``target=True`` plus ``attrs_mapping`` regression coverage."""

    color: str = "legacy_red"
    colour: str = "canonical_red"

    def __init__(self) -> None:
        """Initialise a stable marker so tests can confirm construction succeeded."""
        self.ready = True


class CombinedAttrsArgsTarget:
    """Target class combining a canonical attribute and a constructor keyword for combination matrix tests.

    Carries ``colour`` as the canonical attribute name (paired with deprecated alias ``color`` on a wrapping proxy) and
    a ``new_arg`` constructor keyword (paired with deprecated alias ``old_arg`` on the same proxy).  Wrapped by
    ``DeprecatedAttrsPaletteAllThree`` in :mod:`tests.collection_deprecate` to verify that ``attrs_mapping`` and
    ``args_mapping`` operate on disjoint surfaces (attribute access vs constructor kwargs) without interference.

    """

    colour: str = "red"
    color: str = "target_color_legacy"  # canonical-side alias, never read by the proxy

    def __init__(self, new_arg: int = 0) -> None:
        """Store the canonical constructor keyword argument."""
        self.new_arg = new_arg


class CombinedAttrsArgsSource:
    """Source class for combination matrix tests with deprecated attr alias and constructor kwarg.

    Has ``colour`` (canonical) and ``color`` (deprecated alias).  Constructor takes ``old_arg`` (deprecated) and
    ``new_arg`` (canonical). Wrapped by ``DeprecatedAttrsPaletteAllThree`` with both ``attrs_mapping`` and
    ``args_mapping`` configured against :class:`CombinedAttrsArgsTarget` as the forwarding target.

    """

    colour: str = "source_red"
    color: str = "source_color_legacy"

    def __init__(self, old_arg: int = 0, new_arg: int = 0) -> None:
        """Store either the old or the new keyword argument under a single attribute for assertions."""
        self.value = old_arg or new_arg


class MutableAttrsList:
    """List-like target object for read-only ``attrs_mapping`` regression coverage.

    Wrapped by :mod:`tests.collection_deprecate` with ``read_only=True`` and
    ``attrs_mapping={"push": "append"}`` so tests can verify that a deprecated
    attribute alias resolving to a standard collection mutator cannot mutate the
    underlying object through the proxy.

    """

    def __init__(self) -> None:
        """Initialise the list-like storage used by the mutator."""
        self.items: list[str] = []

    def append(self, item: str) -> None:
        """Append an item to the backing list."""
        self.items.append(item)


class StackedAttrTarget:
    """Target with two canonical attributes replacing deprecated aliases at different versions.

    Wrapped by :mod:`tests.collection_deprecate` as ``StackedAttrProxy`` (two-layer
    ``deprecated_class`` stack) — each layer carries its own ``deprecated_in`` /
    ``remove_in`` pair so per-attribute version metadata flows from the proxy chain.
    """

    newer_attr: str = "value_new"  # canonical replacement for ``older_attr`` (deprecated in v0.9)
    new_attr: str = "value_b"  # canonical replacement for ``old_attr`` (deprecated in v1.0)

    def __init__(self, new_arg: int = 0) -> None:
        """Construct StackedAttrTarget."""
        self.new_arg = new_arg


class StackingDeepBase:
    """Three-layer stacking target; canonical attr value ``deep`` for depth-assertion tests."""

    canonical: str = "deep"


class StackingLeafBase:
    """Leaf class for isinstance/issubclass recursive stacking tests."""

    canonical: str = "leaf"


class StackingSilentBase:
    """Target for canonical-attr-passes-through-both-layers-silently stacking tests."""

    canonical: str = "silent"


class StackingBlanketBase:
    """Target for blanket-outer plus ATTRS_REMAP-inner stacking combination tests."""

    colour: str = "red"


class StackingMutableBase:
    """Target for setattr-propagates-through-stacked-proxy stacking tests."""

    new_attr: str = "original"


class StackingArgsAttrsBase:
    """Combined attribute and constructor-arg target for ATTRS_REMAP plus ARGS_REMAP stacking tests."""

    new_attr: str = "b"

    def __init__(self, new_arg: int = 0) -> None:
        """Construct StackingArgsAttrsBase."""
        self.new_arg = new_arg


@dataclass
class AutoExpandDC:
    """Dataclass target for attrs_mapping dual-surface auto-expand tests.

    Wrapped by ``DepAutoExpandDC`` in :mod:`tests.collection_deprecate` with only
    ``attrs_mapping={"old_field": "new_field"}`` — the proxy auto-expands into
    ``args_mapping`` so both ``instance.old_field`` and ``DC(old_field=5)`` warn.
    """

    new_field: int = 0


@dataclass
class AutoExpandReqDC:
    """Dataclass target with a required field for auto-expand regression tests.

    ``new_field`` has no default; wrapped by ``DepAutoExpandReqDC`` to verify
    auto-expansion on required fields.
    """

    new_field: int


@dataclass
class AutoExpandInitFalseDC:
    """Dataclass with a mix of normal and ``init=False`` fields for auto-expand edge case tests.

    ``new_field`` is a normal constructor parameter; ``computed_field`` is ``init=False`` and
    must NOT be auto-expanded into ``args_mapping`` because it is not a valid ``__init__``
    kwarg.  Wrapped by ``DepAutoExpandInitFalseDC`` in :mod:`tests.collection_deprecate`.
    """

    new_field: int = 0
    computed_field: int = field(default=0, init=False)


@dataclass
class AutoExpandOverriddenInitDC:
    """Dataclass with overridden ``__init__`` for signature-based auto-expand tests.

    ``new_field`` is accepted by the overridden ``__init__``; ``skipped_field`` is declared
    as a dataclass field but intentionally absent from the custom ``__init__``.  Auto-expand
    must consult ``inspect.signature`` — not ``dataclasses.fields()`` — so only ``new_field``
    is expanded into ``args_mapping``.  Wrapped by ``DepAutoExpandOverriddenInitDC`` in
    :mod:`tests.collection_deprecate`.
    """

    new_field: int = 0
    skipped_field: int = 0

    def __init__(self, new_field: int = 0) -> None:
        """Override __init__ to accept only new_field; set skipped_field to a sentinel."""
        self.new_field = new_field
        self.skipped_field = 99


class PositionalOnlyTarget:
    """Target with a POSITIONAL_ONLY constructor parameter for args_mapping compat tests.

    ``new_val`` is declared positional-only so ``PositionalOnlyTarget(new_val=5)`` raises
    ``TypeError``.  Wrapped by ``DepPositionalOnly`` in :mod:`tests.collection_deprecate`
    to verify the proxy emits ``UserWarning`` at decoration time and falls back to
    ``setattr`` at call time instead of crashing.
    """

    def __init__(self, new_val: int = 0, /) -> None:
        """Store positional-only arg."""
        self.new_val = new_val


class SelfOnlyPositionalOnlyTarget:
    """Target where ``self`` is the only POSITIONAL_ONLY parameter.

    Exercises the edge case where ``target_positional_only`` would be an empty
    frozenset if ``self`` were excluded — causing the split-dispatch gate to
    never fire and ``target_func(**{'self': instance})`` to raise ``TypeError``.
    Wrapped by ``OldSelfOnlyClass`` in :mod:`tests.collection_deprecate`.
    """

    def __init__(self, /) -> None:
        """Construct with no user arguments; self is explicitly positional-only."""


def positional_only_target(x: int, /, y: int = 0) -> int:
    """Target with one POSITIONAL_ONLY param for function-decorator compat tests.

    ``x`` is positional-only so ``positional_only_target(x=5)`` raises ``TypeError``.
    Wrapped by ``deprecated_positional_only_source`` in :mod:`tests.collection_deprecate`
    to verify the function decorator emits ``UserWarning`` at decoration time and
    forwards calls correctly at call time.
    """
    return x + y


async def async_positional_only_target(x: int, /, y: int = 0) -> int:
    """Async target with one POSITIONAL_ONLY param for async dispatch tests.

    ``x`` is positional-only. Wrapped by
    ``deprecated_async_positional_only_source`` in
    :mod:`tests.collection_deprecate`.
    """
    return x + y


def positional_only_two_params_target(a: int, b: int, /, c: int = 0) -> int:
    """Target with two POSITIONAL_ONLY params for ordering tests.

    Both ``a`` and ``b`` are positional-only. Wrapped by
    ``deprecated_positional_only_two_params_source`` in
    :mod:`tests.collection_deprecate`.
    """
    return a + b + c


class SelfDeprecatedModel:
    """Target for two-decorator no-target self-deprecation stacking tests.

    Carries ``cuda`` (warn-only deprecated attribute, still served from the class),
    ``device`` (replacement for the deprecated ``gpu`` alias), and accepts
    ``num_layers`` in its constructor (renamed from ``n_layers`` in the outer layer).
    Wrapped by ``DepSelfCombinedTwoLayer`` in :mod:`tests.collection_deprecate` with two
    stacked ``deprecated_class()`` calls — neither forwarding to a different class.
    """

    cuda: bool = False
    device: str = "cpu"

    def __init__(self, num_layers: int = 4) -> None:
        """Construct SelfDeprecatedModel."""
        self.num_layers = num_layers


def fib_recursive(n: int) -> int:
    """Recursive Fibonacci implementation — target for ``dep_fib_callable`` in :mod:`tests.collection_deprecate`.

    Recurses on itself directly (not through any deprecated wrapper) so ``dep_fib_callable``
    only triggers the deprecation wrapper on the initial call — never on recursive steps.
    """
    if n <= 1:
        return n
    return fib_recursive(n - 1) + fib_recursive(n - 2)


def non_cycle_double(x: int) -> int:
    """Target for non-cycling callable-target tests — doubles input.

    Wrapped by ``dep_non_cycle_old_fn`` in :mod:`tests.collection_deprecate`.
    """
    return x * 2


async def async_non_cycle_double(x: int) -> int:
    """Async target for concurrent async non-cycle tests — doubles input.

    Wrapped by ``dep_async_non_cycle_old_fn`` in :mod:`tests.collection_deprecate`.
    """
    return x * 2
