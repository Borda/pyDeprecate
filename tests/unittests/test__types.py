"""Unit tests for :mod:`deprecate._types` — :class:`DeprecationConfig` and the ``_has_deprecation_meta`` guard."""

import dataclasses
from typing import Union, cast

import pytest

import tests.collection_misconfigured as clean_module
from deprecate import TargetMode, deprecated, get_deprecation_config
from deprecate._types import DeprecationConfig, _DeprecatedCallable, _has_deprecation_meta
from deprecate.audit import find_deprecation_wrappers, validate_deprecation_wrapper
from deprecate.proxy import _DeprecatedProxy
from tests.collection_targets import base_sum_kwargs


class TestTemplateMgsAliasProperty:
    """The read-only ``template_mgs`` property mirrors ``message_template`` for external audit callers.

    ``template_mgs`` was a public field on ``DeprecationConfig`` before the ``v0.12`` rename to
    ``message_template``. External audit code that read ``__deprecation_config__.template_mgs`` directly must
    keep working, so the field is kept as a read-only property alias rather than removed outright.
    """

    def test_property_equals_message_template(self) -> None:
        """``cfg.template_mgs`` is the exact same object as ``cfg.message_template``, not a copy."""
        wrapped = deprecated(
            target=base_sum_kwargs,
            deprecated_in="1.0",
            remove_in="2.0",
            message_template="Custom notice.",
        )(base_sum_kwargs)
        cfg = cast(_DeprecatedCallable, wrapped).__deprecation_config__
        assert cfg.template_mgs is cfg.message_template

    def test_assignment_raises_frozen_instance_error(self) -> None:
        """Assigning to ``template_mgs`` raises ``FrozenInstanceError`` — ``DeprecationConfig`` is frozen.

        The frozen dataclass's ``__setattr__`` intercepts every attribute assignment before the
        property's (absent) setter would even be consulted, so the raised type is
        ``dataclasses.FrozenInstanceError``, not a plain ``AttributeError``.
        """
        wrapped = deprecated(
            target=base_sum_kwargs,
            deprecated_in="1.0",
            remove_in="2.0",
        )(base_sum_kwargs)
        cfg = cast(_DeprecatedCallable, wrapped).__deprecation_config__
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.template_mgs = "not allowed"  # type: ignore[misc]


class TestHasDeprecationMeta:
    """Tests for _has_deprecation_meta TypeGuard."""

    def test_returns_true_for_deprecated_proxy(self) -> None:
        """_DeprecatedProxy objects carry DeprecationConfig, so the guard returns True."""
        proxy = _DeprecatedProxy(obj={}, name="x", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert _has_deprecation_meta(proxy) is True

    @pytest.mark.parametrize(
        "target_val",
        [
            pytest.param(TargetMode.ARGS_REMAP, id="TargetMode.ARGS_REMAP"),
            pytest.param(True, marks=pytest.mark.filterwarnings("ignore::FutureWarning"), id="legacy-True"),
        ],
    )
    def test_returns_true_for_deprecated_decorated_callable(self, target_val: Union[TargetMode, bool]) -> None:
        """Return true for both current target representations after the metadata split.

        Applications migrating from the legacy boolean target spelling must receive the same metadata-discovery
        result as applications already using ``TargetMode``.
        """

        @deprecated(deprecated_in="1.0", remove_in="2.0", target=target_val)
        def fn() -> None:
            pass

        assert _has_deprecation_meta(fn) is True

    def test_legacy_callable_is_validated_through_public_fallback(self) -> None:
        """Validate a callable carrying only the pre-v0.13 metadata attribute.

        Mixed-version applications can expose wrappers created by an older pyDeprecate installation. Audit must
        consume their ``DeprecationConfig`` through the supported fallback instead of dereferencing the new attribute.
        """
        info = validate_deprecation_wrapper(clean_module._legacy_metadata_only)

        assert info.function == "legacy_metadata_only"
        assert info.deprecated_info.target is TargetMode.NOTIFY

    def test_legacy_module_is_discovered_through_public_fallback(self) -> None:
        """Discover module metadata created before the v0.13 attribute split.

        A mixed-version audit can encounter a module that stores its config only in ``__deprecated__``. The module
        must remain reportable without triggering an ``AttributeError`` for ``__deprecation_config__``.
        """
        results = find_deprecation_wrappers(clean_module._legacy_metadata_module)

        assert len(results) == 1
        assert results[0].module == "legacy_metadata_module"
        assert results[0].api_type == "module"

    def test_current_wrapper_can_stack_over_legacy_metadata(self) -> None:
        """Stack a current argument rename over a pre-v0.13 warn-only wrapper.

        A rolling upgrade can decorate an older wrapper again before every package has moved its metadata. Stacking
        must inspect the legacy config through the accessor and retain the current outer configuration.
        """
        wrapper = clean_module.make_stacked_legacy_metadata_wrapper()

        config = get_deprecation_config(wrapper)
        assert config is not None
        assert config.target is TargetMode.ARGS_REMAP

    def test_returns_false_for_plain_callable(self) -> None:
        """Undecorated callables have no __deprecation_config__, so the guard returns False."""

        def plain() -> None:
            pass

        assert _has_deprecation_meta(plain) is False

    @pytest.mark.parametrize("obj", [pytest.param("string", id="str"), pytest.param(42, id="int")])
    def test_returns_false_for_non_callable(self, obj: object) -> None:
        """Non-callables without __deprecation_config__ return False."""
        assert _has_deprecation_meta(obj) is False

    def test_meta_is_deprecation_info_instance(self) -> None:
        """The __deprecation_config__ attribute on a proxy is a typed DeprecationConfig dataclass."""
        proxy = _DeprecatedProxy(obj={}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert isinstance(object.__getattribute__(proxy, "__deprecation_config__"), DeprecationConfig)


class TestForeignObjectDeprecationMetaGuard:
    """``_has_deprecation_meta`` must not crash a scan on a foreign object raising a non-AttributeError."""

    def test_hostile_getattr_returns_false(self) -> None:
        """An object whose ``__deprecated__`` property raises is treated as carrying no metadata, not crashed on.

        A recursive audit scan can encounter arbitrary third-party objects; ``getattr(..., default)`` only swallows
        ``AttributeError``, so without the guard a lazy proxy raising ``RuntimeError`` would abort the whole scan.
        Using a ``@property`` that raises on access exercises the ``try/except Exception`` guard without making
        ``__getattr__`` raise for every attribute (which CodeQL flags as broadly hazardous).
        """

        class _Hostile:
            @property
            def __deprecated__(self) -> object:
                raise RuntimeError("attribute access forbidden")

        assert _has_deprecation_meta(_Hostile()) is False
