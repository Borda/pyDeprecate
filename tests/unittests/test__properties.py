"""Unit tests for @deprecated applied to property, cached_property, and descriptor types."""

import builtins
import types
from functools import cached_property
from typing import Optional
from unittest.mock import patch

import pytest

from deprecate import TargetMode, assert_no_warnings, deprecated
from deprecate import property as strict_property
from deprecate._properties import _DeprecatedProperty, _StrictProperty
from deprecate.audit import validate_deprecation_expiry
from tests._helpers import captured_warnings
from tests.collections.api_deprecated import InnerOrderDeprecatedPropCls, ServiceCls
from tests.collections.api_deprecated_forwarding import (
    DelOnlyDeprecatedPropCls,
    ReceiverNamedFactory,
    ReceiverNamedFactoryChild,
)


class TestDescriptorOrderAgnostic:
    """@deprecated on classmethod/staticmethod works in both decorator orders.

    Inner-deprecated order: ``@classmethod @deprecated`` (``@deprecated`` closer to ``def``).
    Outer-deprecated order: ``@deprecated @classmethod`` (``@deprecated`` outermost).
    Transparent unwrap+rewrap preserves the descriptor type: both @classmethod orders produce
    classmethod(deprecated_wrapper), both @staticmethod orders produce staticmethod(deprecated_wrapper).
    The deprecation warning fires at call time in both cases; no UserWarning fires at decoration time.
    """

    def test_inner_deprecated_classmethod_fires_on_call(self) -> None:
        """Inner @classmethod @deprecated order: deprecation FutureWarning fires on call, descriptor preserved."""

        class _Cls:
            @classmethod
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_method(cls, x: int) -> int:
                """Old classmethod."""
                return x

        with pytest.warns(FutureWarning):
            result = _Cls.old_method(5)
        assert result == 5
        assert isinstance(_Cls.__dict__["old_method"], classmethod)

    def test_outer_deprecated_classmethod_fires_on_call(self) -> None:
        """Outer @deprecated @classmethod order: deprecation FutureWarning still fires on call, descriptor preserved."""

        class _Cls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            @classmethod
            def old_method(cls, x: int) -> int:
                """Old classmethod."""
                return x

        with pytest.warns(FutureWarning):
            result = _Cls.old_method(5)
        assert result == 5
        assert isinstance(_Cls.__dict__["old_method"], classmethod)

    def test_outer_deprecated_classmethod_no_decoration_time_warning(self) -> None:
        """Outer @deprecated @classmethod order: no UserWarning at decoration time (transparent unwrap)."""
        with captured_warnings() as caught:

            class _Cls:
                @deprecated(deprecated_in="1.0", remove_in="2.0")
                @classmethod
                def old_method(cls, x: int) -> int:
                    """Old classmethod."""
                    return x

        assert not [w for w in caught if issubclass(w.category, UserWarning)]

    def test_inner_deprecated_staticmethod_fires_on_call(self) -> None:
        """Inner @staticmethod @deprecated order: deprecation FutureWarning fires on call, descriptor preserved."""

        class _Cls:
            @staticmethod
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_method(x: int) -> int:
                """Old staticmethod."""
                return x

        with pytest.warns(FutureWarning):
            result = _Cls.old_method(5)
        assert result == 5
        assert isinstance(_Cls.__dict__["old_method"], staticmethod)

    def test_outer_deprecated_staticmethod_fires_on_call(self) -> None:
        """Outer @deprecated @staticmethod order: deprecation FutureWarning fires on call, descriptor preserved."""

        class _Cls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            @staticmethod
            def old_method(x: int) -> int:
                """Old staticmethod."""
                return x

        with pytest.warns(FutureWarning):
            result = _Cls.old_method(5)
        assert result == 5
        assert isinstance(_Cls.__dict__["old_method"], staticmethod)

    def test_outer_deprecated_staticmethod_no_decoration_time_warning(self) -> None:
        """Outer @deprecated @staticmethod order: no UserWarning at decoration time (transparent unwrap)."""
        with captured_warnings() as caught:

            class _Cls:
                @deprecated(deprecated_in="1.0", remove_in="2.0")
                @staticmethod
                def old_method(x: int) -> int:
                    """Old staticmethod."""
                    return x

        assert not [w for w in caught if issubclass(w.category, UserWarning)]


