"""Unit tests for :mod:`deprecate.audit._findings`: the finding records the CLI gates on."""

import pytest

from deprecate._types import DeprecationConfig, TargetMode
from deprecate.audit import ChainType, DeprecationWrapperInfo, PolicyRule
from deprecate.audit._findings import _Check, _Rule, _wrapper_findings
from deprecate.audit._lifecycle import _check_expiry_for_callables, _expiry_findings
from deprecate.audit._policy import _build_policy_spec, _check_policy_for_callables, _policy_findings

_EXPIRED_WRAPPER = DeprecationWrapperInfo(
    module="pkg", function="old_fn", deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="1.0")
)


class TestWrapperFindings:
    """``_wrapper_findings`` turns the flags of one scanned wrapper into one finding per rule."""

    def test_one_finding_per_rule_with_details(self) -> None:
        """Three invalid argument names are one ``invalid-args`` finding with three details.

        One finding per ``(rule, module, symbol)`` keeps the finding id unique without line numbers, so a report or
        a later baseline can name the wrapper once whatever number of arguments it gets wrong.

        """
        info = DeprecationWrapperInfo(module="pkg", function="old_fn", invalid_args=["a", "b", "c"])
        findings = _wrapper_findings(info)
        assert [(finding.id, finding.details) for finding in findings] == [("invalid-args:pkg:old_fn", ("a", "b", "c"))]

    def test_rules_come_in_fixed_order(self) -> None:
        """A wrapper with every flag set yields the rules in report order, the chain one from the ``chains`` family."""
        info = DeprecationWrapperInfo(
            module="pkg",
            function="old_fn",
            deprecated_info=DeprecationConfig(target=TargetMode.NOTIFY, args_mapping={"a": "a"}),
            invalid_args=["x"],
            identity_args_mapping=["a"],
            no_effect=True,
            misconfigured_target=True,
            chain_type=ChainType.TARGET,
            args_mapping_positional_only=["a"],
            inner_order_property=True,
        )
        findings = _wrapper_findings(info)
        assert [finding.rule.value for finding in findings] == [
            "invalid-args",
            "misconfigured-target",
            "inner-order-property",
            "identity-mapping",
            "no-effect",
            "positional-only-mapping",
            "chain",
        ]
        assert {finding.check for finding in findings} == {_Check.CHECK, _Check.CHAINS}
        assert findings[-1].check is _Check.CHAINS

    @pytest.mark.parametrize(
        ("config", "case"),
        [
            pytest.param(
                DeprecationConfig(target=TargetMode.NOTIFY, args_mapping={"old_x": "x"}),
                "`TargetMode.NOTIFY` with `args_mapping`",
                id="notify-with-args-mapping",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.NOTIFY, args_extra={"bias": 1}),
                "`TargetMode.NOTIFY` with `args_extra`",
                id="notify-with-args-extra",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.ARGS_REMAP, args_mapping={}),
                "`TargetMode.ARGS_REMAP` with an empty `args_mapping`",
                id="args-remap-without-mapping",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.NOTIFY, misconfigured=True), "`target=False`", id="target-false"
            ),
        ],
    )
    def test_misconfigured_target_names_the_case(self, config: DeprecationConfig, case: str) -> None:
        """A misconfigured target names the contradiction, so the report says what to change.

        All three spellings of the misconfiguration (a mapping or extra arguments the notify-only mode ignores, a
        remap mode with nothing to remap, the invalid ``target=False`` sentinel) are errors in ``check``; the reader
        needs to know which one the wrapper has.

        """
        info = DeprecationWrapperInfo(
            module="pkg", function="old_fn", deprecated_info=config, misconfigured_target=True
        )
        (finding,) = _wrapper_findings(info)
        assert (finding.rule, finding.details) == (_Rule.MISCONFIGURED_TARGET, (case,))
        assert case in finding.message

    def test_getter_only_inner_order_property_is_a_finding(self) -> None:
        """An inner-order property is a finding even when it has no setter or deleter yet.

        ``@property @deprecated`` protects only the getter; a setter added later silently never warns, so the shape
        is flagged before that happens.

        """
        info = DeprecationWrapperInfo(module="pkg", function="Cls.value", inner_order_property=True)
        (finding,) = _wrapper_findings(info)
        assert finding.rule is _Rule.INNER_ORDER_PROPERTY

    def test_clean_wrapper_has_no_finding(self) -> None:
        """A wrapper with no flag set yields nothing."""
        assert _wrapper_findings(DeprecationWrapperInfo(module="pkg", function="old_fn")) == []


class TestCheckFindings:
    """The expiry and policy builders carry exactly the messages the Python API returns."""

    def test_expiry_messages_match_the_api(self) -> None:
        """Each ``expired`` finding's message is the string ``validate_deprecation_expiry`` reports.

        The CLI prints and gates on findings while the API returns strings; both must say the same thing.

        """
        findings = _expiry_findings([_EXPIRED_WRAPPER], "2.0")
        assert [finding.rule for finding in findings] == [_Rule.EXPIRED]
        assert [finding.message for finding in findings] == _check_expiry_for_callables([_EXPIRED_WRAPPER], "2.0")
        assert (findings[0].deprecated_in, findings[0].remove_in) == ("1.0", "1.0")

    def test_policy_findings_carry_their_rule(self) -> None:
        """A wrapper breaking both policy rules gives one finding per rule, each with the API's message."""
        spec = _build_policy_spec("0.3", True)
        findings = _policy_findings([_EXPIRED_WRAPPER], spec)
        assert [finding.rule for finding in findings] == [_Rule.MIN_GRACE, _Rule.MESSAGE_REQUIRED]
        assert [finding.message for finding in findings] == _check_policy_for_callables([_EXPIRED_WRAPPER], spec)

    @pytest.mark.parametrize("rule", list(PolicyRule))
    def test_policy_rule_slugs_match(self, rule: PolicyRule) -> None:
        """Each finding rule of a policy check has the slug of the public ``PolicyRule`` it reports."""
        assert _Rule(rule.value).value == rule.value
