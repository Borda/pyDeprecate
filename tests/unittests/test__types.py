"""Unit tests for :class:`~deprecate._types.DeprecationConfig`."""

import dataclasses
from typing import cast

import pytest

from deprecate import deprecated
from deprecate._types import _DeprecatedCallable
from tests.collection_targets import base_sum_kwargs


class TestTemplateMgsAliasProperty:
    """The read-only ``template_mgs`` property mirrors ``message_template`` for external audit callers.

    ``template_mgs`` was a public field on ``DeprecationConfig`` before the ``v0.12`` rename to
    ``message_template``. External audit code that read ``__deprecated__.template_mgs`` directly must
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
        cfg = cast(_DeprecatedCallable, wrapped).__deprecated__
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
        cfg = cast(_DeprecatedCallable, wrapped).__deprecated__
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.template_mgs = "not allowed"  # type: ignore[misc]
