"""Unit tests for :mod:`deprecate._cli.report`: the rich and plain-text output dispatch."""

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
    """Tests for _report_chains_rich and _report_chains_plain via _report_issues."""

    @pytest.mark.parametrize(
        "has_rich",
        [pytest.param(True, id="rich"), pytest.param(False, id="plain")],
    )
    @pytest.mark.parametrize(
        ("chain_type", "expected_label"),
        [
            pytest.param(ChainType.TARGET, "target", id="target-chain"),
            pytest.param(ChainType.STACKED, "stacked", id="stacked-chain"),
        ],
    )
    def test_chain_type_label_in_output(
        self, capsys: pytest.CaptureFixture[str], chain_type: ChainType, expected_label: str, has_rich: bool
    ) -> None:
        """Chain type label appears in both rich and plain output."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", chain_type=chain_type)]
        with patch("deprecate._cli.report._Reporter._HAS_RICH", has_rich):
            assert _Reporter.issues(results) is True
        captured = capsys.readouterr()
        assert expected_label in captured.out.lower()

    @pytest.mark.parametrize(
        "has_rich",
        [pytest.param(True, id="rich"), pytest.param(False, id="plain")],
    )
    def test_chains_flag_true(self, has_rich: bool) -> None:
        """_report_issues returns True when chains are present."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", chain_type=ChainType.TARGET)]
        with patch("deprecate._cli.report._Reporter._HAS_RICH", has_rich):
            assert _Reporter.issues(results) is True


class TestReportExpiry:
    """Tests for _report_expiry_rich and _report_expiry_plain."""

    @pytest.mark.parametrize(
        "has_rich",
        [pytest.param(True, id="rich"), pytest.param(False, id="plain")],
    )
    def test_expired_message_in_output(self, capsys: pytest.CaptureFixture[str], has_rich: bool) -> None:
        """Expired message text appears in both rich and plain output via cmd_expiry."""
        with (
            patch("deprecate._cli.commands.find_deprecation_wrappers", return_value=[]),
            patch("deprecate._cli.commands._check_expiry_for_callables", return_value=[_EXPIRED_MSG]),
            patch("deprecate._cli.report._Reporter._HAS_RICH", has_rich),
        ):
            cmd_expiry(path="some_module", version="2.0")
        captured = capsys.readouterr()
        assert "expired" in captured.out.lower()


# ---------------------------------------------------------------------------
# report_issues chain parametrize extension
# ---------------------------------------------------------------------------


class TestReportPolicy:
    """Tests for _Reporter.policy() directly, isolated from cmd_policy's scan logic."""

    @pytest.mark.parametrize(
        "has_rich",
        [
            pytest.param(
                True,
                id="rich",
                marks=pytest.mark.skipif(
                    _Reporter._RichTable is None, reason="rich not installed — forced has_rich=True is unsupported"
                ),
            ),
            pytest.param(False, id="plain"),
        ],
    )
    def test_rule_slug_prefix_survives_output(self, capsys: pytest.CaptureFixture[str], has_rich: bool) -> None:
        """The `[rule-slug]`-style prefix renders literally in both the rich and plain-text reporters.

        Rich parses square brackets in a cell as style markup, which would swallow a `[min-grace]` prefix
        silently. Calling `_Reporter.policy()` directly (rather than through `cmd_policy()`) isolates the
        reporter's own escaping behavior from the surrounding scan, so a regression here cannot hide behind
        mocked scan data used elsewhere in the test file. The forced ``has_rich=True`` case needs the real
        ``rich`` package on the interpreter — forcing ``_HAS_RICH`` alone leaves ``_RichTable``/``_rich_box``
        unset when ``rich`` was never importable, so that case is skipped in a local checkout without the
        ``cli`` extra.
        """
        message = "[min-grace] Callable `pkg.old_fn` is deprecated in `1.0` and already scheduled for removal in `1.0`."
        with patch("deprecate._cli.report._Reporter._HAS_RICH", has_rich):
            _Reporter.policy([message])
        captured = capsys.readouterr()
        assert "[min-grace]" in captured.out


