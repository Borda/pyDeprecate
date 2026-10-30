"""Target functions for deprecation testing.

This module provides base functions that are used as targets for deprecated functions in other test modules.

"""

import asyncio
import contextvars
import functools
import inspect
import threading
import time
import types
import warnings
from collections.abc import Coroutine, Generator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, ClassVar, Generic, Optional, TypeVar

import typing_extensions

_BoxItem = TypeVar("_BoxItem")


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


class UncomparableDefault:
    """Default value that rejects equality comparisons, like an array with ambiguous truth values."""

    def __eq__(self, other: object) -> bool:
        """Reject equality checks so default filtering must use identity."""
        raise TypeError("default values must not be compared for equality")

    def __ne__(self, other: object) -> bool:
        """Reject inequality checks so default filtering must use identity."""
        raise TypeError("default values must not be compared for inequality")


uncomparable_default = UncomparableDefault()


def return_default_value(value: UncomparableDefault) -> UncomparableDefault:
    """Return the exact default object received by the forwarding target."""
    return value


def stacked_chain_identity(base: int) -> int:
    """Return the base input unchanged for stacked TargetMode chain fixtures."""
    return base


def return_b(b: int) -> int:
    """Return the mapped positional argument."""
    return b


def return_z(z: int = 0) -> int:
    """Return the optional keyword argument used in warning tests."""
    return z


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


class GenericBox(Generic[_BoxItem]):
    """Generic container class; a deprecated alias of it must stay subscriptable (``Alias[int]``) in annotations."""

    def __init__(self, item: _BoxItem) -> None:
        """Store the boxed *item*."""
        self.item = item


class TagList(list):
    """List subclass; a deprecated alias of it is subscriptable through ``list.__class_getitem__`` but not iterable."""


@dataclass
class StateEcho:
    """Callable record that returns the state it is called with; a non-frozen ``@dataclass`` leaves it unhashable."""

    label: str = "state"

    def __call__(self, state: dict[str, int]) -> dict[str, int]:
        """Return *state* unchanged."""
        return state


#: Module-level unhashable callable a ``__reduce__`` can return, which pickle saves by its class reference.
state_echo = StateEcho()


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


def fn_kept_name_target(level: int = -1) -> int:
    """Target callable whose parameter keeps the name the deprecated wrapper already uses.

    Used by the ``args_mapping={"level": ""}`` fixtures: ``""`` means "keep the old argument name", so a caller's
    ``level=...`` must reach this target unchanged.  The default ``-1`` differs from every value the tests pass, so a
    dropped argument shows up as ``-1`` instead of passing unnoticed.

    """
    return level


def fn_shared_default_target(x: int, level: int = 99) -> int:
    """Target sharing the parameter name ``level`` with its own default (99), no rename involved.

    Used by the shared-default forwarding test: the forwarding source declares the same non-renamed
    ``level`` parameter with a *different* default (1) and no ``args_mapping``.  The source signature is the
    contract the caller migrated from, so its default (1) is forwarded and reaches this body — the target's
    own default only applies when the source declares no default for that parameter.

    """
    return level


def source_sharing_level_default(x: int, level: int = 1) -> None:
    """Signature probe sharing ``level`` with :func:`fn_shared_default_target`, with a different default (1)."""


def source_with_forward_defaults(x: int = 0, old_arg: int = 1, y: int = 0) -> None:
    """Signature probe whose three defaults the forward-default tests keep or drop.

    ``old_arg`` is the name a rename makes stale and ``y`` the keyword a ``functools.partial`` target binds, so each
    default is dropped by exactly one forward-default rule.

    """


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


class Pep702ProxyTarget:
    """Target class for PEP 702 stacking on ``deprecated_class`` proxy (B1b).

    Provides a stable ``value()`` method so the stacking test can confirm that
    instantiation and method dispatch survive after PEP 702
    ``typing_extensions.deprecated`` was applied on top of the ``deprecated_class``
    proxy wrapper.

    The outer PEP 702 wrapper's ``__deprecated__ = msg`` assignment lands on the proxy itself, never on this class —
    the regression test asserts this class's own ``__dict__`` stays free of ``__deprecated__``.

    """

    def value(self) -> int:
        """Return a stable sentinel value used by the B1b regression test."""
        return 42


class Pep702StaticTarget:
    """Replacement class a PEP 702-stacked ``deprecated_class`` alias forwards to.

    A library renames ``LegacyReader`` to ``Pep702StaticTarget`` and marks the old name for both runtime warnings
    (pyDeprecate) and static checkers (``typing_extensions.deprecated`` stacked on top).  The replacement itself must
    stay un-deprecated: a type checker or ``inspect`` reading ``__deprecated__`` on it must find nothing.

    """

    def __init__(self, value: int = 0) -> None:
        """Store the value the alias forwards through construction."""
        self.value = value

    def doubled(self) -> int:
        """Return twice the stored value so call forwarding through the alias is observable."""
        return self.value * 2