class TestStaticMethodDescriptorTarget:
    """``target=<staticmethod descriptor>`` is accepted and unwrapped to the underlying function.

    Inside a class body ``some_method`` is still the raw ``staticmethod`` descriptor (the descriptor
    protocol has not yet bound it to the class).  Passing it directly as ``target=some_method`` used
    to require the caller to write ``target=some_method.__func__``.  After the ``_normalize_target``
    descriptor-unwrap fix, the raw descriptor is accepted and silently unwrapped — ``.__func__`` is no
    longer needed.
    """

    def test_forwards_call(self) -> None:
        """Calls are forwarded to the underlying static function; FutureWarning names the target.

        ``ServiceCls.old_static_redirect`` passes the raw ``staticmethod`` descriptor as
        ``target=static_compute`` inside the class body.  ``_normalize_target`` unwraps it to
        ``static_compute.__func__``, so the call is redirected without ``.__func__`` in user code.
        The warning message must include the target name — verifies ``_raise_warn_callable`` uses
        the unwrapped callable for template selection on all Python versions.
        """
        with pytest.warns(FutureWarning, match="in favor of") as warning_info:
            result = ServiceCls.old_static_redirect(5)
        assert result == 10
        assert "static_compute" in str(warning_info.list[0].message)

    def test_with_args_mapping(self) -> None:
        """``args_mapping`` renames the argument before forwarding to the descriptor target.

        ``ServiceCls.old_static_redirect_mapped`` combines descriptor-target unwrapping with
        ``args_mapping``: ``old_x`` is remapped to ``x`` before the forwarded call reaches
        ``static_compute.__func__``.
        """
        with pytest.warns(FutureWarning, match="in favor of"):
            result = ServiceCls.old_static_redirect_mapped(old_x=4)
        assert result == 8

    def test_no_decoration_time_warning(self) -> None:
        """Descriptor unwrap is silent — no UserWarning fires when the class body is executed.

        A class defines a new static method and a deprecated one whose target is the first, both wrapped in
        ``staticmethod``.  While the class body runs, the decorator receives a ``staticmethod`` descriptor as its
        target and has to unwrap it to inspect the real function.  That unwrapping must not produce any
        ``UserWarning`` (such as a misconfiguration notice) at decoration time.

        """
        with captured_warnings() as caught:

            class _Cls:
                @staticmethod
                def bbb(x: int) -> int:
                    """New static implementation."""
                    return x

                @staticmethod
                @deprecated(target=bbb, deprecated_in="1.0", remove_in="2.0")
                def aaa(x: int) -> int:
                    """Deprecated."""
                    return 0

        assert not [w for w in caught if issubclass(w.category, UserWarning)]

    def test_cross_class_descriptor_raises_type_error(self) -> None:
        """Staticmethod descriptor from a different class is rejected by the cross-class guard.

        ``_check_cross_class_method_target`` receives ``target.__func__`` (the unwrapped callable)
        when the target is a raw ``staticmethod`` descriptor.  A descriptor whose ``__func__``
        qualname prefix differs from the source's class is a cross-class forwarding attempt and
        must raise ``TypeError`` — same as passing an already-bound method from a different class.
        """

        class ClassA:
            @staticmethod
            def target_fn(x: int) -> int:
                """Target in a different class."""
                return x

        sm: staticmethod = ClassA.__dict__["target_fn"]  # raw descriptor (not yet bound)

        with pytest.raises(TypeError, match="cross-class method forwarding"):

            class ClassB:
                @deprecated(target=sm, deprecated_in="1.0", remove_in="2.0")
                def old_fn(self, x: int) -> int:
                    """Deprecated — cross-class descriptor target."""
                    return 0


