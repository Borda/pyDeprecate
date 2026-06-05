"""Unit tests for @deprecated applied to property, cached_property, and descriptor types."""

import types
import warnings
from functools import cached_property
from typing import Optional

import pytest

from deprecate import TargetMode, deprecated
from deprecate.audit import validate_deprecation_expiry
from deprecate.deprecation import _DeprecatedProperty
from tests.collection_deprecate import DelOnlyDeprecatedPropCls, InnerOrderDeprecatedPropCls


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
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

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
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            class _Cls:
                @deprecated(deprecated_in="1.0", remove_in="2.0")
                @staticmethod
                def old_method(x: int) -> int:
                    """Old staticmethod."""
                    return x

        assert not [w for w in caught if issubclass(w.category, UserWarning)]


class TestPropertyOrderAgnostic:
    """@deprecated on property/cached_property works in both decorator orders.

    Inner-deprecated order: ``@property @deprecated`` (``@deprecated`` closer to ``def``).
    Outer-deprecated order: ``@deprecated @property`` (``@deprecated`` outermost).
    Transparent unwrap+rewrap makes both orders produce ``property(deprecated_fget)`` — functionally identical.
    The deprecation warning fires at attribute access time in both cases; no UserWarning at decoration time.
    """

    def test_inner_deprecated_property_fires_on_access(self) -> None:
        """Inner @property @deprecated order: FutureWarning fires on attribute access, descriptor preserved."""

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
        """Outer @deprecated @property order: FutureWarning fires on attribute access, descriptor preserved."""

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
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            class _UnusedCls:
                @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
                @property
                def value(self) -> int:
                    """Old property."""
                    return 42

        assert not [w for w in caught if issubclass(w.category, UserWarning)]

    def test_inner_deprecated_cached_property_fires_on_access(self) -> None:
        """Inner @cached_property @deprecated order: FutureWarning fires on first access."""

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
        """Outer @deprecated @cached_property order: FutureWarning fires on first access, descriptor preserved."""

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
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            class _UnusedCls:
                @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
                @cached_property
                def value(self) -> int:
                    """Old cached_property."""
                    return 42

        assert not [w for w in caught if issubclass(w.category, UserWarning)]

    def test_property_setter_fires_warning(self) -> None:
        """Outer @deprecated on property with fset: FutureWarning fires on attribute assignment."""

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
        """Outer @deprecated on property with fdel: FutureWarning fires on attribute deletion."""

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
        """Setter-only property (fget is None): FutureWarning fires on assignment via wrapped fset."""

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
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
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
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
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
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
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
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            second_read = obj.value
        assert second_read == 0
        assert not any(issubclass(w.category, FutureWarning) for w in caught)
        # First write — fset's independent counter still has one warning available.
        with pytest.warns(FutureWarning):
            obj.value = 33  # type: ignore[assignment]
        assert obj._value == 33
        # Second write — fset counter now exhausted, silent.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
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

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            expired = validate_deprecation_expiry(mod, current_version="2.0", recursive=False, include_members=True)

        assert expired, "expected at least one expired entry for deprecated property past remove_in"
        assert any("old_prop" in msg for msg in expired)


class TestPropertyErrorPaths:
    """``@deprecated`` raises ``TypeError`` for unsupported property configurations.

    Defined inline (not in collection_deprecate.py) because all four cases raise at
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
        [TargetMode.ARGS_REMAP, True],
        ids=["TargetMode.ARGS_REMAP", "True_legacy"],
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