class Pep702LibraryBase:
    """Stand-in for a third-party base class whose own methods carry PEP 702 markers.

    A project builds its models on a library base (a validation or ORM base, say) whose authors deprecated a few
    methods with ``typing_extensions.deprecated``. Auditing the project with ``include_pep702=True`` must list those
    methods on this defining class only, never again under each project subclass that merely inherits them — public
    or private.

    """

    @typing_extensions.deprecated("Use `export` instead.")
    def export_legacy(self) -> dict[str, Any]:
        """Legacy export the library deprecated for its callers."""
        return {}

    @typing_extensions.deprecated("Iterate `fields` instead.", category=None)
    def _iter_legacy(self) -> list[str]:
        """Private library helper deprecated together with the public API."""
        return []


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


async def async_target(x: int) -> int:
    """Replacement async function used by ``async_callable`` deprecation wrapper tests.

    Examples:
        >>> import asyncio
        >>> asyncio.run(async_target(x=3))
        6

    """
    return x * 2


class _InnerOrderPropTarget:
    """Target class for inner-order ``@property @deprecated`` regression tests (H2 fixture).

    Inner-order wrapping (``@property`` outermost, ``@deprecated`` closer to ``def``) wraps the
    ``fget`` accessor only.  Setters and deleters added afterwards via ``@value.setter`` /
    ``@value.deleter`` are *not* re-wrapped, so writes and deletes must be silent.

    The class is consumed by :mod:`tests.collections.api_deprecated.core` which subclasses it to attach the
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
    :mod:`tests.collections.api_deprecated.core` to register deprecated aliases (``color`` → ``colour``, ``txt`` →
    ``text``).
    The canonical attributes are mutable class attributes so read/write/delete tests exercise forwarding without
    instantiating the class.

    """

    colour: str = "red"
    text: str = "hello"
    size: int = 42


class PaletteEnum(Enum):
    """Enum with canonical member names for the ``attrs_mapping`` enum redirect test.

    Wrapped by ``DeprecatedAttrsPaletteEnum`` in :mod:`tests.collections.api_deprecated.core` to register a deprecated
    alias
    ``COLOR`` → ``COLOUR``.  Used to verify that proxy ``__getattr__`` redirect logic survives the enum metaclass.

    """

    COLOUR = "red"
    TEXT = "hello"


class PaletteOld:
    """Source class for H4 callable-target + attrs_mapping fixture.

    Has both a deprecated ``color`` attribute and canonical ``colour`` attribute.  Wrapped by ``deprecated_class`` in
    :mod:`tests.collections.api_deprecated.core` with ``target=Palette`` and ``attrs_mapping={"color": "colour"}``
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
    ``DeprecatedAttrsPaletteAllThree`` in :mod:`tests.collections.api_deprecated.core` to verify that ``attrs_mapping``
    and
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

    Wrapped by :mod:`tests.collections.api_deprecated.core` with ``read_only=True`` and
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

    Wrapped by :mod:`tests.collections.api_deprecated.core` as ``StackedAttrProxy`` (two-layer
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

    Wrapped by ``DepAutoExpandDC`` in :mod:`tests.collections.api_deprecated.core` with only
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
    kwarg.  Wrapped by ``DepAutoExpandInitFalseDC`` in :mod:`tests.collections.api_deprecated.core`.
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
    :mod:`tests.collections.api_deprecated.core`.
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
    ``TypeError``.  Wrapped by ``DepPositionalOnly`` in :mod:`tests.collections.api_deprecated.core`
    to verify the proxy emits ``UserWarning`` at decoration time and forwards the remapped
    value positionally at call time instead of crashing.
    """

    def __init__(self, new_val: int = 0, /) -> None:
        """Store positional-only arg."""
        self.new_val = new_val


class SelfOnlyPositionalOnlyTarget:
    """Target where ``self`` is the only POSITIONAL_ONLY parameter.

    Exercises the edge case where ``target_positional_only`` would be an empty
    frozenset if ``self`` were excluded — causing the split-dispatch gate to
    never fire and ``target_func(**{'self': instance})`` to raise ``TypeError``.
    Wrapped by ``OldSelfOnlyClass`` in :mod:`tests.collections.api_deprecated.core`.
    """

    def __init__(self, /) -> None:
        """Construct with no user arguments; self is explicitly positional-only."""


class RequiredPositionalOnlyTarget:
    """Target whose constructor declares a *required* POSITIONAL_ONLY parameter.

    ``new_val`` has no default, so the historical pop-and-``setattr`` fallback raised
    ``TypeError`` before the fallback loop ever ran.  Wrapped by
    ``DepRequiredPositionalOnly`` in :mod:`tests.collections.api_deprecated.core` to verify the
    proxy forwards the remapped value positionally to the constructor.
    """

    def __init__(self, new_val: int, /) -> None:
        """Store the required positional-only arg."""
        self.new_val = new_val