class TestClassMethodDescriptorTarget:
    """``target=<classmethod descriptor>`` is accepted when source is also a classmethod.

    The symmetric same-class case (both deprecated and replacement are classmethods) is the
    supported pattern: the class the call is bound to arrives as the source's first positional
    argument, whatever the source names it, and reaches the target's receiver slot.  A source with
    no leading positional parameter raises ``TypeError`` at decoration time; any other source whose
    first argument is not a class at call time (a static or plain source) raises ``TypeError`` on
    the call, before any warning is emitted.
    """

    def test_forwards_call(self) -> None:
        """Calls are forwarded to the underlying classmethod; FutureWarning names the target.

        ``ServiceCls.old_class_redirect`` passes the raw ``classmethod`` descriptor as
        ``target=class_compute`` inside the class body.  The symmetric pattern works because
        the deprecated source is also a classmethod — ``cls`` is supplied by the caller and
        forwarded to ``class_compute.__func__``.  Warning must name the target.
        """
        with pytest.warns(FutureWarning, match="in favor of") as warning_info:
            result = ServiceCls.old_class_redirect(5)
        assert result == 10
        assert "class_compute" in str(warning_info.list[0].message)

    def test_with_args_mapping(self) -> None:
        """``args_mapping`` renames the argument before forwarding to the classmethod target.

        ``ServiceCls.old_class_redirect_mapped`` combines descriptor-target unwrapping with
        ``args_mapping``: ``old_x`` is remapped to ``x`` before the forwarded call reaches
        ``class_compute.__func__``.
        """
        with pytest.warns(FutureWarning, match="in favor of"):
            result = ServiceCls.old_class_redirect_mapped(old_x=4)
        assert result == 8

    def test_no_decoration_time_warning(self) -> None:
        """Descriptor unwrap is silent — no UserWarning fires when the class body is executed.

        A class defines a new class method and a deprecated one whose target is the first, both wrapped in
        ``classmethod``.  While the class body runs, the decorator receives a ``classmethod`` descriptor as its
        target and has to unwrap it to inspect the real function.  That unwrapping must not produce any
        ``UserWarning`` (such as a misconfiguration notice) at decoration time.

        """
        with captured_warnings() as caught:

            class _Cls:
                @classmethod
                def bbb(cls, x: int) -> int:
                    """New classmethod implementation."""
                    return x * 2

                @classmethod
                @deprecated(target=bbb, deprecated_in="1.0", remove_in="2.0")
                def aaa(cls, x: int) -> int:
                    """Deprecated."""
                    return 0

        assert not [w for w in caught if issubclass(w.category, UserWarning)]

    @pytest.mark.parametrize(
        ("owner", "method", "expected"),
        [
            pytest.param(ReceiverNamedFactory, "old_make_cls", ("ReceiverNamedFactory", 3), id="cls"),
            pytest.param(ReceiverNamedFactory, "old_make_klass", ("ReceiverNamedFactory", 3), id="klass"),
            pytest.param(ReceiverNamedFactory, "old_make_mcs", ("ReceiverNamedFactory", 3), id="mcs-outer-order"),
            pytest.param(ReceiverNamedFactory, "old_make_to_kls", ("ReceiverNamedFactory", 3), id="target-kls"),
            pytest.param(ReceiverNamedFactoryChild, "old_make_klass", ("ReceiverNamedFactoryChild", 3), id="subclass"),
        ],
    )
    def test_forwards_receiver_of_any_name(self, owner: type, method: str, expected: tuple[str, int]) -> None:
        """The class a classmethod is called on reaches the target, whatever either side names its receiver.

        A maintainer renames a factory classmethod and keeps the old one as a deprecated classmethod. Python does
        not force the receiver to be called ``cls``: codebases use ``klass``, metaclass-style ``mcs``, or a target
        written as ``kls``. Each case must decorate cleanly, warn once on the call, and forward the receiving class,
        including a subclass the call is made on, into the target's receiver slot.

        """
        with pytest.warns(FutureWarning, match="in favor of"):
            result = getattr(owner, method)(3)
        assert result == expected

    def test_receiver_of_any_name_with_args_mapping(self) -> None:
        """A ``klass`` receiver and an argument rename forward together.

        The deprecated classmethod names its receiver ``klass`` and also renames ``old_value`` to ``value``. The
        rename must not touch the receiver, and the receiver must not be passed to the target as a keyword it does
        not accept.

        """
        with pytest.warns(FutureWarning, match="in favor of"):
            result = ReceiverNamedFactory.old_make_mapped(old_value=4)
        assert result == ("ReceiverNamedFactory", 4)

    @pytest.mark.parametrize(
        ("args", "kwargs", "problem"),
        [
            pytest.param((5,), {}, "received a value of type `int`", id="positional"),
            pytest.param((), {"value": 5}, "received a value of type `int`", id="keyword"),
            pytest.param((), {}, "supplied no value", id="missing"),
        ],
    )
    def test_static_source_raises_at_call_time(self, args: tuple, kwargs: dict, problem: str) -> None:
        """A static source pointed at a classmethod target raises TypeError on the call, before any warning.

        The source cannot be told apart from a genuine classmethod at decoration, because the receiver name is free.
        At call time its first argument is the caller's value (or nothing at all), not a class, so forwarding it as
        the target's receiver would run the replacement with a wrong ``cls``. The call raises the same curated
        ``TypeError`` instead, naming what the first parameter received, and no deprecation warning is emitted for a
        call that never reaches the target.

        """
        with captured_warnings() as caught, pytest.raises(TypeError, match="leading class argument") as exc_info:
            ReceiverNamedFactory.old_make_static(*args, **kwargs)
        assert problem in str(exc_info.value)
        assert "Either make" in str(exc_info.value)
        assert not [w for w in caught if issubclass(w.category, FutureWarning)]

    def test_source_without_positional_parameter_raises_at_decoration_time(self) -> None:
        """A source with no leading positional parameter is rejected when it is decorated.

        Such a source has no slot through which a class could ever arrive, so forwarding to a classmethod target can
        never work. The guard in ``_normalize_target`` raises ``TypeError`` while the class body runs instead of on
        the first call.

        """
        with pytest.raises(TypeError, match="leading class argument") as exc_info:

            class _Cls:
                @classmethod
                def bbb(cls) -> int:
                    """New classmethod."""
                    return 2

                @staticmethod
                @deprecated(target=bbb, deprecated_in="1.0", remove_in="2.0")
                def aaa() -> int:
                    """Deprecated — no parameter at all, so it raises at decoration time."""
                    return 0

        assert "Either make" in str(exc_info.value)

    def test_later_parameter_named_like_target_receiver_raises_at_decoration_time(self) -> None:
        """A source whose later parameter carries the target's receiver name is rejected when it is decorated.

        The forwarded call renames the source receiver ``klass`` to the target's ``cls``; a source that also takes a
        ``cls=None`` option would hand that option to the replacement as its class and the real class under ``klass``,
        which a ``**kwargs`` target accepts silently. The guard names the clashing parameter instead.

        """
        with pytest.raises(TypeError, match="later parameter `cls` shares the target's receiver name; rename"):

            class _Cls:
                @classmethod
                def bbb(cls, **kwargs: object) -> int:
                    """New classmethod."""
                    return 2

                @classmethod
                @deprecated(target=bbb, deprecated_in="1.0", remove_in="2.0")
                def aaa(klass, cls: object = None) -> int:  # noqa: N804
                    """Deprecated — a later ``cls`` option clashes with the target's receiver."""
                    return 0

    def test_uninspectable_source_still_raises_at_decoration_time(self) -> None:
        """Classmethod guard fires TypeError even when inspect.signature(source) raises.

        The guard wraps ``inspect.signature(source)`` in ``try/except (ValueError, TypeError)``
        and falls back to ``src_params = []`` on failure.  An empty params list means the
        source cannot accept ``cls``, so the guard still raises ``TypeError``.  This covers
        C-extension sources and other callables where signature introspection is unavailable.
        """

        def _new_cm_fn(cls: type, x: int) -> int:
            return x

        cm: classmethod = classmethod(_new_cm_fn)

        with (
            patch("deprecate.routine.inspect.signature", side_effect=ValueError("no sig")),
            pytest.raises(TypeError, match="leading class argument"),
        ):

            @deprecated(target=cm, deprecated_in="1.0", remove_in="2.0")
            def aaa(x: int) -> int:
                return 0


