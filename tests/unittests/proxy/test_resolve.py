"""Unit tests for proxy target resolution and constructor mapping validation."""

import warnings
from dataclasses import dataclass as dc_decorator
from typing import Any

import pytest

from deprecate.proxy import deprecated_class, deprecated_instance
from deprecate.proxy.core import _DeprecatedProxy, _proxy_source_path
from tests._helpers import captured_warnings
from tests.collections.api_current.core import AutoExpandDC, PositionalOnlyTarget
from tests.collections.api_deprecated.core import (
    _DEPRS_CASE_STD_ARGS,
    DepAutoExpandDC,
    DepPositionalOnly,
    DepPositionalOnlyDerived,
    DepPositionalOnlyImmutable,
    DepPositionalOnlyMixed,
    DepPositionalOnlyRequired,
)
from tests.collections.api_deprecated.forwarding import (
    DepAutoExpandInitFalseDC,
    DepAutoExpandOverriddenInitDC,
    DepAutoExpandReqDC,
)

# ---------------------------------------------------------------------------
# Dataclass dual-surface auto-expand
# ---------------------------------------------------------------------------


class TestDataclassAutoExpand:
    """attrs_mapping auto-expansion to args_mapping for ``@dataclass`` targets.

    When ``deprecated_class`` is applied to a ``@dataclass`` with only ``attrs_mapping``
    configured, the proxy must automatically expand into ``args_mapping`` so that both the
    attribute-access surface (post-construction ``__getattr__``) and the constructor-call
    surface (``DC(old_field=5)``) emit ``FutureWarning`` from a single decoration call.
    """

    def test_constructor_kwarg_warns_after_auto_expand(self) -> None:
        """``DepAutoExpandDC(old_field=5)`` emits FutureWarning after auto-expand.

        Before the fix, ``attrs_mapping``-only on a dataclass meant calling ``DC(old_field=5)``
        raised ``TypeError``.  After auto-expand the proxy has ``args_mapping={"old_field":
        "new_field"}`` and the FutureWarning fires; the instance is created with
        ``new_field=5``.
        """
        with pytest.warns(FutureWarning):
            instance = DepAutoExpandDC(old_field=5)  # type: ignore[call-arg]
        assert instance.new_field == 5

    def test_class_proxy_attribute_access_warns(self) -> None:
        """Accessing the deprecated alias via the class proxy emits FutureWarning.

        ``attrs_mapping`` operates at the class-proxy level: ``DepAutoExpandDC.old_field``
        routes through the proxy's ``__getattr__``, emits FutureWarning, and returns the
        value of the canonical field.  Instance-level attribute access is not proxied —
        instances returned by the callable target are plain dataclass objects.
        """
        with pytest.warns(FutureWarning):
            _ = DepAutoExpandDC.old_field  # type: ignore[attr-defined]  # class-proxy access, not instance attr

    def test_auto_expanded_keys_recorded_on_deprecated_meta(self) -> None:
        """``args_mapping_auto_expanded`` on ``__deprecation_config__`` lists the auto-copied key.

        Audit tools read ``DeprecationConfig.args_mapping_auto_expanded`` to distinguish
        auto-generated entries from user-supplied ones.
        """
        meta = object.__getattribute__(DepAutoExpandDC, "__deprecation_config__")
        assert "old_field" in meta.args_mapping_auto_expanded

    def test_explicit_args_mapping_not_overwritten(self) -> None:
        """Explicit ``args_mapping`` entry for the same key is not overwritten.

        When the user supplies ``args_mapping={"old_field": ...}`` explicitly, the auto-
        expand skips that key so the user-supplied value always wins.
        """
        with pytest.warns(UserWarning, match="has both `args_mapping` and `attrs_mapping` configured"):
            proxy = deprecated_class(
                attrs_mapping={"old_field": "new_field"},
                args_mapping={"old_field": "new_field"},
                **_DEPRS_CASE_STD_ARGS,
            )(AutoExpandDC)
        meta = object.__getattribute__(proxy, "__deprecation_config__")
        assert "old_field" not in meta.args_mapping_auto_expanded

    def test_drop_mapping_entry_not_auto_expanded(self) -> None:
        """attrs_mapping={'field': None} (drop-mapping) is not auto-expanded into args_mapping.

        Drop entries (value=None) signal attribute removal, not renaming to a dataclass field.
        Auto-expansion only applies when the redirect value names a dataclass __init__ parameter.
        A None value has no target field name, so expansion must be skipped for that entry.

        """

        @dc_decorator
        class _Target:
            field: int = 0

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            proxy = deprecated_class(target=_Target, **_DEPRS_CASE_STD_ARGS, attrs_mapping={"field": None})(_Target)
        # Drop-mapping entry must not appear in args_mapping (no auto-expansion for None values)
        dep = object.__getattribute__(proxy, "__deprecation_config__")
        assert dep.args_mapping is None or "field" not in (dep.args_mapping or {})

    def test_req_dc_constructor_kwarg_warns_after_auto_expand(self) -> None:
        """``DepAutoExpandReqDC(old_field=5)`` emits FutureWarning and returns a correctly populated instance.

        ``AutoExpandReqDC`` has a required (no-default) field ``new_field``.  After auto-expand
        the proxy has ``args_mapping={"old_field": "new_field"}`` so calling with the deprecated
        kwarg must (a) emit ``FutureWarning`` and (b) create an instance where ``new_field == 5``.
        Without auto-expand the call would raise ``TypeError`` because ``new_field`` is required
        and ``old_field`` is not recognised by the dataclass constructor.
        """
        with pytest.warns(FutureWarning):
            instance = DepAutoExpandReqDC(old_field=5)  # type: ignore[call-arg]
        assert instance.new_field == 5

    def test_req_dc_class_proxy_attribute_access_raises_without_warning(self) -> None:
        """Accessing ``DepAutoExpandReqDC.old_field`` raises AttributeError and emits no FutureWarning.

        ``AutoExpandReqDC`` has a required field ``new_field`` with no class-level default, so the redirected
        class-level read of ``old_field`` → ``new_field`` raises ``AttributeError``. The proxy resolves the mapped
        attribute before warning, so this failed lookup — the same shape as a ``hasattr`` probe — emits nothing and
        leaves the ``old_field`` warn budget unspent.
        """
        with captured_warnings() as caught, pytest.raises(AttributeError):
            _ = DepAutoExpandReqDC.old_field  # type: ignore[attr-defined]

        assert [w for w in caught if issubclass(w.category, FutureWarning)] == []

    def test_init_false_field_excluded_from_auto_expand(self) -> None:
        """``field(init=False)`` entries must not appear in ``args_mapping_auto_expanded``.

        ``AutoExpandInitFalseDC`` has ``new_field`` (normal param) and ``computed_field``
        (``init=False``).  Only ``old_field`` → ``new_field`` should be auto-expanded;
        ``old_computed`` → ``computed_field`` must be excluded because ``computed_field``
        is not a valid ``__init__`` kwarg — passing it to the constructor raises ``TypeError``.
        """
        meta = object.__getattribute__(DepAutoExpandInitFalseDC, "__deprecation_config__")
        assert "old_field" in meta.args_mapping_auto_expanded
        assert "old_computed" not in meta.args_mapping_auto_expanded

    def test_init_false_field_constructor_not_erroring(self) -> None:
        """Calling the proxy with the normal kwarg succeeds; init=False kwarg is not passed.

        Before the fix, ``computed_field`` was included in ``args_mapping``, so passing
        ``old_computed=5`` would remap it to ``computed_field=5`` and pass it to the
        dataclass constructor, raising ``TypeError``.  After the fix the proxy never
        adds ``computed_field`` to ``args_mapping``, so construction via the normal
        param path succeeds.
        """
        with pytest.warns(FutureWarning):
            instance = DepAutoExpandInitFalseDC(old_field=3)  # type: ignore[call-arg]
        assert instance.new_field == 3
        assert instance.computed_field == 0  # default, not touched by constructor

    def test_overridden_init_in_range_field_auto_expanded(self) -> None:
        """Fields present in the overridden ``__init__`` signature are auto-expanded.

        ``AutoExpandOverriddenInitDC`` overrides ``__init__`` to accept only ``new_field``.
        ``old_field`` → ``new_field`` must appear in ``args_mapping_auto_expanded`` because
        ``new_field`` is in ``inspect.signature``.
        """
        meta = object.__getattribute__(DepAutoExpandOverriddenInitDC, "__deprecation_config__")
        assert "old_field" in meta.args_mapping_auto_expanded

    def test_overridden_init_absent_field_not_auto_expanded(self) -> None:
        """Fields absent from the overridden ``__init__`` signature are not auto-expanded.

        ``skipped_field`` is a dataclass field (``init=True`` by default in the ``@dataclass``
        descriptor) but intentionally absent from the overridden ``__init__``.  Using
        ``dataclasses.fields()`` would (incorrectly) include it; ``inspect.signature`` correctly
        excludes it.  Passing ``old_skipped`` to the constructor must not raise ``TypeError``.
        """
        meta = object.__getattribute__(DepAutoExpandOverriddenInitDC, "__deprecation_config__")
        assert "old_skipped" not in meta.args_mapping_auto_expanded

    def test_overridden_init_constructor_kwarg_warns(self) -> None:
        """Calling ``DepAutoExpandOverriddenInitDC`` with the deprecated kwarg warns and constructs.

        The old kwarg ``old_field`` is remapped to ``new_field`` via auto-expanded
        ``args_mapping``; the instance is created by the overridden ``__init__`` which
        accepts ``new_field``.
        """
        with pytest.warns(FutureWarning):
            instance = DepAutoExpandOverriddenInitDC(old_field=7)  # type: ignore[call-arg]
        assert instance.new_field == 7

    def test_positional_arg_warns_on_deprecated_class_construction(self) -> None:
        """``DepAutoExpandDC(5)`` warns because the class is deprecated even with no deprecated kwarg.

        The class itself is deprecated; the proxy always emits FutureWarning on construction,
        regardless of whether the caller uses the old or new field names.
        """
        with pytest.warns(FutureWarning):
            instance = DepAutoExpandDC(5)
        assert instance.new_field == 5

    def test_new_kwarg_wins_when_both_old_and_new_provided_old_first(self) -> None:
        """When old and new kwarg both passed (old first), the explicit new-name value wins.

        ``DepAutoExpandDC(old_field=5, new_field=6)`` must use ``new_field=6`` after
        remapping ``old_field`` to ``new_field``; the explicit new-name value takes precedence
        over the remapped old-field value.
        """
        with pytest.warns(FutureWarning), pytest.warns(UserWarning, match=r"`old_field` is ignored"):
            instance = DepAutoExpandDC(old_field=5, new_field=6)  # type: ignore[call-arg]
        assert instance.new_field == 6

    def test_new_kwarg_wins_when_both_old_and_new_provided_new_first(self) -> None:
        """New-name value wins regardless of whether old or new kwarg is listed first in the call.

        Before the precedence fix, ``DepAutoExpandDC(new_field=6, old_field=5)`` produced
        ``new_field=5`` because the dict-comprehension last-write-wins caused the
        ``old_field → new_field`` rename to overwrite the explicitly passed ``new_field=6``.
        """
        with pytest.warns(FutureWarning), pytest.warns(UserWarning, match=r"`old_field` is ignored"):
            instance = DepAutoExpandDC(new_field=6, old_field=5)  # type: ignore[call-arg]
        assert instance.new_field == 6