class ImmutablePositionalOnlyTarget:
    """Immutable target (``__setattr__`` raises) with a POSITIONAL_ONLY constructor param.

    Models frozen-dataclass-style targets: the historical ``setattr`` fallback raised on
    such instances even when the parameter had a default.  Wrapped by
    ``DepImmutablePositionalOnly`` in :mod:`tests.collections.api_deprecated.core`.
    """

    def __init__(self, new_val: int = 0, /) -> None:
        """Store the positional-only arg via ``object.__setattr__`` (self is immutable)."""
        object.__setattr__(self, "new_val", new_val)

    def __setattr__(self, name: str, value: object) -> None:
        """Reject all attribute assignment — mirrors ``dataclasses.FrozenInstanceError`` semantics."""
        raise AttributeError(f"`{type(self).__name__}` is immutable; cannot set `{name}`.")


class DerivedPositionalOnlyTarget:
    """Target whose constructor derives extra state from its POSITIONAL_ONLY param.

    The historical ``setattr`` fallback bypassed the constructor body (like skipping
    ``__post_init__``), leaving ``double`` stale.  Wrapped by ``DepDerivedPositionalOnly``
    in :mod:`tests.collections.api_deprecated.core` to verify constructor-side derivation runs.
    """

    def __init__(self, new_val: int = 0, /) -> None:
        """Store the positional-only arg and a value derived from it."""
        self.new_val = new_val
        self.double = new_val * 2


class MixedPositionalOnlyTarget:
    """Target with a leading POSITIONAL_ONLY param before the remapped one.

    Callers pass ``w`` positionally and the deprecated ``old_x`` kwarg for ``x`` —
    the proxy must interleave the caller's positional args with the remapped value.
    Wrapped by ``DepMixedPositionalOnly`` in :mod:`tests.collections.api_deprecated.core`.
    """

    def __init__(self, w: int, x: int = 0, /) -> None:
        """Store both positional-only args."""
        self.w = w
        self.x = x


def positional_only_target(x: int, /, y: int = 0) -> int:
    """Target with one POSITIONAL_ONLY param for function-decorator compat tests.

    ``x`` is positional-only so ``positional_only_target(x=5)`` raises ``TypeError``.
    Wrapped by ``deprecated_positional_only_source`` in :mod:`tests.collections.api_deprecated.core`
    to verify the function decorator emits ``UserWarning`` at decoration time and
    forwards calls correctly at call time.
    """
    return x + y


class SourcePathTarget:
    """Plain class whose proxies render ``%(source_path)s`` in a custom ``message_template``.

    ``new_attr`` is the replacement an ``attrs_mapping`` redirects to; ``gone_attr`` is a warn-only (``None``-mapped)
    attribute; ``new`` is the constructor keyword an ``args_mapping`` renames to.  Wrapped by the
    ``DeprSourcePath*`` proxies and ``depr_source_path_instance`` in :mod:`tests.collections.api_deprecated.core`.
    """

    new_attr = "new"
    gone_attr = "gone"

    def __init__(self, new: int = 0) -> None:
        """Store the constructor value."""
        self.new = new


class ModuleRaisingGetattributeMeta(type):
    """Metaclass whose ``__getattribute__`` raises on ``__module__``: a dynamic module lookup on its classes fails."""

    def __getattribute__(cls, name: str) -> Any:  # noqa: ANN401
        """Refuse ``__module__``; delegate every other attribute to ``type``."""
        if name == "__module__":
            raise RuntimeError("dynamic __module__ lookup refused")
        return super().__getattribute__(name)


class ModuleRaisingPropertyMeta(type):
    """Metaclass declaring ``__module__`` as a raising property, which shadows each class's own ``__module__``."""

    @property
    def __module__(cls) -> str:  # type: ignore[override]
        """Refuse the lookup — the class's own namespace still holds the real module name."""
        raise RuntimeError("dynamic __module__ lookup refused")


def make_module_raising_class(metaclass: type) -> type:
    """Build a fresh ``ModuleRaisingTarget`` class under *metaclass*, whose ``__module__`` lookup raises.

    Built on call, not at import, so module scans (audit, docs examples) never meet a class whose attribute lookup
    raises.  The class namespace records this module, which is the path a deprecation proxy over it should render.
    """
    return metaclass("ModuleRaisingTarget", (), {"__module__": __name__, "__doc__": "Class with a refusing metaclass."})


def every_parameter_kind_target(a: int, /, b: int = 2, *rest: int, c: int, d: int = 4, **extra: int) -> None:
    """Target declaring one parameter of every kind, two of them with defaults.

    ``a`` is positional-only, ``b`` positional-or-keyword, ``rest`` the ``*args`` tail, ``c`` and ``d``
    keyword-only and ``extra`` the ``**kwargs`` catch-all.  Read directly (not wrapped) by the
    :func:`deprecate.utils._signature_facts` tests to check that one signature read classifies every kind.
    """