class TestPropertyOrderAgnostic:
    """@deprecated on property/cached_property works in both decorator orders.

    Inner-deprecated order: ``@property @deprecated`` (``@deprecated`` closer to ``def``).
    Outer-deprecated order: ``@deprecated @property`` (``@deprecated`` outermost).
    Transparent unwrap+rewrap makes both orders produce ``property(deprecated_fget)`` — functionally identical.
    The deprecation warning fires at attribute access time in both cases; no UserWarning at decoration time.
    """

    def test_inner_deprecated_property_fires_on_access(self) -> None:
        """Inner @property @deprecated order: FutureWarning fires on attribute access, descriptor preserved.

        A maintainer deprecates a read-only attribute with ``@property`` on the outside and ``@deprecated`` directly
        on the getter.  Reading the attribute on an instance must emit the ``FutureWarning`` and still return the
        getter's value (42), and the class attribute must remain a real ``property`` so introspection and
        subclassing keep working.

        """

        class _Cls:
            @property
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def value(self) -> int:
                """Old property."""
                return 42

        obj = _Cls()
        with pytest.warns(FutureWarning):
            result = obj.value
        assert result == 42
        assert isinstance(_Cls.__dict__["value"], property)

    def test_outer_deprecated_property_fires_on_access(self) -> None:
        """Outer @deprecated @property order: FutureWarning fires on attribute access, descriptor preserved.

        A maintainer writes the stacking the other way round, with ``@deprecated`` on the outside of ``@property``.
        Reading the attribute on an instance must still emit the ``FutureWarning`` and return the getter's value
        (42), and the class attribute must remain a real ``property``, so both orders behave the same for callers.

        """

        class _Cls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @property
            def value(self) -> int:
                """Old property."""
                return 42

        obj = _Cls()
        with pytest.warns(FutureWarning):
            result = obj.value
        assert result == 42
        assert isinstance(_Cls.__dict__["value"], property)

    def test_outer_deprecated_property_no_decoration_time_warning(self) -> None:
        """Outer @deprecated @property order: no UserWarning at decoration time (transparent unwrap)."""
        with captured_warnings() as caught:

            class _UnusedCls:
                @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
                @property
                def value(self) -> int:
                    """Old property."""
                    return 42

        assert not [w for w in caught if issubclass(w.category, UserWarning)]

    def test_inner_deprecated_cached_property_fires_on_access(self) -> None:
        """Inner @cached_property @deprecated order: FutureWarning fires on first access.

        A maintainer deprecates a lazily computed attribute with ``@cached_property`` on the outside and
        ``@deprecated`` directly on the function.  The first read on an instance must emit the ``FutureWarning`` and
        return the computed value (42), and the class attribute must remain a real ``cached_property``.

        """

        class _Cls:
            @cached_property
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def value(self) -> int:
                """Old cached_property."""
                return 42

        obj = _Cls()
        with pytest.warns(FutureWarning):
            result = obj.value
        assert result == 42
        assert isinstance(_Cls.__dict__["value"], cached_property)

    def test_outer_deprecated_cached_property_fires_on_access(self) -> None:
        """Outer @deprecated @cached_property order: FutureWarning fires on first access, descriptor preserved.

        A maintainer writes the stacking the other way round, with ``@deprecated`` on the outside of
        ``@cached_property``.  The first read on an instance must still emit the ``FutureWarning`` and return the
        computed value (42), and the class attribute must remain a real ``cached_property``, so both orders behave
        the same for callers.

        """

        class _Cls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @cached_property
            def value(self) -> int:
                """Old cached_property."""
                return 42

        obj = _Cls()
        with pytest.warns(FutureWarning):
            result = obj.value
        assert result == 42
        assert isinstance(_Cls.__dict__["value"], cached_property)

    def test_outer_deprecated_cached_property_no_decoration_time_warning(self) -> None:
        """Outer @deprecated @cached_property order: no UserWarning at decoration time."""
        with captured_warnings() as caught:

            class _UnusedCls:
                @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
                @cached_property
                def value(self) -> int:
                    """Old cached_property."""
                    return 42

        assert not [w for w in caught if issubclass(w.category, UserWarning)]

    def test_property_setter_fires_warning(self) -> None:
        """Outer @deprecated on property with fset: FutureWarning fires on attribute assignment.

        A maintainer deprecates a read-write attribute by applying ``@deprecated`` to a property built from a getter
        and a setter.  Assigning a new value on an instance is a use of the deprecated attribute, so it must emit
        the ``FutureWarning`` and still store the value through the setter.

        """

        def _get_value(self: object) -> int:
            """Old property."""
            return self._value  # type: ignore[attr-defined]

        def _set_value(self: object, new_value: int) -> None:
            self._value = new_value  # type: ignore[attr-defined]

        wrapped = deprecated(deprecated_in="1.0", remove_in="2.0")(property(_get_value, _set_value))  # type: ignore[arg-type]

        class _Cls:
            value = wrapped

            def __init__(self) -> None:
                self._value = 0

        obj = _Cls()
        with pytest.warns(FutureWarning):
            obj.value = 99  # type: ignore[assignment]
        assert obj._value == 99

    def test_property_deleter_fires_warning(self) -> None:
        """Outer @deprecated on property with fdel: FutureWarning fires on attribute deletion.

        A maintainer deprecates an attribute whose property also defines a deleter by applying ``@deprecated`` to the
        property.  Deleting the attribute on an instance is a use of the deprecated attribute, so it must emit the
        ``FutureWarning`` and still run the deleter, which resets the stored value to ``None``.

        """

        def _get_value(self: object) -> Optional[int]:
            """Old property."""
            return self._value  # type: ignore[attr-defined]

        def _del_value(self: object) -> None:
            self._value = None  # type: ignore[attr-defined]

        wrapped = deprecated(deprecated_in="1.0", remove_in="2.0")(property(_get_value, None, _del_value))  # type: ignore[arg-type]

        class _Cls:
            value = wrapped

            def __init__(self) -> None:
                self._value: Optional[int] = 42

        obj = _Cls()
        with pytest.warns(FutureWarning):
            del obj.value
        assert obj._value is None

    def test_property_setter_only_fires_warning(self) -> None:
        """Setter-only property (fget is None): FutureWarning fires on assignment via wrapped fset.

        A maintainer deprecates a write-only attribute, a property with a setter but no getter.  Assigning a value on
        an instance must emit the ``FutureWarning`` and still store the value through the setter, so a property
        without a getter is not skipped by the deprecation wrapper.

        """

        def _set_value(self: object, new_value: int) -> None:
            self._value = new_value  # type: ignore[attr-defined]

        wrapped_setter = deprecated(deprecated_in="1.0", remove_in="2.0")(property(None, _set_value))  # type: ignore[arg-type]

        class _Cls:
            value = wrapped_setter

            def __init__(self) -> None:
                self._value = 0

        obj = _Cls()
        with pytest.warns(FutureWarning):
            obj.value = 7  # type: ignore[assignment]
        assert obj._value == 7

    def test_chain_style_setter_fires_warning(self) -> None:
        """Chain-style ``@value.setter`` after outer ``@deprecated @property``: FutureWarning fires on read AND write.

        Validates the ``_DeprecatedProperty.setter`` override: built-in ``property.setter`` rebuilds a
        plain ``property``, losing the deprecation wrap; the subclass re-wraps the new accessor with the
        same packing config so the warning fires on both attribute read and attribute write.
        """

        class _Cls:
            def __init__(self) -> None:
                self._value: int = 0

            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @property
            def value(self) -> int:
                """Old chain-style property."""
                return self._value

            @value.setter  # type: ignore[no-redef, prop-decorator]
            def value(self, v: int) -> None:
                self._value = v

        obj = _Cls()
        with pytest.warns(FutureWarning):
            result = obj.value
        assert result == 0
        with pytest.warns(FutureWarning) as w:
            obj.value = 99
        assert obj._value == 99
        # Verify the warning points at the caller site, not at deprecation internals.
        assert w[0].filename == __file__

    def test_chain_style_deleter_fires_warning(self) -> None:
        """Chain-style ``@value.deleter`` after outer ``@deprecated @property``: FutureWarning fires on ``del``.

        Validates ``_DeprecatedProperty.deleter``: ensures the freshly-supplied ``fdel`` is wrapped with
        the same packing closure so attribute deletion still emits the deprecation warning.
        """

        class _Cls:
            def __init__(self) -> None:
                self._value: Optional[int] = 42

            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @property
            def value(self) -> Optional[int]:
                """Old chain-style property."""
                return self._value

            @value.deleter  # type: ignore[no-redef, prop-decorator]
            def value(self) -> None:
                self._value = None

        obj = _Cls()
        with pytest.warns(FutureWarning):
            del obj.value
        assert obj._value is None

    def test_chain_style_setter_warns_once(self) -> None:
        """Chain-style setter fires FutureWarning exactly once — second write is silent.

        Validates that the ``_WrapperState`` counter for the wrapped ``fset`` advances after
        the first write so ``num_warns=1`` silences subsequent writes.
        """

        class _Cls:
            def __init__(self) -> None:
                self._value: int = 0

            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @property
            def value(self) -> int:
                """Old chain-style property."""
                return self._value

            @value.setter  # type: ignore[no-redef, prop-decorator]
            def value(self, v: int) -> None:
                self._value = v

        obj = _Cls()
        with pytest.warns(FutureWarning):
            obj.value = 10
        assert obj._value == 10
        # Second write must be silent after num_warns=1 is exhausted.
        with captured_warnings() as caught:
            obj.value = 20
        assert not any(issubclass(w.category, FutureWarning) for w in caught)
        assert obj._value == 20

    def test_inner_deprecated_property_setter_does_not_warn(self) -> None:
        """Inner ``@property @deprecated`` order: chain-style setter is NOT wrapped — writes are silent.

        Inner order wraps the ``fget`` accessor only.  ``@value.setter`` afterwards rebuilds a plain
        :class:`property` whose ``fset`` is the freshly-supplied raw callable — no deprecation closure.
        Attribute writes must therefore emit no ``FutureWarning`` and the body must still mutate state.
        """
        obj = InnerOrderDeprecatedPropCls()
        with captured_warnings() as caught:
            obj.value = 17
        assert not any(issubclass(w.category, FutureWarning) for w in caught)
        assert obj._value == 17

    def test_inner_deprecated_property_deleter_does_not_warn(self) -> None:
        """Inner ``@property @deprecated`` order: chain-style deleter is NOT wrapped — deletes are silent.

        Symmetric to the setter case: ``@value.deleter`` afterwards rebuilds a plain :class:`property`
        whose ``fdel`` carries no deprecation closure.  ``del obj.value`` must emit no
        ``FutureWarning`` while still executing the delete-accessor body.
        """
        obj = InnerOrderDeprecatedPropCls()
        with captured_warnings() as caught:
            del obj.value
        assert not any(issubclass(w.category, FutureWarning) for w in caught)
        assert obj._del_value is None

    def test_deleter_only_property_fires_warning(self) -> None:
        """Deleter-only ``property(None, None, fdel)``: FutureWarning fires on ``del`` via wrapped fdel.

        Symmetric to ``test_property_setter_only_fires_warning`` — when the deprecation surface attaches
        to the only accessor the property carries (``fdel``), the warning must still fire on access.
        """
        obj = DelOnlyDeprecatedPropCls()
        with pytest.warns(FutureWarning):
            del obj.delete_only
        assert obj._value is None

    def test_all_three_accessors_fire_independently(self) -> None:
        """Outer ``deprecated(...)(property(fget, fset, fdel))``: each accessor fires its own FutureWarning.

        Validates that read, write, and delete each emit a ``FutureWarning`` via their own wrapped
        accessor.  The three accessors share a packing config but each carries an independent
        deprecation closure derived from ``_DeprecatedProperty``.
        """

        def _get_value(self: object) -> Optional[int]:
            """Old property getter."""
            return self._value  # type: ignore[attr-defined]

        def _set_value(self: object, new_value: int) -> None:
            self._value = new_value  # type: ignore[attr-defined]

        def _del_value(self: object) -> None:
            self._value = None  # type: ignore[attr-defined]

        wrapped = deprecated(deprecated_in="1.0", remove_in="2.0")(
            property(_get_value, _set_value, _del_value)  # type: ignore[arg-type]
        )

        class _Cls:
            value = wrapped

            def __init__(self) -> None:
                self._value: Optional[int] = 5

        obj = _Cls()
        with pytest.warns(FutureWarning):
            result = obj.value
        assert result == 5
        with pytest.warns(FutureWarning):
            obj.value = 11  # type: ignore[assignment]
        assert obj._value == 11
        with pytest.warns(FutureWarning):
            del obj.value
        assert obj._value is None

    def test_cross_accessor_counter_independence(self) -> None:
        """Each accessor has its own ``num_warns`` counter — exhausting fget's does not silence fset.

        Validates that ``_WrapperState`` is per-accessor: after the first read silences subsequent
        reads (``num_warns=1`` default), the first write still fires its own ``FutureWarning`` from
        an independent counter, and a second write is silent only after the fset counter is exhausted.
        """

        def _get_value(self: object) -> int:
            """Old property getter."""
            return self._value  # type: ignore[attr-defined]

        def _set_value(self: object, new_value: int) -> None:
            self._value = new_value  # type: ignore[attr-defined]

        wrapped = deprecated(deprecated_in="1.0", remove_in="2.0")(
            property(_get_value, _set_value)  # type: ignore[arg-type]
        )

        class _Cls:
            value = wrapped

            def __init__(self) -> None:
                self._value = 0

        obj = _Cls()
        # First read — fget counter fires.
        with pytest.warns(FutureWarning):
            first_read = obj.value
        assert first_read == 0
        # Second read — fget counter exhausted, silent.
        with captured_warnings() as caught:
            second_read = obj.value
        assert second_read == 0
        assert not any(issubclass(w.category, FutureWarning) for w in caught)
        # First write — fset's independent counter still has one warning available.
        with pytest.warns(FutureWarning):
            obj.value = 33  # type: ignore[assignment]
        assert obj._value == 33
        # Second write — fset counter now exhausted, silent.
        with captured_warnings() as caught:
            obj.value = 44  # type: ignore[assignment]
        assert obj._value == 44
        assert not any(issubclass(w.category, FutureWarning) for w in caught)

    def test_validate_expiry_with_deprecated_property(self) -> None:
        """``validate_deprecation_expiry`` reports a deprecated property whose ``remove_in`` is past.

        Builds a synthetic module containing a class with an explicit-construction outer-order
        deprecated property whose ``remove_in='1.0'`` is below the supplied ``current_version='2.0'``.
        The audit walker must discover the wrapped accessor via class scan and report it as expired.
        """

        def _fget(self: object) -> int:
            """Old property getter."""
            return 1

        mod = types.ModuleType("test_mod_expiry_property")

        class OldCls:
            old_prop: property = deprecated(deprecated_in="0.9", remove_in="1.0")(property(_fget))  # type: ignore[assignment,arg-type]

        OldCls.__module__ = mod.__name__
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with assert_no_warnings():
            expired = validate_deprecation_expiry(mod, current_version="2.0", recursive=False, include_members=True)

        assert expired, "expected at least one expired entry for deprecated property past remove_in"
        assert any("old_prop" in msg for msg in expired)


