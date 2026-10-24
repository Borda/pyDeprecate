"""Deprecated wrappers and factories for dispatch, fatal errors and behavior regressions."""

import logging
from dataclasses import dataclass
from enum import Enum
from functools import cached_property, partial
from types import SimpleNamespace
from typing import Any, Callable, Optional
from warnings import catch_warnings, simplefilter

from deprecate import TargetMode, deprecated, deprecated_callable, deprecated_class, deprecated_instance, void
from deprecate.proxy import _DeprecatedProxy, _ProxySpec
from tests.collection_deprecate import (
    _DEPRS_CASE_FATAL_ARGS,
    _DEPRS_CASE_STD_ARGS,
    _DEPRS_CASE_STD_INF_ARGS,
    original_sum,
)
from tests.collection_targets import (
    CountingSkipCondition,
    DoclessTarget,
    HostileSignatureCallable,
    LazySignatureCallable,
    NewCls,
    NonStringNameCallable,
    OpaqueSignatureCallable,
    Palette,
    PaletteOld,
    SourcePathTarget,
    area_dispatch,
    async_target,
    base_sum_kwargs,
    callable_adder_target,
    double_value,
    identity_value,
    legacy_host_scale,
    make_module_raising_class,
    nested_partial_target,
    partial_async_target,
    partial_new_cls_target,
    partial_target,
    positional_only_target,
    raw_signature_patched,
    stream_wraps_warn_forwarding_stacklevel,
    wraps_chain_outer,
    zero_argument_target,
)

# ========== deprecated_callable — strict callable-only alias fixtures ==========

# `deprecated_callable` is the strict form of `deprecated`: identical behaviour on callables
# (functions, methods, descriptors), but a class source is rejected at decoration time instead
# of being delegated to `deprecated_class`. The two function fixtures below share the same target
# and version kwargs so tests can assert the alias forwards and records metadata identically to
# `deprecated`; the classmethod fixture exercises the descriptor path (extra wrapper frame threaded
# through `_packing_descriptor`).


@deprecated_callable(target=double_value, **_DEPRS_CASE_STD_INF_ARGS)
def deprecated_callable_double(x: int) -> int:
    """`@deprecated_callable` on a function — parity twin of ``deprecated_double_twin``."""
    return void(x)


@deprecated(target=double_value, **_DEPRS_CASE_STD_INF_ARGS)
def deprecated_double_twin(x: int) -> int:
    """`@deprecated` twin with identical config — the parity baseline for the alias."""
    return void(x)


class HolderWithDeprecatedCallableMethod:
    """Class whose classmethod is deprecated via the strict alias (descriptor-path smoke).

    Warn-only (default ``TargetMode.NOTIFY``) so the descriptor path is exercised without the
    ``cls``-forwarding signature concerns of a callable target — the body executes and returns.
    """

    @deprecated_callable(**_DEPRS_CASE_STD_INF_ARGS)
    @classmethod
    def old_double(cls, x: int) -> int:
        """Deprecated classmethod (warn-only) — body executes and doubles the input."""
        return x * 2


def make_deprecated_callable_on_class() -> type:
    """Apply ``@deprecated_callable`` to a class — raises ``TypeError`` at decoration time.

    The strict alias rejects a class source up front rather than delegating to ``deprecated_class``.
    Because the decoration raises on execution, it cannot exist as a module-level wrapper; this
    factory defers it so the assertion site controls when it fires.
    """

    @deprecated_callable(**_DEPRS_CASE_STD_ARGS)
    class RejectedClass:
        """Source class — never actually wrapped; decoration raises first."""

    return RejectedClass


# ========== Phase 2 — ``deprecated()`` dispatcher: class-path first-class support ==========
#
# `@deprecated` on a class is no longer the "will become a TypeError" wart from v0.6.0 — it now
# dispatches to `deprecated_class` and emits a one-time informational `UserWarning` instead
# (removed in v1.0). Every factory below defers decoration to the call site so tests can control
# when the informational warning is captured, mirroring the forwarding collection's class-target factory.


def make_deprecated_on_fresh_class() -> type:
    """Apply ``@deprecated`` to a brand-new plain class — one-time informational ``UserWarning`` fixture.

    Every call executes the same ``class`` statement (same qualname, fresh object each time), so
    calling this factory twice lets a test prove the informational warning fires on the first
    application and is suppressed on the second — "one-time" per class, not a spam-per-loop guard.

    """

    @deprecated(deprecated_in="1.0", remove_in="2.0")
    class _DispatchedFreshClass:
        """Plain class source deprecated via the ``@deprecated`` front door (class-path dispatch)."""

        def __init__(self, value: int = 0) -> None:
            """Initialize with an optional value."""
            self.value = value

    return _DispatchedFreshClass


def make_deprecated_on_fresh_class_silent() -> type:
    """Apply ``@deprecated(stream=None)`` to a fresh class — informational ``UserWarning`` suppressed."""

    @deprecated(stream=None, deprecated_in="1.0", remove_in="2.0")
    class _DispatchedFreshClassSilent:
        """Plain class source deprecated via ``@deprecated`` with ``stream=None``."""

    return _DispatchedFreshClassSilent


def make_deprecated_on_fresh_enum_class() -> type:
    """Apply ``@deprecated`` directly to an Enum class — class-path routing via the dispatcher."""

    @deprecated(deprecated_in="1.0", remove_in="2.0", stream=None)
    class _DispatchedEnum(Enum):
        """Enum source deprecated via the ``@deprecated`` dispatcher front door."""

        ALPHA = "alpha"

    return _DispatchedEnum


def make_deprecated_on_fresh_dataclass() -> type:
    """Apply ``@deprecated`` directly to a dataclass — class-path routing via the dispatcher."""

    @deprecated(deprecated_in="1.0", remove_in="2.0", stream=None)
    @dataclass
    class _DispatchedDataClass:
        """Dataclass source deprecated via the ``@deprecated`` dispatcher front door."""

        label: str = "x"

    return _DispatchedDataClass


