"""Test the audit module functions."""

import importlib
import importlib.metadata
import importlib.util
import inspect
import pkgutil
import warnings
from typing import Any
from unittest.mock import patch

import pytest

import tests
import tests.collection_chains as chain_module
import tests.collection_deprecate as proxy_module
import tests.collection_misconfigured as sample_module
from deprecate import deprecated, validate_deprecation_expiry
from deprecate._types import DeprecationConfig
from deprecate.audit import (
    ChainType,
    DeprecationWrapperInfo,
    _check_deprecated_wrapper_expiry,
    _get_package_version,
    _parse_version,
    find_deprecation_wrappers,
    validate_deprecation_chains,
    validate_deprecation_wrapper,
)
from tests.collection_targets import plain_function_target

# Check if packaging is available for version comparison tests
_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None
_requires_packaging = pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging library")


class TestValidateDeprecatedWrapper:
    """Tests for validate_deprecation_wrapper()."""

    def test_valid_deprecation(self) -> None:
        """Properly configured deprecated function returns a clean validation result."""
        result = validate_deprecation_wrapper(proxy_module.decorated_pow_self)
        assert isinstance(result, DeprecationWrapperInfo)
        assert result.invalid_args == []
        assert result.empty_mapping is False
        assert result.identity_mapping == []
        assert result.self_reference is False
        assert result.no_effect is False

    def test_invalid_args(self) -> None:
        """args_mapping keys absent from the function signature are reported."""
        result = validate_deprecation_wrapper(sample_module.invalid_args_deprecation)
        assert result.invalid_args == ["nonexistent_arg"]
        assert result.empty_mapping is False
        assert result.identity_mapping == []
        assert result.self_reference is False

    @pytest.mark.parametrize("func_name", ["empty_mapping_deprecation", "none_mapping_deprecation"])
    def test_empty_mapping(self, func_name: str) -> None:
        """Empty or None args_mapping on a self-deprecation yields no_effect=True."""
        result = validate_deprecation_wrapper(getattr(sample_module, func_name))
        assert result.invalid_args == []
        assert result.empty_mapping is True
        assert result.identity_mapping == []
        assert result.self_reference is False
        assert result.no_effect is True

    def test_single_identity_mapping(self) -> None:
        """A single key==value entry in args_mapping is detected as identity."""
        result = validate_deprecation_wrapper(sample_module.identity_mapping_deprecation)
        assert result.invalid_args == []
        assert result.empty_mapping is False
        assert result.identity_mapping == ["arg1"]
        assert result.no_effect is True

    def test_all_identity_mappings(self) -> None:
        """All key==value entries in args_mapping yield no_effect=True."""
        result = validate_deprecation_wrapper(sample_module.all_identity_mapping_deprecation)
        assert result.identity_mapping == ["arg1", "arg2"]
        assert result.no_effect is True

    def test_partial_identity_mapping(self) -> None:
        """Partially identity args_mapping still has effect via non-identity entries."""
        result = validate_deprecation_wrapper(sample_module.partial_identity_mapping_deprecation)
        assert result.identity_mapping == ["arg1"]
        assert result.no_effect is False

    def test_self_reference(self) -> None:
        """Target pointing to the same function yields self_reference=True and no_effect=True."""
        result = validate_deprecation_wrapper(sample_module.self_referencing_deprecation)
        assert result.invalid_args == []
        assert result.empty_mapping is False
        assert result.identity_mapping == []
        assert result.self_reference is True
        assert result.no_effect is True

    def test_different_target(self) -> None:
        """Forwarding to a different function has effect: no_effect=False."""
        result = validate_deprecation_wrapper(proxy_module.depr_accuracy_target)
        assert result.invalid_args == []
        assert result.empty_mapping is False
        assert result.identity_mapping == []
        assert result.self_reference is False
        assert result.no_effect is False

    def test_proxy_function_name_is_source_not_target(self) -> None:
        """validate_deprecation_wrapper reports the deprecated source name, not the target's name.

        Proxy objects route ``__name__`` through ``__getattr__`` → target class, so
        ``getattr(proxy, "__name__")`` returns the *target's* name.  The audit function
        must read ``dep_info.name`` (always set by the proxy at decoration time) to avoid
        reporting the wrong callable name.
        """
        result = validate_deprecation_wrapper(proxy_module.DeprecatedColorEnum)
        assert result.function == "DeprecatedColorEnum"
        # Confirm the proxy leaks the wrong __name__ via __getattr__:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert proxy_module.DeprecatedColorEnum.__name__ == "TargetColorEnum"
        assert result.function != result.deprecated_info.target.__name__

    def test_no_deprecated_attr(self) -> None:
        """Non-decorated callable raises ValueError."""

        def plain_function(x: int) -> int:
            return x

        with pytest.raises(ValueError, match="missing or invalid `__deprecated__` metadata"):
            validate_deprecation_wrapper(plain_function)


