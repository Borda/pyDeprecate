"""Tests for the Phase 2 ``deprecated()`` dispatcher — class-path first-class support.

``@deprecated`` on a class no longer carries the v0.6.0 "will become a `TypeError`" threat; it now
dispatches to :func:`~deprecate.proxy.deprecated_class` and emits a one-time informational
``UserWarning`` instead (removed in v1.0). This module also covers the two decoration-time
``TypeError`` guards (non-callable/non-class source; callable instance lacking ``__name__``), the
``TargetMode.AUTO`` front-door inference contract (mapping selects the remap mode; strict factories
reject AUTO), the common-args-only contract of the front door (class-only ``attrs_mapping`` rejected,
common ``message_template``/``skip_if`` forwarded), and stacking the dispatcher over an already-wrapped
callable or an existing class proxy.

"""

import warnings
from typing import cast

import pytest

from deprecate import TargetMode, assert_no_warnings, deprecated, deprecated_callable, deprecated_class
from deprecate._types import DeprecationConfig, _DeprecatedCallable
from deprecate.proxy import _DeprecatedProxy
from tests.collection_deprecate import (
    make_deprecated_attrs_remap_target_on_class,
    make_deprecated_auto_args_mapping_on_function,
    make_deprecated_explicit_auto_on_function,
    make_deprecated_front_door_skip_if_true_on_class,
    make_deprecated_notify_args_extra_alone_on_class,
    make_deprecated_on_callable_without_name,
    make_deprecated_on_fresh_class,
    make_deprecated_on_fresh_class_silent,
    make_deprecated_on_fresh_dataclass,
    make_deprecated_on_fresh_enum_class,
    make_deprecated_on_fresh_function_warn_only,
    make_deprecated_on_non_callable_source,
    make_deprecated_on_partial_of_class,
    make_deprecated_on_partial_of_function,
    make_deprecated_over_class_proxy,
    make_deprecated_qualname_collision_pair,
    make_deprecated_stacked_over_wrapped_callable,
    make_deprecated_with_args_mapping_on_class_default_target,
    make_deprecated_with_attrs_mapping_kwarg,
    make_deprecated_with_message_template_on_class,
)
from tests.collection_targets import Palette


