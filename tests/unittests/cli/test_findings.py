"""Unit tests for :mod:`deprecate._cli.findings`: the level of each finding per subcommand and the exit code."""

import pytest

from deprecate._cli.findings import _exit_code, _gate_levels, _Level, _level
from deprecate.audit import DeprecationWrapperInfo
from deprecate.audit._findings import _Check, _Finding, _make_finding, _Rule

_WRAPPER = DeprecationWrapperInfo(module="pkg", function="old_fn")


def _finding(rule: _Rule, check: _Check = _Check.CHECK) -> _Finding:
    """Build a finding of *rule* for the same wrapper."""
    return _make_finding(_WRAPPER, rule, check, f"{rule.value} finding")


class TestLevel:
    """The level map: the single source of what fails which subcommand."""

    @pytest.mark.parametrize(
        ("rule", "expected"),
        [
            pytest.param(_Rule.INVALID_ARGS, ("E", "-", "-", "-", "E"), id="invalid-args"),
            pytest.param(_Rule.MISCONFIGURED_TARGET, ("E", "-", "-", "-", "E"), id="misconfigured-target"),
            pytest.param(_Rule.INNER_ORDER_PROPERTY, ("E", "-", "-", "-", "E"), id="inner-order-property"),
            pytest.param(_Rule.IDENTITY_MAPPING, ("W", "-", "-", "-", "W"), id="identity-mapping"),
            pytest.param(_Rule.NO_EFFECT, ("W", "-", "-", "-", "W"), id="no-effect"),
            pytest.param(_Rule.POSITIONAL_ONLY_MAPPING, ("W", "-", "-", "-", "W"), id="positional-only-mapping"),
            pytest.param(_Rule.CHAIN, ("W", "-", "-", "E", "E"), id="chain"),
            pytest.param(_Rule.EXPIRED, ("-", "E", "-", "-", "E"), id="expired"),
            pytest.param(_Rule.MIN_GRACE, ("-", "-", "E", "-", "W"), id="min-grace"),
            pytest.param(_Rule.MESSAGE_REQUIRED, ("-", "-", "E", "-", "W"), id="message-required"),
        ],
    )
    def test_rule_row(self, rule: _Rule, expected: tuple[str, ...]) -> None:
        """Each rule is an error, a warning or not evaluated in ``check``, ``expiry``, ``policy``, ``chains``, ``all``.

        Misconfigured targets and inner-order properties became errors in ``check`` and ``all``, and policy
        violations stay warnings in ``all``; a row drifting from this table changes a CI gate silently.

        """
        short = {_Level.ERROR: "E", _Level.WARNING: "W", None: "-"}
        levels = tuple(short[_level(rule, sub)] for sub in ("check", "expiry", "policy", "chains", "all"))
        assert levels == expected


class TestExitCode:
    """``_exit_code`` is 1 when any evaluated finding is an error, unless ``exit_zero`` downgrades it."""

    @pytest.mark.parametrize(
        ("rules", "subcommand", "exit_zero", "expected"),
        [
            pytest.param((), "check", False, 0, id="no-findings"),
            pytest.param((_Rule.NO_EFFECT,), "check", False, 0, id="warning-only"),
            pytest.param((_Rule.MISCONFIGURED_TARGET,), "check", False, 1, id="error"),
            pytest.param((_Rule.MISCONFIGURED_TARGET,), "check", True, 0, id="error-exit-zero"),
            pytest.param((_Rule.EXPIRED,), "check", False, 0, id="not-evaluated"),
            pytest.param((_Rule.MESSAGE_REQUIRED,), "all", False, 0, id="policy-advisory-in-all"),
            pytest.param((_Rule.MESSAGE_REQUIRED,), "policy", False, 1, id="policy-gates-policy"),
        ],
    )
    def test_truth_table(self, rules: tuple[_Rule, ...], subcommand: str, exit_zero: bool, expected: int) -> None:
        """Findings × subcommand × ``exit_zero`` give the documented exit code."""
        findings = [_finding(rule) for rule in rules]
        assert _exit_code(findings, subcommand, exit_zero=exit_zero) == expected

    def test_all_keeps_the_error_of_a_duplicated_chain(self) -> None:
        """``all`` sees a chain twice, a warning from ``check`` and an error from ``chains``; it stays an error.

        Both passes derive the same finding id, which collapses into one entry at the higher level, so the order the
        passes ran in cannot turn a failing chain into a pass.

        """
        findings = [_finding(_Rule.CHAIN, _Check.CHAINS), _finding(_Rule.CHAIN, _Check.CHECK)]
        assert _gate_levels(findings, "all") == {"chain:pkg:old_fn": _Level.ERROR}
        assert _exit_code(findings, "all") == 1
