"""Unit tests for :mod:`deprecate._cli.report`: the plain-text finding sections and the ``--report-json`` file."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deprecate._cli.commands import cmd_check, cmd_expiry
from deprecate._cli.report import _GapKind, _print, _Reporter, _RunRecord, _ScanRecord, _write_report
from deprecate._types import DeprecationConfig
from deprecate.audit import ChainType, DeprecationWrapperInfo
from tests.unittests.cli._helpers import _EXPIRED_MSG

# ---------------------------------------------------------------------------
# Chain and expiry reporters
# ---------------------------------------------------------------------------


class TestReportChains:
    """Tests for the chain section printed by ``_Reporter.issues``."""

    @pytest.mark.parametrize(
        ("chain_type", "expected_label"),
        [
            pytest.param(ChainType.TARGET, "target", id="target-chain"),
            pytest.param(ChainType.STACKED, "stacked", id="stacked-chain"),
        ],
    )
    def test_chain_type_label_in_output(
        self, capsys: pytest.CaptureFixture[str], chain_type: ChainType, expected_label: str
    ) -> None:
        """The chain type label appears next to the wrapper it describes."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", chain_type=chain_type)]
        assert _Reporter.issues(results) is True
        assert f"\t- mod.fn: {expected_label}" in capsys.readouterr().out


class TestReportExpiry:
    """Tests for the expiry section printed by ``cmd_expiry``."""

    def test_expired_message_in_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The expired message is printed under the ``[ERROR]`` header of the expiry section."""
        with (
            patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[]),
            patch("deprecate._cli.commands._check_expiry_for_callables", return_value=[_EXPIRED_MSG]),
        ):
            cmd_expiry(path="some_module", version="2.0")
        out = capsys.readouterr().out
        assert "[ERROR] Found expired deprecated wrappers:" in out
        assert f"\t- {_EXPIRED_MSG}" in out


class TestReportPolicy:
    """Tests for _Reporter.policy() directly, isolated from cmd_policy's scan logic."""

    @pytest.mark.parametrize(
        ("advisory", "header"),
        [
            pytest.param(False, "[ERROR] Found deprecation policy violations:", id="gating"),
            pytest.param(True, "[WARNING] Found deprecation policy violations:", id="advisory"),
        ],
    )
    def test_rule_slug_prefix_survives_output(
        self, capsys: pytest.CaptureFixture[str], advisory: bool, header: str
    ) -> None:
        """The `[rule-slug]` prefix of a violation is printed literally under the section header.

        Calling `_Reporter.policy()` directly (rather than through `cmd_policy()`) isolates the reporter from the
        surrounding scan, so a regression here cannot hide behind mocked scan data used elsewhere in the test file.
        `all` reports violations it does not fail on, so the advisory section must say `[WARNING]`, not `[ERROR]`.

        """
        message = "[min-grace] Callable `pkg.old_fn` is deprecated in `1.0` and already scheduled for removal in `1.0`."
        _Reporter.policy([message], advisory=advisory)
        out = capsys.readouterr().out
        assert header in out
        assert f"\t- {message}" in out


class TestReportIssues:
    """Tests for ``_Reporter.issues``: whether it reports anything, and how each finding is printed."""

    @pytest.mark.parametrize(
        ("results", "expected"),
        [
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])], True, id="invalid-args"
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", identity_args_mapping=["a"])],
                True,
                id="identity-mapping",
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", empty_args_mapping=True, no_effect=True)],
                True,
                id="no-effect-empty-mapping",
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", self_reference=True, no_effect=True)],
                True,
                id="no-effect-self-reference",
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", identity_args_mapping=["a"], no_effect=True)],
                True,
                id="no-effect-identity-only",
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", chain_type=ChainType.TARGET)],
                True,
                id="chain-target",
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", chain_type=ChainType.STACKED)],
                True,
                id="chain-stacked",
            ),
            pytest.param([DeprecationWrapperInfo(module="mod", function="fn")], False, id="no-issues"),
        ],
    )
    def test_flag(self, results: list, expected: bool) -> None:
        """_Reporter.issues returns whether it found anything to report."""
        assert _Reporter.issues(results) is expected

    def test_partial_identity_with_self_reference(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Partial identity mappings are not reported as all-identity."""
        results = [
            DeprecationWrapperInfo(
                module="mod",
                function="fn",
                deprecated_info=DeprecationConfig(args_mapping={"a": "a", "b": "c"}),
                identity_args_mapping=["a"],
                self_reference=True,
                no_effect=True,
            )
        ]
        assert _Reporter.issues(results) is True
        captured = capsys.readouterr()
        assert "Self reference" in captured.out
        assert "All identity mappings" not in captured.out

    def test_square_brackets_in_detail_are_literal(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A detail such as an argument named ``[x]`` reaches the output unchanged.

        Output is plain text with no markup layer, so nothing in a module, function or argument name can be swallowed
        as styling.

        """
        results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["[x]"])]
        assert _Reporter.issues(results) is True
        out = capsys.readouterr().out
        assert "[ERROR] Found functions with invalid argument mappings:" in out
        assert "\t- mod.fn: [x]" in out