class TestMisconfiguredTarget:
    """Tests for the misconfigured_target field in validate_deprecation_wrapper()."""

    @pytest.mark.parametrize(
        "func_name",
        [
            "target_false_deprecation",
            "whole_with_mapping_deprecation",
            "args_only_no_mapping_deprecation",
        ],
    )
    def test_misconfigured_target_true(self, func_name: str) -> None:
        """Invalid target configurations are flagged as misconfigured_target=True."""
        result = validate_deprecation_wrapper(getattr(sample_module, func_name))
        assert result.misconfigured_target is True

    @pytest.mark.parametrize(
        "func_name",
        [
            "whole_clean_deprecation",
            "args_only_clean_deprecation",
        ],
    )
    def test_misconfigured_target_false_for_valid_configs(self, func_name: str) -> None:
        """Correctly configured TargetMode wrappers have misconfigured_target=False."""
        result = validate_deprecation_wrapper(getattr(sample_module, func_name))
        assert result.misconfigured_target is False

    def test_valid_wrapper_also_not_misconfigured(self) -> None:
        """Callable-target wrapper is not flagged as misconfigured."""
        result = validate_deprecation_wrapper(proxy_module.decorated_pow_self)
        assert result.misconfigured_target is False


class TestValidateDeprecatedWrapperCallableProxy:
    """validate_deprecation_wrapper with deprecated_class and deprecated_instance proxies."""

    @pytest.mark.parametrize(
        ("proxy_obj", "fn_name", "target_name"),
        [
            (proxy_module.DeprecatedColorEnum, "DeprecatedColorEnum", "TargetColorEnum"),
            (proxy_module.DeprecatedColorDataClass, "DeprecatedColorDataClass", "NewDataClass"),
        ],
    )
    def test_deprecated_class_with_target(self, proxy_obj: Any, fn_name: str, target_name: str) -> None:  # noqa: ANN401
        """deprecated_class proxy with a forwarding target reports correct metadata."""
        result = validate_deprecation_wrapper(proxy_obj)
        assert result.function == fn_name
        assert result.deprecated_info.deprecated_in == "1.0"
        assert result.deprecated_info.remove_in == "2.0"
        assert result.deprecated_info.target.__name__ == target_name
        assert result.deprecated_info.args_mapping is None
        assert result.empty_mapping is True  # no args_mapping
        assert result.self_reference is False
        assert result.no_effect is False  # has a different target → effective
        assert result.chain_type is None

    @pytest.mark.parametrize(
        ("proxy_obj", "fn_name", "expected_mapping", "target_name"),
        [
            (proxy_module.MappedColorEnum, "MappedColorEnum", {"val": "value"}, "TargetColorEnum"),
            (
                proxy_module.MappedDataClass,
                "MappedDataClass",
                {"name": "label", "count": "total"},
                "NewDataClass",
            ),
            (
                proxy_module.MappedDropArgDataClass,
                "MappedDropArgDataClass",
                {"legacy_flag": None, "name": "label"},
                "NewDataClass",
            ),
        ],
    )
    def test_deprecated_class_with_args_mapping(
        self,
        proxy_obj: Any,  # noqa: ANN401
        fn_name: str,
        expected_mapping: dict,
        target_name: str,
    ) -> None:
        """deprecated_class proxy with args_mapping reports mapping with no identity entries."""
        result = validate_deprecation_wrapper(proxy_obj)
        assert result.function == fn_name
        assert result.deprecated_info.args_mapping == expected_mapping
        assert result.deprecated_info.target.__name__ == target_name
        assert result.empty_mapping is False
        assert result.identity_mapping == []
        assert result.no_effect is False

    def test_deprecated_class_warn_only(self) -> None:
        """deprecated_class with no target is still effective — it emits warnings."""
        result = validate_deprecation_wrapper(proxy_module.WarnOnlyColorEnum)
        assert result.function == "WarnOnlyColorEnum"
        assert result.deprecated_info.target is None
        assert result.empty_mapping is True
        assert result.no_effect is False  # target=None still warns

    def test_deprecated_instance_uses_type_name(self) -> None:
        """deprecated_instance with no explicit name defaults function to the wrapped type name."""
        result = validate_deprecation_wrapper(proxy_module.depr_config_dict)
        # No name= passed → resolved to type({}).__name__ = "dict"
        assert result.function == "dict"
        assert result.deprecated_info.deprecated_in == "1.0"
        assert result.deprecated_info.remove_in == "2.0"
        assert result.deprecated_info.target is None
        assert result.empty_mapping is True
        assert result.no_effect is False  # still emits warnings

    def test_deprecated_instance_read_only_same_audit_result(self) -> None:
        """read_only flag is a runtime proxy concern and does not appear in DeprecationConfig."""
        read_only = validate_deprecation_wrapper(proxy_module.depr_config_dict_read_only)
        read_write = validate_deprecation_wrapper(proxy_module.depr_config_dict)
        assert read_only.function == read_write.function == "dict"
        assert read_only.deprecated_info.deprecated_in == read_write.deprecated_info.deprecated_in
        assert read_only.deprecated_info.remove_in == read_write.deprecated_info.remove_in
        assert read_only.empty_mapping == read_write.empty_mapping
        assert read_only.no_effect == read_write.no_effect

    @pytest.mark.parametrize(
        ("proxy_obj", "fn_name", "has_target", "has_mapping", "invalid_args"),
        [
            # deprecated_class — no args_mapping
            (proxy_module.DeprecatedColorEnum, "DeprecatedColorEnum", True, False, []),
            (proxy_module.DeprecatedColorDataClass, "DeprecatedColorDataClass", True, False, []),
            (proxy_module.WarnOnlyColorEnum, "WarnOnlyColorEnum", False, False, []),
            # deprecated_class — with args_mapping: signature check is skipped for proxies → invalid_args == []
            (proxy_module.MappedColorEnum, "MappedColorEnum", True, True, []),
            (proxy_module.MappedDataClass, "MappedDataClass", True, True, []),
            (proxy_module.MappedDropArgDataClass, "MappedDropArgDataClass", True, True, []),
            # deprecated_instance — no args_mapping
            (proxy_module.depr_config_dict, "dict", False, False, []),
            (proxy_module.depr_config_dict_read_only, "dict", False, False, []),
        ],
    )
    def test_all_proxy_types_pass_basic_validation(
        self,
        proxy_obj: Any,  # noqa: ANN401
        fn_name: str,
        has_target: bool,
        has_mapping: bool,
        invalid_args: list,
    ) -> None:
        """All 8 proxy objects (6 deprecated_class + 2 deprecated_instance) pass validate_deprecation_wrapper.

        Verifies the unified DeprecationConfig schema — function name, version fields, and
        chain_type — for every proxy variant. Proxy __call__ is (*args, **kwargs) so the
        signature check is skipped and invalid_args is always [] for proxy objects.
        """
        result = validate_deprecation_wrapper(proxy_obj)
        assert result.function == fn_name
        assert result.deprecated_info.deprecated_in == "1.0"
        assert result.deprecated_info.remove_in == "2.0"
        assert result.invalid_args == invalid_args
        assert result.chain_type is None
        assert (result.deprecated_info.target is not None) == has_target
        assert (result.deprecated_info.args_mapping is not None) == has_mapping


