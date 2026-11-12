"""Unit tests for private helpers in :mod:`deprecate.audit._wrappers`."""

import dataclasses
import importlib.util
import types
import warnings
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import pytest

import tests.collections.api_deprecated.core as core_wrappers
import tests.collections.modules.old_math as old_math
import tests.collections.policy as policy_wrappers
from deprecate import (
    DeprecatedError,
    as_exceptions,
    deprecated,
    deprecated_instance,
    find_deprecation_wrappers,
    validate_deprecation_expiry,
)
from deprecate._types import DeprecationConfig
from deprecate.audit import (
    ChainType,
    DeprecationWrapperInfo,
    validate_deprecation_wrapper,
)
from deprecate.audit._wrappers import (
    _ApiType,
    _classify_member_api_type,
    _classify_wrapper_api_type,
    _source_location,
)
from deprecate.proxy import _DeprecatedProxy, _ProxySpec
from tests.collections.api_current.core import (
    ColorEnum,
    ModuleRaisingGetattributeMeta,
    ModuleRaisingPropertyMeta,
    RaisingEqualityData,
    SourcePathTarget,
)
from tests.collections.api_deprecated.regressions import (
    DeprSourcePathClass,
    DeprSourcePathStacked,
    depr_notify_lru_cached,
    make_depr_module_raising_class,
)
from tests.collections.chains import caller_via_notify_layer

_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None
_requires_packaging = pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging library")