async def async_positional_only_target(x: int, /, y: int = 0) -> int:
    """Async target with one POSITIONAL_ONLY param for async dispatch tests.

    ``x`` is positional-only. Wrapped by
    ``deprecated_async_positional_only_source`` in
    :mod:`tests.collections.api_deprecated.core`.
    """
    return x + y


def positional_only_two_params_target(a: int, b: int, /, c: int = 0) -> int:
    """Target with two POSITIONAL_ONLY params for ordering tests.

    Both ``a`` and ``b`` are positional-only. Wrapped by
    ``deprecated_positional_only_two_params_source`` in
    :mod:`tests.collections.api_deprecated.core`.
    """
    return a + b + c


def gapped_positional_only_target(a: int = 1, b: int = 2, /, c: int = 3) -> dict[str, int]:
    """Target whose POSITIONAL_ONLY params all carry defaults, for gap-misbinding tests.

    When a forwarding source supplies ``b`` but not ``a``, positional binding would slide
    ``b``'s value into ``a``'s slot; the split dispatch must raise ``TypeError`` instead of
    silently misbinding.  Wrapped by ``deprecated_gapped_positional_only_source`` (plus the
    async and full-prefix variants) in :mod:`tests.collections.api_deprecated.core`.
    """
    return {"a": a, "b": b, "c": c}


def var_positional_target(a: int, *extras: int) -> int:
    """Target accepting a var-positional tail, for surplus ``*args`` forwarding tests.

    Wrapped by ``deprecated_var_positional_forward`` in :mod:`tests.collections.api_deprecated.core` —
    the surplus positional tail of a deprecated ``*args`` source must reach ``extras``
    instead of being silently dropped.
    """
    return a + sum(extras)


async def async_var_positional_target(a: int, *extras: int) -> int:
    """Async twin of :func:`var_positional_target` for the async dispatch path.

    Wrapped by ``deprecated_async_var_positional_forward`` in :mod:`tests.collections.api_deprecated.core`.
    """
    return a + sum(extras)


def trio_positional_target(a: int, b: int = 0, c: int = 0) -> int:
    """Target with fixed positional slots (no ``*args``) that can absorb a surplus tail.

    Wrapped by ``deprecated_var_positional_to_fixed`` in :mod:`tests.collections.api_deprecated.core`;
    a two-element surplus tail from the source's ``*args`` must land in ``b`` and ``c``.
    """
    return a * 100 + b * 10 + c


def single_positional_target(a: int) -> int:
    """Target with a single positional slot and no ``*args``, for surplus-overflow tests.

    Wrapped by ``deprecated_var_positional_overflow`` in :mod:`tests.collections.api_deprecated.core`;
    forwarding a surplus tail that cannot fit must raise a curated ``TypeError`` instead of
    silently dropping the extra values.
    """
    return a


class SelfDeprecatedModel:
    """Target for two-decorator no-target self-deprecation stacking tests.

    Carries ``cuda`` (warn-only deprecated attribute, still served from the class),
    ``device`` (replacement for the deprecated ``gpu`` alias), and accepts
    ``num_layers`` in its constructor (renamed from ``n_layers`` in the outer layer).
    Wrapped by ``DepSelfCombinedTwoLayer`` in :mod:`tests.collections.api_deprecated.core` with two
    stacked ``deprecated_class()`` calls — neither forwarding to a different class.
    """

    cuda: bool = False
    device: str = "cpu"

    def __init__(self, num_layers: int = 4) -> None:
        """Construct SelfDeprecatedModel."""
        self.num_layers = num_layers


def fib_recursive(n: int) -> int:
    """Recursive Fibonacci implementation, the target of ``dep_fib_callable``.

    Wrapped in :mod:`tests.collections.api_deprecated.core`.

    Recurses on itself directly (not through any deprecated wrapper) so ``dep_fib_callable``
    only triggers the deprecation wrapper on the initial call — never on recursive steps.
    """
    if n <= 1:
        return n
    return fib_recursive(n - 1) + fib_recursive(n - 2)


def non_cycle_double(x: int) -> int:
    """Target for non-cycling callable-target tests — doubles input.

    Wrapped by ``dep_non_cycle_old_fn`` in :mod:`tests.collections.api_deprecated.core`.
    """
    return x * 2


async def async_non_cycle_double(x: int) -> int:
    """Async target for concurrent async non-cycle tests — doubles input.

    Wrapped by ``dep_async_non_cycle_old_fn`` in :mod:`tests.collections.api_deprecated.core`.
    """
    return x * 2


async def async_suspending_double(x: int) -> int:
    """Async target that yields to the event loop once before doubling its input.

    Wrapped by ``dep_async_suspending_old_fn`` in :mod:`tests.collections.api_deprecated.forwarding`.  A guarded call
    that stays in flight across a task switch lets a sibling task observe it, so the switch must really happen.
    """
    await asyncio.sleep(0)
    return x * 2