class TestReportIssues:
    """Tests for _report_issues covering both the rich and plain-text output paths."""

    @pytest.mark.parametrize(
        "has_rich",
        [pytest.param(True, id="rich"), pytest.param(False, id="plain")],
    )
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
    def test_flag(self, results: list, expected: bool, has_rich: bool) -> None:
        """_report_issues returns the correct has-issues flag for both rich and plain paths."""
        with patch("deprecate._cli.report._Reporter._HAS_RICH", has_rich):
            assert _Reporter.issues(results) is expected

    @pytest.mark.parametrize(
        ("has_rich", "expected_present", "expected_absent"),
        [
            pytest.param(True, "Self reference", "All identity mappings", id="rich"),
            pytest.param(False, "Self reference", "All identity mappings", id="plain"),
        ],
    )
    def test_partial_identity_with_self_reference(
        self, capsys: pytest.CaptureFixture[str], has_rich: bool, expected_present: str, expected_absent: str
    ) -> None:
        """Partial identity mappings should not be reported as all-identity in either output path."""
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
        with patch("deprecate._cli.report._Reporter._HAS_RICH", has_rich):
            assert _Reporter.issues(results) is True
        captured = capsys.readouterr()
        assert expected_present in captured.out
        assert expected_absent not in captured.out


def test_report_issues_dispatches() -> None:
    """Test _report_issues dispatches based on _HAS_RICH."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])]
    assert _Reporter.issues(results) is True


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
        with (
            patch("deprecate._cli.report._Reporter._HAS_RICH", False),
            patch("deprecate._cli.report.generate_deprecation_table", side_effect=RuntimeError("boom")),
        ):
            _write_report(str(report_file), record)
        report = json.loads(report_file.read_text(encoding="utf-8"))
        assert report["table"] is None
        assert [gap["kind"] for gap in report["gaps"]] == [_GapKind.PACKAGING_MISSING.value]
        assert "Could not render the deprecation table for the JSON report: boom" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# TestHasRichFalse (preserved from original suite)
# ---------------------------------------------------------------------------


class TestHasRichFalse:
    """Tests for the plain-text fallback path when ``_HAS_RICH`` is ``False``."""

    @pytest.mark.parametrize(("stderr", "stream"), [(False, "out"), (True, "err")])
    def test_print_routes_to_builtin_print(self, capsys: pytest.CaptureFixture[str], stderr: bool, stream: str) -> None:
        """``_print()`` falls back to built-in ``print()`` when rich is unavailable."""
        with patch("deprecate._cli.report._Reporter._HAS_RICH", False):
            _print("hello", stderr=stderr)
        captured = capsys.readouterr()
        assert "hello" in getattr(captured, stream)

    def test_report_issues_dispatches_to_plain(self) -> None:
        """``_Reporter.issues()`` delegates to the plain reporter when rich is unavailable."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])]
        with patch("deprecate._cli.report._Reporter._HAS_RICH", False):
            assert _Reporter.issues(results) is True

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_check_output_streams(self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """``cmd_check()`` prints scanning and no-results messages to stdout when rich is unavailable."""
        mock_find.return_value = []
        with patch("deprecate._cli.report._Reporter._HAS_RICH", False):
            assert cmd_check(path="some_module") == 0
        captured = capsys.readouterr()
        assert "Scanning:" in captured.out
        assert "No deprecated callables found" in captured.out

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_nested_files_warning_stderr(
        self, mock_find: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Nested-files warning lands in stderr when rich is unavailable."""
        (tmp_path / "module_a.py").touch()
        subdir = tmp_path / "subpkg"
        subdir.mkdir()
        (subdir / "nested.py").touch()

        mock_find.return_value = []
        with patch("deprecate._cli.report._Reporter._HAS_RICH", False):
            assert cmd_check(path=str(tmp_path)) == 0
        captured = capsys.readouterr()
        assert "Skipping nested Python files" in captured.err
