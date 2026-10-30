"""Unit tests for proxy factories and typing contracts in deprecate.proxy."""

import importlib
import inspect
import pickle
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest

import deprecate
import deprecate.proxy as proxy_package
from deprecate import DeprecationProxy
from deprecate._fatal import DeprecatedError
from deprecate._types import DeprecationConfig, TargetMode
from deprecate.docstring.inject import normalize_docstring_style
from deprecate.messaging import (
    TEMPLATE_ARGUMENT_MAPPING,
    TEMPLATE_WARNING_ARGUMENTS,
    TEMPLATE_WARNING_CALLABLE,
    TEMPLATE_WARNING_NO_TARGET,
)
from deprecate.proxy import deprecated_class, deprecated_instance
from deprecate.proxy.core import _DeprecatedProxy
from tests._helpers import captured_warnings
from tests.collections.api_current.core import ColorEnum, NewDataClass, Palette
from tests.collections.api_deprecated.core import (
    _DEPRS_CASE_STD_ARGS,
    DeprecatedColorDataClass,
    DeprecatedColorEnum,
    DeprecatedColorEnumFunctional,
    DeprecatedPaletteFunctionalFallback,
    WarnOnlyColorEnum,
    depr_ast_breadcrumb_dict,
)
from tests.unittests.proxy._helpers import _ast_class_proxy


class TestFactoryExports:
    """Public factory exports retain their identity when their implementation moves."""

    @pytest.mark.parametrize("name", ["deprecated_class", "deprecated_instance"])
    def test_package_export_is_implementation_and_pickleable(self, name: str) -> None:
        """Factory imports and pickle references keep resolving through the existing public package.

        A consumer may import a factory from either the package or the top-level API and store it by reference.
        The implementation module must expose the same function without changing its established pickle path.
        """
        factory = getattr(proxy_package, name)
        implementation = importlib.import_module("deprecate.proxy.factories")
        assert getattr(implementation, name) is factory
        assert getattr(deprecate, name) is factory
        assert factory.__module__ == "deprecate.proxy"
        assert pickle.loads(pickle.dumps(factory)) is factory  # noqa: S301 - trusted local round trip