# ---------------------------------------------------------------------------
# Positional-only constructor guard + positional forwarding
# ---------------------------------------------------------------------------


class TestPositionalOnlyForwarding:
    """``args_mapping`` on a class with ``POSITIONAL_ONLY`` constructor parameters.

    The proxy must emit ``UserWarning`` at decoration time, record
    ``args_mapping_positional_only`` on ``DeprecationConfig``, and at call time reorder
    the remapped values into positional arguments by target-signature declaration order
    (the decorator's split approach) — forwarding ``new_val=7`` as a keyword would raise
    ``TypeError``, and the historical pop-and-``setattr`` fallback broke required params,
    immutable instances, and constructor-derived state.
    """

    def test_decoration_emits_user_warning(self) -> None:
        """Creating ``deprecated_class`` with ``args_mapping`` to a positional-only param warns.

        The ``UserWarning`` must mention that the target parameter is positional-only and
        that the remapped values are forwarded positionally, so developers understand the
        call shape their users will hit.
        """
        with pytest.warns(UserWarning, match="POSITIONAL_ONLY"):
            deprecated_class(
                args_mapping={"old_val": "new_val"},
                **_DEPRS_CASE_STD_ARGS,
            )(PositionalOnlyTarget)

    def test_incompatible_key_recorded_on_deprecated_meta(self) -> None:
        """``args_mapping_positional_only`` on ``__deprecation_config__`` lists the offending old key.

        The field is populated at decoration time so audit tools can surface it without
        re-inspecting the constructor signature at report time.
        """
        meta = object.__getattribute__(DepPositionalOnly, "__deprecation_config__")
        assert "old_val" in meta.args_mapping_positional_only

    def test_call_with_deprecated_kwarg_does_not_crash(self) -> None:
        """``DepPositionalOnly(old_val=7)`` succeeds via positional forwarding.

        Remapping ``old_val``→``new_val`` must not produce
        ``PositionalOnlyTarget(new_val=7)`` (a ``TypeError``); the proxy reorders the
        remapped value into the constructor's positional slot instead.
        """
        with pytest.warns(FutureWarning):
            instance = DepPositionalOnly(old_val=7)  # type: ignore[call-arg]
        assert instance.new_val == 7

    def test_required_positional_only_param_constructs(self) -> None:
        """A *required* positional-only constructor param is satisfied by the remapped value.

        A library renames ``old_val`` to a required positional-only ``new_val`` in the
        replacement class.  The historical ``setattr`` fallback crashed before it ever ran
        (``TypeError: missing 1 required positional argument``); positional forwarding
        must construct the instance correctly.
        """
        with pytest.warns(FutureWarning):
            instance = DepPositionalOnlyRequired(old_val=5)  # type: ignore[call-arg]
        assert instance.new_val == 5

    def test_immutable_target_constructs_via_positional_forwarding(self) -> None:
        """An immutable target (``__setattr__`` raises) works because no post-hoc setattr happens.

        Frozen-dataclass-style targets reject attribute assignment, so the old fallback
        raised even when the positional-only param had a default.  Forwarding the value
        through the constructor sidesteps mutation entirely.
        """
        with pytest.warns(FutureWarning):
            instance = DepPositionalOnlyImmutable(old_val=3)  # type: ignore[call-arg]
        assert instance.new_val == 3  # type: ignore[attr-defined]

    def test_constructor_derivation_runs(self) -> None:
        """State derived inside the constructor reflects the remapped value.

        The old ``setattr`` fallback assigned ``new_val`` *after* construction, silently
        bypassing any ``__post_init__``-style derivation — ``double`` stayed at its
        zero-argument value.  Positional forwarding runs the real constructor body.
        """
        with pytest.warns(FutureWarning):
            instance = DepPositionalOnlyDerived(old_val=7)  # type: ignore[call-arg]
        assert instance.new_val == 7
        assert instance.double == 14

    def test_positional_args_and_mapped_kwarg_combine(self) -> None:
        """Caller positional args fill the leading slots; the remapped value follows them.

        ``DepPositionalOnlyMixed(1, old_x=5)`` targets ``__init__(self, w, x, /)``: the
        caller's ``1`` binds to ``w`` and the remapped ``old_x``→``x`` value must land in
        the *next* positional slot — not trip the gap guard on ``w``.
        """
        with pytest.warns(FutureWarning):
            instance = DepPositionalOnlyMixed(1, old_x=5)  # type: ignore[call-arg]
        assert instance.w == 1
        assert instance.x == 5


