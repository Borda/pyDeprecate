"""Unit tests for proxy runtime state, introspection, identity, and copy/pickle support."""

import abc
import copy
import inspect
import pickle
import warnings
from collections.abc import Callable, Mapping
from dataclasses import FrozenInstanceError
from enum import Enum
from typing import Any, cast

import pytest

from deprecate import DeprecationProxy
from deprecate.messaging import deprecation_warning
from deprecate.proxy import deprecated_class, deprecated_instance
from deprecate.proxy.core import _DeprecatedProxy, _ProxySpec
from tests.collection_deprecate import DeprecatedColorEnum, ast_breadcrumb_source_dict, depr_ast_breadcrumb_dict
from tests.collection_deprecate_forwarding import pep702_proxy_stacked
from tests.collection_deprecate_regressions import (
    make_deprecated_hostile_signature_instance,
    make_deprecated_instance_over_marked_object,
    make_deprecated_instance_skip_if_true_read_only,
)
from tests.collection_misconfigured import make_stacked_legacy_proxy
from tests.collection_pep702 import StackedAlias
from tests.collection_targets import (
    ColorEnum,
    HostileSignatureCallable,
    Pep702StaticTarget,
    SubclassableBase,
    _Pep702ProxyTarget,
    base_sum_kwargs,
)
from tests.unittests.proxy._helpers import _ast_class_proxy, _P8Source, _P8Target


class TestProxySpec:
    """Frozen construction settings kept separate from serialized runtime state."""

    def test_defaults(self) -> None:
        """Keep the original constructor defaults.

        A private caller can provide only a display name and retain the same warn-once, writable proxy policy.
        """
        spec = _ProxySpec(name="legacy")
        assert spec.name == "legacy"
        assert spec.target is None
        assert spec.args_mapping is None
        assert spec.args_extra is None
        assert spec.attrs_mapping is None
        assert spec.deprecated_in == ""
        assert spec.remove_in == ""
        assert spec.num_warns == 1
        assert spec.stream is deprecation_warning
        assert spec.message_template is None
        assert spec.skip_if is False
        assert spec.as_exception is None
        assert spec.read_only is False
        assert spec.docstring_style == "auto"
        assert spec._misconfigured_override is False

    def test_frozen(self) -> None:
        """Reject reassignment of construction settings.

        Once a caller has described a proxy, later field assignments must fail rather than silently change its spec.
        """
        spec = _ProxySpec(name="legacy", deprecated_in="1.0", remove_in="2.0")
        with pytest.raises(FrozenInstanceError):
            setattr(spec, "name", "changed")
        assert spec.name == "legacy"


class TestProxyInit:
    """Internal state initialisation for _DeprecatedProxy instances."""

    def test_internal_state_stored_correctly(self) -> None:
        """Store runtime state separately from public audit metadata.

        Proxy construction must keep mutable warning state private while exposing an immutable configuration through
        the v0.13 metadata contract.
        """
        obj = {"a": 1}
        proxy = _DeprecatedProxy(
            obj=obj,
            spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0", num_warns=3, stream=None),
        )
        cfg = object.__getattribute__(proxy, "_DeprecatedProxy__config")
        assert cfg.obj is obj
        assert cfg.num_warns == 3
        assert cfg.stream is None
        assert cfg.read_only is False
        assert cfg.state.warned_calls == 0
        meta = object.__getattribute__(proxy, "__deprecation_config__")
        assert meta.name == "x"
        assert meta.deprecated_in == "1.0"
        assert meta.remove_in == "2.0"
        assert meta.target is None

    def test_stacking_reads_legacy_proxy_metadata_through_accessor(self) -> None:
        """Stack a current class proxy over metadata created with the legacy layout.

        Rolling upgrades may expose a pre-v0.13 proxy as the input to a current ``deprecated_class`` call. The new
        proxy must preserve its declared name without dereferencing an attribute the inner proxy does not have.
        """
        proxy = make_stacked_legacy_proxy()

        meta = object.__getattribute__(proxy, "__deprecation_config__")
        assert meta.name == "LegacyClass"
        assert meta.args_mapping is None