def make_deprecated_with_attrs_mapping_kwarg() -> Any:  # noqa: ANN401
    """Call ``deprecated(attrs_mapping=...)`` — the front door no longer accepts class-only knobs.

    ``deprecated()`` exposes only the arguments common to ``deprecated_callable`` and ``deprecated_class``;
    ``attrs_mapping`` is class-only and lives on ``deprecated_class`` alone, so the factory call itself must
    raise ``TypeError`` (unexpected keyword argument) before any source is decorated.

    """
    # Intentionally invalid kwarg — the runtime TypeError is the behaviour under test.
    return deprecated(attrs_mapping={"color": "colour"}, **_DEPRS_CASE_STD_ARGS)  # type: ignore[call-arg]


def make_deprecated_attrs_remap_target_on_class() -> Any:  # noqa: ANN401
    """Apply ``@deprecated(target=TargetMode.ATTRS_REMAP)`` to a class — infeasible on the front door.

    ``ATTRS_REMAP`` renames only the attribute names listed in ``attrs_mapping``, but the ``@deprecated``
    front door does not expose ``attrs_mapping`` (it is class-only, on ``deprecated_class``). A developer who
    reaches for the mode on the friendly front door must get a loud ``TypeError`` at decoration time pointing
    to ``deprecated_class(attrs_mapping=...)`` — not a silently built proxy that can never redirect anything.

    """
    return deprecated(target=TargetMode.ATTRS_REMAP, **_DEPRS_CASE_STD_ARGS)(PaletteOld)


def make_deprecated_front_door_skip_if_true_on_class() -> Any:  # noqa: ANN401
    """Apply ``@deprecated(target=..., skip_if=True)`` to a class — skip_if forwards onto the proxy path.

    ``skip_if`` is common to both dispatch shapes, so the front door must forward it to ``deprecated_class``:
    with the condition ``True`` the proxy serves the wrapped source class silently — instantiation returns a
    source instance with no warning and no target forwarding.

    """
    with catch_warnings():
        simplefilter("ignore", UserWarning)  # suppress the one-time informational class-dispatch notice
        return deprecated(target=Palette, skip_if=True, **_DEPRS_CASE_STD_ARGS)(PaletteOld)


def make_deprecated_class_skip_if_true() -> Any:  # noqa: ANN401
    """Wrap ``PaletteOld`` with ``deprecated_class(target=Palette, skip_if=True)`` — machinery inactive.

    With the skip condition statically ``True`` the proxy transparently serves the wrapped source class:
    attribute reads resolve on ``PaletteOld`` (not the ``Palette`` target) and emit no warning.

    """
    return deprecated_class(target=Palette, skip_if=True, **_DEPRS_CASE_STD_ARGS)(PaletteOld)


def make_deprecated_class_skip_if_flag() -> tuple[Any, dict[str, bool]]:
    """Wrap ``PaletteOld`` with a flag-driven ``skip_if`` callable so tests can flip the condition at runtime.

    Returns the proxy together with the mutable flag dict (``{"skip": True}``) the ``skip_if`` lambda reads —
    tests flip ``flag["skip"]`` to verify the proxy re-evaluates the condition per access.

    """
    flag = {"skip": True}
    proxy = deprecated_class(target=Palette, skip_if=lambda: flag["skip"], **_DEPRS_CASE_STD_ARGS)(PaletteOld)
    return proxy, flag


def make_deprecated_class_attrs_skip_if_true() -> Any:  # noqa: ANN401
    """Wrap ``PaletteOld`` with ``attrs_mapping`` plus ``skip_if=True`` — the redirect is inactive under skip.

    Under an active skip condition the deprecated alias ``color`` must read the source attribute directly
    (no redirect to ``colour``, no warning) — the proxy behaves as if the class were undecorated.

    """
    return deprecated_class(attrs_mapping={"color": "colour"}, skip_if=True, **_DEPRS_CASE_STD_ARGS)(PaletteOld)


def make_deprecated_class_skip_if_non_bool() -> Any:  # noqa: ANN401
    """Wrap ``Palette`` with a ``skip_if`` callable returning a non-bool — access must raise ``TypeError``.

    Mirrors the callable-decorator contract: the skip condition must return a strict ``bool``; a truthy int
    is rejected at access time with ``TypeError("User function 'skip_if' shall return bool, ...")``.

    """
    # Intentionally wrong return type — the runtime TypeError is the behaviour under test.
    return deprecated_class(skip_if=lambda: 42, **_DEPRS_CASE_STD_ARGS)(Palette)  # type: ignore[arg-type,return-value]


def make_deprecated_instance_skip_if_true_read_only() -> Any:  # noqa: ANN401
    """Wrap a list with ``deprecated_instance(skip_if=True, read_only=True)`` — skip bypasses the guard.

    With the skip condition ``True`` the proxy serves the wrapped list directly: standard mutators such as
    ``append`` must succeed without warning even though ``read_only=True`` would normally block them.

    """
    return deprecated_instance([1, 2], name="legacy_list", skip_if=True, read_only=True, **_DEPRS_CASE_STD_ARGS)


def make_deprecated_instance_over_marked_object() -> Any:  # noqa: ANN401
    """Wrap an object that carries its own ``__deprecated__`` marker in a fresh ``deprecated_instance`` proxy.

    The proxy keeps its own ``__deprecated__`` as metadata; the wrapped object's marker must never leak through it —
    not after the proxy's marker is deleted, and not through copies. Built fresh per call so tests can mutate it.

    """
    marked = SimpleNamespace(__deprecated__="wrapped object's own marker")
    return deprecated_instance(marked, name="legacy_settings", stream=None, **_DEPRS_CASE_STD_ARGS)


def make_deprecated_on_fresh_function_warn_only() -> Any:  # noqa: ANN401
    """Apply bare ``@deprecated`` (no target, no mapping) to a function — AUTO resolves to ``NOTIFY``.

    The stored ``__deprecated__.target`` must record the resolved mode, never the ``TargetMode.AUTO``
    decoration-time sentinel.

    """

    @deprecated(**_DEPRS_CASE_STD_ARGS)
    def _warn_only_fn(x: int = 0) -> int:
        """Source function — warn-only, body executes unchanged."""
        return x

    return _warn_only_fn


