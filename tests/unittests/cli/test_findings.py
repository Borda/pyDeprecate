"""Unit tests for :mod:`deprecate._cli.findings`: the level of each finding per subcommand and the exit code."""

import pytest

from deprecate._cli.findings import (
    _gate_reasons,
    _GateReason,
    _Level,
    _level,
    _merge_reasons,
    _verdict,
)
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


def _gate_code(
    findings: list[_Finding],
    subcommand: str,
    *,
    exit_zero: bool = False,
    strict: bool = False,
    gaps: int = 0,
    gates: tuple[str, ...] = (),
) -> int:
    """Return the exit code one path's gate gives *findings*, composed as ``_finish_gate`` composes it."""
    reasons = _gate_reasons(findings, subcommand, strict=strict, gaps=gaps, gates=gates)
    return _verdict(_merge_reasons([reasons]), exit_zero=exit_zero)


class TestExitCode:
    """The gate exits 1 when any evaluated finding is an error, unless ``exit_zero`` downgrades it."""

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
        assert _gate_code(findings, subcommand, exit_zero=exit_zero) == expected

    @pytest.mark.parametrize(
        ("rules", "strict", "gaps", "exit_zero", "expected"),
        [
            pytest.param((), True, 1, False, 1, id="strict-gap-fails"),
            pytest.param((), True, 0, False, 0, id="strict-no-gap-passes"),
            pytest.param((), False, 3, False, 0, id="gap-without-strict-passes"),
            pytest.param((_Rule.NO_EFFECT,), True, 2, False, 1, id="strict-gap-with-warning-fails"),
            pytest.param((_Rule.MISCONFIGURED_TARGET,), True, 0, False, 1, id="strict-error-without-gap-fails"),
            pytest.param((), True, 1, True, 0, id="strict-gap-exit-zero"),
        ],
    )
    def test_strict_counts_gaps(
        self, rules: tuple[_Rule, ...], strict: bool, gaps: int, exit_zero: bool, expected: int
    ) -> None:
        """In strict mode a recorded gap fails the run as an error finding would; without it gaps never count.

        A CI gate on a package whose submodule failed to import audited only part of it. Strict mode turns that partial
        audit into a failure while a clean run still passes, and the errors of a run fail it whether or not it is
        strict.

        """
        findings = [_finding(rule) for rule in rules]
        assert _gate_code(findings, "check", exit_zero=exit_zero, strict=strict, gaps=gaps) == expected

    def test_all_keeps_the_error_of_a_duplicated_chain(self) -> None:
        """``all`` sees a chain twice, from ``check`` and ``chains``; it stays an error.

        Both passes derive the same finding id. The gate needs only one error to fail, so duplicates cannot turn a
        failing chain into a pass.

        """
        findings = [_finding(_Rule.CHAIN, _Check.CHAINS), _finding(_Rule.CHAIN, _Check.CHECK)]
        assert _gate_reasons(findings, "all") == [_GateReason.FINDINGS]
        assert _gate_code(findings, "all") == 1


class TestGates:
    """``all --gate``: a listed check gates ``all`` as its own subcommand does, never more."""

    @pytest.mark.parametrize(
        ("rule", "gates", "expected"),
        [
            pytest.param(_Rule.MIN_GRACE, (), _Level.WARNING, id="policy-advisory"),
            pytest.param(_Rule.MESSAGE_REQUIRED, ("policy",), _Level.ERROR, id="policy-gated"),
            pytest.param(_Rule.IDENTITY_MAPPING, ("check",), _Level.WARNING, id="check-warning-stays-warning"),
            pytest.param(_Rule.CHAIN, (), _Level.ERROR, id="chain-always-gated"),
        ],
    )
    def test_level_in_all(self, rule: _Rule, gates: tuple[str, ...], expected: _Level) -> None:
        """A gated check's rules take the level of that check's own subcommand; warnings of ``check`` stay warnings.

        ``--gate=check`` names a check ``all`` already gates on, so it must change nothing — only ``policy`` moves.

        """
        assert _level(rule, "all", gates) is expected

    def test_gate_never_changes_other_subcommands(self) -> None:
        """``--gate`` exists only on ``all``; the level map of every other subcommand ignores it."""
        assert _level(_Rule.MIN_GRACE, "check", ("policy",)) is None

    def test_gated_policy_fails_all(self) -> None:
        """A policy violation fails ``all`` only when the run gates on ``policy``.

        The same finding passes an ungated run, so the gate list is the only switch between advisory and blocking.

        """
        findings = [_finding(_Rule.MESSAGE_REQUIRED, _Check.POLICY)]
        assert (_gate_code(findings, "all"), _gate_code(findings, "all", gates=("policy",))) == (0, 1)

    def test_merged_reasons_keep_report_order(self) -> None:
        """A run over several paths fails for every reason any path failed for, findings before gaps."""
        assert _merge_reasons([[_GateReason.GAPS], [], [_GateReason.FINDINGS]]) == [
            _GateReason.FINDINGS,
            _GateReason.GAPS,
        ]