def _circular_proxy_pair() -> tuple[Any, Any]:
    """Return two instance proxies that wrap each other, a stack no decorator produces (the cycle guard's input)."""
    first = deprecated_instance(object(), name="first", **_DEPRS_CASE_STD_ARGS)
    second = deprecated_instance(first, name="second", **_DEPRS_CASE_STD_ARGS)
    object.__setattr__(object.__getattribute__(first, "_DeprecatedProxy__config"), "obj", second)
    return first, second


class TestAttrsMappingValidation:
    """``attrs_mapping`` redirect targets are validated when the proxy is built, not on first access."""

    def test_missing_redirect_target_raises_value_error(self) -> None:
        """A redirect to an attribute the class lacks fails at decoration with the offending pair in the message.

        Otherwise the first caller touching the deprecated alias would get an ``AttributeError`` instead of a warning.

        """

        class _Config:
            new_attr = 1

        expected = r"redirect targets not found on the active class: \[\"'old_attr' -> 'gone'\"\]"
        with pytest.raises(ValueError, match=expected):
            deprecated_class(attrs_mapping={"old_attr": "gone", "other": "new_attr"}, **_DEPRS_CASE_STD_ARGS)(_Config)


class TestCircularProxyStacks:
    """A circular proxy stack is cut by the cycle guards instead of looping forever."""

    def test_isinstance_against_circular_stack_is_false(self) -> None:
        """``isinstance(x, proxy)`` on a stack that wraps itself answers ``False`` rather than hanging."""
        first, _ = _circular_proxy_pair()

        assert isinstance(1, first) is False

    def test_issubclass_against_circular_stack_is_false(self) -> None:
        """``issubclass(X, proxy)`` on a stack that wraps itself answers ``False`` rather than hanging."""
        first, _ = _circular_proxy_pair()

        assert issubclass(int, first) is False

    def test_static_owner_lookup_terminates_on_circular_stack(self) -> None:
        """Resolving the object attribute validation inspects stops inside the cycle and returns a proxy of it."""
        first, second = _circular_proxy_pair()

        owner = _DeprecatedProxy._get_static_attr_owner(first)

        assert owner is first or owner is second

    def test_static_owner_of_plain_object_is_itself(self) -> None:
        """An object that is no proxy is its own validation owner."""
        plain = object()

        assert _DeprecatedProxy._get_static_attr_owner(plain) is plain

    def test_source_path_terminates_on_circular_stack(self) -> None:
        """The ``%(source_path)s`` of a proxy over a self-wrapping stack falls back to the display name."""
        first, _ = _circular_proxy_pair()

        assert _proxy_source_path(first, "first") == "first"