class TestClassDispatchInformationalWarning:
    """``@deprecated`` on a class emits a one-time informational ``UserWarning``, not the v0.6.0 threat."""

    def test_fresh_class_warns_once_naming_class_not_old_message(self) -> None:
        """The first decoration of a class emits exactly one informational ``UserWarning`` naming the class.

        A maintainer migrating a class-based API reaches for the familiar ``@deprecated`` decorator instead of
        the class-specific ``@deprecated_class`` — this must work (class dispatch is first-class in Phase 2) and
        nudge them toward the explicit API via a single, non-alarming notice that names their own class (not
        internal dispatcher machinery like "packing") and drops the old v0.6.0 "will become a TypeError" threat.

        Single Act, one decoration: all three assertions read the SAME captured warning so this test does not
        depend on execution order relative to other tests that also decorate ``_DispatchedFreshClass`` (the
        one-time guard is per-class — a second observer elsewhere in the suite would see nothing).

        """
        with pytest.warns(UserWarning, match="_DispatchedFreshClass") as caught:
            make_deprecated_on_fresh_class()

        user_warns = [w for w in caught if issubclass(w.category, UserWarning)]
        assert len(user_warns) == 1
        message = str(user_warns[0].message)
        assert "packing" not in message
        assert "_packing_class_source" not in message
        assert "will become a" not in message
        assert "v0.6.0" not in message

    def test_repeated_decoration_does_not_rewarn(self) -> None:
        """Decorating the SAME class definition a second time does not re-emit the informational warning.

        ``make_deprecated_on_fresh_class`` executes the identical ``class`` statement (same qualname) on every
        call; the guard must be "one-time" per class rather than spamming on every decoration, matching the
        locked spec's "do not spam per decoration in a loop" requirement.

        """
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            make_deprecated_on_fresh_class()  # first application — consumes the one-time warning

        with assert_no_warnings(UserWarning):
            make_deprecated_on_fresh_class()  # second application of the same class — must stay silent

    def test_qualname_collision_across_modules_both_warn(self) -> None:
        """Two distinct classes sharing a ``__qualname__`` in different modules each get their own notice.

        A maintainer decorates two unrelated classes that happen to share a name (e.g. two packages
        both define a top-level ``Config`` or nested ``Outer.Inner``) — the one-time dispatch notice
        must not silently drop the second class's warning just because the dedup set only tracked the
        bare ``__qualname__`` string, blind to which module the class actually lives in.

        """
        with pytest.warns(UserWarning, match="_QualnameCollision") as caught:
            make_deprecated_qualname_collision_pair()

        user_warns = [w for w in caught if issubclass(w.category, UserWarning)]
        assert len(user_warns) == 2

    def test_silent_when_stream_none(self) -> None:
        """``@deprecated(stream=None)`` on a class suppresses the informational ``UserWarning`` entirely."""
        with assert_no_warnings():
            make_deprecated_on_fresh_class_silent()

    def test_call_time_deprecation_warning_still_fires(self) -> None:
        """Instantiating the resulting proxy still emits the normal call-time ``FutureWarning``.

        The one-time informational notice is purely a decoration-time nudge; it must not replace or suppress the
        proxy's own runtime deprecation warning on access.

        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            cls = make_deprecated_on_fresh_class()

        with pytest.warns(FutureWarning):
            cls()

    def test_call_time_warning_stacklevel_points_to_caller(self) -> None:
        """The call-time ``FutureWarning`` filename points to the caller's file, not to ``deprecation.py``.

        The dispatcher adds one extra frame when delegating a class source to ``deprecated_class``; that frame
        must not leak into the reported warning location.

        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            cls = make_deprecated_on_fresh_class()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            cls()

        future_warns = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert future_warns
        assert future_warns[0].filename.endswith("test_dispatch_class_route.py")

    def test_notify_args_extra_alone_misconfig_warning_points_to_decoration_site(self) -> None:
        """The NOTIFY + bare ``args_extra`` misconfig ``UserWarning`` reports the decoration site, not internals.

        A maintainer decorates a class with a stray ``args_extra`` and no ``args_mapping`` — this does not
        auto-resolve to a forwarding mode (auto-resolve only applies when a mapping is present), so the proxy
        still flags it as a misconfiguration. The dispatcher inserts two extra frames (``packing`` and
        ``_packing_class_source``) versus a direct ``deprecated_class(...)`` call; without the matching
        stacklevel offset the reported location would point into `deprecation.py`/`proxy.py` instead of here.

        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            make_deprecated_notify_args_extra_alone_on_class()

        misconfig_warns = [w for w in caught if issubclass(w.category, UserWarning) and "args_extra" in str(w.message)]
        assert len(misconfig_warns) == 1
        assert misconfig_warns[0].filename.endswith("collection_deprecate.py")


class TestEnumAndDataclassDispatch:
    """``@deprecated`` routes Enum and dataclass class sources through the class path exactly like plain classes."""

    def test_enum_source_wrapped_as_proxy(self) -> None:
        """An Enum decorated directly with ``@deprecated`` becomes a working ``_DeprecatedProxy``."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            enum_cls = make_deprecated_on_fresh_enum_class()

        assert isinstance(enum_cls, _DeprecatedProxy)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert enum_cls.ALPHA.value == "alpha"  # type: ignore[attr-defined]

    def test_dataclass_source_wrapped_as_proxy(self) -> None:
        """A dataclass decorated directly with ``@deprecated`` becomes a working ``_DeprecatedProxy``."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            dc_cls = make_deprecated_on_fresh_dataclass()

        assert isinstance(dc_cls, _DeprecatedProxy)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            instance = dc_cls(label="y")  # type: ignore[operator]
        assert instance.label == "y"


class TestAutoTargetOnFrontDoor:
    """``TargetMode.AUTO`` — the ``@deprecated``-only inference default."""

    def test_bare_args_mapping_on_function_infers_args_remap(self) -> None:
        """``@deprecated(args_mapping=...)`` with no ``target`` self-remaps the argument — no misconfig.

        A maintainer renaming a keyword argument reaches for the front door with just the mapping: AUTO must
        infer ``ARGS_REMAP`` so the old name warns and renames, instead of the pre-AUTO behaviour where the
        default ``NOTIFY`` flagged the mapping as ignored.

        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fn = make_deprecated_auto_args_mapping_on_function()

        assert not [w for w in caught if "ignores `args_mapping`" in str(w.message)]
        assert fn.__deprecated__.target is TargetMode.ARGS_REMAP
        with pytest.warns(FutureWarning, match="old_c"):
            assert fn(old_c=3.0) == 6.0

    def test_explicit_auto_equals_omitted_target(self) -> None:
        """Passing ``target=TargetMode.AUTO`` explicitly behaves exactly like omitting ``target``."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fn = make_deprecated_explicit_auto_on_function()

        assert not [w for w in caught if issubclass(w.category, UserWarning)]
        assert fn.__deprecated__.target is TargetMode.ARGS_REMAP

    def test_auto_never_stored_in_metadata_for_warn_only(self) -> None:
        """A warn-only ``@deprecated`` function resolves AUTO to ``NOTIFY`` in its stored metadata.

        ``TargetMode.AUTO`` is a decoration-time inference value only — audit metadata must always record
        the resolved mode so downstream tooling never has to understand the sentinel.

        """
        fn = make_deprecated_on_fresh_function_warn_only()
        assert fn.__deprecated__.target is TargetMode.NOTIFY

    def test_strict_callable_form_rejects_auto(self) -> None:
        """``deprecated_callable(target=TargetMode.AUTO)`` raises ``TypeError`` naming the front door.

        The strict forms require the decoration site to document its own intent with an explicit mode;
        inference is the front door's job.

        """
        with pytest.raises(TypeError, match="only valid on the `@deprecated` front door"):
            deprecated_callable(target=TargetMode.AUTO, deprecated_in="1.0", remove_in="2.0")

    def test_strict_class_form_rejects_auto(self) -> None:
        """``deprecated_class(target=TargetMode.AUTO)`` raises ``TypeError`` naming the front door."""
        with pytest.raises(TypeError, match="only valid on the `@deprecated` front door"):
            deprecated_class(target=TargetMode.AUTO, deprecated_in="1.0", remove_in="2.0")


class TestCommonArgsOnlyOnDispatcher:
    """``deprecated()`` exposes only the arguments common to ``deprecated_callable`` and ``deprecated_class``."""

    def test_attrs_mapping_kwarg_raises_type_error(self) -> None:
        """``deprecated(attrs_mapping=...)`` raises ``TypeError`` — the knob is class-only, on ``deprecated_class``.

        A developer who learned ``attrs_mapping`` from the ``deprecated_class`` docs tries it on the friendly
        front door: the call must fail loudly at the factory call itself (unexpected keyword argument) instead
        of silently accepting a knob the callable path could never honour — the full class scope lives on
        ``deprecated_class`` directly.

        """
        with pytest.raises(TypeError, match="attrs_mapping"):
            make_deprecated_with_attrs_mapping_kwarg()

    def test_attrs_remap_target_on_class_raises_type_error(self) -> None:
        """``deprecated(target=TargetMode.ATTRS_REMAP)`` on a class raises ``TypeError`` — mode unreachable here.

        ``ATTRS_REMAP`` only renames the attribute names listed in ``attrs_mapping``, but the front door does
        not expose ``attrs_mapping``, so the mode can never do anything through ``@deprecated``. A maintainer
        who selects it on a class source must fail loudly at decoration time — with the error pointing to
        ``deprecated_class(attrs_mapping=...)`` — rather than receive a silently built proxy that redirects
        nothing. This mirrors the callable path, where the same mode already raises as proxy-only.

        """
        with pytest.raises(TypeError, match="ATTRS_REMAP"):
            make_deprecated_attrs_remap_target_on_class()

    def test_skip_if_forwards_on_class_path(self) -> None:
        """``deprecated(target=..., skip_if=True)`` on a class deactivates the proxy machinery.

        ``skip_if`` is common to both dispatch shapes, so a maintainer gating a class deprecation on a runtime
        condition through the front door must get the same behaviour as ``deprecated_class(skip_if=...)``:
        with the condition ``True``, instantiation returns a wrapped-source instance with no ``FutureWarning``
        and no forwarding to the target class.

        """
        proxy = make_deprecated_front_door_skip_if_true_on_class()

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            instance = proxy()
        assert type(instance).__name__ == "PaletteOld"

    def test_message_template_forwards_on_class_path(self) -> None:
        """``deprecated(message_template=...)`` on a class renders the custom template on proxy access.

        ``message_template`` is common to both dispatch shapes, so a maintainer setting a custom warning message
        through the front door must see it honoured by the class proxy exactly as with a direct
        ``deprecated_class(message_template=...)`` call — the dispatcher used to drop the template silently.

        """
        proxy = make_deprecated_with_message_template_on_class()

        with pytest.warns(FutureWarning, match="Custom notice for `Palette`."):
            proxy()

    def test_template_mgs_alias_fires_exactly_once_on_class_dispatch(self) -> None:
        """Instantiating a class-dispatch proxy built via ``template_mgs=`` emits exactly one ``FutureWarning``.

        The alias's own decoration-time migration notice is a separate, one-time event; it must not cause
        the proxy's call-time warning to double-fire. A maintainer combining the front door with the legacy
        alias on a class source must see the same "at most once" contract that ``message_template=`` already
        has on this path.

        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # decoration fires its own one-time alias + class-dispatch notices
            proxy = deprecated(
                deprecated_in="1.0", remove_in="2.0", template_mgs="Alias notice for `%(source_name)s`."
            )(Palette)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy()

        future_warns = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert len(future_warns) == 1