class TestProxyReadOnly:
    """Read-only mode enforcement."""

    def test_raises_when_active(self) -> None:
        """_check_read_only raises AttributeError in read_only mode."""
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(name="d", deprecated_in="1.0", remove_in="2.0", read_only=True),
        )
        with pytest.raises(AttributeError, match="read-only"):
            proxy._check_read_only("Test operation")

    def test_silent_when_inactive(self) -> None:
        """_check_read_only does nothing when read_only is False."""
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(name="d", deprecated_in="1.0", remove_in="2.0", read_only=False),
        )
        proxy._check_read_only("Test operation")  # must not raise

    @pytest.mark.parametrize(
        "operation",
        [
            pytest.param(lambda p: p.__setitem__("k", 2), id="setitem"),
            pytest.param(lambda p: p.__delitem__("k"), id="delitem"),
            pytest.param(lambda p: setattr(p, "some_attr", "value"), id="setattr"),
            pytest.param(lambda p: delattr(p, "some_attr"), id="delattr"),
        ],
    )
    def test_mutation_raises_when_read_only(self, operation: Callable) -> None:
        """All write operations raise AttributeError in read_only mode."""
        proxy = _DeprecatedProxy(
            obj={"k": 1},
            spec=_ProxySpec(name="d", deprecated_in="1.0", remove_in="2.0", read_only=True, stream=None),
        )
        with pytest.raises(AttributeError, match="read-only"):
            operation(proxy)

    def test_setitem_forwards_to_source(self) -> None:
        """``__setitem__`` mutates the source object when not read-only, without emitting a warning."""
        inner = {"k": 1}
        proxy = _DeprecatedProxy(
            obj=inner,
            spec=_ProxySpec(name="d", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy["k"] = 99
        assert inner["k"] == 99
        assert not caught

    def test_delitem_removes_from_source(self) -> None:
        """``__delitem__`` removes the key from the source object when not read-only."""
        inner = {"k": 1, "m": 2}
        proxy = _DeprecatedProxy(
            obj=inner,
            spec=_ProxySpec(name="d", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
        del proxy["k"]
        assert "k" not in inner
        assert "m" in inner

    def test_custom_mutator_bypasses_read_only_guard(self) -> None:
        """Custom method names not in the blocked set pass through ``read_only=True`` (known limitation)."""

        class RegistryWithCustomMutator:
            def __init__(self) -> None:
                self.items: list[str] = []

            def register(self, item: str) -> None:
                self.items.append(item)

        obj = RegistryWithCustomMutator()
        proxy = deprecated_instance(obj, deprecated_in="1.0", remove_in="2.0", read_only=True, stream=None)
        # `register` is not in the blocked set — it must NOT raise
        proxy.register("x")
        assert obj.items == ["x"]


class TestProxyGetActive:
    """Active object selection: source vs target."""

    def test_returns_obj_when_no_target(self) -> None:
        """Without target, _get_active returns the source object."""
        obj = {"k": 1}
        proxy = _DeprecatedProxy(obj=obj, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        assert proxy._get_active() is obj

    def test_returns_target_when_set(self) -> None:
        """With target set, _get_active returns the target."""
        obj = {"k": 1}
        tgt = {"k": 2}
        proxy = _DeprecatedProxy(obj=obj, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0", target=tgt))
        assert proxy._get_active() is tgt

    def test_writes_mutate_target_when_set(self) -> None:
        """Write operations mutate the active target object when a target is configured."""
        source = {"k": 1}
        target = {"k": 2}
        proxy = _DeprecatedProxy(
            obj=source,
            spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0", target=target, stream=None),
        )
        proxy["k"] = 9
        assert source["k"] == 1
        assert target["k"] == 9


class TestProxyNoWarnMethods:
    """Methods that delegate without emitting a warning."""

    def test_repr_no_warn(self) -> None:
        """``__repr__`` delegates to the source without warning."""
        inner = [1, 2, 3]
        proxy = _DeprecatedProxy(obj=inner, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            r = repr(proxy)
        assert r == repr(inner)
        assert not caught

    def test_str_no_warn(self) -> None:
        """``__str__`` delegates without warning."""
        inner = {"a": 1}
        proxy = _DeprecatedProxy(obj=inner, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert str(proxy) == str(inner)
        assert not caught

    def test_bool_no_warn(self) -> None:
        """``__bool__`` delegates without warning."""
        proxy_t = _DeprecatedProxy(obj=[1], spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        proxy_f = _DeprecatedProxy(obj=[], spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert bool(proxy_t)
            assert not bool(proxy_f)
        assert not caught

    def test_len_no_warn(self) -> None:
        """``__len__`` delegates without warning."""
        proxy = _DeprecatedProxy(obj=[1, 2, 3], spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert len(proxy) == 3
        assert not caught

    def test_len_uses_target_when_set(self) -> None:
        """__len__ reflects the target when a target is configured."""
        proxy = _DeprecatedProxy(
            obj=[1],
            spec=_ProxySpec(target=[1, 2, 3], name="x", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
        assert len(proxy) == 3

    def test_contains_no_warn(self) -> None:
        """__contains__ delegates without warning."""
        proxy = _DeprecatedProxy(obj={"k": 1}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert "k" in proxy
            assert "z" not in proxy
        assert not caught

    def test_contains_uses_target_when_set(self) -> None:
        """__contains__ reflects the target when a target is configured."""
        proxy = _DeprecatedProxy(
            obj={"old": 1},
            spec=_ProxySpec(target={"new": 2}, name="x", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
        assert "new" in proxy
        assert "old" not in proxy

    def test_eq_no_warn(self) -> None:
        """__eq__ does not emit a warning."""
        inner = {"a": 1}
        proxy = _DeprecatedProxy(obj=inner, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = proxy == inner
        assert result
        assert not caught

    def test_eq_proxy_vs_proxy(self) -> None:
        """Two proxies wrapping equal objects compare equal."""
        inner = {"a": 1}
        p1 = _DeprecatedProxy(obj=inner, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        p2 = _DeprecatedProxy(obj=inner, spec=_ProxySpec(name="y", deprecated_in="2.0", remove_in="3.0"))
        assert p1 == p2

    def test_ne(self) -> None:
        """__ne__ is the inverse of __eq__."""
        p1 = _DeprecatedProxy(obj={"a": 1}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        p2 = _DeprecatedProxy(obj={"a": 2}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        assert p1 != p2

    def test_hash_matches_inner(self) -> None:
        """``hash(proxy)`` equals hash(wrapped object) for hashable types, without emitting a warning."""
        inner = (1, 2, 3)
        proxy = _DeprecatedProxy(
            obj=inner,
            spec=_ProxySpec(name="t", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            h = hash(proxy)
        assert h == hash(inner)
        assert not caught


class TestProxyWarnMethods:
    """Methods that warn on access."""

    def test_getitem_warns(self) -> None:
        """__getitem__ emits warning."""
        proxy = _DeprecatedProxy(obj={"k": 99}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with pytest.warns(FutureWarning, match=r"The `x` was deprecated since v1\.0"):
            val = proxy["k"]
        assert val == 99

    def test_getattr_warns(self) -> None:
        """__getattr__ emits warning."""
        proxy = _DeprecatedProxy(obj={"k": 1}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with pytest.warns(FutureWarning, match=r"The `x` was deprecated since v1\.0"):
            method = proxy.get
        assert callable(method)

    def test_iter_warns(self) -> None:
        """__iter__ emits warning and yields all elements of the wrapped iterable."""
        proxy = _DeprecatedProxy(obj=[10, 20, 30], spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with pytest.warns(FutureWarning, match=r"The `x` was deprecated since v1\.0"):
            items = list(proxy)
        assert items == [10, 20, 30]

    def test_call_warns_and_invokes(self) -> None:
        """__call__ emits warning and invokes the active object."""
        proxy = _DeprecatedProxy(obj=lambda x: x * 2, spec=_ProxySpec(name="fn", deprecated_in="1.0", remove_in="2.0"))
        with pytest.warns(FutureWarning, match=r"The `fn` was deprecated since v1\.0"):
            result = proxy(5)
        assert result == 10

    def test_call_with_target_invokes_target(self) -> None:
        """__call__ with a target invokes the target, not the source."""
        source = lambda x: x  # noqa: E731
        target = lambda x: x * 3  # noqa: E731
        proxy = _DeprecatedProxy(
            obj=source,
            spec=_ProxySpec(name="fn", deprecated_in="1.0", remove_in="2.0", target=target, stream=None),
        )
        assert proxy(4) == 12


class TestContainerProtocolWithTarget:
    """Container protocol behaviour when a target is set on the proxy.

    Pins the source-vs-target routing so it is not silently changed. __len__, __contains__, and __bool__ all use
    _get_active() (the target when set). See TestProxyNoWarnMethods for the no-target variants of __len__ and
    __contains__.

    """

    def test_bool_reads_from_target_when_set(self) -> None:
        """``bool(proxy)`` evaluates the active object (target when set), not the original source."""
        proxy = _DeprecatedProxy(
            obj=[1, 2, 3],
            spec=_ProxySpec(target=[], name="x", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
        assert not bool(proxy)


class TestHashOnUnhashableType:
    """``hash()`` behaviour for proxies wrapping unhashable objects."""

    def test_hash_raises_for_unhashable_source(self) -> None:
        """``hash(proxy)`` raises TypeError when the wrapped object is unhashable (e.g. dict).

        Propagates TypeError from the underlying hash() call with no additional context.

        """
        proxy = _DeprecatedProxy(
            obj={"k": 1},
            spec=_ProxySpec(name="d", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
        with pytest.raises(TypeError):
            hash(proxy)


class TestTypeProtocol:
    """Tests for __instancecheck__ and __subclasscheck__ on _DeprecatedProxy."""

    def test_isinstance_delegates_to_target_class(self) -> None:
        """``isinstance(x, proxy)`` returns True when x is an instance of the target class."""

        class NewConfig:
            pass

        @deprecated_class(target=NewConfig, deprecated_in="1.0", remove_in="2.0", stream=None)
        class OldConfig:
            pass

        obj = NewConfig()
        assert isinstance(obj, OldConfig)

    def test_isinstance_returns_false_for_unrelated_type(self) -> None:
        """``isinstance(x, proxy)`` returns False when x is not an instance of the target."""

        class NewConfig:
            pass

        @deprecated_class(target=NewConfig, deprecated_in="1.0", remove_in="2.0", stream=None)
        class OldConfig:
            pass

        assert not isinstance(42, OldConfig)

    def test_isinstance_no_warning_emitted(self) -> None:
        """``isinstance(x, proxy)`` is a structural check — must not consume the warning budget."""

        class Target:
            pass

        proxy = _DeprecatedProxy(
            obj=Target,
            spec=_ProxySpec(name="old", deprecated_in="1.0", remove_in="2.0", num_warns=1),
        )
        obj = Target()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            isinstance(obj, cast(Any, proxy))

        assert not caught  # no warning from isinstance
        with pytest.warns(FutureWarning):
            proxy()  # warning budget remains untouched

    def test_issubclass_delegates_to_target_class(self) -> None:
        """``issubclass(Sub, proxy)`` returns True when Sub is a subclass of the target."""

        class Base:
            pass

        class Sub(Base):
            pass

        @deprecated_class(target=Base, deprecated_in="1.0", remove_in="2.0", stream=None)
        class OldBase:
            pass

        assert issubclass(Sub, OldBase)

    def test_issubclass_respects_metaclass_semantics(self) -> None:
        """Issubclass uses the target metaclass logic (including virtual subclasses)."""

        class AbstractBase(metaclass=abc.ABCMeta):
            pass

        class VirtualSubclass:
            pass

        AbstractBase.register(VirtualSubclass)

        @deprecated_class(target=AbstractBase, deprecated_in="1.0", remove_in="2.0", stream=None)
        class OldAbstractBase:
            pass

        assert issubclass(VirtualSubclass, OldAbstractBase)

    def test_issubclass_no_warning_emitted(self) -> None:
        """``issubclass(Sub, proxy)`` is structural and must not consume warning budget."""

        class Base:
            pass

        class Sub(Base):
            pass

        proxy = _DeprecatedProxy(obj=Base, spec=_ProxySpec(name="old_cls", deprecated_in="1.0", remove_in="2.0"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            issubclass(Sub, cast(Any, proxy))
        assert not caught
        with pytest.warns(FutureWarning):
            proxy()  # warning budget remains untouched

    def test_isinstance_raises_typeerror_for_non_type_active(self) -> None:
        """``isinstance(x, instance_proxy)`` raises TypeError like the builtin.

        Using an *instance* proxy (one wrapping a value rather than a class) as the second argument to
        ``isinstance`` is a misuse. Previously the proxy silently returned ``False``, hiding the mistake; it now
        raises the same ``TypeError`` the builtin raises when arg 2 is not a type.
        """
        proxy = _DeprecatedProxy(
            obj={"key": "val"},
            spec=_ProxySpec(name="old_cfg", deprecated_in="1.0", remove_in="2.0"),
        )
        with pytest.raises(TypeError, match="arg 2 must be a type"):
            isinstance(42, cast(Any, proxy))


class TestPEP702ProxyStackingRegression:
    """Stacking ``typing_extensions.deprecated`` outside ``deprecated_class`` does not break the proxy (B1b).

    PEP 702's ``typing_extensions.deprecated`` assigns ``arg.__deprecated__ = msg`` on the
    object it decorates.  For a ``_DeprecatedProxy`` instance, ``__setattr__`` stores that
    message on the proxy itself — never on the wrapped class — while pyDeprecate's
    configuration stays on ``__deprecation_config__`` untouched.  These tests guard both
    halves: the proxy keeps working, and the replacement class is never marked deprecated.

    """

    def test_pep702_proxy_stacked_instantiation_does_not_crash(self) -> None:
        """Stacked PEP 702 + ``deprecated_class`` proxy instantiates and dispatches methods."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            instance = pep702_proxy_stacked()
            assert instance.value() == 42

    def test_pep702_proxy_stacked_returns_target_instance(self) -> None:
        """The stacked wrapper produces an instance of the wrapped target class."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            instance = pep702_proxy_stacked()
        assert isinstance(instance, _Pep702ProxyTarget)

    def test_pep702_proxy_stacked_emits_pep702_deprecation_warning(self) -> None:
        """Outer ``typing_extensions.deprecated`` emits its DeprecationWarning on call, and the inner proxy still warns.

        A library stacks the static-checker decorator over a ``deprecated_class`` alias.  One call must deliver both the
        PEP 702 ``DeprecationWarning`` and pyDeprecate's own ``FutureWarning``; neither layer may swallow the other.

        """
        with (
            pytest.warns(FutureWarning, match="`_Pep702ProxyTarget` was deprecated since v0.8"),
            pytest.warns(DeprecationWarning, match="use `Pep702ProxyTarget`"),
        ):
            pep702_proxy_stacked()

    @pytest.mark.parametrize(
        "target_cls",
        [
            pytest.param(_Pep702ProxyTarget, id="default-category"),
            pytest.param(Pep702StaticTarget, id="category-none"),
        ],
    )
    def test_replacement_class_stays_undeprecated(self, target_cls: type) -> None:
        """The PEP 702 marker written onto a stacked proxy never reaches the replacement class.

        A library stacks ``typing_extensions.deprecated`` over a ``deprecated_class`` alias so type checkers flag the
        old name. PEP 702 writes ``__deprecated__`` onto the object it decorates — the proxy. Forwarding that write to
        the wrapped class would mark the *replacement* deprecated for every tool that reads the attribute.

        """
        assert "__deprecated__" not in vars(target_cls)

    def test_stacked_alias_carries_pep702_message(self) -> None:
        """The proxy itself holds the PEP 702 message after stacking, as PEP 702 specifies for the decorated object.

        Runtime tools reading ``StackedAlias.__deprecated__`` must see the message the author gave the static-checker
        decorator, while pyDeprecate's own configuration stays reachable through ``get_deprecation_config``.

        """
        assert object.__getattribute__(StackedAlias, "__deprecated__") == "Use `Pep702StaticTarget` instead."

    def test_pep702_marker_write_ignores_read_only_and_skip_if(self) -> None:
        """Writing ``__deprecated__`` on a read-only, skipped proxy updates proxy metadata instead of raising.

        ``__deprecated__`` is metadata about the proxy, not state of the wrapped object: the read-only guard and the
        ``skip_if`` pass-through (which would forward the write to a plain ``list`` and raise) must not apply to it.

        """
        proxy = make_deprecated_instance_skip_if_true_read_only()
        proxy.__deprecated__ = "Use `new_list` instead."
        assert object.__getattribute__(proxy, "__deprecated__") == "Use `new_list` instead."

    def test_pep702_marker_delete_ignores_read_only_and_skip_if(self) -> None:
        """Deleting proxy metadata leaves the wrapped object unchanged under skip and read-only settings.

        A static checker decorator may remove its own marker during cleanup. The proxy must delete its local marker
        without forwarding the deletion to the read-only list it wraps.

        """
        proxy = make_deprecated_instance_skip_if_true_read_only()
        wrapped = proxy.__wrapped__
        before = list(wrapped)

        del proxy.__deprecated__

        assert "__deprecated__" not in vars(proxy)
        assert wrapped == before

    def test_deleted_marker_reads_as_missing(self) -> None:
        """After ``del proxy.__deprecated__`` the marker reads as missing, never as the wrapped object's own marker.

        A tool that removes the proxy's PEP 702 marker and then probes for it must see ``AttributeError`` — falling
        through to the wrapped object would report *its* marker as the proxy's, and a second ``del`` must fail the
        same way it does on any object whose attribute is already gone.

        """
        proxy = make_deprecated_instance_over_marked_object()
        del proxy.__deprecated__

        with pytest.raises(AttributeError):
            _ = proxy.__deprecated__
        with pytest.raises(AttributeError):
            del proxy.__deprecated__

    @pytest.mark.parametrize(
        "duplicate",
        [
            pytest.param(copy.copy, id="copy"),
            pytest.param(copy.deepcopy, id="deepcopy"),
            pytest.param(lambda obj: pickle.loads(pickle.dumps(obj)), id="pickle"),  # noqa: S301
        ],
    )
    def test_copy_keeps_user_set_marker(self, duplicate: Callable[[Any], Any]) -> None:
        """A PEP 702 message written onto the proxy survives copy, deepcopy, and a pickle round-trip.

        A library stacks a PEP 702 decorator over a deprecated alias, and later code copies or pickles that alias
        (config snapshots, multiprocessing). The duplicate must carry the message the author wrote, not a freshly
        rendered pyDeprecate message that silently replaces it.

        """
        proxy = make_deprecated_instance_over_marked_object()
        proxy.__deprecated__ = "Use `new_settings` instead."

        duplicated = duplicate(proxy)

        assert object.__getattribute__(duplicated, "__deprecated__") == "Use `new_settings` instead."

    @pytest.mark.parametrize(
        "duplicate",
        [
            pytest.param(copy.copy, id="copy"),
            pytest.param(copy.deepcopy, id="deepcopy"),
            pytest.param(lambda obj: pickle.loads(pickle.dumps(obj)), id="pickle"),  # noqa: S301
        ],
    )
    def test_copy_keeps_deleted_marker_deleted(self, duplicate: Callable[[Any], Any]) -> None:
        """A proxy whose PEP 702 marker was deleted stays marker-less after copy, deepcopy, and a pickle round-trip.

        A static-checker decorator removes its marker during cleanup, and later code copies or pickles the alias. The
        duplicate must not re-add a freshly rendered ``__deprecated__`` that the author deliberately removed, nor
        expose the wrapped object's own marker in its place.

        """
        proxy = make_deprecated_instance_over_marked_object()
        del proxy.__deprecated__

        duplicated = duplicate(proxy)

        assert "__deprecated__" not in vars(duplicated)
        with pytest.raises(AttributeError):
            _ = duplicated.__deprecated__

    def test_legacy_payload_renders_default_marker(self) -> None:
        """A pickle payload written before the marker was recorded rebuilds a proxy with the default message.

        Pickles stored by an older release carry only ``(cfg, dep, doc)``; loading one must still give a proxy whose
        ``__deprecated__`` is the rendered default message, not a missing attribute and not the wrapped object's own
        marker. The payload is built from the current ``__reduce_ex__`` result with the marker argument dropped.

        """
        proxy = make_deprecated_instance_over_marked_object()
        reconstructor, args = proxy.__reduce_ex__(2)[:2]

        rebuilt = reconstructor(*args[:3])

        marker = object.__getattribute__(rebuilt, "__deprecated__")
        assert marker == object.__getattribute__(proxy, "__deprecated__")
        assert marker != "wrapped object's own marker"


class TestProxyIntrospectionProbes:
    """Introspection probes must not warn spuriously or exhaust the warn budget.

    With the default ``num_warns=1`` the single warning a user ever sees must not be consumed by
    machinery — ``hasattr`` duck-typing probes, ``copy.deepcopy`` protocol probes, or doc tools
    reading dunders — otherwise the first *real* deprecated access is silent.
    """

    def test_hasattr_missing_does_not_warn(self) -> None:
        """A library duck-types the config object with ``hasattr`` for an absent attribute.

        The failed probe must stay silent: the attribute is resolved first and the warning fires
        only on successful access.
        """
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert not hasattr(proxy, "missing_attr")
        assert not caught

    def test_missing_attribute_raises_without_burning_budget(self) -> None:
        """After a failed attribute lookup the first real access must still warn.

        The ``num_warns=1`` budget must survive the ``AttributeError`` path so the deprecation
        notice reaches actual user code.
        """
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", num_warns=1)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            with pytest.raises(AttributeError):
                _ = proxy.missing_attr
        with pytest.warns(FutureWarning, match=r"The `cfg` was deprecated since v1\.0"):
            _ = proxy.get

    def test_dunder_access_does_not_warn(self) -> None:
        """A doc tool reads ``__mro__`` on a deprecated class alias (sphinx-style introspection).

        Dunder reads are machinery, never a user migrating code — they must neither warn nor
        consume the budget, mirroring the ``__instancecheck__`` "structural check" rationale.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = DeprecatedColorEnum.__mro__
        assert not caught

    def test_deepcopy_does_not_consume_warn_budget(self) -> None:
        """``copy.deepcopy`` probes copy-protocol dunders before copying.

        Those probes must not consume the ``num_warns=1`` budget: the first real access on the
        original proxy after a deepcopy must still emit the warning.
        """
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", num_warns=1)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            copy.deepcopy(proxy)
        assert not caught
        with pytest.warns(FutureWarning, match=r"The `cfg` was deprecated since v1\.0"):
            _ = proxy.get


class TestProxyClassProperty:
    """``__class__`` forwarding gives instance-side type transparency.

    Downstream code type-checks deprecated objects (json encoders, pydantic validators, plain
    defensive ``isinstance``); wrapping an object in a proxy must not change those checks during
    the migration window.
    """

    def test_isinstance_of_wrapped_builtin_type(self) -> None:
        """Consumer code calls ``isinstance(cfg, dict)`` on a deprecated config dict."""
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert isinstance(proxy, dict)

    def test_isinstance_against_abc(self) -> None:
        """Consumer code checks the deprecated config against ``collections.abc.Mapping``."""
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert isinstance(proxy, Mapping)

    def test_isinstance_uses_target_when_set(self) -> None:
        """With a target configured, ``__class__`` reflects the active (target) object's type."""
        proxy = _DeprecatedProxy(
            obj=[1],
            spec=_ProxySpec(target={"a": 1}, name="x", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
        assert isinstance(proxy, dict)
        assert not isinstance(proxy, list)

    def test_isinstance_walks_stacked_proxies(self) -> None:
        """A proxy stacked over another proxy resolves ``__class__`` to the innermost object."""
        inner = deprecated_instance({"k": 1}, name="inner", deprecated_in="1.0", remove_in="2.0", stream=None)
        outer = deprecated_instance(inner, name="outer", deprecated_in="1.1", remove_in="2.0", stream=None)
        assert isinstance(outer, dict)

    def test_class_proxy_keeps_reporting_proxy_class(self) -> None:
        """A deprecated class alias must not claim to be a ``type``.

        Class-dispatching decorators (e.g. PEP 702 ``typing_extensions.deprecated`` stacked over
        the proxy) branch on ``isinstance(arg, type)`` — forwarding the metaclass would make them
        patch the wrapped class in place instead of wrapping the proxy callable.  Class-side
        transparency is covered by ``__instancecheck__``/``__subclasscheck__`` instead.
        """
        assert DeprecatedColorEnum.__class__ is _DeprecatedProxy
        assert not isinstance(DeprecatedColorEnum, type)

    def test_type_still_reveals_proxy(self) -> None:
        """``type(proxy)`` keeps returning the proxy class for code that needs the truth."""
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert type(proxy) is _DeprecatedProxy
        assert isinstance(proxy, _DeprecatedProxy)

    def test_isinstance_does_not_warn(self) -> None:
        """``isinstance(proxy, dict)`` is structural — no warning, budget untouched."""
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", num_warns=1)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            isinstance(proxy, dict)
        assert not caught
        with pytest.warns(FutureWarning, match=r"The `cfg` was deprecated since v1\.0"):
            _ = proxy.get


class TestProxyCopyPickle:
    """``copy.copy`` / ``copy.deepcopy`` / pickle round-trips reconstruct the proxy.

    A library ships a deprecated module-level config dict wrapped in ``deprecated_instance``;
    downstream consumers routinely snapshot such configs with ``copy``/``deepcopy`` or ship them
    across process boundaries via pickle.  All three must return a working proxy (deprecation
    semantics preserved) instead of crashing with ``RecursionError``.
    """

    def test_copy_returns_proxy_wrapping_copied_object(self) -> None:
        """A consumer takes a shallow working copy of a deprecated config dict.

        The copy must be a proxy again (the deprecation travels with the object) and must wrap
        an independent shallow copy — mutating the copy must not touch the original config.
        """
        proxy = deprecated_instance({"threshold": 0.5}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        dup = copy.copy(proxy)
        assert type(dup) is _DeprecatedProxy
        assert dup["threshold"] == 0.5
        dup["threshold"] = 0.9
        assert proxy["threshold"] == 0.5

    def test_deepcopy_returns_proxy_with_independent_nested_state(self) -> None:
        """A consumer deep-copies a deprecated nested config before mutating it.

        ``deepcopy`` of the proxy must deep-copy the wrapped object so nested containers are
        fully independent of the original.
        """
        proxy = deprecated_instance(
            {"limits": {"low": 1}}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None
        )
        dup = copy.deepcopy(proxy)
        assert type(dup) is _DeprecatedProxy
        dup["limits"]["low"] = 99
        assert proxy["limits"]["low"] == 1

    def test_copy_preserves_deprecation_metadata(self) -> None:
        """Preserve deprecation metadata when copying a proxy.

        Applications commonly copy configuration-like proxies before mutation; the duplicate must remain visible to
        audit tooling with the same name and lifecycle versions.
        """
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        dup = copy.copy(proxy)
        meta = object.__getattribute__(dup, "__deprecation_config__")
        assert meta.name == "cfg"
        assert meta.deprecated_in == "1.0"
        assert meta.remove_in == "2.0"

    def test_pickle_roundtrip_preserves_proxy_and_warning(self) -> None:
        """A deprecated config object crosses a process boundary via pickle.

        The unpickled object must be a proxy again with intact metadata and default warning
        stream — the first real access on the restored proxy still emits ``FutureWarning``.
        """
        proxy = deprecated_instance({"k": 41}, name="cfg", deprecated_in="1.0", remove_in="2.0")

        restored = pickle.loads(pickle.dumps(proxy))  # noqa: S301
        assert type(restored) is _DeprecatedProxy
        assert object.__getattribute__(restored, "__deprecation_config__").name == "cfg"
        with pytest.warns(FutureWarning, match=r"The `cfg` was deprecated since v1\.0"):
            assert restored["k"] == 41

    def test_deepcopy_of_class_proxy_keeps_wrapped_class(self) -> None:
        """Deep-copying a deprecated class alias keeps forwarding to the same target class.

        Classes are atomic under ``deepcopy``, so the copied proxy must forward to the identical
        target class object.
        """
        dup = copy.deepcopy(DeprecatedColorEnum)
        assert type(dup) is _DeprecatedProxy
        assert dup.RED is ColorEnum.RED

    def test_uninitialised_instance_raises_attribute_error(self) -> None:
        """Copy/pickle machinery creates instances via ``cls.__new__(cls)`` with no config set.

        Attribute access on such a half-initialised proxy must raise a clean ``AttributeError``
        (routing back into ``__getattr__`` must not recurse into the ``_cfg`` property again).
        """
        blank = _DeprecatedProxy.__new__(_DeprecatedProxy)
        with pytest.raises(AttributeError):
            _ = blank.anything

    def test_copy_of_exhausted_proxy_is_silent(self) -> None:
        """A copy of an already-warned proxy inherits the exhausted budget and stays silent.

        With ``num_warns=1`` the original can warn exactly once.  After that warning fires,
        copying the proxy snapshots the exhausted counter — the copy never warns, even though
        it was never accessed before the copy.  Each copy is independent of the original but
        inherits the counter value at copy time, not a fresh budget.
        """
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", num_warns=1)
        with pytest.warns(FutureWarning):
            _ = proxy["k"]  # exhaust the budget on the original
        dup = copy.copy(proxy)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            _ = dup["k"]  # must not warn — counter is snapshotted as exhausted

    def test_pickle_raises_for_nonpicklable_stream(self) -> None:
        """A proxy with a non-picklable stream (lambda) cannot be pickled.

        The ``stream`` callable is serialised as part of the internal config; lambdas and
        closures are not picklable by the standard pickle protocol.  This test documents the
        known limitation so future changes do not silently regress it.
        """
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=lambda msg: None)
        with pytest.raises((AttributeError, pickle.PicklingError)):
            pickle.dumps(proxy)

    def test_pickle_raises_for_class_proxy(self) -> None:
        """Pickling a deprecated_class proxy is not supported when the class name is replaced.

        When ``@deprecated_class`` replaces the module-level name ``DeprecatedColorEnum`` with
        a proxy, the wrapped class (``cfg.obj``) cannot be serialised by reference: pickle
        finds the proxy at ``tests.collection_deprecate.DeprecatedColorEnum``, not the original
        class.  This test documents the known limitation — ``deprecated_instance`` proxies
        wrapping plain objects (dicts, lists) remain fully picklable.
        """
        with pytest.raises(pickle.PicklingError):
            pickle.dumps(DeprecatedColorEnum)

    def test_copy_preserves_ast_breadcrumbs(self) -> None:
        """A shallow copy carries the same AST breadcrumbs as a freshly constructed proxy.

        Reconstruction bypasses ``__init__``, so the breadcrumbs have to be re-established explicitly.
        Without that, a consumer who snapshots a deprecated symbol with ``copy.copy`` gets a proxy that
        ``inspect.unwrap`` can no longer see through and that no longer structurally satisfies the public
        ``DeprecationProxy`` protocol — the "every proxy carries ``__wrapped__``" contract would hold only until
        somebody copied one.
        """
        proxy = deprecated_instance(
            base_sum_kwargs, name="legacy_sum", deprecated_in="1.0", remove_in="2.0", stream=None
        )

        dup = copy.copy(proxy)

        assert dup.__wrapped__ is base_sum_kwargs
        assert dup.__signature__ == inspect.signature(base_sum_kwargs)
        assert isinstance(dup, DeprecationProxy)

    def test_deepcopy_rebinds_breadcrumb_to_copied_object(self) -> None:
        """A deep copy points ``__wrapped__`` at its *own* wrapped object, not the original's.

        ``deepcopy`` duplicates the wrapped payload, so a breadcrumb naively carried over from the source
        proxy would describe an object the copy does not actually serve — mutating the copy would leave
        ``inspect.unwrap`` reporting stale state. The breadcrumb must track the object the copy forwards to.
        """
        proxy = deprecated_instance(
            {"limits": {"low": 1}}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None
        )

        dup = copy.deepcopy(proxy)

        assert dup.__wrapped__ is object.__getattribute__(dup, "_DeprecatedProxy__config").obj
        assert dup.__wrapped__ is not proxy.__wrapped__

    def test_deepcopy_of_class_proxy_preserves_breadcrumbs(self) -> None:
        """Deep-copying a deprecated class alias keeps both breadcrumbs intact.

        Classes are atomic under ``deepcopy``, so the copy forwards to the identical source class and must
        report exactly the same ``__wrapped__`` and ``__signature__`` as the original alias — this is the
        path Sphinx autodoc and IDEs walk when they encounter a copied module namespace.
        """
        original = cast(DeprecationProxy[Any], DeprecatedColorEnum)

        dup = cast(DeprecationProxy[Any], copy.deepcopy(DeprecatedColorEnum))

        assert dup.__wrapped__ is original.__wrapped__
        assert dup.__signature__ == original.__signature__
        assert dup.__signature__ is not None

    def test_pickle_roundtrip_preserves_ast_breadcrumbs(self) -> None:
        """A pickled proxy comes back with its breadcrumbs restored.

        ``__reduce_ex__`` rebuilds the proxy through the same reconstruction helper as ``copy``, so a proxy
        shipped across a process boundary must arrive introspectable rather than silently degraded. The
        signature is recomputed from the restored source instead of travelling through the pickle stream,
        which keeps ``Signature`` objects out of the payload entirely.
        """
        proxy = deprecated_instance(
            base_sum_kwargs, name="legacy_sum", deprecated_in="1.0", remove_in="2.0", stream=None
        )

        restored = pickle.loads(pickle.dumps(proxy))  # noqa: S301

        assert restored.__wrapped__ is base_sum_kwargs
        assert restored.__signature__ == inspect.signature(base_sum_kwargs)


class TestProxySubclassing:
    """PEP 560 subclassing of deprecated class aliases via ``__mro_entries__``."""

    def test_subclass_derives_from_active_class(self) -> None:
        """``class Child(DeprecatedAlias)`` transparently subclasses the wrapped class and inherits its behaviour.

        During a migration window a public base class is renamed and wrapped in ``deprecated_class``; existing
        downstream ``class Child(OldName): ...`` definitions must keep resolving to a real, usable base class.
        """
        alias = deprecated_class(deprecated_in="1.0", remove_in="2.0", stream=None)(SubclassableBase)

        class Child(alias):  # type: ignore[misc,valid-type]
            """Subclass built off the deprecated alias."""

        assert (issubclass(Child, SubclassableBase), Child().greet()) == (True, "hello")

    def test_subclassing_warns_once(self) -> None:
        """Subclassing a deprecated alias is a use of the deprecated name and emits one deprecation warning."""
        alias = deprecated_class(deprecated_in="1.0", remove_in="2.0")(SubclassableBase)
        with pytest.warns(FutureWarning, match=r"The `SubclassableBase` was deprecated since v1\.0"):

            class Child(alias):  # type: ignore[misc,valid-type]
                """Subclass whose creation should warn."""

        assert issubclass(Child, SubclassableBase)

    def test_attrs_remap_subclassing_does_not_warn(self) -> None:
        """An ATTRS_REMAP alias deprecates only listed attributes, so subclassing it stays silent.

        Mirrors the ``__call__`` policy: when only specific attribute aliases are deprecated, deriving a new
        class from the alias is not a deprecated surface and must not brand the whole class deprecated.
        """
        alias = deprecated_class(attrs_mapping={"marker": None}, deprecated_in="1.0", remove_in="2.0")(SubclassableBase)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            class Child(alias):  # type: ignore[misc,valid-type]
                """Subclass whose creation must stay silent under ATTRS_REMAP."""

        assert not [w for w in caught if issubclass(w.category, FutureWarning)]
        assert issubclass(Child, SubclassableBase)

    def test_args_remap_subclassing_does_not_warn(self) -> None:
        """An ARGS_REMAP alias deprecates only constructor argument names, so subclassing it stays silent.

        ARGS_REMAP and ATTRS_REMAP share the same subclassing guard in ``__mro_entries__``: both scope
        deprecation to a specific axis (argument names or attribute names) rather than the class name
        itself, so deriving a new class from the alias must not fire any deprecation warning.
        """
        alias = deprecated_class(args_mapping={"old_arg": "new_arg"}, deprecated_in="1.0", remove_in="2.0")(
            SubclassableBase
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            class Child(alias):  # type: ignore[misc,valid-type]
                """Subclass whose creation must stay silent under ARGS_REMAP."""

        assert not [w for w in caught if issubclass(w.category, FutureWarning)]
        assert issubclass(Child, SubclassableBase)


class TestProxyIdentityUsesActive:
    """``__eq__`` / ``__hash__`` / ``__repr__`` / ``__str__`` reflect the served (active) object."""

    def test_eq_matches_active_target_not_source(self) -> None:
        """A target-forwarding proxy compares equal to the object it actually serves, not the deprecated source.

        Data access forwards to the active object (the ``target`` when set), so a proxy comparing equal to its
        source while never returning it was inconsistent. Identity now routes through the active object too.
        """
        proxy = _DeprecatedProxy(
            obj=_P8Source,
            spec=_ProxySpec(target=_P8Target, name="_P8Source", deprecated_in="1.0", remove_in="2.0"),
        )
        assert proxy == _P8Target
        assert proxy != _P8Source

    def test_repr_shows_active_target(self) -> None:
        """``repr`` of a target-forwarding proxy shows the active object rather than the deprecated source."""
        proxy = _DeprecatedProxy(
            obj=_P8Source,
            spec=_ProxySpec(target=_P8Target, name="_P8Source", deprecated_in="1.0", remove_in="2.0"),
        )
        assert repr(proxy) == repr(_P8Target)


class TestProxySubclassCheckTypeError:
    """``issubclass`` with an instance proxy raises TypeError like the builtin."""

    def test_issubclass_raises_typeerror_for_non_type_active(self) -> None:
        """Using an instance proxy as ``issubclass`` arg 2 raises TypeError instead of silently returning False."""
        proxy = _DeprecatedProxy(
            obj={"key": "val"},
            spec=_ProxySpec(name="old_cfg", deprecated_in="1.0", remove_in="2.0"),
        )
        with pytest.raises(TypeError, match="arg 2 must be a class"):
            issubclass(int, cast(Any, proxy))


class TestProxyAstFriendliness:
    """The proxy exposes ``__wrapped__`` and ``__signature__`` for static-analysis tools.

    Tools like Sphinx autodoc and ``inspect.signature`` need a stable breadcrumb back to the
    original callable so they can render the source's documentation and signature rather than
    the proxy's own internals. This set of tests pins down the contract.
    """

    def test_wrapped_points_to_source_on_class_proxy(self) -> None:
        """``DeprecatedColorEnum.__wrapped__`` is the source enum class.

        A user inspecting the deprecated symbol via ``inspect.unwrap`` or an AST-aware tool
        that walks ``__wrapped__`` should land on the original class definition. Here the
        source is the enum class itself (defined in the same fixture, wrapped by the proxy).
        """
        wrapped = _ast_class_proxy.__wrapped__
        cfg = object.__getattribute__(DeprecatedColorEnum, "_DeprecatedProxy__config")
        assert wrapped is cfg.obj
        assert inspect.isclass(wrapped)
        assert issubclass(wrapped, Enum)

    def test_unwrap_reaches_source_on_class_proxy(self) -> None:
        """``inspect.unwrap`` walks the proxy's ``__wrapped__`` chain down to the source class.

        Sphinx autodoc and similar tools call ``inspect.unwrap`` rather than reading ``__wrapped__``
        directly. Since the source enum is not itself a wrapper, unwrapping must terminate there
        instead of looping or stopping at the proxy.
        """
        assert inspect.unwrap(DeprecatedColorEnum) is _ast_class_proxy.__wrapped__

    def test_signature_matches_source_on_class_proxy(self) -> None:
        """``DeprecatedColorEnum.__signature__`` equals ``inspect.signature`` of its source.

        ``inspect.signature`` follows ``__signature__`` when present, so static tools that do
        not walk ``__wrapped__`` still see the source's constructor signature.
        """
        source = _ast_class_proxy.__wrapped__
        assert _ast_class_proxy.__signature__ == inspect.signature(source)

    def test_inspect_signature_on_proxy_returns_source_signature(self) -> None:
        """``inspect.signature(proxy)`` returns the source's signature.

        This is the user-visible payoff: tools that call ``inspect.signature`` directly on the
        deprecated symbol now get the real signature instead of an unhelpful ``(*args, **kwargs)``
        or ``ValueError``.
        """
        source = _ast_class_proxy.__wrapped__
        assert inspect.signature(DeprecatedColorEnum) == inspect.signature(source)

    def test_wrapped_set_on_instance_proxy(self) -> None:
        """``deprecated_instance`` also sets ``__wrapped__`` to the wrapped object.

        Both public entry points must preserve the breadcrumb so the contract is consistent
        across class and instance deprecation.
        """
        assert depr_ast_breadcrumb_dict.__wrapped__ is ast_breadcrumb_source_dict

    def test_signature_silently_skipped_for_non_introspectable_object(self) -> None:
        """``__signature__`` is ``None`` when ``inspect.signature(obj)`` raises.

        Builtins and objects without a Python-level signature (e.g. ``dict``) make
        ``inspect.signature`` raise ``ValueError``/``TypeError``. The proxy must not propagate
        the exception at decoration time — wrapping must stay infallible. The attribute is
        always set (to ``None``) so ``@runtime_checkable`` Protocol checks succeed uniformly.
        """
        assert depr_ast_breadcrumb_dict.__signature__ is None

    def test_signature_fallback_survives_hostile_descriptor(self) -> None:
        """A source whose ``__signature__`` descriptor raises ``RuntimeError`` still wraps successfully.

        ``ValueError``/``TypeError`` are only the *normalised* introspection failures. A wrapped object may
        expose a ``__signature__`` property that raises anything at all — mocks, lazily built extension
        wrappers, and some model metaclasses do — and ``inspect.signature`` forwards that exception verbatim
        instead of converting it. Since the breadcrumbs are a convenience for static tooling, never a
        precondition of the deprecation itself, such a source must degrade to ``__signature__ = None`` rather
        than turning a routine ``deprecated_instance`` call into an import-time crash.
        """
        proxy = make_deprecated_hostile_signature_instance()
        assert proxy.__signature__ is None
        assert isinstance(proxy.__wrapped__, HostileSignatureCallable)

    def test_existing_runtime_behavior_unchanged(self) -> None:
        """Adding ``__wrapped__``/``__signature__`` does not affect runtime forwarding.

        A regression guard: the new attributes must be purely additive. Attribute access,
        ``isinstance``, and item access continue to behave exactly as before.
        """
        with pytest.warns(FutureWarning):
            assert DeprecatedColorEnum.RED is ColorEnum.RED