def make_deprecated_auto_args_mapping_on_function() -> Any:  # noqa: ANN401
    """Apply ``@deprecated(args_mapping=...)`` with NO ``target`` to a function — AUTO infers ``ARGS_REMAP``.

    The front door's ``TargetMode.AUTO`` default infers the mode from the configuration: a mapping present
    selects per-argument deprecation without the caller spelling out ``TargetMode.ARGS_REMAP`` — the handy
    zero-ceremony form. No misconfig warning may fire and the old keyword must warn-and-remap at call time.

    """

    @deprecated(**_DEPRS_CASE_STD_ARGS, args_mapping={"old_c": "c"})
    def _auto_remap_fn(c: float = 1.0) -> float:
        """Source function — body runs with remapped kwargs (self-deprecation)."""
        return c * 2

    return _auto_remap_fn


def make_deprecated_explicit_auto_on_function() -> Any:  # noqa: ANN401
    """Apply ``@deprecated(target=TargetMode.AUTO, args_mapping=...)`` — explicit AUTO equals omitted.

    Passing ``TargetMode.AUTO`` explicitly to the front door is documented as identical to omitting
    ``target``: the mapping still selects ``ARGS_REMAP`` and no misconfig warning fires.

    """

    @deprecated(target=TargetMode.AUTO, **_DEPRS_CASE_STD_ARGS, args_mapping={"old_c": "c"})
    def _explicit_auto_fn(c: float = 1.0) -> float:
        """Source function — body runs with remapped kwargs (self-deprecation)."""
        return c * 2

    return _explicit_auto_fn


def make_deprecated_with_message_template_on_class() -> Any:  # noqa: ANN401
    """Apply ``@deprecated(message_template=...)`` to a class — the dispatcher forwards the template to the proxy.

    ``message_template`` is common to both dispatch shapes, so the front door must forward it onto
    ``deprecated_class`` — proxy access warnings must render the custom template exactly as a direct
    ``deprecated_class(message_template=...)`` call would (the dispatcher used to drop it silently).

    """
    with catch_warnings():
        simplefilter("ignore", UserWarning)  # suppress the one-time informational class-dispatch notice
        return deprecated(message_template="Custom notice for `%(source_name)s`.", **_DEPRS_CASE_STD_ARGS)(Palette)


def make_deprecated_with_args_mapping_on_class_default_target() -> Any:  # noqa: ANN401
    """Apply ``@deprecated(args_mapping=...)`` to a class with NO explicit ``target=``.

    The dispatcher's own default is ``target=TargetMode.NOTIFY``; passing
    ``args_mapping`` alongside an omitted target must auto-resolve NOTIFY to ``TargetMode.ARGS_REMAP`` —
    the same self-deprecation auto-resolve ``deprecated_class(args_mapping=...)`` has always applied for
    an omitted target — reached here through the friendly ``@deprecated`` front door instead.

    """
    with catch_warnings():
        simplefilter("ignore", UserWarning)  # suppress the one-time informational class-dispatch notice
        return deprecated(args_mapping={"old_c": "c"}, **_DEPRS_CASE_STD_ARGS)(NewCls)


def make_deprecated_on_non_callable_source() -> Any:  # noqa: ANN401
    """Apply ``@deprecated`` to a plain object — raises ``TypeError`` naming ``deprecated_instance``."""
    # Intentionally passing a non-callable source to prove the dispatcher's runtime TypeError guard fires.
    return deprecated(**_DEPRS_CASE_STD_ARGS)(object())  # type: ignore[type-var]


class _CallableWithoutName:
    """Callable object with no ``__name__`` attribute — the third dispatch bucket's crash case.

    Used by ``make_deprecated_on_callable_without_name`` to prove the dispatcher detects this up front rather than
    crashing deep inside the callable-path machinery at ``source.__name__``.
    """

    def __call__(self, x: int) -> int:
        """Return *x* unchanged — body is never reached; decoration raises first."""
        return x


def make_deprecated_on_callable_without_name() -> Any:  # noqa: ANN401
    """Apply ``@deprecated`` to a ``__call__``-only instance lacking ``__name__`` — raises ``TypeError``.

    The instance passes the initial ``callable(source)`` test but would crash at ``source.__name__`` deep inside
    the callable-path machinery; the dispatcher must detect and reject it up front with the same
    ``deprecated_instance``-naming ``TypeError`` used for non-callable sources.

    """
    return deprecated(**_DEPRS_CASE_STD_ARGS)(_CallableWithoutName())


def make_deprecated_on_partial_of_function() -> Any:  # noqa: ANN401
    """Apply ``@deprecated`` to ``functools.partial(double_value)`` — raises the dedicated ``functools.partial`` error.

    ``functools.partial`` objects never carry ``__name__`` and have no function body to wrap. Unlike
    ``make_deprecated_on_callable_without_name`` (a hand-written ``__call__`` object, pointed at
    ``deprecated_instance``), the rejection names the ``functools.partial`` type and the wrap-in-a-``def`` fix.

    """
    return deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)(partial(double_value))


def make_deprecated_on_partial_of_class() -> Any:  # noqa: ANN401
    """Apply ``@deprecated`` to ``functools.partial(NewCls)`` — documents partial-of-class routing.

    ``functools.partial(NewCls)`` is NOT class-dispatched (``inspect.isclass`` is ``False`` on a ``partial``
    object) — it falls into the callable bucket and is rejected there with the dedicated ``functools.partial``
    ``TypeError``, the same as a partial of a function. This proves the dispatcher never silently treats a
    partial-wrapped class as a class, which would lose proxy semantics (isinstance/pickling/etc).

    """
    return deprecated(**_DEPRS_CASE_STD_ARGS)(partial(NewCls))


def make_deprecated_qualname_collision_pair() -> tuple[Any, Any]:
    """Apply ``@deprecated`` to two distinct classes sharing a ``__qualname__`` across different modules.

    ``_CLASS_DISPATCH_NOTIFIED`` dedups the one-time informational notice by ``__qualname__``; two
    unrelated classes named identically in different modules must not collide — each must still get
    its own notice. Both classes are built via ``type()`` with an explicit ``__module__`` override so
    the ``__qualname__`` string is identical while the fully-qualified identity differs.

    """
    first = type("_QualnameCollision", (), {"__module__": "tests.fake_module_one"})
    second = type("_QualnameCollision", (), {"__module__": "tests.fake_module_two"})

    wrapped_first = deprecated(deprecated_in="1.0", remove_in="2.0")(first)
    wrapped_second = deprecated(deprecated_in="1.0", remove_in="2.0")(second)
    return wrapped_first, wrapped_second