class TestFindDeprecatedWrappers:
    """Tests for find_deprecation_wrappers()."""

    def test_finds_decorated_functions(self) -> None:
        """Scans a module and returns DeprecationWrapperInfo for every @deprecated function."""
        results = find_deprecation_wrappers(sample_module, recursive=False)
        assert len(results) > 0
        assert all(isinstance(r, DeprecationWrapperInfo) for r in results)
        func_names = [r.function for r in results]
        assert "invalid_args_deprecation" in func_names
        assert "empty_mapping_deprecation" in func_names
        assert "identity_mapping_deprecation" in func_names

    def test_detects_no_effect_wrappers(self) -> None:
        """Identifies zero-impact wrappers via empty_mapping and identity_mapping fields."""
        by_name = {r.function: r for r in find_deprecation_wrappers(sample_module, recursive=False)}
        assert by_name["empty_mapping_deprecation"].empty_mapping is True
        assert "arg1" in by_name["identity_mapping_deprecation"].identity_mapping

    def test_accepts_string_module_path(self) -> None:
        """String module paths are accepted in addition to module objects."""
        results = find_deprecation_wrappers("tests.collection_deprecate", recursive=False)
        assert len(results) > 0
        assert all(isinstance(r, DeprecationWrapperInfo) for r in results)

    def test_results_groupable_by_issue_type(self) -> None:
        """Results can be filtered by invalid_args, empty_mapping, and identity_mapping."""
        results = find_deprecation_wrappers(sample_module, recursive=False)
        assert len([r for r in results if r.invalid_args]) > 0
        assert len([r for r in results if r.empty_mapping]) > 0

    def test_discovers_proxy_based_deprecations(self) -> None:
        """Proxy-based deprecations are discoverable with correct names and metadata."""
        by_name = {r.function: r for r in find_deprecation_wrappers(proxy_module, recursive=False)}
        assert "depr_config_dict" in by_name
        assert "DeprecatedColorEnum" in by_name
        assert by_name["DeprecatedColorEnum"].deprecated_info.target.__name__ == "TargetColorEnum"
        assert by_name["MappedColorEnum"].deprecated_info.args_mapping == {"val": "value"}
        # deprecated_class on dataclass
        assert by_name["DeprecatedColorDataClass"].deprecated_info.target.__name__ == "NewDataClass"
        assert by_name["MappedDataClass"].deprecated_info.args_mapping == {"name": "label", "count": "total"}

    def test_discovers_proxy_without_target_and_drop_mapping(self) -> None:
        """Proxy deprecations without targets and with dropped args are discoverable."""
        by_name = {r.function: r for r in find_deprecation_wrappers(proxy_module, recursive=False)}
        assert by_name["WarnOnlyColorEnum"].deprecated_info.target is None
        assert by_name["WarnOnlyColorEnum"].deprecated_info.remove_in == "2.0"
        assert by_name["MappedDropArgDataClass"].deprecated_info.args_mapping == {"legacy_flag": None, "name": "label"}
        assert "depr_config_dict_read_only" in by_name

    def test_instance_proxy_function_name_comes_from_variable_not_type(self) -> None:
        """find_deprecation_wrappers reports the module variable name, not dep_info.name.

        ``deprecated_instance`` stores the wrapped type name (``"dict"``) in ``dep_info.name``,
        but the scanner overrides ``function`` with the attribute name from ``inspect.getmembers``.
        Calling ``validate_deprecation_wrapper`` directly uses ``dep_info.name`` instead.
        """
        # Direct validation: uses dep_info.name → "dict" (wrapped type)
        direct = validate_deprecation_wrapper(proxy_module.depr_config_dict)
        assert direct.function == "dict"
        # Module scan: overrides function with the attribute name
        by_name = {r.function: r for r in find_deprecation_wrappers(proxy_module, recursive=False)}
        assert "depr_config_dict" in by_name
        assert by_name["depr_config_dict"].deprecated_info.name == "dict"

    def test_scan_handles_uninspectable_module(self) -> None:
        """Scan skips a module whose member inspection raises rather than crashing."""
        with patch.object(inspect, "getattr_static", side_effect=TypeError("bad module")):
            results = find_deprecation_wrappers(proxy_module, recursive=False)
        assert results == []

    def test_recursive_scan_handles_walk_packages_error(self) -> None:
        """Recursive scan continues gracefully when pkgutil.walk_packages raises."""
        # `tests` has __path__ so the recursive branch is entered.
        with patch.object(pkgutil, "walk_packages", side_effect=OSError("no walk")):
            results = find_deprecation_wrappers(tests, recursive=True)
        assert isinstance(results, list)