def var_positional_remap_body(a: int, *extras: int, old_kwarg: str = "", new_kwarg: str = "") -> tuple:
    """Source body for ARGS_REMAP test with ``*args`` — receives remapped ``new_kwarg`` from caller's ``old_kwarg``.

    Wrapped by ``deprecated_var_positional_remap`` in :mod:`tests.collections.api_deprecated.core`.
    """
    return (a, *extras, new_kwarg)


class HostileSignatureCallable:
    """Callable source whose ``__signature__`` raises ``RuntimeError`` when read.

    Wrapped objects are not always well-behaved: mocks, lazily built extension wrappers, and some model
    metaclasses expose a ``__signature__`` descriptor that raises on access, and ``inspect.signature``
    forwards that exception verbatim instead of normalising it to ``ValueError``/``TypeError``.  Wrapped by
    ``make_deprecated_hostile_signature_instance`` in :mod:`tests.collections.api_deprecated.core` to prove that a
    hostile introspection surface never turns into a decoration-time crash.
    """

    def __call__(self) -> str:
        """Return a fixed marker; exists so ``inspect.signature`` gets past its ``callable()`` guard."""
        return "called"

    @property
    def __signature__(self) -> Any:  # noqa: ANN401
        """Always raise ``RuntimeError`` — stands in for any descriptor that cannot be introspected."""
        raise RuntimeError("`__signature__` is deliberately unreadable")


# ========== Characterization fixtures: call shapes and warning locations ==========

# Sources whose runtime call shape differs from what ``inspect.signature`` reports, plus callable objects
# without a usable ``__name__`` and a side-effect-counting container.  Wrapped in
# :mod:`tests.collections.api_deprecated.core` and exercised by ``tests/integration/test_call_transparency.py`` and
# ``tests/integration/test_warning_locations.py``, which pin today's behaviour before internal refactors.


@functools.singledispatch
def area_dispatch(shape: object) -> str:
    """Describe a shape by its type — generic fallback of a ``functools.singledispatch`` source.

    ``singledispatch`` dispatches on the type of the first *positional* argument and raises ``TypeError`` when
    called with keywords only, so a wrapper that rewrites positionals into keywords breaks it.
    """
    return f"generic:{shape}"


@area_dispatch.register
def _area_dispatch_int(shape: int) -> str:
    """Describe a square by its integer side length."""
    return f"int:{shape}"


@area_dispatch.register
def _area_dispatch_str(shape: str) -> str:
    """Describe a shape by its name."""
    return f"str:{shape}"


def raw_signature_patched(*args: object, **kwargs: object) -> tuple[tuple[object, ...], dict[str, object]]:
    """Return the raw ``(args, kwargs)`` it received while ``__signature__`` advertises a single ``x`` parameter.

    Mirrors C-accelerated wrappers and argument-clinic shims whose real call shape (``*args, **kwargs``) differs
    from the signature they report, so the observed return value shows exactly how a wrapper forwarded the call.
    """
    return args, kwargs