def make_deprecated_stacked_over_wrapped_callable() -> Any:  # noqa: ANN401
    """Stack ``@deprecated`` (dispatcher) OUTSIDE an already-``@deprecated``-wrapped function.

    Exercises the dispatcher's callable arm re-wrapping a source that already carries ``__deprecated__``
    metadata — the existing stacking-misconfiguration guard must still fire, now reached through the
    ``deprecated()`` front door on both layers rather than ``deprecated_callable()`` directly.

    """

    @deprecated(target=TargetMode.NOTIFY, deprecated_in="0.9", remove_in="1.5")
    @deprecated(target=double_value, deprecated_in="1.0", remove_in="2.0")
    def _dispatch_stacked_callable(x: int) -> int:
        """Inner source — deprecated twice via the dispatcher front door."""
        return x

    return _dispatch_stacked_callable


def make_deprecated_over_class_proxy() -> Any:  # noqa: ANN401
    """Apply ``@deprecated`` OUTSIDE an already-``deprecated_class``-wrapped proxy.

    The proxy returned by ``deprecated_class`` is a ``_DeprecatedProxy`` INSTANCE (``inspect.isclass`` is
    ``False`` on it), so the outer ``@deprecated`` routes through the CALLABLE arm rather than
    class-dispatching a second time — ``__name__`` resolves via the proxy's ``__getattr__`` forwarding to the
    wrapped class, so this does not trip the callable-without-``__name__`` guard.

    """

    class _DispatchOverProxyInner:
        """Minimal class wrapped by ``deprecated_class`` — the inner layer of the proxy-stacking fixture."""

        def __init__(self, value: int = 0) -> None:
            """Initialize with an optional value."""
            self.value = value

    inner_proxy = deprecated_class(deprecated_in="0.5", remove_in="1.0", stream=None)(_DispatchOverProxyInner)
    return deprecated(target=TargetMode.NOTIFY, deprecated_in="0.9", remove_in="1.5")(inner_proxy)


def make_deprecated_hostile_signature_instance() -> Any:  # noqa: ANN401
    """Wrap a source whose ``__signature__`` raises ``RuntimeError`` — wrapping must still succeed.

    ``inspect.signature`` forwards a hostile descriptor's exception verbatim, so the proxy's breadcrumb helper
    has to swallow it and fall back to ``__signature__ = None``.  Built lazily rather than at module level: a
    regression to propagating the error would otherwise abort the import of this whole collection module and
    take every unrelated test down with it.

    """
    return deprecated_instance(HostileSignatureCallable(), **_DEPRS_CASE_STD_INF_ARGS, stream=None)


# ---------------------------------------------------------------------------------------------------------
# Fatal deprecations (``as_exception``) — Ft-14.  Each wrapper below keeps the DEFAULT ``num_warns=1`` unless
# the scenario needs otherwise, because the budget-bypass contract (a fatal deprecation raises on every call,
# never once) is only observable with a finite budget.  ``_DEPRS_CASE_FATAL_ARGS`` remains in
# ``tests.collection_deprecate`` alongside the other shared version-kwargs constants.
# ---------------------------------------------------------------------------------------------------------

depr_scoped_switch_warn_only = deprecated(target=double_value, **_DEPRS_CASE_STD_INF_ARGS)(identity_value)

#: Callable deprecation routed through ``logging.warning`` instead of the warnings machinery; defers to the global.
depr_scoped_switch_log_stream = deprecated(
    target=TargetMode.NOTIFY, stream=logging.warning, **_DEPRS_CASE_STD_INF_ARGS
)(identity_value)


@deprecated_class(**_DEPRS_CASE_STD_INF_ARGS)
class DeprecatedScopedSwitchWidget:
    """Class whose deprecation defers to the global switch — it warns until a scope makes it fatal."""

    DEFAULT_SIZE = 3

    def __init__(self, size: int = 1) -> None:
        """Store the widget size."""
        self.size = size


#: Instance deprecation that defers to the global switch — reading a key warns until a scope makes it fatal.
depr_scoped_switch_settings = deprecated_instance(
    {"threshold": 0.5}, name="depr_scoped_switch_settings", **_DEPRS_CASE_STD_INF_ARGS
)


@deprecated(target=base_sum_kwargs, **_DEPRS_CASE_FATAL_ARGS)
def decorated_sum_fatal(a: int, b: int = 5) -> int:
    """Forward to `base_sum_kwargs`, but as a fatal deprecation — calling it raises instead of warning."""
    return a + b


@deprecated(target=base_sum_kwargs, deprecated_in="1.0", remove_in="2.0", as_exception=False)
def decorated_sum_fatal_opt_out(a: int, b: int = 5) -> int:
    """Forward to `base_sum_kwargs` with `as_exception=False` — not fatal by default, yields to a True global."""
    return a + b


@deprecated(target=base_sum_kwargs, **_DEPRS_CASE_FATAL_ARGS, stream=None)
def decorated_sum_fatal_no_stream(a: int, b: int = 5) -> int:
    """Fatal deprecation whose warning stream is silenced — the raise is a gate, not a message channel."""
    return a + b


@deprecated(target=base_sum_kwargs, **_DEPRS_CASE_FATAL_ARGS, skip_if=True)
def decorated_sum_fatal_skipped(a: int, b: int = 5) -> int:
    """Fatal deprecation that `skip_if=True` disables — a skipped deprecation is not a deprecation."""
    return a + b


@deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_FATAL_ARGS)
def decorated_sum_fatal_notify(a: int, b: int = 5) -> int:
    """Warn-only (NOTIFY) deprecation turned fatal — the source body must NOT run once it raises."""
    return a + b


@deprecated(target=TargetMode.ARGS_REMAP, **_DEPRS_CASE_FATAL_ARGS, args_mapping={"old_x": "x"})
def decorated_remap_fatal(x: int = 0, old_x: int = 0) -> int:
    """Self-deprecating argument rename turned fatal — the body must NOT run once it raises."""
    return x


@deprecated(target=base_sum_kwargs, **_DEPRS_CASE_FATAL_ARGS, args_mapping={"old_a": "a"})
def decorated_args_fatal(old_a: int = 0, b: int = 5) -> int:
    """Fatal deprecation reached through the renamed-argument reason rather than the callable reason."""
    return old_a + b