class TestValidateDeprecationChains:
    """Tests for validate_deprecation_chains()."""

    @pytest.fixture(scope="class")
    def chain_issues(self) -> list[DeprecationWrapperInfo]:
        """Run validate_deprecation_chains once for the class and share the result."""
        return validate_deprecation_chains(chain_module, recursive=False)

    @pytest.mark.parametrize(
        ("fn_name", "expected_chain_type"),
        [
            ("caller_sum_via_depr_sum", ChainType.TARGET),
            ("caller_acc_via_depr_map", ChainType.TARGET),
        ],
    )
    def test_detects_target_chain(self, chain_issues: list, fn_name: str, expected_chain_type: ChainType) -> None:
        """Deprecated wrapper whose target is itself deprecated is flagged as TARGET chain."""
        by_name = {i.function: i for i in chain_issues}
        assert fn_name in by_name
        assert by_name[fn_name].chain_type is expected_chain_type

    def test_detects_chain_with_composed_arg_mappings(self, chain_issues: list) -> None:
        """Chain wrapper that also carries its own args_mapping exposes both facts."""
        by_name = {i.function: i for i in chain_issues}
        info = by_name["caller_acc_comp_depr_map"]
        assert info.chain_type is ChainType.TARGET
        assert info.deprecated_info.args_mapping == {"predictions": "preds", "labels": "truth"}

    @pytest.mark.parametrize(
        "fn_pattern",
        [
            "caller_stacked_args_map",
            "caller_pow_via_self_depr",
            "caller_stacked_args_enum_enum",
            "caller_stacked_args_legacy_enum",
            "caller_stacked_args_enum_legacy",
        ],
    )
    def test_detects_stacked_chain(self, chain_issues: list, fn_pattern: str) -> None:
        """Stacked arg-mapping decorators or self-deprecation targets are flagged as STACKED."""
        matched = [i for i in chain_issues if fn_pattern in i.function]
        assert len(matched) > 0
        assert matched[0].chain_type is ChainType.STACKED

    def test_no_warning_for_clean_target(self, chain_issues: list) -> None:
        """Wrapper pointing directly to a non-deprecated target is not reported."""
        assert all("caller_sum_direct" not in i.function for i in chain_issues)

    def test_returns_list_of_infos(self, chain_issues: list) -> None:
        """Return value is a non-empty list of DeprecationWrapperInfo with chain_type set."""
        assert isinstance(chain_issues, list)
        assert len(chain_issues) > 0
        assert all(isinstance(i, DeprecationWrapperInfo) and i.chain_type is not None for i in chain_issues)

    def test_detects_proxy_to_proxy_chain(self) -> None:
        """deprecated_class proxy whose target is itself a proxy is flagged as TARGET chain."""
        result = validate_deprecation_wrapper(proxy_module.ChainedProxyColorEnum)
        assert result.chain_type is ChainType.TARGET

    def test_detects_function_to_proxy_chain(self) -> None:
        """@deprecated function whose target is a proxy is flagged as TARGET chain."""
        result = validate_deprecation_wrapper(proxy_module.depr_func_targeting_proxy)
        assert result.chain_type is ChainType.TARGET

    def test_chains_scan_includes_proxies(self) -> None:
        """validate_deprecation_chains finds proxy-based chains when scanning a module."""
        chains = validate_deprecation_chains(proxy_module, recursive=False)
        by_name = {i.function: i for i in chains}
        assert "ChainedProxyColorEnum" in by_name
        assert "depr_func_targeting_proxy" in by_name
        assert by_name["ChainedProxyColorEnum"].chain_type is ChainType.TARGET
        assert by_name["depr_func_targeting_proxy"].chain_type is ChainType.TARGET