class TestWriteReport:
    """Tests for the ``--report-json`` file written by ``_write_report``."""

    @pytest.mark.parametrize("kind", [pytest.param(kind, id=kind.name) for kind in _GapKind])
    def test_every_gap_kind_is_written_with_a_hint(self, tmp_path: Path, kind: _GapKind) -> None:
        """Each kind of gap is written to the report with its value, its detail and a non-empty hint.

        A CI action quotes the report's hint next to every gap it annotates. A gap kind added to the CLI without a
        hint would either crash the run after the gate finished or leave an annotation with no advice, so every kind
        the CLI can record must come out of the file with usable text.
        """
        report_file = tmp_path / "report.json"
        _write_report(str(report_file), _RunRecord(gaps=[(kind, "some detail")], table="| a |"))
        gaps = json.loads(report_file.read_text(encoding="utf-8"))["gaps"]
        assert [(gap["kind"], gap["detail"]) for gap in gaps] == [(kind.value, "some detail")]
        assert gaps[0]["hint"].strip()

    def test_table_that_cannot_be_rendered_is_left_out(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A table that fails to render is reported on stderr and left out, while the gaps are still written.

        A gate subcommand recorded its scan but not a table, so the report renders one at the end. A scanned package
        whose rendering raises must neither fail the run nor cost the report its gaps.
        """
        report_file = tmp_path / "report.json"
        record = _RunRecord(
            gaps=[(_GapKind.PACKAGING_MISSING, "no packaging")],
            scan=_ScanRecord(path="some_module", wrappers=[], version="1.0"),
        )
        with patch("deprecate._cli.report.generate_deprecation_table", side_effect=RuntimeError("boom")):
            _write_report(str(report_file), record)
        report = json.loads(report_file.read_text(encoding="utf-8"))
        assert report["table"] is None
        assert [gap["kind"] for gap in report["gaps"]] == [_GapKind.PACKAGING_MISSING.value]
        assert "Could not render the deprecation table for the JSON report: boom" in capsys.readouterr().err


class TestPlainOutput:
    """Every message goes through built-in ``print`` to the stream it belongs on."""

    @pytest.mark.parametrize(("stderr", "stream"), [(False, "out"), (True, "err")])
    def test_print_routes_to_builtin_print(self, capsys: pytest.CaptureFixture[str], stderr: bool, stream: str) -> None:
        """``_print()`` writes the message unchanged to stdout, or to stderr when asked."""
        _print("hello", stderr=stderr)
        captured = capsys.readouterr()
        assert getattr(captured, stream) == "hello\n"

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_check_output_streams(self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """``cmd_check()`` prints scanning and no-results messages to stdout."""
        mock_find.return_value = []
        assert cmd_check(path="some_module") == 0
        captured = capsys.readouterr()
        assert "Scanning:" in captured.out
        assert "No deprecated callables found" in captured.out

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_nested_files_warning_stderr(
        self, mock_find: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The nested-files gap lands on stderr, apart from the scan output on stdout."""
        (tmp_path / "module_a.py").touch()
        subdir = tmp_path / "subpkg"
        subdir.mkdir()
        (subdir / "nested.py").touch()

        mock_find.return_value = []
        assert cmd_check(path=str(tmp_path)) == 0
        captured = capsys.readouterr()
        assert "Skipping nested Python files" in captured.err