@deprecated_class(**_DEPRS_CASE_FATAL_ARGS)
class FatalLegacyWidget:
    """Class whose deprecation is fatal — any attribute access or instantiation raises."""

    DEFAULT_SIZE = 3

    def __init__(self, size: int = 1) -> None:
        """Store the widget size."""
        self.size = size


@deprecated(**_DEPRS_CASE_FATAL_ARGS)
class FatalLegacyWidgetViaFrontDoor:
    """Class-source fatal deprecation applied through the ``@deprecated`` front door.

    Not ``deprecated_class`` directly — exercises the ``as_exception`` forwarding in
    :func:`deprecate.deprecation._packing_class_source`.
    """

    def __init__(self, size: int = 1) -> None:
        """Store the widget size."""
        self.size = size


#: Instance deprecation turned fatal — reading a key raises rather than warning.
fatal_legacy_settings = deprecated_instance({"threshold": 0.5}, name="fatal_legacy_settings", **_DEPRS_CASE_FATAL_ARGS)


@deprecated(target=async_target, **_DEPRS_CASE_FATAL_ARGS)
async def decorated_async_fatal(x: int) -> int:
    """Async source with a fatal deprecation — awaiting the coroutine raises before the target runs."""
    return x * 2


def make_positional_docstring_wrapper(decorator: Callable, options: tuple[Any, ...], fatal: bool = False) -> Callable:
    """Build a wrapper using the established positional docstring options and keyword fatal mode."""
    return decorator(TargetMode.NOTIFY, "1.0", "2.0", None, 1, None, None, None, False, *options, as_exception=fatal)(
        double_value
    )


class DescriptorSourcesHolder:
    """Descriptor sources wrapped in outer order (decorator above the descriptor) by both decorator spellings.

    ``tests/unittests/test_deprecation.py`` pins, statically only, that each wrapper keeps the descriptor's own type,
    so reads and calls through the deprecated member stay typed ``int`` instead of widening to ``Any``.

    """

    @deprecated_callable(**_DEPRS_CASE_STD_ARGS)  # type: ignore[prop-decorator]
    @property
    def legacy_value(self) -> int:
        """Deprecated read-only property."""
        return 1

    @deprecated(**_DEPRS_CASE_STD_ARGS)  # type: ignore[prop-decorator]
    @cached_property
    def legacy_cached(self) -> int:
        """Deprecated cached property."""
        return 2

    @deprecated_callable(**_DEPRS_CASE_STD_ARGS)
    @classmethod
    def legacy_factory(cls, x: int) -> int:
        """Deprecated classmethod."""
        return x

    @deprecated(**_DEPRS_CASE_STD_ARGS)
    @staticmethod
    def legacy_helper(x: int) -> int:
        """Deprecated staticmethod."""
        return x


# ========== Warn-after-success proxies: a failed operation must not spend the warn budget ==========


def make_warn_after_success_proxy(obj: object, **overrides: Any) -> Any:  # noqa: ANN401
    """Wrap *obj* as the deprecated ``legacy_box`` instance with the default one-warning budget.

    *overrides* replace any ``deprecated_instance`` keyword — ``as_exception=True`` for the fatal rows, ``skip_if=True``
    for the inactive rows, ``num_warns=-1`` to see every emission. The caller keeps its own reference to *obj*, so it
    can flip a failure flag without reading through the proxy (which would itself warn).
    """
    return deprecated_instance(obj, **{"name": "legacy_box", **_DEPRS_CASE_STD_ARGS, **overrides})


def make_silent_proxy_stack(obj: object, layers: int) -> Any:  # noqa: ANN401
    """Wrap *obj* in *layers* silent ``legacy_box`` proxies, each wrapping the previous one.

    ``stream=None`` keeps the inner layers from warning, so only a proxy stacked on top reports the deprecation.
    """
    for _ in range(layers):
        obj = make_warn_after_success_proxy(obj, stream=None)
    return obj


# ========== Proxies stored as class attributes: descriptor binding ==========


def make_deprecated_method_host(**overrides: Any) -> type[Any]:  # noqa: ANN401
    """Build a fresh host class whose ``scale`` method is a ``deprecated_instance`` of a plain function.

    The proxy lives in the class body, so instance access goes through its ``__get__`` and must bind the wrapped
    function like a method. Built per call so every test starts with an unspent warn budget. *overrides* replace any
    ``deprecated_instance`` keyword — ``skip_if=True``, ``as_exception=True``, ``num_warns=-1``.
    """

    class MethodHost:
        """Host class carrying the deprecated ``scale`` method."""

        scale = deprecated_instance(legacy_host_scale, **{"name": "scale", **_DEPRS_CASE_STD_ARGS, **overrides})

    return MethodHost


def make_deprecated_data_host() -> type[Any]:
    """Build a fresh host class holding a deprecated dict constant and a deprecated class alias as class attributes.

    Neither wrapped object binds (a ``dict`` instance and a class are not descriptors), so instance access must return
    the very proxy stored in the class body, exactly as before the proxy gained ``__get__``.
    """

    class DataHost:
        """Host class carrying non-binding deprecated class attributes."""

        defaults = deprecated_instance({"threshold": 0.5}, name="defaults", stream=None, **_DEPRS_CASE_STD_ARGS)
        Palette = deprecated_class(stream=None, **_DEPRS_CASE_STD_ARGS)(Palette)

    return DataHost


def make_read_only_settings_proxy(*, skip_if: bool) -> Any:  # noqa: ANN401
    """Wrap a fresh dict as the read-only ``legacy_settings`` instance with the given ``skip_if`` condition."""
    return deprecated_instance(
        {"threshold": 0.5, "enabled": True},
        name="legacy_settings",
        read_only=True,
        skip_if=skip_if,
        **_DEPRS_CASE_STD_ARGS,
    )


def make_flaky_attr_alias(host: type, **overrides: Any) -> Any:  # noqa: ANN401
    """Wrap *host* in ``deprecated_class`` renaming ``legacy_total`` to ``total``, one warning per deprecated name.

    *overrides* replace any ``deprecated_class`` keyword, e.g. ``as_exception=True`` for the fatal row.
    """
    return deprecated_class(attrs_mapping={"legacy_total": "total"}, **{**_DEPRS_CASE_STD_ARGS, **overrides})(host)