@_requires_packaging
class TestCheckDeprecationExpiry:
    """Tests for _check_deprecated_wrapper_expiry()."""

    @pytest.mark.parametrize(
        ("callable_", "current_version"),
        [
            (proxy_module.decorated_pow_self, "0.4"),  # @deprecated, remove_in="0.5", before deadline
            (proxy_module.decorated_sum, "0.4.9"),  # @deprecated, pre-release
            (proxy_module.decorated_sum, "0.5.0a1"),  # @deprecated, alpha before stable deadline
            (proxy_module.DeprecatedColorEnum, "1.9"),  # deprecated_class, with target
            (proxy_module.WarnOnlyColorEnum, "1.9"),  # deprecated_class, no target
            (proxy_module.MappedColorEnum, "1.9"),  # deprecated_class, with args_mapping
            (proxy_module.depr_config_dict, "1.9"),  # deprecated_instance
            (proxy_module.depr_config_dict_read_only, "1.9"),  # deprecated_instance, read_only
        ],
    )
    def test_not_expired_before_deadline(self, callable_: Any, current_version: str) -> None:  # noqa: ANN401
        """Callable before its remove_in deadline passes silently."""
        _check_deprecated_wrapper_expiry(callable_, current_version)

    @pytest.mark.parametrize(
        ("callable_", "current_version", "remove_in"),
        [
            (proxy_module.decorated_pow_self, "0.5", "0.5"),  # @deprecated, at deadline
            (proxy_module.decorated_pow_self, "0.6", "0.5"),  # @deprecated, past deadline
            (proxy_module.decorated_sum, "0.5.0", "0.5"),  # @deprecated, at deadline (patch)
            (proxy_module.decorated_sum, "0.5.1", "0.5"),  # @deprecated, past deadline (patch)
            (proxy_module.DeprecatedColorEnum, "2.0", "2.0"),  # deprecated_class, with target
            (proxy_module.WarnOnlyColorEnum, "2.0", "2.0"),  # deprecated_class, no target
            (proxy_module.MappedColorEnum, "2.0", "2.0"),  # deprecated_class, with args_mapping
            (proxy_module.depr_config_dict, "2.0", "2.0"),  # deprecated_instance
            (proxy_module.depr_config_dict_read_only, "2.0", "2.0"),  # deprecated_instance, read_only
        ],
    )
    def test_raises_at_or_after_deadline(self, callable_: Any, current_version: str, remove_in: str) -> None:  # noqa: ANN401
        """Callable at or past its remove_in deadline raises AssertionError."""
        with pytest.raises(
            AssertionError,
            match=rf"scheduled for removal in version {remove_in}.*still exists in version {current_version}",
        ):
            _check_deprecated_wrapper_expiry(callable_, current_version)

    def test_error_message_content(self) -> None:
        """Error message includes callable name, both versions, and actionable guidance."""
        with pytest.raises(AssertionError) as exc_info:
            _check_deprecated_wrapper_expiry(proxy_module.decorated_pow_self, "1.0")  # remove_in="0.5"
        msg = str(exc_info.value)
        assert "decorated_pow_self" in msg
        assert "0.5" in msg
        assert "1.0" in msg
        assert "scheduled for removal" in msg
        assert "Please delete this deprecated code" in msg

    def test_non_deprecated_callable_raises_value_error(self) -> None:
        """Non-decorated callable raises ValueError immediately."""
        with pytest.raises(ValueError, match="missing or invalid `__deprecated__` metadata"):
            _check_deprecated_wrapper_expiry(plain_function_target, "1.0")

    def test_no_remove_in_raises_value_error(self) -> None:
        """Deprecated callable without remove_in raises ValueError."""
        with pytest.raises(ValueError, match="does not have a 'remove_in' version specified"):
            _check_deprecated_wrapper_expiry(proxy_module.depr_func_no_remove_in, "2.0")

    def test_invalid_current_version_raises_value_error(self) -> None:
        """Malformed current_version raises ValueError with clear message."""
        with pytest.raises(ValueError, match="Invalid current_version"):
            _check_deprecated_wrapper_expiry(proxy_module.decorated_pow_self, "invalid-version")

    def test_invalid_remove_in_raises_value_error(self) -> None:
        """Callable with a malformed remove_in string raises ValueError naming the callable."""

        @deprecated(target=None, deprecated_in="1.0", remove_in="not-semver")
        def _bad_version_fn() -> None:
            pass

        with pytest.raises(ValueError, match="Invalid remove_in.*_bad_version_fn"):
            _check_deprecated_wrapper_expiry(_bad_version_fn, "1.0")

    def test_version_stage_ordering(self) -> None:
        """Pre-release stages order correctly per PEP 440: alpha < beta < rc < stable < post."""
        versions = ["1.5.0a1", "1.5.0b1", "1.5.0rc1", "1.5.0", "1.5.0.post1"]
        parsed = [_parse_version(v) for v in versions]
        assert parsed == sorted(parsed)