class TestDecoratorFactory:
    """deprecated_class used as a class decorator."""

    def test_decorated_class_is_deprecated_proxy(self) -> None:
        """@deprecated_class wraps the class in a _DeprecatedProxy."""
        assert isinstance(WarnOnlyColorEnum, _DeprecatedProxy)
        assert isinstance(DeprecatedColorEnum, _DeprecatedProxy)

    def test_uses_class_name_as_proxy_name(self) -> None:
        """The proxy name is taken from the decorated class __name__."""
        name = object.__getattribute__(WarnOnlyColorEnum, "__deprecation_config__").name
        assert name == "WarnOnlyColorEnum"

    def test_no_target_reads_from_source(self) -> None:
        """Without a target, attribute access reads from the source class."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert WarnOnlyColorEnum.A.value == "a"

    def test_with_target_reads_from_target(self) -> None:
        """With a target, attribute access returns the target's member."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert DeprecatedColorEnum.RED is ColorEnum.RED

    def test_bare_call_default_target_is_none_not_misconfigured(self) -> None:
        """``deprecated_class()`` called fresh with no explicit ``target=`` records ``None`` as its target.

        ``None`` is the proxy's unset value: ``TargetMode.NOTIFY`` appears in audit metadata only when the
        caller explicitly chose it, so tools can distinguish "warn-only by default" from "warn-only on
        purpose"; the warn-on-access behaviour is identical either way.

        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)

            @deprecated_class(**_DEPRS_CASE_STD_ARGS)
            class BareDefaultTargetClass:
                """Plain class deprecated with no explicit target — proves the factory default."""

        dep = object.__getattribute__(BareDefaultTargetClass, "__deprecation_config__")
        assert dep.target is None
        assert dep.misconfigured is False

    @pytest.mark.parametrize(
        ("raw_target", "warning_category", "warning_message"),
        [
            (
                True,
                FutureWarning,
                "`target=True` without `args_mapping` resolves to `TargetMode.NOTIFY`"
                " (warns on every access). Will be `TypeError` in `v1.0`.",
            ),
            (
                False,
                UserWarning,
                "`target=False` is not valid for `deprecated_class()`. Will be `TypeError` in `v1.0`.",
            ),
        ],
    )
    def test_boolean_target_is_normalized_and_class_access_still_works(
        self, raw_target: bool, warning_category: type[Warning], warning_message: str
    ) -> None:
        """Legacy boolean targets are normalized before proxy metadata and access use them.

        Older code passed ``target=True`` or ``target=False`` to ``deprecated_class``.  Decorating a class that way
        must emit exactly one warning with the documented text (a ``FutureWarning`` for ``True`` and a
        ``UserWarning`` for ``False``), the stored configuration must hold ``target=None`` instead of the boolean,
        and the class must remain usable: it can still be instantiated and its methods called.

        """
        with pytest.warns(warning_category) as caught:

            @deprecated_class(target=raw_target, **_DEPRS_CASE_STD_ARGS, stream=None)
            class OldClass:
                def method(self) -> str:
                    return "ok"

        assert len(caught) == 1
        assert str(caught[0].message) == warning_message

        dep = object.__getattribute__(OldClass, "__deprecation_config__")
        assert dep.target is None

        obj = OldClass()
        assert obj.method() == "ok"

    def test_true_with_args_mapping_resolves_to_args_remap(self) -> None:
        """``target=True`` + non-empty args_mapping resolves to ARGS_REMAP with FutureWarning."""
        with pytest.warns(FutureWarning, match="TargetMode.ARGS_REMAP") as caught:

            @deprecated_class(target=True, **_DEPRS_CASE_STD_ARGS, args_mapping={"old_attr": "new_attr"}, stream=None)
            class OldClass:
                def method(self) -> str:
                    return "ok"

        assert len(caught) == 1
        dep = object.__getattribute__(OldClass, "__deprecation_config__")
        assert dep.target is TargetMode.ARGS_REMAP


class TestDecoratorEnum:
    """@deprecated_class applied to Enum classes."""

    @pytest.mark.parametrize(
        "action",
        [
            pytest.param(lambda: DeprecatedColorEnum(1), id="call"),
            pytest.param(lambda: DeprecatedColorEnum.RED, id="attribute"),
            pytest.param(lambda: DeprecatedColorEnum["RED"], id="item"),
        ],
    )
    def test_warns_and_redirects_to_target_member(self, action: Callable[[], object]) -> None:
        """DeprecationProxy Enum call, attribute, and item access should warn and resolve to target member."""
        with pytest.warns(
            FutureWarning,
            match=(
                r"The `DeprecatedColorEnum` was deprecated since v1\.0 in favor of "
                r"`tests\.collections\.api_current\.core\.ColorEnum`"
            ),
        ):
            result = action()
        assert result is ColorEnum.RED

    def test_no_target_warns_and_reads_source(self) -> None:
        """With ``target=None``, deprecated Enum should warn and return members from the original source Enum."""
        with pytest.warns(FutureWarning, match=r"The `WarnOnlyColorEnum` was deprecated since v1\.0"):
            val = WarnOnlyColorEnum.A
        assert val.value == "a"

    def test_returns_deprecated_proxy(self) -> None:
        """Decorator should return proxy objects so decorated classes expose warning-forwarding behavior."""
        assert isinstance(DeprecatedColorEnum, _DeprecatedProxy)
        assert isinstance(WarnOnlyColorEnum, _DeprecatedProxy)


class TestDecoratorDataclass:
    """@deprecated_class applied to dataclasses."""

    def test_instantiation_warns_and_redirects(self) -> None:
        """Constructing deprecated dataclass should warn and instantiate the replacement dataclass type."""
        with pytest.warns(
            FutureWarning,
            match=(
                r"The `DeprecatedColorDataClass` was deprecated since v1\.0 in favor of "
                r"`tests\.collections\.api_current\.core\.NewDataClass`"
            ),
        ):
            obj = DeprecatedColorDataClass(label="test", total=5)
        assert isinstance(obj, NewDataClass)
        assert obj.label == "test"
        assert obj.total == 5


class TestDeprecatedClassReadOnly:
    """Constraints on deprecated_class — unsupported parameters."""

    def test_read_only_not_in_signature(self) -> None:
        """deprecated_class does not expose read_only in its public API."""
        assert "read_only" not in inspect.signature(deprecated_class).parameters


class TestEmptyVersionGuard:
    """Decoration-time UserWarning when both ``deprecated_in`` and ``remove_in`` are empty.

    Both ``deprecated_class()`` and ``deprecated_instance()`` warn at construction time when neither version string is
    provided, because the rendered notice would otherwise contain empty ``v`` placeholders. ``stream=None`` suppresses
    the guard so callers that opt out of warnings entirely remain silent.

    """

    def test_deprecated_class_empty_versions_warns(self) -> None:
        """``@deprecated_class()`` with empty versions emits UserWarning at decoration time."""
        with pytest.warns(UserWarning, match=r"no `deprecated_in` set"):

            @deprecated_class()
            class OldEmptyVersions:
                """Source class with no version metadata supplied."""

                pass

    def test_deprecated_class_empty_versions_stream_none_silent(self) -> None:
        """``@deprecated_class(stream=None)`` suppresses the empty-versions UserWarning."""
        with captured_warnings() as caught:

            @deprecated_class(stream=None)
            class OldEmptyVersionsSilent:
                """Source class with ``stream=None`` — guard must stay silent."""

                pass

        assert not caught

    def test_deprecated_instance_empty_versions_warns(self) -> None:
        """``deprecated_instance()`` with empty versions emits UserWarning at instantiation time."""
        with pytest.warns(UserWarning, match=r"no `deprecated_in` set"):
            deprecated_instance({"k": 1})

    def test_deprecated_instance_empty_versions_stream_none_silent(self) -> None:
        """``deprecated_instance(stream=None)`` suppresses the empty-versions UserWarning."""
        with captured_warnings() as caught:
            deprecated_instance({"k": 1}, stream=None)
        assert not caught


class TestDeprecatedInstance:
    """deprecated_instance() wraps any Python object with transparent deprecation warnings."""

    def test_returns_deprecated_proxy(self) -> None:
        """deprecated_instance always returns a _DeprecatedProxy instance."""
        proxy = deprecated_instance({}, **_DEPRS_CASE_STD_ARGS, stream=None)
        assert isinstance(proxy, _DeprecatedProxy)

    def test_name_auto_inferred_from_type(self) -> None:
        """Without name=, proxy name defaults to type(obj).__name__."""
        proxy = deprecated_instance({"k": 1}, **_DEPRS_CASE_STD_ARGS, stream=None)
        dep = object.__getattribute__(proxy, "__deprecation_config__")
        assert dep.name == "dict"

    def test_name_auto_inferred_for_list(self) -> None:
        """Type name inference works for any built-in type."""
        proxy = deprecated_instance([1, 2], **_DEPRS_CASE_STD_ARGS, stream=None)
        dep = object.__getattribute__(proxy, "__deprecation_config__")
        assert dep.name == "list"

    def test_name_explicitly_set(self) -> None:
        """Explicit name= overrides the type-based inference."""
        proxy = deprecated_instance({}, name="my_config", **_DEPRS_CASE_STD_ARGS, stream=None)
        dep = object.__getattribute__(proxy, "__deprecation_config__")
        assert dep.name == "my_config"

    def test_version_metadata_stored(self) -> None:
        """deprecated_in and remove_in are stored verbatim in DeprecationConfig."""
        proxy = deprecated_instance([], deprecated_in="2.0", remove_in="3.5", stream=None)
        dep = object.__getattribute__(proxy, "__deprecation_config__")
        assert dep.deprecated_in == "2.0"
        assert dep.remove_in == "3.5"

    def test_warns_once_by_default(self) -> None:
        """Default ``num_warns=1`` means only the first access emits a warning.

        This is specific to deprecated_instance() — unlike _DeprecatedProxy which requires an explicit num_warns,
        deprecated_instance() defaults to num_warns=1.

        """
        proxy = deprecated_instance({"k": 1}, **_DEPRS_CASE_STD_ARGS)
        with captured_warnings() as caught:
            _ = proxy["k"]
            _ = proxy["k"]
        assert len(caught) == 1

    def test_stream_none_suppresses_on_item_access(self) -> None:
        """``stream=None`` suppresses warnings even when items are accessed via __getitem__."""
        proxy = deprecated_instance({"k": "v"}, name="x", **_DEPRS_CASE_STD_ARGS, stream=None)
        with captured_warnings() as caught:
            _ = proxy["k"]
        assert not caught


if TYPE_CHECKING:
    # The static type of a `deprecated_class` alias follows what the proxy forwards to at runtime. Pinned statically
    # because the runtime object is a `_DeprecatedProxy` either way: a regression back to the proxy type, or a widening
    # to `Any`, would leave every runtime test passing. mypy analyses this block (always true for a type checker) but
    # the interpreter never executes it, so the calls construct nothing and emit no warnings.
    # NOTE: `[tool.mypy] mypy_path = "src"` is what makes these bite — without it `deprecate` is
    # unresolvable from `tests/**`, `ignore_missing_imports` turns every symbol into `Any`, and
    # `assert_type` degrades to a silent no-op that passes against any signature whatsoever.
    from typing_extensions import assert_type

    # Without a replacement class the alias stands in for the wrapped class and keeps its own type.
    assert_type(DeprecatedPaletteFunctionalFallback, type[Palette])
    # Using a proxy-typed alias would break downstream: `isinstance`/`issubclass` narrowing, construction, and handing
    # the alias on wherever a class object is expected.
    _any_object: object = Palette()
    if isinstance(_any_object, DeprecatedPaletteFunctionalFallback):
        assert_type(_any_object, Palette)
    _any_class: type = Palette
    if issubclass(_any_class, DeprecatedPaletteFunctionalFallback):
        assert_type(_any_class, type[Palette])
    assert_type(DeprecatedPaletteFunctionalFallback(), Palette)
    # With `target=NewCls` the alias is the target: the proxy resolves `isinstance`, construction and attribute access
    # against it, so the private source it wraps never shows up in the type (its instances are not instances of it).
    assert_type(DeprecatedColorEnumFunctional, type[ColorEnum])
    if isinstance(_any_object, DeprecatedColorEnumFunctional):
        assert_type(_any_object, ColorEnum)
    assert_type(DeprecatedColorEnumFunctional(1), ColorEnum)
    # The wrapped class does not matter for the result: a bare `type` (built with `type(name, bases, ns)`) is accepted
    # and the alias is still the target. Stacking another alias or a proxy-typed object is accepted as well.
    assert_type(
        deprecated_class(target=Palette, deprecated_in="1.0", remove_in="2.0")(type("_Dynamic", (), {})), type[Palette]
    )
    assert_type(
        deprecated_class(target=Palette, deprecated_in="1.0", remove_in="2.0")(DeprecatedColorEnumFunctional),
        type[Palette],
    )
    assert_type(
        deprecated_class(target=Palette, deprecated_in="1.0", remove_in="2.0")(_ast_class_proxy), _DeprecatedProxy
    )
    # Stacking over a class-typed alias keeps the class type; a class built with `type(name, bases, ns)` stays a plain
    # `type` (a `type[_T] -> type[_T]` signature would infer `type[Never]` there); an object already typed as a proxy
    # keeps the concrete proxy type.
    assert_type(
        deprecated_class(deprecated_in="1.0", remove_in="2.0")(DeprecatedPaletteFunctionalFallback), type[Palette]
    )
    assert_type(deprecated_class(deprecated_in="1.0", remove_in="2.0")(type("_Dynamic", (), {})), type)
    assert_type(deprecated_class(deprecated_in="1.0", remove_in="2.0")(_ast_class_proxy), _DeprecatedProxy)
    # `skip_if` cannot be modelled: the steady-state target type is used, so a source that is served while skipped
    # must stay compatible with it.
    assert_type(deprecated_class(target=Palette, skip_if=True, stream=None)(Palette), type[Palette])

    # Decorator form: the alias is a class statement, so it is also valid in annotations and as a base class.
    # A call-form alias is a variable, which no type checker accepts in a type expression.
    def _accepts_legacy(config: DeprecatedColorDataClass) -> DeprecatedColorDataClass:
        return config

    class _LegacySubclass(DeprecatedColorDataClass):
        pass


class TestDeprecatedProtocol:
    """The ``DeprecationProxy`` Protocol is part of the public API surface.

    Provides a documented, public type for the result of ``deprecated_class`` and
    ``deprecated_instance`` so user code can annotate against it without importing a private name.
    """

    def test_public_symbol_exported(self) -> None:
        """``DeprecationProxy`` is importable from ``deprecate`` and listed in ``__all__``.

        This is the contract users rely on for type annotations without needing to know the private
        ``_DeprecatedProxy`` name; a symbol missing from ``__all__`` is invisible to star-imports and
        to the docs build, so both halves are pinned here.
        """
        assert deprecate.DeprecationProxy is DeprecationProxy
        assert "DeprecationProxy" in deprecate.__all__

    @pytest.mark.parametrize(
        "legacy_name",
        [
            pytest.param("Deprecated", id="bare-protocol-alias"),
            pytest.param("DeprecatedClass", id="class-alias"),
            pytest.param("DeprecatedInstance", id="instance-alias"),
        ],
    )
    def test_legacy_aliases_removed(self, legacy_name: str) -> None:
        """The pre-rename aliases are gone from the package surface, not merely undocumented.

        ``Deprecated``, ``DeprecatedClass`` and ``DeprecatedInstance`` were all bound to the same object
        before the rename to ``DeprecationProxy``. Absence is currently only a matter of nobody
        re-adding them; without this guard a stray re-export — or an ``__all__`` entry left behind by a
        merge — would restore names the rename exists to retire, and no other test would notice.
        """
        assert not hasattr(deprecate, legacy_name)
        assert legacy_name not in deprecate.__all__

    @pytest.mark.parametrize(
        ("name", "canonical"),
        [
            pytest.param("DeprecationConfig", DeprecationConfig, id="DeprecationConfig"),
            pytest.param("DeprecatedError", DeprecatedError, id="DeprecatedError"),
            pytest.param("normalize_docstring_style", normalize_docstring_style, id="normalize_docstring_style"),
            pytest.param("TEMPLATE_ARGUMENT_MAPPING", TEMPLATE_ARGUMENT_MAPPING, id="TEMPLATE_ARGUMENT_MAPPING"),
            pytest.param("TEMPLATE_WARNING_ARGUMENTS", TEMPLATE_WARNING_ARGUMENTS, id="TEMPLATE_WARNING_ARGUMENTS"),
            pytest.param("TEMPLATE_WARNING_CALLABLE", TEMPLATE_WARNING_CALLABLE, id="TEMPLATE_WARNING_CALLABLE"),
            pytest.param("TEMPLATE_WARNING_NO_TARGET", TEMPLATE_WARNING_NO_TARGET, id="TEMPLATE_WARNING_NO_TARGET"),
        ],
    )
    def test_pre_split_names_still_reachable(self, name: str, canonical: object) -> None:
        """A public-looking name the single-module ``deprecate.proxy`` exposed is still an attribute of the package.

        Before ``deprecate.proxy`` became a package, code could write ``from deprecate.proxy import
        TEMPLATE_WARNING_CALLABLE`` (or ``DeprecationConfig``, ``DeprecatedError``, ...) because the module imported
        those names.  Such an import must keep working after the split and must hand back the very object its
        defining module holds, not a copy that could drift from it.
        """
        assert getattr(proxy_package, name) is canonical

    def test_runtime_checkable_protocol(self) -> None:
        """``DeprecationProxy`` is ``@runtime_checkable``: ``isinstance`` works on proxy instances.

        Although the Protocol is structural, runtime checks let user code write
        ``isinstance(my_proxy, DeprecationProxy)`` without a cast. Both ``deprecated_class`` and
        ``deprecated_instance`` outputs must satisfy the Protocol at runtime.
        """
        assert isinstance(DeprecatedColorEnum, DeprecationProxy)
        assert isinstance(depr_ast_breadcrumb_dict, DeprecationProxy)

    def test_issubclass_rejects_data_protocol(self) -> None:
        """``issubclass`` against ``DeprecationProxy`` raises ``TypeError``.

        ``DeprecationProxy`` declares attributes (``__wrapped__``, ``__deprecation_config__``), which makes it a
        *data* Protocol — Python only supports ``isinstance`` for those. Users reaching for
        ``issubclass`` get a hard error rather than a wrong answer, so the docstring warning is pinned
        here to catch any future change that silently loosens it.
        """
        with pytest.raises(TypeError, match="non-method members"):
            issubclass(dict, DeprecationProxy)  # type: ignore[misc]

    def test_wrapped_is_typed_as_attribute_on_proxy_class(self) -> None:
        """``_DeprecatedProxy`` declares ``__wrapped__`` and ``__signature__`` at class level.

        Static type-checkers cannot introspect attributes set only in ``__init__`` without a
        class-level declaration. The annotation lets mypy/pyright see the breadcrumb attributes.
        """
        # Inspect class annotations directly — attributes set only in __init__ are absent from __annotations__.
        assert "__wrapped__" in _DeprecatedProxy.__annotations__
        assert "__signature__" in _DeprecatedProxy.__annotations__

    def test_functional_form_forwards_like_decorator_form(self) -> None:
        """The functional/assignment form forwards construction exactly as the decorator form does.

        ``deprecated_class`` returns the concrete ``_DeprecatedProxy`` in every call shape, so the choice
        between ``deprecated_class(target=ColorEnum)(OldCls)`` and ``@deprecated_class(target=ColorEnum)``
        is a static-annotation concern only. This pins that it stays a static one: whichever form a caller
        picks — annotated or not — the proxy must forward construction to the target identically at runtime.
        """
        # Runtime: calling the proxy forwards to the target constructor, exactly as the decorator form does.
        assert DeprecatedColorEnumFunctional(1) is ColorEnum.RED
        assert isinstance(DeprecatedColorEnumFunctional, DeprecationProxy)