class LocDescriptorHost:
    """Descriptor sources (decorator above the descriptor) for the call-time warning-location tests.

    Class-body wrappers are not module-level, so ``tests/conftest.py`` cannot reset them: unlimited budget.
    """

    @deprecated(**_DEPRS_CASE_STD_INF_ARGS)  # type: ignore[prop-decorator]
    @property
    def legacy_value(self) -> int:
        """Deprecated read-only property."""
        return 1

    @deprecated(**_DEPRS_CASE_STD_INF_ARGS)  # type: ignore[prop-decorator]
    @cached_property
    def legacy_cached(self) -> int:
        """Deprecated cached property; read it on a fresh instance, the cache skips the getter afterwards."""
        return 2

    @deprecated(**_DEPRS_CASE_STD_INF_ARGS)
    @classmethod
    def legacy_factory(cls, x: int) -> int:
        """Deprecated classmethod."""
        return x

    @deprecated(**_DEPRS_CASE_STD_INF_ARGS)
    @staticmethod
    def legacy_helper(x: int) -> int:
        """Deprecated staticmethod."""
        return x


@deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)
async def loc_async_notify(x: int) -> int:
    """Async warn-only source — the warning fires when awaited and must point at the awaiting line."""
    return x * 2


#: NOTIFY over a ``functools.singledispatch`` source, which dispatches on its first positional argument.
shape_notify_area_dispatch = deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)(area_dispatch)
#: NOTIFY over a source whose ``__signature__`` reports ``(x)`` while its real shape is ``(*args, **kwargs)``.
shape_notify_raw_signature = deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)(raw_signature_patched)
#: NOTIFY over a ``functools.wraps`` forwarder that rejects keyword arguments.
shape_notify_wraps_chain = deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)(wraps_chain_outer)
#: NOTIFY over a source whose first parameter ``x`` is positional-only.
shape_notify_positional_only = deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)(positional_only_target)


@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old": "new"}, **_DEPRS_CASE_STD_ARGS)
def shape_remap_old_new(old: Optional[int] = None, new: Optional[int] = None) -> tuple[Optional[int], Optional[int]]:
    """Self-rename source (``old`` -> ``new``) that returns exactly what it received."""
    return old, new


@deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)
def shape_notify_two_params(a: int, b: int = 0) -> tuple[int, int]:
    """Warn-only source with two positional slots, for the arity-error characterization."""
    return a, b


#: ARGS_REMAP over a ``functools.singledispatch`` source; ``figure`` is the deprecated spelling of ``shape``.
shape_remap_area_dispatch = deprecated(
    target=TargetMode.ARGS_REMAP, args_mapping={"figure": "shape"}, **_DEPRS_CASE_STD_ARGS
)(area_dispatch)


@deprecated(
    target=TargetMode.ARGS_REMAP, args_mapping={"old": "new"}, args_extra={"flag": True}, **_DEPRS_CASE_STD_ARGS
)
def shape_remap_with_extra(
    old: Optional[int] = None, new: Optional[int] = None, flag: bool = False
) -> tuple[Optional[int], Optional[int], bool]:
    """Self-rename source that also gets ``flag`` injected by ``args_extra``; returns exactly what it received."""
    return old, new, flag


@deprecated(
    target=TargetMode.ARGS_REMAP, args_mapping={"old": "new"}, args_extra={"flag": True}, **_DEPRS_CASE_STD_ARGS
)
def shape_remap_var_positional_with_extra(
    *values: int, old: Optional[int] = None, new: Optional[int] = None, flag: bool = False
) -> tuple[tuple[int, ...], Optional[int], Optional[int], bool]:
    """Self-rename source with a ``*values`` tail and an ``args_extra`` injected ``flag``; returns what it received."""
    return values, old, new, flag


@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old": "new"}, args_extra={"new": 5}, **_DEPRS_CASE_STD_ARGS)
def shape_remap_var_positional_extra_fills_slot(
    new: int = 0, *values: int, **others: int
) -> tuple[int, tuple[int, ...], dict[str, int]]:
    """``new`` leads a ``*values`` tail and ``args_extra`` injects ``new`` as well; returns what it received."""
    return new, values, others


@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old": "new"}, **_DEPRS_CASE_STD_ARGS)
def shape_remap_var_positional_rename_fills_slot(
    new: int = 0, *values: int, **others: int
) -> tuple[int, tuple[int, ...], dict[str, int]]:
    """``new`` leads a ``*values`` tail and ``old`` is renamed to it; returns what it received."""
    return new, values, others


def make_pickle_settings_proxy() -> Any:  # noqa: ANN401
    """Wrap a fresh settings dict in a ``deprecated_instance`` proxy with the default one-warning budget.

    ``tests/integration/test_pickle_compat.py`` holds payloads pickled by pyDeprecate 0.14.0 from a proxy built with
    exactly these arguments; keep them in sync or the payload-versus-fresh comparisons stop meaning anything.
    """
    return deprecated_instance({"threshold": 0.5, "enabled": True}, name="legacy_settings", **_DEPRS_CASE_STD_ARGS)


# ---------------------------------------------------------------------------
# Callable-kind targets without a ``__name__`` of their own (partial, callable instance)
# ---------------------------------------------------------------------------


@deprecated(target=partial_target, **_DEPRS_CASE_STD_ARGS)
def depr_scale_partial(x: int, y: int = 0) -> int:
    """Forward to ``partial(new_scale, y=5)``; the source default ``y=0`` differs from the partial-bound ``y=5``."""
    return void(x, y)


@deprecated(target=partial_target, **_DEPRS_CASE_STD_INF_ARGS)
def depr_scale_partial_every_call(x: int, y: int = 0) -> int:
    """Forward to ``partial(new_scale, y=5)`` and warn on every call (``num_warns=-1``)."""
    return void(x, y)


@deprecated(target=partial_target, **_DEPRS_CASE_STD_ARGS)
def depr_scale_partial_no_default(x: int) -> int:
    """Forward to ``partial(new_scale, y=5)`` from a source that has no ``y`` parameter at all."""
    return void(x)