raw_signature_patched.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
    [inspect.Parameter("x", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
)


def kwargs_rejecting_inner(func: Callable[..., int]) -> Callable[..., int]:
    """Wrap *func* in a ``functools.wraps`` forwarder whose inner body rejects keyword arguments.

    ``inspect.signature`` follows ``__wrapped__`` and reports *func*'s named parameters, but the forwarder itself
    only passes positionals through — the common shape of hand-written decorator layers in user code.
    """

    @functools.wraps(func)
    def inner(*args: object, **kwargs: object) -> int:
        if kwargs:
            raise TypeError(f"{func.__name__}() forwarder accepts positional arguments only, got {sorted(kwargs)}")
        return func(*args)

    return inner


@kwargs_rejecting_inner
def wraps_chain_outer(value: int) -> int:
    """Double *value* behind a positional-only ``functools.wraps`` forwarder (see :func:`kwargs_rejecting_inner`)."""
    return value * 2


def new_scale(x: int, y: int = 1) -> int:
    """Scale *x* by *y* — replacement callable bound through :data:`partial_target`."""
    return x * y


#: ``functools.partial`` replacement target; a partial object has no ``__name__`` of its own.
partial_target = functools.partial(new_scale, y=5)


class CallableAdder:
    """Callable object adding a fixed offset — an instance target that has no ``__name__`` of its own."""

    def __init__(self, offset: int = 1) -> None:
        """Store the offset added on every call."""
        self.offset = offset

    def __call__(self, x: int) -> int:
        """Return ``x`` plus the stored offset."""
        return x + self.offset


#: Callable-instance replacement target (instances of :class:`CallableAdder` carry no ``__name__``).
callable_adder_target = CallableAdder(offset=10)


def _build_nested_partial_target() -> "functools.partial[int]":
    """Return ``partial(partial(new_scale, y=5))`` with the inner layer kept intact.

    CPython flattens ``partial(partial(f, ...))`` into one layer unless the inner object carries an instance
    attribute, so the inner partial gets one; the result is a genuine two-level chain whose bound keyword ``y``
    lives on the inner layer only.
    """
    inner = functools.partial(new_scale, y=5)
    inner.__dict__["origin"] = "nested"
    return functools.partial(inner)


#: Two-level ``functools.partial`` chain over :func:`new_scale` binding ``y=5`` on the inner layer.
nested_partial_target = _build_nested_partial_target()

#: ``functools.partial`` over :class:`NewCls` binding ``d`` — a class replacement with a preset constructor keyword.
partial_new_cls_target = functools.partial(NewCls, d="preset")


class NonStringNameCallable:
    """Callable whose ``__name__`` is a property returning an ``int`` rather than a string.

    A warning renderer must not trust ``__name__`` blindly: here it exists but is not a name, so the display falls
    back to the type name.
    """

    @property
    def __name__(self) -> int:
        """Return a non-string value where a name is expected."""
        return 42

    def __call__(self, x: int) -> int:
        """Return *x* unchanged."""
        return x


#: Async replacement wrapped in ``functools.partial`` — awaitable target without a ``__name__`` of its own.
partial_async_target = functools.partial(async_target)


def moduleless_function(x: int) -> int:
    """Return *x* unchanged — its ``__module__`` is cleared below, as for code built with ``exec`` or ``types``."""
    return x


moduleless_function.__module__ = None  # type: ignore[assignment]

#: ``functools`` wrapper kinds that are not functions: each lacks a function body ``@deprecated`` could wrap.
partial_of_double = functools.partial(double_value)
partialmethod_of_double = functools.partialmethod(double_value)
singledispatchmethod_of_double = functools.singledispatchmethod(double_value)
property_of_partial = property(partial_of_double)


class SideEffectBox:
    """Container counting every ``__iadd__``, ``__enter__`` and ``__next__`` call; ``fail=True`` makes them raise.

    Wrapped by a ``deprecated_instance`` proxy, the counters show how often the proxy actually invoked each operation
    on the wrapped object, and the failure flag lets a test observe what the proxy does when the operation raises.
    """

    def __init__(self, items: tuple[int, ...] = (1, 2), fail: bool = False) -> None:
        """Store the items to iterate over and whether every counted operation should raise."""
        self.items = list(items)
        self.fail = fail
        self.calls: dict[str, int] = {"__iadd__": 0, "__enter__": 0, "__next__": 0}
        self._cursor = 0

    def _record(self, operation: str) -> None:
        """Count *operation* and raise ``RuntimeError`` when the box was built with ``fail=True``."""
        self.calls[operation] += 1
        if self.fail:
            raise RuntimeError(f"SideEffectBox.{operation} failed on purpose")

    def __iadd__(self, other: int) -> "SideEffectBox":
        """Append *other* in place."""
        self._record("__iadd__")
        self.items.append(other)
        return self

    def __enter__(self) -> "SideEffectBox":
        """Enter the context and return the box itself."""
        self._record("__enter__")
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Leave the context without suppressing exceptions."""

    def __iter__(self) -> "SideEffectBox":
        """Restart iteration over the stored items."""
        self._cursor = 0
        return self

    def __next__(self) -> int:
        """Return the next stored item."""
        self._record("__next__")
        if self._cursor >= len(self.items):
            raise StopIteration
        self._cursor += 1
        return self.items[self._cursor - 1]


class FlakyProtocolBox(SideEffectBox):
    """``SideEffectBox`` that also counts the pure, async and call protocols a deprecation proxy forwards.

    Subscript, iteration, negation, ``round``, ``__aiter__``, ``async with`` entry, ``__anext__``, ``await`` and calling
    each record themselves in ``calls`` and raise ``RuntimeError`` while ``fail`` is set — the same contract as the
    inherited ``__iadd__``/``__enter__``/``__next__``. A test can therefore show whether a failed operation consumed the
    proxy's warn budget, and whether a fatal deprecation reached the wrapped object at all.
    """

    def __init__(self, items: tuple[int, ...] = (1, 2), fail: bool = False) -> None:
        """Store the items and extend the call counters with every additionally recorded protocol."""
        super().__init__(items, fail)
        recorded = ("__getitem__", "__iter__", "__neg__", "__round__", "__aiter__", "__aenter__", "__anext__")
        self.calls.update(dict.fromkeys((*recorded, "__await__", "__call__"), 0))

    def __getitem__(self, index: int) -> int:
        """Return the stored item at *index*."""
        self._record("__getitem__")
        return self.items[index]

    def __iter__(self) -> "FlakyProtocolBox":
        """Restart iteration over the stored items."""
        self._record("__iter__")
        self._cursor = 0
        return self

    def __neg__(self) -> int:
        """Return the negated sum of the stored items."""
        self._record("__neg__")
        return -sum(self.items)

    def __round__(self, ndigits: Optional[int] = None) -> int:
        """Return the sum of the stored items, which is already a whole number."""
        self._record("__round__")
        return sum(self.items)

    def __aiter__(self) -> "FlakyProtocolBox":
        """Restart async iteration over the stored items."""
        self._record("__aiter__")
        self._cursor = 0
        return self

    async def __aenter__(self) -> "FlakyProtocolBox":
        """Enter the async context and return the box itself."""
        self._record("__aenter__")
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Leave the async context without suppressing exceptions."""

    def __anext__(self) -> Coroutine[Any, Any, int]:
        """Record the step synchronously, then return the awaitable that produces the next stored item."""
        self._record("__anext__")
        return self._next_item()

    async def _next_item(self) -> int:
        """Produce the next stored item, ending async iteration once the items run out."""
        if self._cursor >= len(self.items):
            raise StopAsyncIteration
        self._cursor += 1
        return self.items[self._cursor - 1]

    def __await__(self) -> Generator[Any, None, list[int]]:
        """Record the await synchronously, then delegate to a coroutine that returns the stored items."""
        self._record("__await__")
        return self._snapshot().__await__()

    async def _snapshot(self) -> list[int]:
        """Return a copy of the stored items."""
        return list(self.items)

    def __call__(self) -> int:
        """Return how many items the box holds."""
        self._record("__call__")
        return len(self.items)


class DecliningInplaceOperand:
    """Number-like operand whose ``__iadd__`` declines with ``NotImplemented`` so Python falls back to ``__add__``.

    A deprecation proxy forwarding ``proxy += 1`` first tries the in-place hook; when that declines, Python retries the
    plain ``__add__`` on the proxy. The proxy must warn once for the whole statement, not once per attempted hook.
    """

    def __init__(self, value: int) -> None:
        """Store the wrapped integer value."""
        self.value = value

    def __iadd__(self, other: int) -> Any:  # noqa: ANN401
        """Decline the in-place form so the interpreter falls back to ``__add__``."""
        return NotImplemented

    def __add__(self, other: int) -> "DecliningInplaceOperand":
        """Return a new operand holding the sum."""
        return DecliningInplaceOperand(self.value + other)


class _FlakyClassAttribute:
    """Class-level descriptor returning ``3``; counts reads on its owner and raises while the ``fail`` flag is set."""

    def __get__(self, instance: Optional[object], owner: "type[FlakyAttrHost]") -> int:
        """Count the read on the owning class, then return the value, or raise when that class is flagged to fail."""
        owner.reads += 1
        if owner.fail:
            raise RuntimeError("FlakyAttrHost.total failed on purpose")
        return 3


class FlakyAttrHost:
    """Class whose ``total`` attribute raises on read while the class-level ``fail`` flag is set.

    Wrapped by ``deprecated_class(attrs_mapping={"legacy_total": "total"})``, it lets a test make the redirected lookup
    fail and then succeed under the same deprecated name, which is what the per-name warn budget is keyed on. The
    class-level ``reads`` counter records every execution of the ``total`` getter, so a test can show it never ran.
    Build a fresh subclass per test with :func:`make_flaky_attr_host` so the flag and counter never leak between tests.
    """

    fail: ClassVar[bool] = False
    reads: ClassVar[int] = 0
    total = _FlakyClassAttribute()


def make_flaky_attr_host() -> "type[FlakyAttrHost]":
    """Return a fresh ``FlakyAttrHost`` subclass with its own ``fail`` flag and ``reads`` counter (``False``, ``0``)."""
    return type("FlakyAttrHost", (FlakyAttrHost,), {"fail": False, "reads": 0})


# ========== Plain function stored as a class attribute: the proxy binds like a method ==========


def legacy_host_scale(host: Any, factor: int) -> tuple[Any, int]:  # noqa: ANN401
    """Return the receiving *host* together with *factor*, so a test sees which object the call was bound to.

    A host class stores this function as a method; wrapped in ``deprecated_instance`` it must still receive the
    instance as its first argument when called through an instance, exactly as the undeprecated function would.
    """
    return host, factor


# ========== Module subclasses, skip conditions and introspection-hostile callables ==========

# Stand-ins for ``stream=`` callables live in :mod:`tests.collections.streams`.


class LocCustomModuleType(types.ModuleType):
    """Module subclass standing in for a lazy loader's module class, which ``deprecated_module`` overwrites."""


class _SlottedModuleType(types.ModuleType):
    """Custom module subclass with an incompatible ``__slots__`` layout.

    Stands in for a memory-optimized module shim; ``deprecated_module`` cannot swap ``__class__`` on its instances.
    """

    __slots__ = ("extra_slot",)


class CountingSkipCondition:
    """``skip_if`` callable that counts how often a proxy consults it and answers from a scripted sequence.

    A feature flag read from configuration or the environment is the usual ``skip_if``: it may be slow, and its answer
    may change between two reads. ``answers`` lists the replies in call order; the last one repeats once the list is
    used up, so ``(False,)`` never skips and ``(False, True)`` is live for the first read only.
    """

    def __init__(self, answers: tuple[bool, ...] = (False,)) -> None:
        """Store the scripted replies and start the call counter at zero."""
        self.answers = answers
        self.calls = 0

    def __call__(self) -> bool:
        """Return the next scripted reply and count the call."""
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return answer


class OpaqueSignatureCallable:
    """Named callable whose ``__signature__`` raises ``ValueError``, like a C-level callable without metadata.

    A maintainer forwards a deprecated function to such a callable, or decorates one directly. pyDeprecate reads every
    signature when it decorates; an unreadable source must still fail there, and an unreadable target must still fail
    only on the forwarded call that needs its signature.
    """

    __name__ = "opaque_callable"

    @property
    def __signature__(self) -> Any:  # noqa: ANN401
        """Always raise ``ValueError``, as ``inspect.signature`` does for an unsupported builtin."""
        raise ValueError("no signature found for opaque_callable")

    def __call__(self, *args: Any, **kwargs: Any) -> tuple[tuple[Any, ...], dict[str, Any]]:  # noqa: ANN401
        """Return the call's arguments, so a test can see what reached the callable."""
        return args, kwargs


class SignatureNotBuiltError(ValueError):
    """``ValueError`` subclass whose constructor takes more than the message, so it cannot be rebuilt from ``args``."""

    def __init__(self, name: str, reason: str) -> None:
        """Build the message from the callable's name and the reason its signature is not available."""
        super().__init__(f"{name}: {reason}")
        self.reason = reason


class SignatureNeedsContextError(ValueError):
    """``ValueError`` subclass that can be neither copied nor rebuilt, because its ``__new__`` demands the arguments."""

    def __new__(cls, name: str, reason: str) -> "SignatureNeedsContextError":
        """Require the callable's name and the reason, so ``cls()`` and ``cls(*args)`` both raise ``TypeError``."""
        return super().__new__(cls, f"{name}: {reason}")

    def __init__(self, name: str, reason: str) -> None:
        """Build the message from the callable's name and the reason its signature is not available."""
        super().__init__(f"{name}: {reason}")


class LazySignatureCallable(OpaqueSignatureCallable):
    """Named callable whose ``__signature__`` raises :class:`SignatureNotBuiltError` while the signature is not built.

    Frameworks that build signatures lazily raise their own error type until the signature exists. A deprecated
    function forwarding to such a callable must surface that error on the forwarded call, with its own type.
    """

    __name__ = "lazy_callable"

    @property
    def __signature__(self) -> Any:  # noqa: ANN401
        """Always raise :class:`SignatureNotBuiltError`."""
        raise SignatureNotBuiltError("lazy_callable", "not built yet")


class ContextCapturingLookup:
    """Capture a redirect's context before returning an answer or failing its first lookup."""

    def __init__(self, fail_first: bool) -> None:
        """Configure the first lookup and retain every context captured by the target."""
        self.fail_first = fail_first
        self.contexts: list[contextvars.Context] = []

    def __call__(self, name: str) -> int:
        """Capture the in-flight redirect context and resolve the requested answer."""
        self.contexts.append(contextvars.copy_context())
        if name != "answer" or (self.fail_first and len(self.contexts) == 1):
            raise AttributeError(name)
        return 42


class InheritedContextLookup:
    """Module ``__getattr__`` that answers a name by looking it up again from a thread started in a copied context.

    Free-threaded CPython 3.14 starts every new thread with a copy of the creating thread's context. A module
    attribute resolved through a redirect may hand work to such a thread, which then makes its own lookup of the same
    name; that nested lookup answers ``7``. The outer call returns what the worker got (or re-raises its error).
    """

    def __init__(self, source: types.ModuleType) -> None:
        """Remember the deprecated module the worker looks the name up on."""
        self.source = source
        self.depth = 0

    def __call__(self, name: str) -> Any:  # noqa: ANN401
        """Resolve *name* from a worker thread running in a copy of the current context."""
        if self.depth:
            return 7
        self.depth += 1
        outcome: list[Any] = []
        context = contextvars.copy_context()
        worker = threading.Thread(target=self._lookup, args=(context, name, outcome))
        worker.start()
        worker.join(timeout=10)
        self.depth -= 1
        if isinstance(outcome[0], Exception):
            raise outcome[0]
        return outcome[0]

    def _lookup(self, context: contextvars.Context, name: str, outcome: list[Any]) -> None:
        """Look *name* up on the source module inside *context*, recording the value or the ``AttributeError``."""
        try:
            outcome.append(context.run(getattr, self.source, name))
        except AttributeError as err:
            outcome.append(err)


class DoclessTarget:  # noqa: D101 - intentionally lacks documentation for proxy docstring preservation
    pass


def zero_argument_target() -> int:
    """Return a fixed value without accepting any forwarded arguments."""
    return 7


class InPlaceAliasSource:
    """Class that keeps its own module-level name while a deprecated alias of it is published elsewhere."""

    def __init__(self, level: int) -> None:
        """Store the sample level."""
        self.level = level

    def __eq__(self, other: object) -> bool:
        """Compare by exact type and level, so a round trip that changed the class does not compare equal."""
        return type(other) is type(self) and vars(other) == vars(self)

    __hash__ = None  # type: ignore[assignment]