class TestArgsMappingDefaultTargetAutoResolveOnDispatcher:
    """``@deprecated(args_mapping=...)`` on a class with NO explicit ``target=`` auto-resolves."""

    def test_default_target_auto_resolves_no_misconfig(self) -> None:
        """Omitting ``target=`` while passing ``args_mapping`` to a class auto-resolves to ``ARGS_REMAP``.

        The dispatcher's own default is ``target=TargetMode.NOTIFY``; before this auto-resolve behavior was
        added, this combination was flagged as a misconfiguration and the mapping was dropped. It must now
        behave exactly like ``deprecated_class(args_mapping=...)`` with an omitted target always has:
        auto-promote to ``ARGS_REMAP``, no misconfig warning.

        """
        wrapped = make_deprecated_with_args_mapping_on_class_default_target()

        dep = object.__getattribute__(wrapped, "__deprecated__")
        assert dep.target is TargetMode.ARGS_REMAP
        assert dep.args_mapping == {"old_c": "c"}
        assert dep.misconfigured is False

    def test_remap_fires_on_old_arg(self) -> None:
        """Calling with the deprecated ``old_c`` keyword warns and remaps to ``c``."""
        wrapped = make_deprecated_with_args_mapping_on_class_default_target()

        with pytest.warns(FutureWarning):
            instance = wrapped(old_c=3)  # type: ignore[operator]
        assert instance.my_c == 3

    def test_silent_on_new_arg(self) -> None:
        """Calling with the new ``c`` keyword directly is silent — ARGS_REMAP only warns on the old name."""
        wrapped = make_deprecated_with_args_mapping_on_class_default_target()

        with assert_no_warnings(FutureWarning):
            instance = wrapped(c=4)  # type: ignore[operator]
        assert instance.my_c == 4