@deprecated(target=partial_target, args_mapping={"factor": "y"}, **_DEPRS_CASE_STD_ARGS)
def depr_scale_partial_renamed(x: int, factor: int = 0) -> int:
    """Forward to ``partial(new_scale, y=5)`` renaming ``factor`` to the partial-bound ``y``."""
    return void(x, factor)


@deprecated(target=nested_partial_target, **_DEPRS_CASE_STD_ARGS)
def depr_scale_nested_partial(x: int, y: int = 0) -> int:
    """Forward to a two-level partial chain whose inner layer binds ``y=5``."""
    return void(x, y)


@deprecated(target=callable_adder_target, **_DEPRS_CASE_STD_ARGS)
def depr_add_callable_instance(x: int) -> int:
    """Forward to the ``CallableAdder(offset=10)`` instance."""
    return void(x)


@deprecated(target=NonStringNameCallable(), **_DEPRS_CASE_STD_ARGS)
def depr_non_string_name_target(x: int) -> int:
    """Forward to a callable whose ``__name__`` is a non-string property."""
    return void(x)


@deprecated(target=max, **_DEPRS_CASE_STD_ARGS)
def depr_builtin_max_target(a: int, b: int) -> int:
    """Name the C builtin ``max`` as the replacement (only the static ``__deprecated__`` text is exercised)."""
    return void(a, b)


@deprecated(target=partial_async_target, **_DEPRS_CASE_STD_ARGS)
def depr_sync_over_partial_async(x: int) -> int:
    """Sync source forwarding to ``partial(async_target)`` — the dispatcher refuses to call it."""
    return void(x)


@deprecated_class(target=partial_new_cls_target, **_DEPRS_CASE_STD_ARGS)
class LegacyPresetCls:
    """Legacy class replaced by ``NewCls`` with ``d`` preset through ``functools.partial(NewCls, d="preset")``."""

    def __init__(self, c: float) -> None:
        """Store *c* — never runs, construction is forwarded to the partial."""
        self.c = c


def make_front_door_notify_on(source: Any) -> Any:  # noqa: ANN401
    """Apply the ``@deprecated`` front door with ``TargetMode.NOTIFY`` to *source*."""
    return deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)(source)


def make_front_door_redirect_on(source: Any) -> Any:  # noqa: ANN401
    """Apply the ``@deprecated`` front door with a callable ``target`` to *source*."""
    return deprecated(target=double_value, **_DEPRS_CASE_STD_ARGS)(source)


def make_strict_callable_notify_on(source: Any) -> Any:  # noqa: ANN401
    """Apply the strict ``deprecated_callable`` form with ``TargetMode.NOTIFY`` to *source*."""
    return deprecated_callable(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)(source)


def make_read_only_inplace_proxy(obj: object, flag: dict[str, bool]) -> Any:  # noqa: ANN401
    """Wrap an object with a dynamically skipped read-only guard and fatal gate."""
    return deprecated_instance(
        obj,
        name="legacy_box",
        read_only=True,
        skip_if=lambda: flag["skip"],
        as_exception=True,
        **_DEPRS_CASE_STD_ARGS,
    )


def make_switching_entry_proxy(source: object, target: object) -> Any:  # noqa: ANN401
    """Change the active context during failed warning delivery to test cleanup ownership."""
    flag = {"skip": False}

    def failing_stream(message: str) -> None:
        """Switch to the source before propagating the delivery failure."""
        flag["skip"] = True
        raise RuntimeError("delivery failed")

    return _DeprecatedProxy(
        obj=source,
        spec=_ProxySpec(
            target=target,
            name="legacy_context",
            stream=failing_stream,
            skip_if=lambda: flag["skip"],
            **_DEPRS_CASE_STD_ARGS,
        ),
    )


def make_skip_gated_target_proxy(source: object, target: object, skip_if: Callable[[], bool]) -> Any:  # noqa: ANN401
    """Wrap *source* as ``legacy_box`` forwarding to *target* while *skip_if* is false, with the default budget."""
    return _DeprecatedProxy(
        obj=source,
        spec=_ProxySpec(target=target, name="legacy_box", skip_if=skip_if, **_DEPRS_CASE_STD_ARGS),
    )


def make_deprecated_partial_host() -> type[Any]:
    """Build a fresh host class whose ``double`` attribute is a ``deprecated_instance`` of ``partial(double_value)``.

    A ``functools.partial`` is callable and, from Python 3.13, defines ``__get__`` (it warns on 3.13 and binds on 3.14).
    The proxy must serve it unbound on every version, so ``host.double(3)`` calls ``double_value(3)``.
    """

    class PartialHost:
        """Host class carrying a deprecated ``functools.partial`` as a class attribute."""

        double = deprecated_instance(partial(double_value), name="double", **_DEPRS_CASE_STD_ARGS)

    return PartialHost


def make_sum_deprecation_with_stream(stream: Callable[..., Any]) -> Callable[..., int]:
    """Deprecate ``original_sum`` in favour of ``base_sum_kwargs``, delivering every warning through ``stream``."""
    return deprecated(target=base_sum_kwargs, deprecated_in="1.0", remove_in="2.0", num_warns=-1, stream=stream)(
        original_sum
    )


def make_pickle_fatal_settings_proxy() -> Any:  # noqa: ANN401
    """Wrap a fresh settings dict in a ``deprecated_instance`` proxy that raises ``DeprecatedError`` instead of warning.

    Same data and name as :func:`make_pickle_settings_proxy`, but ``as_exception=True`` — the one non-default setting
    ``tests/integration/test_pickle_compat.py`` pickles besides the default and the spent-budget proxy.
    """
    return deprecated_instance({"threshold": 0.5, "enabled": True}, name="legacy_settings", **_DEPRS_CASE_FATAL_ARGS)


@deprecated(target=TargetMode.NOTIFY, stream=stream_wraps_warn_forwarding_stacklevel, **_DEPRS_CASE_STD_ARGS)
def loc_notify_wrapped_stream(x: int) -> int:
    """Warn-only source delivering through a ``functools.wraps`` stream that has its own ``stacklevel`` parameter."""
    return x * 2


# ========== Proxy ``%(source_path)s`` in a custom ``message_template`` ==========
# Unlimited budget ensures every access warns, including repeated accesses within the same test.