class TestPropertyErrorPaths:
    """``@deprecated`` raises ``TypeError`` for unsupported property configurations.

    Defined inline (not in api_deprecated.py) because all four cases raise at
    decoration time — placing them at module level would abort the entire import.
    This matches the AGENTS.md three-layer-rule exception for error-path tests that
    test the decorator itself raising TypeError.
    """

    def test_double_deprecated_property_raises(self) -> None:
        """Applying ``@deprecated`` to an already-decorated ``_DeprecatedProperty`` raises TypeError.

        Double-wrapping would emit two FutureWarnings per access and fire the stacking guard
        three times. The guard raises early with a clear message naming the offending property.
        """

        def _getter(self: object) -> int:
            """Old property getter."""
            return 0

        once_wrapped = deprecated(deprecated_in="1.0", remove_in="2.0")(property(_getter))  # type: ignore[arg-type]
        assert isinstance(once_wrapped, _DeprecatedProperty)

        with pytest.raises(TypeError, match=r"cannot be applied twice to the already-deprecated property"):
            deprecated(deprecated_in="2.0", remove_in="3.0")(once_wrapped)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("kwarg_name", "kwarg_value"),
        [
            ("args_mapping", {"old_arg": "new_arg"}),
            ("args_extra", {"injected": True}),
        ],
    )
    def test_unsupported_kwarg_with_property_raises(self, kwarg_name: str, kwarg_value: object) -> None:
        """Passing ``args_mapping`` or ``args_extra`` when decorating a ``property`` raises TypeError.

        Both kwargs require a function signature to apply argument remapping; properties expose
        accessor callables only and cannot meaningfully use these kwargs.
        """

        def _getter(self: object) -> int:
            """Old property getter."""
            return 0

        with pytest.raises(TypeError, match=rf"`{kwarg_name}` is not supported when decorating a `property`"):
            deprecated(deprecated_in="1.0", remove_in="2.0", **{kwarg_name: kwarg_value})(  # type: ignore[arg-type]
                property(_getter)  # type: ignore[arg-type]
            )

    def test_callable_target_with_property_raises(self) -> None:
        """Passing a callable ``target=`` when decorating a ``property`` raises TypeError.

        Call forwarding requires a function to reroute to; a property's accessors cannot be
        forwarded wholesale to another callable. The error message names the rejected target
        and suggests ``TargetMode.NOTIFY`` as the supported alternative.
        """

        def _getter(self: object) -> int:
            """Old property getter."""
            return 0

        def _new_getter(self: object) -> int:
            """New property getter."""
            return 1

        with pytest.raises(TypeError, match=r"`target` as a callable is not supported when decorating a `property`"):
            deprecated(target=_new_getter, deprecated_in="1.0", remove_in="2.0")(  # type: ignore[arg-type]
                property(_getter)  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize(
        "target_val",
        [
            pytest.param(TargetMode.ARGS_REMAP, id="TargetMode.ARGS_REMAP"),
            pytest.param(True, id="True_legacy"),
        ],
    )
    def test_args_remap_target_with_property_raises(self, target_val: object) -> None:
        """Passing ``target=TargetMode.ARGS_REMAP`` or legacy ``True`` when decorating a ``property`` raises TypeError.

        A developer tries to use self-deprecation mode (ARGS_REMAP / ``True``) on a
        property, expecting argument remapping. Because properties expose three separate
        accessor callables with no shared call signature, ARGS_REMAP semantics cannot
        apply. The library must reject this at decoration time with a clear message
        naming the rejected value and suggesting ``TargetMode.NOTIFY`` as an alternative.
        """

        def _getter(self: object) -> int:
            """Old property getter."""
            return 0

        with pytest.raises(TypeError, match=r"`target=TargetMode\.ARGS_REMAP` \(or legacy `True`\) is not supported"):
            deprecated(target=target_val, deprecated_in="1.0", remove_in="2.0")(  # type: ignore[arg-type]
                property(_getter)  # type: ignore[arg-type]
            )

    def test_accessor_with_non_config_deprecated_attr_does_not_raise(self) -> None:
        """Accessor bearing ``__deprecated__`` set to a non-DeprecationConfig value is not rejected.

        A user's getter function carries a ``__deprecated__`` attribute set to an arbitrary
        value (e.g. a string from a third-party decorator). When wrapping that property with
        ``@deprecated``, the double-decorate guard must not fire a false positive — only a
        real ``DeprecationConfig`` instance should trigger the guard.
        """

        def _getter(self: object) -> int:
            """Old property getter."""
            return 0

        _getter.__deprecated__ = "not-a-DeprecationConfig"  # type: ignore[attr-defined]

        result = deprecated(deprecated_in="1.0", remove_in="2.0")(  # type: ignore[arg-type]
            property(_getter)  # type: ignore[arg-type]
        )
        assert isinstance(result, _DeprecatedProperty)


class TestDescriptorStacklevel:
    """Decoration-time warnings from the _packing_descriptor paths attribute to the caller.

    ``_packing_descriptor`` recursively calls ``packing_fn`` after unwrapping the descriptor, so
    the decoration-time warning is emitted from deeper inside the ``deprecate`` package than on
    the plain-function path.  No offset is forwarded for those extra frames: the warning site
    calls ``deprecate._frames._caller_stacklevel()``, which walks the stack and attributes the
    warning to the first frame outside the package.  Each test below triggers the "no
    ``deprecated_in`` set" UserWarning at decoration time and asserts that ``warning.filename``
    ends with this test file's name, confirming the warning points at the ``@deprecated``
    application line rather than an internal helper frame.
    """

    def test_warn_filename_points_to_caller_on_classmethod_path(self) -> None:
        """Classmethod path: decoration-time UserWarning attributes to caller's frame.

        A developer applies ``@deprecated(remove_in="2.0")`` to a classmethod without
        providing ``deprecated_in``.  ``_packing_descriptor`` unwraps the classmethod and
        recursively calls ``packing_fn`` on the function; ``_caller_stacklevel()`` skips the
        extra package frames, so the "missing deprecated_in" UserWarning must point to the
        ``@deprecated`` line in the caller's file — not to an internal frame inside ``deprecate``.
        """
        with captured_warnings() as caught:

            class _Cls:
                @deprecated(remove_in="2.0")
                @classmethod
                def old_method(cls) -> None:
                    """Old classmethod."""

        user_warnings = [w for w in caught if issubclass(w.category, UserWarning) and "deprecated_in" in str(w.message)]
        assert user_warnings, "Expected UserWarning for missing deprecated_in on classmethod path"
        assert user_warnings[0].filename.endswith("test__properties.py")

    def test_warn_filename_points_to_caller_on_staticmethod_path(self) -> None:
        """Staticmethod path: decoration-time UserWarning attributes to caller's frame.

        A developer applies ``@deprecated(remove_in="2.0")`` to a staticmethod without
        providing ``deprecated_in``.  The same recursive-packing path as the classmethod
        case fires, and the UserWarning must attribute to the decorator application line
        in this test file rather than to an internal frame.
        """
        with captured_warnings() as caught:

            class _Cls:
                @deprecated(remove_in="2.0")
                @staticmethod
                def old_method() -> None:
                    """Old staticmethod."""

        user_warnings = [w for w in caught if issubclass(w.category, UserWarning) and "deprecated_in" in str(w.message)]
        assert user_warnings, "Expected UserWarning for missing deprecated_in on staticmethod path"
        assert user_warnings[0].filename.endswith("test__properties.py")

    def test_warn_filename_points_to_caller_on_property_path(self) -> None:
        """Property path: decoration-time UserWarning attributes to caller's frame.

        A developer applies ``@deprecated(remove_in="2.0")`` to a property without providing
        ``deprecated_in``.  ``_packing_descriptor`` wraps ``fget`` by calling
        ``packing_fn(source.fget)``; ``_caller_stacklevel()`` attributes the resulting
        UserWarning to the ``@deprecated`` application line in this test file.
        """
        with captured_warnings() as caught:

            class _Cls:
                @deprecated(remove_in="2.0")  # type: ignore[arg-type, prop-decorator]
                @property
                def old_prop(self) -> int:
                    """Old property."""
                    return 0

        user_warnings = [w for w in caught if issubclass(w.category, UserWarning) and "deprecated_in" in str(w.message)]
        assert user_warnings, "Expected UserWarning for missing deprecated_in on property path"
        assert user_warnings[0].filename.endswith("test__properties.py")

    def test_warn_filename_points_to_caller_on_cached_property_path(self) -> None:
        """cached_property path: decoration-time UserWarning attributes to caller's frame.

        A developer applies ``@deprecated(remove_in="2.0")`` to a cached_property without
        providing ``deprecated_in``.  ``_packing_descriptor`` calls
        ``packing_fn(source.func)``; ``_caller_stacklevel()`` makes the UserWarning attribute
        to the ``@deprecated`` line in this test file, not to an internal frame.
        """
        with captured_warnings() as caught:

            class _Cls:
                @deprecated(remove_in="2.0")  # type: ignore[arg-type, prop-decorator]
                @cached_property
                def old_prop(self) -> int:
                    """Old cached property."""
                    return 0

        user_warnings = [w for w in caught if issubclass(w.category, UserWarning) and "deprecated_in" in str(w.message)]
        assert user_warnings, "Expected UserWarning for missing deprecated_in on cached_property path"
        assert user_warnings[0].filename.endswith("test__properties.py")


class TestStrictProperty:
    """``from deprecate import property`` is a strict ``property`` that rejects inner-order ``@deprecated``.

    The standard library makes ``@property`` the outermost decorator, so authors habitually write
    ``@property`` above ``@deprecated`` — the inner order that wraps only ``fget``. The exported strict
    ``property`` opts a module into a guard: at class-body evaluation time it raises :class:`TypeError` the
    moment it is handed a getter already carrying ``__deprecated__`` metadata, turning a silent-write/delete
    bug into a loud failure before any instance exists. Modules that do not import it keep the builtin
    behaviour untouched, so the strictness is purely opt-in.
    """

    def test_raises_on_inner_order(self) -> None:
        """Wrapping an already-``@deprecated`` getter raises ``TypeError`` mentioning the order swap.

        An author writes ``@property`` (the strict import) above ``@deprecated`` out of habit. Because the
        getter already carries deprecation metadata, the strict property must refuse construction immediately
        and explain that only ``fget`` would warn, pointing the author at the canonical outer order.
        """

        @deprecated(deprecated_in="1.0", remove_in="2.0")
        def old_getter(self: object) -> int:
            """Already-deprecated getter fed into the strict property."""
            return 42

        with pytest.raises(TypeError, match="(?i)inner.order|outer order"):
            strict_property(old_getter)

    def test_allows_normal_usage(self) -> None:
        """A plain (non-deprecated) getter constructs the strict property without error.

        The common case — a fresh ``@property`` with no deprecation involved — must behave exactly like the
        builtin: the strict guard only fires when deprecation metadata is present, so undecorated getters pass
        straight through and the resulting descriptor returns the getter's value.
        """

        class _Cls:
            @strict_property
            def value(self) -> int:
                """Plain strict property — no deprecation involved."""
                return 7

        assert _Cls().value == 7

    def test_allows_outer_order(self) -> None:
        """Outer order ``@deprecated @strict_property`` builds a ``_DeprecatedProperty`` correctly.

        When the strict property is the inner decorator and ``@deprecated`` wraps it, the getter handed to the
        strict property is still plain (deprecation is applied afterwards by ``@deprecated`` re-wrapping the
        accessors), so no ``TypeError`` fires. The outer ``@deprecated`` sees the ``_StrictProperty`` via the
        ``isinstance(source, property)`` branch and converts it to a warning-emitting ``_DeprecatedProperty``.
        """

        class _Cls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[arg-type]
            @strict_property
            def value(self) -> int:
                """Outer-order deprecated strict property."""
                return 42

        assert isinstance(_Cls.__dict__["value"], _DeprecatedProperty)
        obj = _Cls()
        with pytest.warns(FutureWarning):
            result = obj.value
        assert result == 42

    def test_is_subclass_of_property(self) -> None:
        """``_StrictProperty`` subclasses the builtin ``property`` so existing ``isinstance`` checks pass.

        The whole decorator and audit machinery branches on ``isinstance(obj, property)``. The strict property
        must therefore be a genuine ``property`` subclass; otherwise the outer-order conversion path and the
        audit scanner would skip it entirely. This pins the subclass relationship as part of the contract.
        """
        assert issubclass(strict_property, builtins.property)
        assert strict_property is _StrictProperty