class TestNonCallableNonClassSource:
    """A source that is neither callable nor a class is rejected — ``@deprecated`` is not for plain objects."""

    def test_raises_type_error(self) -> None:
        """Decorating a plain ``object()`` instance raises ``TypeError`` at decoration time."""
        with pytest.raises(TypeError):
            make_deprecated_on_non_callable_source()

    def test_error_names_deprecated_instance(self) -> None:
        """The rejection message points the caller at ``deprecated_instance`` for object wrapping."""
        with pytest.raises(TypeError, match="deprecated_instance"):
            make_deprecated_on_non_callable_source()


class TestCallableInstanceWithoutName:
    """A callable object lacking ``__name__`` passes the callable test but is rejected up front, not deep inside."""

    def test_raises_type_error(self) -> None:
        """A ``__call__``-only instance with no ``__name__`` raises ``TypeError`` instead of crashing on `.__name__`."""
        with pytest.raises(TypeError):
            make_deprecated_on_callable_without_name()

    def test_error_names_deprecated_instance(self) -> None:
        """The nameless-callable rejection reuses the ``deprecated_instance`` message from non-callable sources."""
        with pytest.raises(TypeError, match="deprecated_instance"):
            make_deprecated_on_callable_without_name()

    def test_partial_of_function_raises_type_error(self) -> None:
        """``functools.partial(some_function)`` as a source raises the same nameless-callable ``TypeError``.

        ``functools.partial`` objects never carry ``__name__`` — this proves the guard generalises beyond a
        hand-written ``__call__`` class to the stdlib ``partial`` kind called out in the dispatcher's callable-kind
        matrix.

        """
        with pytest.raises(TypeError, match="deprecated_instance"):
            make_deprecated_on_partial_of_function()

    def test_partial_of_class_raises_type_error_not_class_dispatch(self) -> None:
        """``functools.partial(SomeClass)`` is rejected as a nameless callable — it is never class-dispatched.

        ``inspect.isclass`` is ``False`` on a ``functools.partial`` object even when it wraps a class, so the
        dispatcher must NOT silently treat it as a class (which would lose proxy semantics) — it falls into the
        callable bucket and is rejected there for lacking ``__name__``, documenting that
        ``functools.partial``-of-a-class is out of scope by design.

        """
        with pytest.raises(TypeError, match="deprecated_instance"):
            make_deprecated_on_partial_of_class()