@_requires_packaging
class TestCheckModuleDeprecationExpiry:
    """Tests for validate_deprecation_expiry()."""

    def test_no_expired_before_deadline(self) -> None:
        """Module scan before any removal deadlines returns an empty list."""
        assert validate_deprecation_expiry("tests.collection_deprecate", "0.1", recursive=False) == []

    def test_expired_callables_detected(self) -> None:
        """Scan at or past a removal deadline returns error messages for expired callables."""
        expired = validate_deprecation_expiry("tests.collection_deprecate", "2.0", recursive=False)
        assert len(expired) > 0
        assert all(isinstance(msg, str) for msg in expired)
        assert all("scheduled for removal" in msg and "still exists" in msg for msg in expired)

    def test_accepts_module_object(self) -> None:
        """Module objects are accepted alongside string paths."""
        assert validate_deprecation_expiry(proxy_module, "0.1", recursive=False) == []
        assert len(validate_deprecation_expiry(proxy_module, "1.0", recursive=False)) > 0

    def test_recursive_scans_submodules(self) -> None:
        """Recursive mode finds expired callables across the full package tree."""
        expired = validate_deprecation_expiry("tests", "10.0", recursive=True)
        assert len(expired) > 0

    def test_skips_callables_without_remove_in(self) -> None:
        """Warning-only deprecations without remove_in are silently skipped."""
        expired = validate_deprecation_expiry("tests.collection_deprecate", "100.0", recursive=False)
        assert isinstance(expired, list)

    def test_invalid_current_version_raises_before_scan(self) -> None:
        """Malformed current_version raises ValueError before any callable is checked."""
        with pytest.raises(ValueError, match="Invalid current_version"):
            validate_deprecation_expiry("tests.collection_deprecate", "invalid", recursive=False)

    def test_auto_detect_version_fails_for_non_package(self) -> None:
        """Auto-detection raises ImportError when the package has no version metadata."""
        with pytest.raises(ImportError, match="Could not determine version"):
            validate_deprecation_expiry("tests.collection_deprecate", None, recursive=False)

    def test_auto_detect_requires_module_name(self) -> None:
        """Auto-detection raises ValueError when the module object has no __name__."""
        with pytest.raises(ValueError, match="Cannot auto-detect version.*__name__"):
            validate_deprecation_expiry(object(), None, recursive=False)

    def test_version_fallback_to_dunder_version(self) -> None:
        """_get_package_version falls back to module.__version__ when importlib.metadata fails."""
        with patch.object(importlib.metadata, "version", side_effect=importlib.metadata.PackageNotFoundError):
            version = _get_package_version("deprecate")
        assert isinstance(version, str)
        assert len(version) > 0

    def test_proxy_objects_included_in_scan(self) -> None:
        """deprecated_class and deprecated_instance proxies appear in the expired list."""
        expired = validate_deprecation_expiry("tests.collection_deprecate", "3.0", recursive=False)
        expired_names = " ".join(expired)
        assert "DeprecatedColorEnum" in expired_names
        assert "DeprecatedColorDataClass" in expired_names
        assert "WarnOnlyColorEnum" in expired_names
        assert "depr_config_dict" in expired_names

    def test_silently_skips_invalid_remove_in_version(self) -> None:
        """Callables with a malformed remove_in are silently skipped, not raised."""
        bad_info = DeprecationWrapperInfo(
            module="tests.collection_deprecate",
            function="bad_version_fn",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="not-semver"),
        )
        with patch("deprecate.audit.find_deprecation_wrappers", return_value=[bad_info]):
            result = validate_deprecation_expiry("tests.collection_deprecate", "2.0", recursive=False)
        assert result == []

    def test_gracefully_skips_import_errors(self) -> None:
        """Recursive scan continues gracefully when submodule imports raise ImportError.

        The ``tests`` package object is passed directly to skip the string→module
        resolution step, ensuring only the recursive per-submodule imports are patched.
        Those are wrapped in ``contextlib.suppress(ImportError)``, so the scan must
        not raise and must return a plain list.
        """
        with patch.object(importlib, "import_module", side_effect=ImportError("bad submodule")):
            result = validate_deprecation_expiry(tests, "2.0", recursive=True)
        assert isinstance(result, list)

    def test_return_format(self) -> None:
        """Returns a list of string error messages; each message names the callable and version."""
        expired = validate_deprecation_expiry("tests.collection_deprecate", "2.0", recursive=False)
        assert all(isinstance(msg, str) for msg in expired)
        for msg in expired:
            assert "Callable" in msg or "scheduled" in msg