#: Custom template rendering the path next to the name, so one assertion pins both placeholders.
_SOURCE_PATH_TEMPLATE = "%(source_path)s :: %(source_name)s"

#: Whole-class deprecation of :class:`tests.collection_targets.SourcePathTarget` — path is module-qualified.
DeprSourcePathClass = deprecated_class(message_template=_SOURCE_PATH_TEMPLATE, **_DEPRS_CASE_STD_INF_ARGS)(
    SourcePathTarget
)

#: Selective attribute deprecation — one redirect (``old_attr`` -> ``new_attr``) and one warn-only entry.
DeprSourcePathAttrs = deprecated_class(
    attrs_mapping={"old_attr": "new_attr", "gone_attr": None},
    message_template=_SOURCE_PATH_TEMPLATE,
    **_DEPRS_CASE_STD_INF_ARGS,
)(SourcePathTarget)

#: Constructor keyword rename ``old`` -> ``new``.
DeprSourcePathArgs = deprecated_class(
    args_mapping={"old": "new"}, message_template=_SOURCE_PATH_TEMPLATE, **_DEPRS_CASE_STD_INF_ARGS
)(SourcePathTarget)

#: Outer proxy stacked over :data:`DeprSourcePathClass` — renders the path of the class both layers wrap.
DeprSourcePathStacked = deprecated_class(message_template=_SOURCE_PATH_TEMPLATE, **_DEPRS_CASE_STD_INF_ARGS)(
    DeprSourcePathClass
)

#: Instance proxy — an instance has no module of its own, so the path is the given ``name``.
depr_source_path_instance = deprecated_instance(
    SourcePathTarget(), name="source_path_target", message_template=_SOURCE_PATH_TEMPLATE, **_DEPRS_CASE_STD_INF_ARGS
)


def make_depr_module_raising_class(metaclass: type) -> Any:  # noqa: ANN401
    """Deprecate a fresh class whose *metaclass* refuses a dynamic ``__module__`` lookup.

    Decorating at call time keeps the refusing class out of module scans; the proxy renders ``%(source_path)s`` from the
    class's own namespace (see :func:`tests.collection_targets.make_module_raising_class`).
    """
    return deprecated_class(message_template=_SOURCE_PATH_TEMPLATE, **_DEPRS_CASE_STD_INF_ARGS)(
        make_module_raising_class(metaclass)
    )


# ---------------------------------------------------------------------------
# Spent warn budget — calls after the one warning was delivered skip the call plan
# ---------------------------------------------------------------------------


@deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)
def depr_notify_spent_budget(x: int, y: int = 1) -> int:
    """Warn-only deprecation whose single ``FutureWarning`` is spent by the first call."""
    return x + y


@deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)
async def depr_notify_spent_budget_async(x: int, y: int = 1) -> int:
    """Async warn-only deprecation whose single ``FutureWarning`` is spent by the first awaited call."""
    return x + y


#: Counts every ``skip_if`` evaluation of :func:`depr_notify_skip_counted`; it never skips.
notify_skip_condition = CountingSkipCondition()


@deprecated(target=TargetMode.NOTIFY, skip_if=notify_skip_condition, **_DEPRS_CASE_STD_ARGS)
def depr_notify_skip_counted(x: int) -> int:
    """Warn-only deprecation behind a feature flag that is consulted on every call."""
    return x


# ---------------------------------------------------------------------------
# Unreadable signatures — the error surfaces where it did before facts were read at decoration
# ---------------------------------------------------------------------------


@deprecated(target=OpaqueSignatureCallable(), args_mapping={"old_x": "x"}, **_DEPRS_CASE_STD_ARGS)
def depr_opaque_target_renamed(x: int = 0, old_x: int = 0) -> Any:  # noqa: ANN401
    """Forwards to a target whose signature cannot be read; the rename needs the target's defaults."""
    return void(x, old_x)


@deprecated(target=OpaqueSignatureCallable(), **_DEPRS_CASE_STD_ARGS)
def depr_opaque_target_var_positional(*values: int) -> Any:  # noqa: ANN401
    """Forwards a ``*args`` tail to a target whose signature cannot be read; the tail needs the target's slots."""
    return void(values)


@deprecated(target=LazySignatureCallable(), args_mapping={"old_x": "x"}, **_DEPRS_CASE_STD_ARGS)
def depr_lazy_signature_target_renamed(x: int = 0, old_x: int = 0) -> Any:  # noqa: ANN401
    """Forwards to a target whose signature error type cannot be rebuilt from its ``args``; the rename needs it."""
    return void(x, old_x)


def make_opaque_source_wrapper() -> Callable[..., Any]:
    """Decorate a callable whose own signature cannot be read; the signature error is raised by this call."""
    return deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)(OpaqueSignatureCallable())


def make_transient_notify_pair() -> tuple[Callable[..., int], Callable[..., int]]:
    """Build a fresh source function and its silent NOTIFY wrapper, for a test that drops both again."""

    def transient_source(x: int, y: int = 1) -> int:
        return x + y

    return transient_source, deprecated(target=TargetMode.NOTIFY, stream=None, **_DEPRS_CASE_STD_ARGS)(transient_source)


@deprecated(target=OpaqueSignatureCallable(), **_DEPRS_CASE_STD_INF_ARGS)
def depr_opaque_target_unmapped(x: int) -> Any:  # noqa: ANN401
    """Forward an ordinary parameter without renaming to a target with unreadable signature."""
    return void(x)


@deprecated(target=OpaqueSignatureCallable(), **_DEPRS_CASE_STD_INF_ARGS)
def depr_opaque_target_empty() -> Any:  # noqa: ANN401
    """Forward an empty call that needs no target signature validation."""
    return void()


depr_docless_class = deprecated_class(update_docstring=True, stream=None, **_DEPRS_CASE_STD_ARGS)(DoclessTarget)
depr_documented_class = deprecated_class(
    update_docstring=True, docstring_style="rst", stream=None, **_DEPRS_CASE_STD_ARGS
)(NewCls)


@deprecated(target=zero_argument_target, **_DEPRS_CASE_STD_INF_ARGS)
def depr_zero_argument_target(x: int) -> Any:  # noqa: ANN401
    """Forward a value to a readable target whose signature accepts no parameters."""
    return void(x)