class TestDispatcherStacking:
    """Stacking the ``deprecated()`` dispatcher over an already-wrapped callable or an existing class proxy."""

    def test_stacked_over_wrapped_callable_both_warn(self) -> None:
        """Two ``@deprecated`` layers on the same function each emit a warning when called.

        Confirms the dispatcher's callable arm still supports multi-step migration stacking after being routed
        through the ``deprecated()`` front door on both layers, not just the strict ``deprecated_callable()`` form.

        """
        fn = make_deprecated_stacked_over_wrapped_callable()
        cast(_DeprecatedCallable, fn)._state.warned_calls = 0

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = fn(5)

        dep_warns = [w for w in caught if w.category in (FutureWarning, DeprecationWarning)]
        assert len(dep_warns) == 2
        assert result == 10  # forwards to double_value(5)

    def test_over_class_proxy_routes_through_callable_arm(self) -> None:
        """``@deprecated`` applied outside an already-``deprecated_class``-wrapped proxy does not crash.

        The inner proxy is a ``_DeprecatedProxy`` instance, not a ``type`` — ``inspect.isclass`` is ``False`` on
        it, so the dispatcher must route it through the callable arm rather than attempting to class-dispatch a
        second time. ``__name__`` resolves via the proxy's attribute forwarding, so decoration must succeed.

        """
        wrapped = make_deprecated_over_class_proxy()

        assert hasattr(wrapped, "__deprecated__")
        dep = cast(_DeprecatedCallable, wrapped).__deprecated__
        assert isinstance(dep, DeprecationConfig)
        assert dep.target is TargetMode.NOTIFY

    def test_over_class_proxy_call_still_warns(self) -> None:
        """Calling the doubly-wrapped proxy still emits at least one deprecation warning."""
        wrapped = make_deprecated_over_class_proxy()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            wrapped()  # type: ignore[operator]

        dep_warns = [w for w in caught if w.category in (FutureWarning, DeprecationWarning)]
        assert dep_warns


class TestTemplateMgsAliasOnClassDispatchEntryPoint:
    """The deprecated ``template_mgs`` alias resolves through the ``@deprecated`` front door on a CLASS source.

    The alias fold happens at the very top of ``deprecated()``, before the callable-vs-class dispatch
    decision is made — a maintainer who never migrated off the old, typo'd ``template_mgs`` spelling and
    reaches for the friendly front door on a class must get the exact same fold as the callable path.
    """

    def test_alias_alone_warns_and_resolves(self) -> None:
        """Supplying only ``template_mgs`` warns ``FutureWarning`` and is adopted as ``message_template``."""
        with pytest.warns(FutureWarning, match="`template_mgs` is deprecated"):
            proxy = deprecated(
                deprecated_in="1.0", remove_in="2.0", template_mgs="Alias notice for `%(source_name)s`."
            )(Palette)
        dep = object.__getattribute__(proxy, "__deprecated__")
        assert dep.message_template == "Alias notice for `%(source_name)s`."

    def test_alias_and_message_template_together_raises(self) -> None:
        """Supplying both ``template_mgs`` and ``message_template`` on a class source raises ``TypeError``.

        The conflict is detected before the class-dispatch decision is even made, so a class source fails
        just as loudly as a callable one — no silent merge.
        """
        with pytest.raises(TypeError, match="pass only one"):
            deprecated(
                deprecated_in="1.0",
                remove_in="2.0",
                message_template="Canonical notice.",
                template_mgs="Legacy notice.",
            )(Palette)
