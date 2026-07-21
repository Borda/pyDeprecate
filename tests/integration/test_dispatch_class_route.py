"""Tests for the Phase 2 ``deprecated()`` dispatcher — class-path first-class support.

``@deprecated`` on a class no longer carries the v0.6.0 "will become a `TypeError`" threat; it now
dispatches (permanently) to :func:`~deprecate.proxy.deprecated_class` and emits a one-time
informational ``UserWarning`` instead (notice only — removed in v1.0). This module also covers
the two new decoration-time
``TypeError`` guards (non-callable/non-class source; callable instance lacking ``__name__``), the
new ``attrs_mapping`` parameter on the dispatcher, and stacking the dispatcher over an
already-wrapped callable or an existing class proxy.

"""

import warnings
from typing import cast

import pytest

from deprecate import TargetMode, assert_no_warnings
from deprecate._types import DeprecationConfig, _DeprecatedCallable
from deprecate.proxy import _DeprecatedProxy
from tests.collection_deprecate import (
    make_deprecated_notify_args_extra_alone_on_class,
    make_deprecated_on_callable_without_name,
    make_deprecated_on_fresh_class,
    make_deprecated_on_fresh_class_silent,
    make_deprecated_on_fresh_dataclass,
    make_deprecated_on_fresh_enum_class,
    make_deprecated_on_non_callable_source,
    make_deprecated_on_partial_of_class,
    make_deprecated_on_partial_of_function,
    make_deprecated_over_class_proxy,
    make_deprecated_qualname_collision_pair,
    make_deprecated_stacked_over_wrapped_callable,
    make_deprecated_with_args_mapping_on_class_default_target,
    make_deprecated_with_attrs_mapping_on_callable,
    make_deprecated_with_attrs_mapping_on_class,
    make_deprecated_with_explicit_target_and_attrs_mapping_on_class,
)


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


class TestAttrsMappingOnDispatcher:
    """``attrs_mapping`` on the ``deprecated()`` front door — class path only (new in Phase 2)."""

    def test_attrs_mapping_on_class_redirects(self) -> None:
        """``@deprecated(attrs_mapping=...)`` on a class forwards the mapping to ``deprecated_class`` correctly.

        A team that only knows the friendly ``@deprecated`` decorator can now also deprecate a single
        renamed attribute (``color`` -> ``colour``) without reaching for ``deprecated_class`` explicitly —
        the dispatcher must forward ``attrs_mapping`` unchanged onto the class path.

        """
        proxy = make_deprecated_with_attrs_mapping_on_class()

        with pytest.warns(FutureWarning, match="color"):
            value = proxy.color  # type: ignore[attr-defined]
        assert value == "red"

    def test_attrs_mapping_on_class_auto_resolves_no_misconfig(self) -> None:
        """``@deprecated(attrs_mapping=...)`` on a class auto-resolves to ``ATTRS_REMAP`` — no misconfig warning.

        Option C (2026-07-20) means the dispatcher's own ``TargetMode.NOTIFY`` default no longer collides with
        ``attrs_mapping``: the resulting proxy's ``__deprecated__.target`` must be ``ATTRS_REMAP``, and no
        "NOTIFY ignores attrs_mapping" misconfig warning should have fired during construction.

        """
        proxy = make_deprecated_with_attrs_mapping_on_class()

        dep = object.__getattribute__(proxy, "__deprecated__")
        assert dep.target is TargetMode.ATTRS_REMAP
        assert dep.misconfigured is False

    def test_attrs_mapping_on_callable_raises_type_error(self) -> None:
        """``attrs_mapping`` on a callable source is class-path-only — raises ``TypeError`` at decoration time."""
        with pytest.raises(TypeError):
            make_deprecated_with_attrs_mapping_on_callable()

    def test_attrs_mapping_on_callable_error_names_deprecated_class(self) -> None:
        """The rejection message points the caller at ``deprecated_class`` for attribute-level deprecation."""
        with pytest.raises(TypeError, match="deprecated_class"):
            make_deprecated_with_attrs_mapping_on_callable()

    def test_explicit_callable_target_with_attrs_mapping_forwards_both(self) -> None:
        """``@deprecated(target=<class>, attrs_mapping=...)`` forwards BOTH a real target and the mapping.

        Every other dispatcher ``attrs_mapping`` fixture omits an explicit non-``NOTIFY`` callable ``target``
        (auto-resolve only) — this locks the remaining combination the review flagged as unverified.

        """
        proxy = make_deprecated_with_explicit_target_and_attrs_mapping_on_class()

        dep = object.__getattribute__(proxy, "__deprecated__")
        assert dep.target is not TargetMode.NOTIFY
        assert dep.misconfigured is False

        with pytest.warns(FutureWarning, match="color"):
            value = proxy.color  # type: ignore[attr-defined]
        assert value == "blue"


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