@_requires_packaging
class TestValidateDeprecationWrapperWithProxy:
    """Unit tests for validate_deprecation_wrapper with inline _DeprecatedProxy objects.

    Uses _DeprecatedProxy directly (not collection fixtures) for true isolation.

    """

    def test_proxy_without_target_no_effect_false(self) -> None:
        """Proxy with no forwarding target is effective (still emits warnings) → no_effect=False."""
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(name="legacy_cfg", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.function == "legacy_cfg"
        assert result.no_effect is False
        assert result.chain_type is None

    def test_proxy_with_callable_target_no_effect_false(self) -> None:
        """Proxy forwarding to a callable target is effective → no_effect=False."""
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(name="old_enum", deprecated_in="1.0", remove_in="2.0", target=ColorEnum, stream=None),
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.function == "old_enum"
        assert result.deprecated_info.target is ColorEnum
        assert result.no_effect is False

    def test_proxy_with_args_mapping_skips_signature_validation(self) -> None:
        """Proxy __call__ is (*args, **kwargs) so signature check is skipped — invalid_args is always [].

        A package deprecates a class with ``deprecated_class`` and renames a constructor argument through
        ``args_mapping``.  The audit cannot validate the mapped names against a signature, because the proxy's
        ``__call__`` accepts ``(*args, **kwargs)``, so it must not report the mapping keys as invalid arguments.  The
        configured mapping is still exposed unchanged on the result.

        """
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(
                name="mapped",
                deprecated_in="1.0",
                remove_in="2.0",
                target=ColorEnum,
                args_mapping={"old_key": "value"},
                stream=None,
            ),
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.deprecated_info.args_mapping == {"old_key": "value"}
        assert result.invalid_args == []

    def test_proxy_with_identity_args_mapping_detected(self) -> None:
        """Proxy with an identity args_mapping entry still detects it — invalid_args stays [].

        A maintainer wrote a mapping that renames an argument to itself (``{"value": "value"}``) on a
        ``deprecated_class`` proxy, which is a mistake that would warn about nothing useful.  Although the proxy
        offers no signature to check, the audit must still list that entry under ``identity_args_mapping`` and keep
        ``invalid_args`` empty, so the lint rule works for proxies as it does for decorated functions.

        """
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(
                name="identity_mapped",
                deprecated_in="1.0",
                remove_in="2.0",
                target=ColorEnum,
                args_mapping={"value": "value"},
                stream=None,
            ),
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.identity_args_mapping == ["value"]
        assert result.invalid_args == []

    def test_proxy_no_target_with_args_mapping(self) -> None:
        """Proxy with target=None and args_mapping: invalid_args=[] and no_effect=False (still warns).

        A class is deprecated with warn-only behaviour (no replacement target) and also remaps an argument through
        ``args_mapping``.  A proxy with a mapping still emits a warning when used, so the audit must not flag it as
        having no effect, and as for every proxy the signature check is skipped, so ``invalid_args`` stays empty.

        """
        proxy = _DeprecatedProxy(
            obj=ColorEnum,
            spec=_ProxySpec(
                name="warn_only_mapped",
                deprecated_in="1.0",
                remove_in="2.0",
                target=None,
                args_mapping={"x": "y"},
                stream=None,
            ),
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.invalid_args == []
        assert result.no_effect is False

    def test_proxy_attrs_mapping_chain_detected_as_stacked_chain(self) -> None:
        """Audit reports an ``attrs_mapping`` chain without decoration-time failure.

        A deprecated class proxy maps attribute ``a`` to ``b`` and ``b`` to ``c``, so a caller reading ``a`` would be
        sent to a name that is itself deprecated.  The audit must not fail while inspecting such a proxy and must
        classify the result as a stacked chain, so the misconfiguration is reported instead of raising.

        """

        class Palette:
            a = 1
            b = 2
            c = 3

        proxy = _DeprecatedProxy(
            obj=Palette,
            spec=_ProxySpec(
                name="Palette",
                deprecated_in="1.0",
                remove_in="2.0",
                attrs_mapping={"a": "b", "b": "c"},
                stream=None,
            ),
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.chain_type is ChainType.STACKED

    def test_proxy_function_name_from_dep_info(self) -> None:
        """Function field comes from dep_info.name, not from getattr(proxy, '__name__').

        Without this, getattr routes through __getattr__ and leaks the target's __name__.

        """
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(name="SourceName", deprecated_in="1.0", remove_in="2.0", target=ColorEnum, stream=None),
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.function == "SourceName"
        assert result.function != ColorEnum.__name__

    def test_proxy_empty_args_mapping_true_when_no_args_mapping(self) -> None:
        """Proxy with args_mapping=None reports empty_args_mapping=True."""
        proxy = _DeprecatedProxy(obj={}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0", stream=None))
        result = validate_deprecation_wrapper(proxy)
        assert result.deprecated_info.args_mapping is None
        assert result.empty_args_mapping is True

    def test_callable_targeting_notify_wrapper_is_target_chain(self) -> None:
        """A callable target pointing to a NOTIFY wrapper is a forwarding TARGET chain.

        A team first turns an old helper into a warning-only shim, then later redirects another wrapper at that
        shim instead of at the surviving implementation. The audit must report the outer wrapper as a chain even
        though the inner link forwards nowhere further.
        """
        result = validate_deprecation_wrapper(caller_via_notify_layer)
        assert result.chain_type is ChainType.TARGET

    def test_instance_target_that_is_deprecated_is_target_chain(self) -> None:
        """An instance alias whose replacement is itself a deprecated data object is a forwarding TARGET chain.

        ``OLD_SETTINGS`` was pointed at ``MID_SETTINGS``, which a later release deprecated in turn. A settings dict is
        not callable, yet callers of the old name now hop through a second deprecated object, so the audit must
        report the chain and tell the author to point straight at the final replacement.
        """
        mid = deprecated_instance({"timeout": 30}, name="MID_SETTINGS", deprecated_in="1.5", remove_in="3.0")
        old = deprecated_instance(
            {"timeout": 10}, name="OLD_SETTINGS", target=mid, deprecated_in="1.0", remove_in="2.0"
        )
        result = validate_deprecation_wrapper(old)
        assert result.chain_type is ChainType.TARGET

    def test_instance_with_raising_equality_replacement_audits_without_comparing(self) -> None:
        """Audit a data alias whose replacement raises from ``==`` without calling that ``__eq__``.

        ``OLD_ARRAY`` was pointed at a replacement such as a numpy array or a data frame, whose ``==`` against a
        scalar raises. The audit looks at the replacement only by identity, so a CI run over such a package must
        finish and report the alias, rather than crash while checking whether it forwards to itself.
        """
        old = deprecated_instance(
            {"values": [1]}, name="OLD_ARRAY", target=RaisingEqualityData(), deprecated_in="1.0", remove_in="2.0"
        )
        result = validate_deprecation_wrapper(old)
        assert result.function == "OLD_ARRAY"
        assert result.self_reference is False
        assert result.chain_type is None

    def test_data_proxy_with_args_mapping_is_misconfigured_target(self) -> None:
        """Audit flags a data object deprecated with an ``args_mapping`` it can never apply.

        A settings dict is never called, so renaming a call argument on it has no effect. The factory already warns
        at creation; the audit run over the package must also report the alias as a misconfigured target so the
        mistake surfaces in CI and not only on import.
        """
        with pytest.warns(UserWarning, match="is not callable"):
            proxy = deprecated_instance(
                {"timeout": 10}, name="OLD_SETTINGS", args_mapping={"old": "new"}, deprecated_in="1.0", remove_in="2.0"
            )
        result = validate_deprecation_wrapper(proxy)
        assert result.misconfigured_target is True

    def test_instance_target_with_inert_args_mapping_is_target_chain(self) -> None:
        """A deprecated data replacement carrying an ``args_mapping`` it never applies is a TARGET link, not STACKED.

        ``MID_SETTINGS`` was deprecated with an ``args_mapping`` by mistake: a settings dict is never called, so the
        mapping is inert, yet its own config resolves to ``ARGS_REMAP``. Reporting ``OLD_SETTINGS`` as a stacked chain
        would steer the author toward composing call mappings that never run; the old name only hops through a second
        deprecated data object.
        """
        with pytest.warns(UserWarning, match="is not callable"):
            mid = deprecated_instance(
                {"timeout": 30}, name="MID_SETTINGS", args_mapping={"old": "new"}, deprecated_in="1.5", remove_in="3.0"
            )
        old = deprecated_instance(
            {"timeout": 10}, name="OLD_SETTINGS", target=mid, deprecated_in="1.0", remove_in="2.0"
        )
        result = validate_deprecation_wrapper(old)
        assert result.chain_type is ChainType.TARGET


def _defining_line(info: DeprecationWrapperInfo) -> str:
    """Return the stripped source line ``info.source_file``:``info.source_line`` points at."""
    assert info.source_file is not None
    assert info.source_line is not None
    return Path(info.source_file).read_text(encoding="utf-8").splitlines()[info.source_line - 1].strip()


class TestSourceLocation:
    """``source_file``/``source_line`` place each deprecated wrapper kind in its source; nothing else changes."""

    @pytest.mark.parametrize(
        ("wrapper", "file_name", "line_start"),
        [
            pytest.param(core_wrappers.decorated_sum, "core.py", "@_deprecation_sum", id="function"),
            pytest.param(depr_notify_lru_cached, "regressions.py", "@lru_cache", id="outer-lru-cache"),
            pytest.param(core_wrappers.DeprecatedEnum, "core.py", "@deprecated_class(", id="class-proxy"),
            pytest.param(DeprSourcePathStacked, "core.py", "class SourcePathTarget", id="stacked-class-proxies"),
            pytest.param(
                core_wrappers.InnerOrderDeprecatedPropCls.__dict__["value"].fget,
                "core.py",
                "@property",
                id="inner-order-property",
            ),
        ],
    )
    def test_points_at_first_decorator(self, wrapper: Any, file_name: str, line_start: str) -> None:  # noqa: ANN401
        """The location is the defining file and the line of the definition's first decorator.

        A CI annotation or SARIF result built from the location must land on the line a reviewer recognises as the
        deprecation, for a function, a ``deprecated_class`` proxy and a property whose getter carries the wrapper.

        """
        info = validate_deprecation_wrapper(wrapper)
        assert info.source_file is not None
        assert Path(info.source_file).name == file_name
        assert Path(info.source_file).is_absolute()
        assert _defining_line(info).startswith(line_start)

    def test_outer_wrapper_over_class_proxy_is_located_by_the_class(self) -> None:
        """A decorator applied above a ``deprecated_class`` proxy is located by the class the proxy wraps.

        A logging or caching decorator over a deprecated class reaches the proxy through ``__wrapped__``.  Following
        that chain stops at the proxy and lets the proxy rule decide, because reading through a proxy would land on
        whatever object it wraps, which is not always where the deprecation was declared; for a class proxy the rule
        gives the wrapped class.

        """

        def _outer() -> None:
            """Stand-in for the wrapper function an outer decorator returns."""

        _outer.__dict__["__wrapped__"] = DeprSourcePathClass
        assert _source_location(_outer) == _source_location(SourcePathTarget)

    def test_module_has_file_without_line(self) -> None:
        """A deprecated module is located by its file only; it has no single defining line.

        ``deprecated_module`` marks the whole module, so annotating its first line would point at unrelated code.

        """
        info = validate_deprecation_wrapper(old_math)
        assert info.source_file is not None
        assert Path(info.source_file).name == "old_math.py"
        assert info.source_line is None

    def test_instance_has_no_location(self) -> None:
        """A ``deprecated_instance`` wraps a data object, which has no defining source of its own.

        The proxy's wrapped value is a plain instance created at runtime; guessing its class's file would point at
        the wrong definition, so both fields stay ``None``.

        """
        info = validate_deprecation_wrapper(policy_wrappers.warns_without_template_instance)
        assert (info.source_file, info.source_line) == (None, None)

    def test_string_compiled_code_has_no_location(self) -> None:
        """Code compiled from a string has no file to point at, so ``<string>`` never becomes a path.

        Generated or ``exec``-built functions report ``co_filename == "<string>"``; a path made from it would be a
        file that does not exist.

        """
        namespace: dict[str, Any] = {}
        exec(compile("def generated():\n    pass\n", "<string>", "exec"), namespace)  # noqa: S102
        assert _source_location(namespace["generated"]) == (None, None)

    @pytest.mark.parametrize(
        "dynamic_class",
        [
            pytest.param(Enum("DynamicColor", "RED GREEN"), id="functional-enum"),
            pytest.param(type("DynamicSubclass", (SourcePathTarget,), {}), id="type-built-subclass"),
        ],
    )
    def test_dynamically_built_class_has_no_location(self, dynamic_class: type) -> None:
        """A class built at runtime has no definition in any file, so it is not given its base class's line.

        A functional ``Enum("Color", "RED GREEN")`` or a ``type(name, bases, ns)`` call creates a class with no
        ``class`` statement.  From Python 3.13 the compiler stores ``__firstlineno__`` on each ``class`` statement, and
        such a class inherits its base's value through the MRO (``Enum``'s, or ``SourcePathTarget``'s here).  Reading
        it would annotate an unrelated line in the base's file, so only the class's own attribute may count.

        """
        assert _source_location(dynamic_class) == (None, None)

    @pytest.mark.parametrize(
        "metaclass",
        [
            pytest.param(ModuleRaisingGetattributeMeta, id="getattribute-raises"),
            pytest.param(ModuleRaisingPropertyMeta, id="module-property-raises"),
        ],
    )
    def test_refusing_metaclass_has_no_location(self, metaclass: type) -> None:
        """A deprecated class whose metaclass raises on attribute lookups is audited, just without a location.

        A framework class may use a metaclass that intercepts attribute lookups and raises on names it does not serve
        (here ``__module__``, which the source lookup reads).  The location is optional metadata, so
        ``validate_deprecation_wrapper`` must still return its result with both fields ``None`` instead of letting
        the ``RuntimeError`` abort the audit of every other wrapper.

        """
        info = validate_deprecation_wrapper(make_depr_module_raising_class(metaclass))
        assert (info.source_file, info.source_line) == (None, None)

    def test_location_takes_no_part_in_equality(self) -> None:
        """Two results that differ only in location compare equal and print the same ``repr``.

        Existing callers compare ``DeprecationWrapperInfo`` values in tests and pipelines; adding the location must
        not make equal configurations unequal.

        """
        info = validate_deprecation_wrapper(core_wrappers.decorated_sum)
        moved = dataclasses.replace(info, source_file="/elsewhere.py", source_line=1)
        assert moved == info
        assert repr(moved) == repr(info)

    def test_pep702_row_is_located(self) -> None:
        """A PEP 702-only row is located like a pyDeprecate wrapper of the same shape.

        ``include_pep702=True`` lists symbols deprecated with ``warnings.deprecated`` alone; they are annotated in CI
        the same way, so they need the same location.

        """
        rows = find_deprecation_wrappers("tests.collections.pep702", include_pep702=True, recursive=False)
        pep702_rows = [row for row in rows if row.pep702_message is not None]
        assert pep702_rows
        assert {Path(row.source_file or "").name for row in pep702_rows} == {"pep702.py"}


class TestDeprecationWrapperInfoEmptyVersions:
    """DeprecationWrapperInfo.empty_deprecated_in reflects missing version metadata (F1b)."""

    def test_empty_deprecated_in_true_when_both_missing(self) -> None:
        """empty_deprecated_in=True when both deprecated_in and remove_in are absent."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)

            @deprecated()
            def fn_no_versions() -> None:
                pass

        info = validate_deprecation_wrapper(fn_no_versions)
        assert info.empty_deprecated_in is True

    def test_empty_deprecated_in_false_when_remove_in_only_missing(self) -> None:
        """empty_deprecated_in=False when deprecated_in is set but remove_in is omitted — valid use case."""

        @deprecated(deprecated_in="1.0")
        def fn_partial() -> None:
            pass

        info = validate_deprecation_wrapper(fn_partial)
        assert info.empty_deprecated_in is False

    def test_empty_deprecated_in_false_when_both_present(self) -> None:
        """empty_deprecated_in=False when both deprecated_in and remove_in are set."""

        @deprecated(deprecated_in="1.0", remove_in="2.0")
        def fn_complete() -> None:
            pass

        info = validate_deprecation_wrapper(fn_complete)
        assert info.empty_deprecated_in is False


class TestDeprecationWrapperInfoCompatAliases:
    """Deprecated @property aliases are deprecated through pyDeprecate itself and so are audit-visible (H3)."""

    def _make_info(self) -> DeprecationWrapperInfo:
        """Return a minimal DeprecationWrapperInfo for alias access tests."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)

            @deprecated()
            def _fn() -> None:
                pass

        return validate_deprecation_wrapper(_fn)

    @pytest.mark.parametrize(
        ("alias", "replacement"),
        [
            pytest.param("empty_mapping", "empty_args_mapping", id="empty_mapping"),
            pytest.param("identity_mapping", "identity_args_mapping", id="identity_mapping"),
        ],
    )
    def test_alias_emits_future_warning(self, alias: str, replacement: str) -> None:
        """Accessing a legacy alias emits a FutureWarning naming the replacement.

        The alias is wrapped with pyDeprecate's own decorator, so it warns with the library's category rather
        than a plain ``DeprecationWarning`` that default filters hide from the code reading the audit result.
        """
        info = self._make_info()
        with pytest.warns(FutureWarning, match=f"renamed to '{replacement}'"):
            getattr(info, alias)

    @pytest.mark.parametrize(
        ("alias", "replacement"),
        [
            pytest.param("empty_mapping", "empty_args_mapping", id="empty_mapping"),
            pytest.param("identity_mapping", "identity_args_mapping", id="identity_mapping"),
        ],
    )
    def test_alias_returns_replacement_value(self, alias: str, replacement: str) -> None:
        """Accessing a legacy alias returns the same value as its replacement field."""
        info = self._make_info()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            assert getattr(info, alias) == getattr(info, replacement)

    @pytest.mark.parametrize("alias", ["empty_mapping", "identity_mapping"])
    def test_alias_warns_on_every_read(self, alias: str) -> None:
        """A legacy alias warns on each read, not only the first (``num_warns=-1``).

        A caller reading the alias in several places must be told at every one of them; a one-shot warning would
        leave the second and later call sites quietly on the removed name until v1.0 breaks them.
        """
        info = self._make_info()
        with pytest.warns(FutureWarning, match="renamed to") as caught:
            [getattr(info, alias) for _ in range(2)]
        assert len([w for w in caught if issubclass(w.category, FutureWarning)]) == 2

    @pytest.mark.parametrize("alias", ["empty_mapping", "identity_mapping"])
    def test_alias_raises_inside_as_exceptions(self, alias: str) -> None:
        """Reading a legacy alias inside ``as_exceptions()`` raises ``DeprecatedError`` instead of warning.

        A strict CI section that sweeps for deprecated usage must catch the audit result's old field names too,
        since the aliases are ordinary pyDeprecate wrappers deferring to the global switch.
        """
        info = self._make_info()
        with as_exceptions(), pytest.raises(DeprecatedError, match="renamed to"):
            getattr(info, alias)

    @pytest.mark.parametrize("alias", ["empty_mapping", "identity_mapping"])
    def test_alias_is_listed_by_find_deprecation_wrappers(self, alias: str) -> None:
        """The package's own audit lists each legacy alias with its 0.8 deprecation and 1.0 removal versions.

        The scheduled removal of these aliases is tracked by the same audit the package offers its users, so the
        alias must be discoverable as a class member of the module defining it, with the versions it documents.
        """
        found = {
            item.function: item for item in find_deprecation_wrappers("deprecate.audit._wrappers", recursive=False)
        }
        config = found[f"DeprecationWrapperInfo.{alias}"].deprecated_info
        assert (config.deprecated_in, config.remove_in) == ("0.8", "1.0")

    @_requires_packaging
    @pytest.mark.parametrize("alias", ["empty_mapping", "identity_mapping"])
    @pytest.mark.parametrize(
        ("current_version", "flagged"),
        [
            pytest.param("0.9", False, id="before-removal"),
            pytest.param("1.0", True, id="at-removal"),
            pytest.param("1.1", True, id="past-removal"),
        ],
    )
    def test_alias_is_flagged_by_expiry_audit_from_v1_0(self, alias: str, current_version: str, flagged: bool) -> None:
        """The expiry audit flags a legacy alias once the current version reaches its scheduled 1.0 removal.

        The aliases exist so that the project's own CI turns red when v1.0 is cut with them still in place; at
        0.9 they are still within their deprecation period and must not be reported.
        """
        expired = validate_deprecation_expiry("deprecate.audit._wrappers", current_version, recursive=False)
        assert (
            any(f"`deprecate.audit._wrappers.DeprecationWrapperInfo.{alias}`" in message for message in expired)
            is flagged
        )


class TestDwiCompatInit:
    """_dwi_compat_init shim translates legacy constructor kwargs with DeprecationWarning (H4)."""

    def test_old_empty_mapping_kwarg_is_translated(self) -> None:
        """DeprecationWrapperInfo(empty_mapping=True) emits DeprecationWarning and sets empty_args_mapping."""
        with pytest.warns(DeprecationWarning, match="renamed to 'empty_args_mapping'"):
            info = DeprecationWrapperInfo(  # type: ignore[call-arg]
                function="f", deprecated_info=DeprecationConfig(), empty_mapping=True
            )
        assert info.empty_args_mapping is True

    def test_old_identity_mapping_kwarg_is_translated(self) -> None:
        """DeprecationWrapperInfo(identity_mapping=[...]) emits DeprecationWarning and sets identity_args_mapping."""
        with pytest.warns(DeprecationWarning, match="renamed to 'identity_args_mapping'"):
            info = DeprecationWrapperInfo(  # type: ignore[call-arg]
                function="f", deprecated_info=DeprecationConfig(), identity_mapping=["a"]
            )
        assert info.identity_args_mapping == ["a"]

    def test_both_old_kwargs_each_emit_deprecation_warning(self) -> None:
        """Passing both old kwargs emits one DeprecationWarning per renamed field."""
        with pytest.warns(DeprecationWarning, match="renamed") as caught:
            DeprecationWrapperInfo(  # type: ignore[call-arg]
                function="f", deprecated_info=DeprecationConfig(), empty_mapping=True, identity_mapping=["b"]
            )
        categories = [str(w.message) for w in caught.list if issubclass(w.category, DeprecationWarning)]
        assert any("empty_args_mapping" in m for m in categories)
        assert any("identity_args_mapping" in m for m in categories)

    def test_conflict_old_name_wins_when_both_supplied(self) -> None:
        """When both old and new names are supplied (as in replace()), old-name value is honoured."""
        with pytest.warns(DeprecationWarning, match="renamed to 'empty_args_mapping'"):
            info = DeprecationWrapperInfo(  # type: ignore[call-arg]
                function="f", deprecated_info=DeprecationConfig(), empty_mapping=True, empty_args_mapping=False
            )
        assert info.empty_args_mapping is True

    def test_replace_with_old_name_honoured_over_auto_injected_new(self) -> None:
        """dataclasses.replace() with old name honours caller intent over auto-injected new name.

        ``dataclasses.replace(info, empty_mapping=True)`` merges the caller's ``empty_mapping=True`` with the current
        ``empty_args_mapping=False`` (auto-injected by replace()).  The shim must detect this conflict, discard the
        auto-injected value, and honour the old-name value.

        """
        base = DeprecationWrapperInfo(function="f", deprecated_info=DeprecationConfig(), empty_args_mapping=False)

        with pytest.warns(DeprecationWarning, match="renamed to 'empty_args_mapping'"):
            result = dataclasses.replace(base, empty_mapping=True)  # type: ignore[call-arg]

        assert result.empty_args_mapping is True


class TestClassifyMemberApiType:
    """Tests for _classify_member_api_type — labels class-member audit rows."""

    def test_init_without_mapping_returns_class_constructor(self) -> None:
        """member_name='__init__' with has_mapping=False returns 'class constructor'."""
        assert _classify_member_api_type("__init__", None, False) == "class constructor"

    def test_init_with_mapping_returns_class_constructor_args(self) -> None:
        """member_name='__init__' with has_mapping=True returns 'class constructor args'."""
        assert _classify_member_api_type("__init__", None, True) == "class constructor args"


class _AudTargetCls:
    """Plain class used both as the wrapped object and target of a self-referential proxy (fixture)."""


@dataclasses.dataclass
class _AudPlainRecord:
    """Plain dataclass fed to the api-type classifier (fixture)."""

    value: int = 0


def _aud_plain_function() -> None:
    """Plain function fed to the api-type classifier (fixture)."""


def _aud_proxy(obj: object) -> _DeprecatedProxy:
    """Wrap *obj* in a bare deprecation proxy for the api-type classifier."""
    return _DeprecatedProxy(obj=obj, spec=_ProxySpec(name="aud_proxy", deprecated_in="1.0", remove_in="2.0"))


_AUD_MAPPED_INFO = DeprecationWrapperInfo(deprecated_info=DeprecationConfig(args_mapping={"old": "new"}))
_AUD_PLAIN_INFO = DeprecationWrapperInfo()


class TestClassifyWrapperApiTypeCharacterization:
    """Characterization of every branch of ``_classify_wrapper_api_type`` before it is restructured."""

    @pytest.mark.parametrize(
        ("wrapped_obj", "info", "member_name", "descriptor_kind", "expected"),
        [
            pytest.param(None, _AUD_PLAIN_INFO, "__init__", None, "class constructor", id="init"),
            pytest.param(None, _AUD_MAPPED_INFO, "__init__", None, "class constructor args", id="init-args"),
            pytest.param(None, _AUD_PLAIN_INFO, "__init__", "classmethod", "class constructor", id="init-wins"),
            pytest.param(None, _AUD_PLAIN_INFO, "build", "classmethod", "classmethod", id="classmethod"),
            pytest.param(None, _AUD_MAPPED_INFO, "build", "classmethod", "classmethod args", id="classmethod-args"),
            pytest.param(None, _AUD_PLAIN_INFO, "util", "staticmethod", "staticmethod", id="staticmethod"),
            pytest.param(None, _AUD_MAPPED_INFO, "util", "staticmethod", "staticmethod args", id="staticmethod-args"),
            pytest.param(None, _AUD_PLAIN_INFO, "run", None, "class method", id="method"),
            pytest.param(None, _AUD_MAPPED_INFO, "run", "property", "class method args", id="method-args"),
            pytest.param(types.ModuleType("aud_mod"), _AUD_PLAIN_INFO, None, None, "module", id="module"),
            pytest.param(_aud_proxy(_AudTargetCls), _AUD_PLAIN_INFO, None, None, "class", id="proxy-class"),
            pytest.param(_aud_proxy(_AudTargetCls), _AUD_MAPPED_INFO, None, None, "class", id="proxy-class-args"),
            pytest.param(
                _aud_proxy(_aud_proxy(_AudTargetCls)), _AUD_PLAIN_INFO, None, None, "class", id="proxy-nested-class"
            ),
            pytest.param(_aud_proxy(_AudPlainRecord), _AUD_PLAIN_INFO, None, None, "dataclass", id="proxy-dataclass"),
            pytest.param(
                _aud_proxy(_AudPlainRecord),
                _AUD_MAPPED_INFO,
                None,
                None,
                "dataclass attributes",
                id="proxy-dataclass-args",
            ),
            pytest.param(_aud_proxy({"key": 1}), _AUD_PLAIN_INFO, None, None, "data", id="proxy-data"),
            pytest.param(_AudTargetCls, _AUD_PLAIN_INFO, None, None, "class", id="class"),
            pytest.param(_AudPlainRecord, _AUD_PLAIN_INFO, None, None, "dataclass", id="dataclass"),
            pytest.param(_AudPlainRecord, _AUD_MAPPED_INFO, None, None, "dataclass attributes", id="dataclass-args"),
            pytest.param(_aud_plain_function, _AUD_PLAIN_INFO, None, None, "callable", id="callable"),
            pytest.param(_aud_plain_function, _AUD_MAPPED_INFO, None, None, "args", id="callable-args"),
        ],
    )
    def test_label_per_shape(
        self,
        wrapped_obj: object,
        info: DeprecationWrapperInfo,
        member_name: Optional[str],
        descriptor_kind: Optional[str],
        expected: str,
    ) -> None:
        """Every wrapper shape gets the report label it has today, as a plain string.

        The report's API Type column, the policy remedy text and the expiry subject noun all key on this label, so the
        restructuring of the classifier into lookup tables must keep each shape's label and keep handing a plain ``str``
        to callers that format it. A class-member label always wins over the wrapped object's own shape.
        """
        label = _classify_wrapper_api_type(wrapped_obj, info, member_name=member_name, descriptor_kind=descriptor_kind)
        assert label == expected


class TestApiTypeField:
    """``DeprecationWrapperInfo.api_type`` stays a plain ``str`` whatever the classifier hands over."""

    def test_member_stored_as_plain_string(self) -> None:
        """An ``_ApiType`` member passed to the field is stored as its plain string value.

        Callers format ``api_type`` into report text, JSON and their own messages; on Python 3.12+ a ``str`` mixin
        enum member formats as ``_ApiType.MODULE``, so the field must hold the label itself to render the same on
        every supported Python.
        """
        info = DeprecationWrapperInfo(api_type=_ApiType.MODULE)
        assert type(info.api_type) is str
        assert f"{info.api_type}" == "module"

    @pytest.mark.parametrize(
        "module",
        [
            pytest.param(core_wrappers, id="callables-classes-members"),
            pytest.param(old_math, id="deprecated-module"),
        ],
    )
    def test_scanned_rows_hold_plain_strings(self, module: types.ModuleType) -> None:
        """Every row a scan reports carries a plain ``str`` ``api_type``, not an enum member.

        The scan stores the classifier's label through ``dataclasses.replace`` and the module-row builder; a
        package mixing callables, classes, class members, instances and a deprecated module must still hand plain
        strings to every consumer of the public field.
        """
        rows = find_deprecation_wrappers(module, recursive=False)
        assert rows
        assert {type(row.api_type) for row in rows} == {str}


class _AudAttrsMappingTarget:
    """Fixture class with both old and new attribute names for the attrs_mapping self-reference test."""

    old_attr: int = 0
    new_attr: int = 0


class TestProxySelfReferenceDetection:
    """A proxy whose deprecated target is its own wrapped object is a self-reference."""

    def test_self_reference_detected_for_proxy(self) -> None:
        """``target is func.wrapped`` marks the proxy as self-referential even though ``target is func`` is False.

        The wrapper object is the proxy while the deprecated target is the wrapped class, so the plain identity
        check never matched and a no-op self-referential proxy was reported as effective.
        """
        proxy = _DeprecatedProxy(
            obj=_AudTargetCls,
            spec=_ProxySpec(target=_AudTargetCls, name="_AudTargetCls", deprecated_in="1.0", remove_in="2.0"),
        )
        info = validate_deprecation_wrapper(proxy)
        assert info.self_reference is True

    def test_effective_proxy_with_args_mapping_not_self_reference(self) -> None:
        """Same target as wrapped but non-empty ``args_mapping`` means the proxy is NOT a self-reference.

        A _DeprecatedProxy whose target matches its wrapped object but also carries an active
        ``args_mapping`` still performs meaningful argument remapping and must not be flagged as
        a no-op self-reference. The self-reference predicate narrows to the zero-remapping
        case only; a proxy with mapping is an effective wrapper even when target is func.wrapped.
        """
        proxy = _DeprecatedProxy(
            obj=_AudTargetCls,
            spec=_ProxySpec(
                target=_AudTargetCls,
                name="_AudTargetCls",
                deprecated_in="1.0",
                remove_in="2.0",
                args_mapping={"old_x": "new_x"},
            ),
        )
        info = validate_deprecation_wrapper(proxy)
        assert info.self_reference is False
        assert info.no_effect is False

    def test_effective_proxy_with_attrs_mapping_not_self_reference(self) -> None:
        """Same target as wrapped but non-empty ``attrs_mapping`` means the proxy is NOT a self-reference.

        A proxy carrying an active ``attrs_mapping`` remaps attribute access on the deprecated wrapper,
        so it performs meaningful work even when target is func.wrapped.  Both ``args_mapping`` and
        ``attrs_mapping`` independently disqualify the self-reference label.
        """
        proxy = _DeprecatedProxy(
            obj=_AudAttrsMappingTarget,
            spec=_ProxySpec(
                target=_AudAttrsMappingTarget,
                name="_AudAttrsMappingTarget",
                deprecated_in="1.0",
                remove_in="2.0",
                attrs_mapping={"old_attr": "new_attr"},
            ),
        )
        info = validate_deprecation_wrapper(proxy)
        assert info.self_reference is False
        assert info.no_effect is False
