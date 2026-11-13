"""Unit tests for :mod:`deprecate._cli.report`: the plain-text finding sections and the ``--report-json`` file."""

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import jsonschema
import pytest

from deprecate._cli.commands import cmd_all, cmd_chains, cmd_check, cmd_expiry, cmd_policy
from deprecate._cli.report import _Gap, _GapKind, _print, _Reporter, _RunRecord, _ScanRecord, _write_report
from deprecate._types import DeprecationConfig, TargetMode
from deprecate.audit import ChainType, DeprecationWrapperInfo
from deprecate.audit._findings import _Check, _Finding, _make_finding, _Rule, _wrapper_findings
from tests.unittests.cli._helpers import _EXPIRED_FINDING, _EXPIRED_MSG, _scanned

# ---------------------------------------------------------------------------
# Chain and expiry reporters
# ---------------------------------------------------------------------------


def _findings_of(wrappers: list[DeprecationWrapperInfo]) -> list[_Finding]:
    """Derive the configuration findings of *wrappers*, as ``check`` does before printing them."""
    return [finding for info in wrappers for finding in _wrapper_findings(info)]


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
        assert _Reporter.issues(_findings_of(results)) is True
        assert f"\t- mod.fn: {expected_label}" in capsys.readouterr().out


class TestReportExpiry:
    """Tests for the expiry section printed by ``cmd_expiry``."""

    def test_expired_message_in_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The expired message is printed under the ``[ERROR]`` header of the expiry section."""
        with (
            patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[]),
            patch("deprecate._cli.commands._expiry_findings", return_value=[_EXPIRED_FINDING]),
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
        assert _Reporter.issues(_findings_of(results)) is expected

    def test_partial_identity_with_self_reference(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Partial identity mappings are not reported as all-identity.

        A wrapper maps ``a`` to itself but also renames ``b`` to ``c``, and it is flagged as a self-reference with no
        effect.  Only one of its two mapping entries is an identity, so the CLI report must print the "Self
        reference" finding and return that issues were found, but must not print the "All identity mappings"
        wording, which describes a mapping made only of identity entries.

        """
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
        assert _Reporter.issues(_findings_of(results)) is True
        captured = capsys.readouterr()
        assert "Self reference" in captured.out
        assert "All identity mappings" not in captured.out

    def test_square_brackets_in_detail_are_literal(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A detail such as an argument named ``[x]`` reaches the output unchanged.

        Output is plain text with no markup layer, so nothing in a module, function or argument name can be swallowed
        as styling.

        """
        results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["[x]"])]
        assert _Reporter.issues(_findings_of(results)) is True
        out = capsys.readouterr().out
        assert "[ERROR] Found functions with invalid argument mappings:" in out
        assert "\t- mod.fn: [x]" in out


class TestReportSchema:
    """Validate schema 1 reports from both legacy and current producers."""

    def test_legacy_report_is_accepted(self) -> None:
        """A 0.15 report omits fields added later without changing its schema version.

        Consumers validating older audit results must still accept their original three-field layout and gap records.

        """
        schema = json.loads(
            (Path(__file__).resolve().parents[3] / "docs/schemas/report-1.schema.json").read_text(encoding="utf-8")
        )
        report = {
            "schema": 1,
            "table": None,
            "gaps": [{"kind": "packaging missing", "detail": "missing", "hint": "install"}],
        }
        jsonschema.validate(report, schema)

    def test_present_gap_path_is_validated(self) -> None:
        """A newer gap path, when present, must retain its documented type.

        Legacy gaps omit the path, but consumers must reject malformed path values from newer producers.

        """
        schema = json.loads(
            (Path(__file__).resolve().parents[3] / "docs/schemas/report-1.schema.json").read_text(encoding="utf-8")
        )
        report = {"schema": 1, "table": None, "gaps": [{"kind": "x", "detail": "x", "hint": "x", "path": 7}]}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(report, schema)

    @pytest.mark.parametrize(
        ("field", "value"),
        [pytest.param("mode", "unknown", id="invalid-mode"), pytest.param("exit_code", 2, id="invalid-exit-code")],
    )
    def test_present_new_fields_are_validated(self, field: str, value: object) -> None:
        """Optional additions remain constrained when a producer includes them.

        A malformed later field must not become valid merely because legacy reports may omit it.

        """
        schema = json.loads(
            (Path(__file__).resolve().parents[3] / "docs/schemas/report-1.schema.json").read_text(encoding="utf-8")
        )
        report = {"schema": 1, "table": None, "gaps": [], field: value}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(report, schema)


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
        _write_report(str(report_file), _RunRecord(gaps=[_Gap(kind, "some detail")], table="| a |"))
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
            gaps=[_Gap(_GapKind.PACKAGING_MISSING, "no packaging")],
            scan=_ScanRecord(path="some_module", wrappers=[], version="1.0"),
        )
        with patch("deprecate._cli.report._render_deprecation_table", side_effect=RuntimeError("boom")):
            _write_report(str(report_file), record)
        report = json.loads(report_file.read_text(encoding="utf-8"))
        assert report["table"] is None
        assert [gap["kind"] for gap in report["gaps"]] == [_GapKind.PACKAGING_MISSING.value]
        assert "Could not render the deprecation table for the JSON report: boom" in capsys.readouterr().err

    def test_unrendered_table_of_one_path_keeps_its_heading(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Over several paths, a table that fails to render leaves a note under its heading; the other keeps its own.

        A ``check`` run over two packages builds the report table from both scans. When the first package's table
        raises, the second must not be written bare as if it were the only package: a job summary built from the
        report would show an unlabelled table and hide that one package is missing.
        """
        report_file = tmp_path / "report.json"
        record = _RunRecord(scans=[_ScanRecord("pkg_a", [], "1.0"), _ScanRecord("pkg_b", [], "1.0")])
        with patch("deprecate._cli.report._render_deprecation_table", side_effect=[RuntimeError("boom"), "| B |"]):
            _write_report(str(report_file), record)
        report = json.loads(report_file.read_text(encoding="utf-8"))
        assert report["table"] == (
            "### pkg_a\n\n_The deprecation table could not be rendered: boom_\n\n### pkg_b\n\n| B |"
        )
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


def _record_of(command: str, *findings: _Finding) -> _RunRecord:
    """Build the run record of *command* holding *findings*, each from the scan path ``pkg``."""
    return _RunRecord(command=command, findings=list(findings), finding_paths=["pkg"] * len(findings))


def _located(function: str, source_file: str, rule: _Rule, check: _Check) -> _Finding:
    """Build a finding of *rule* for a wrapper named *function* defined at line 7 of *source_file*."""
    info = DeprecationWrapperInfo(module="pkg", function=function, source_file=source_file, source_line=7)
    return _make_finding(info, rule, check, f"{function} message")


class TestReportFindings:
    """The ``findings`` array of the report: levels per subcommand, order, de-duplication and locations."""

    def test_sorted_by_level_then_id(self, tmp_path: Path) -> None:
        """Errors come before warnings, and findings of one level are ordered by id.

        A consumer that diffs two reports, or shows the first few findings, needs the same order for the same run.

        """
        source = str(tmp_path / "pkg.py")
        record = _record_of(
            "check",
            _located("b_fn", source, _Rule.NO_EFFECT, _Check.CHECK),
            _located("z_fn", source, _Rule.INVALID_ARGS, _Check.CHECK),
            _located("a_fn", source, _Rule.INVALID_ARGS, _Check.CHECK),
        )
        report_file = tmp_path / "report.json"
        _write_report(str(report_file), record, 1)
        findings = json.loads(report_file.read_text(encoding="utf-8"))["findings"]
        assert [(finding["level"], finding["id"]) for finding in findings] == [
            ("error", "invalid-args:pkg:a_fn"),
            ("error", "invalid-args:pkg:z_fn"),
            ("warning", "no-effect:pkg:b_fn"),
        ]

    def test_chain_seen_twice_by_all_is_one_error(self, tmp_path: Path) -> None:
        """``all`` derives a chain in its ``check`` pass and its ``chains`` pass; the report holds it once, as an error.

        Two records for one problem would double every count a dashboard shows and make ids non-unique.

        """
        source = str(tmp_path / "pkg.py")
        chain = _located("old_fn", source, _Rule.CHAIN, _Check.CHAINS)
        report_file = tmp_path / "report.json"
        _write_report(str(report_file), _record_of("all", chain, chain), 1)
        findings = json.loads(report_file.read_text(encoding="utf-8"))["findings"]
        assert [(finding["id"], finding["level"]) for finding in findings] == [("chain:pkg:old_fn", "error")]

    def test_location_relative_under_working_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A wrapper under the working directory is located by a POSIX path relative to it; others stay absolute.

        Code scanning and annotations resolve paths from the repository root, which is where CI runs the CLI.

        """
        inside = tmp_path / "src" / "pkg.py"
        outside = tmp_path.parent / "elsewhere.py"
        monkeypatch.chdir(tmp_path)
        record = _record_of(
            "check",
            _located("a_fn", str(inside), _Rule.INVALID_ARGS, _Check.CHECK),
            _located("b_fn", str(outside), _Rule.INVALID_ARGS, _Check.CHECK),
        )
        report_file = tmp_path / "report.json"
        _write_report(str(report_file), record, 1)
        findings = json.loads(report_file.read_text(encoding="utf-8"))["findings"]
        assert [finding["location"] for finding in findings] == [
            {"file": "src/pkg.py", "line": 7},
            {"file": outside.as_posix(), "line": 7},
        ]

    def test_finding_not_evaluated_by_the_subcommand_is_left_out(self, tmp_path: Path) -> None:
        """A finding the subcommand does not evaluate never reaches the report.

        ``status`` gates on nothing, so even a recorded finding has no level there and would misrepresent the run.

        """
        source = str(tmp_path / "pkg.py")
        record = _record_of("status", _located("a_fn", source, _Rule.EXPIRED, _Check.EXPIRY))
        report_file = tmp_path / "report.json"
        _write_report(str(report_file), record, 0)
        assert json.loads(report_file.read_text(encoding="utf-8"))["findings"] == []


# One wrapper list with two rows in each of the seven configuration sections of the console report.
_EVERY_SECTION = [
    DeprecationWrapperInfo(module="pkg.a", function="bad_one", invalid_args=["x", "y"]),
    DeprecationWrapperInfo(module="pkg.a", function="bad_two", invalid_args=["z"]),
    DeprecationWrapperInfo(
        module="pkg.b",
        function="notify_map",
        misconfigured_target=True,
        deprecated_info=DeprecationConfig(target=TargetMode.NOTIFY, args_mapping={"a": "b"}),
    ),
    DeprecationWrapperInfo(
        module="pkg.b",
        function="remap_empty",
        misconfigured_target=True,
        deprecated_info=DeprecationConfig(target=TargetMode.ARGS_REMAP),
    ),
    DeprecationWrapperInfo(module="pkg.c", function="Cls.prop", inner_order_property=True),
    DeprecationWrapperInfo(module="pkg.c", function="Cls.other", inner_order_property=True),
    DeprecationWrapperInfo(module="pkg.d", function="same", identity_args_mapping=["a"]),
    DeprecationWrapperInfo(module="pkg.d", function="same2", identity_args_mapping=["b", "c"]),
    DeprecationWrapperInfo(
        module="pkg.e", function="noop", no_effect=True, empty_args_mapping=True, self_reference=True
    ),
    DeprecationWrapperInfo(module="pkg.e", function="noop2", no_effect=True, all_identity=True),
    DeprecationWrapperInfo(module="pkg.f", function="chain_t", chain_type=ChainType.TARGET),
    DeprecationWrapperInfo(module="pkg.f", function="chain_s", chain_type=ChainType.STACKED),
    DeprecationWrapperInfo(module="pkg.g", function="Pos", args_mapping_positional_only=["p"]),
    DeprecationWrapperInfo(module="pkg.g", function="Pos2", args_mapping_positional_only=["q", "r"]),
]
# Two wrappers past their removal at version 2.0.
_TWO_EXPIRED = [
    DeprecationWrapperInfo(
        module="pkg", function="gone", deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="1.5")
    ),
    DeprecationWrapperInfo(
        module="pkg", function="gone2", deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="1.8")
    ),
]
# Two warn-only wrappers that name no replacement, tripping ``message-required``.
_TWO_SILENT = [
    DeprecationWrapperInfo(
        module="pkg", function="quiet", deprecated_info=DeprecationConfig(deprecated_in="1.0", target=TargetMode.NOTIFY)
    ),
    DeprecationWrapperInfo(
        module="pkg",
        function="quiet2",
        deprecated_info=DeprecationConfig(deprecated_in="1.0", target=TargetMode.NOTIFY),
    ),
]
_GOLDEN_CHECK = (
    "\n"
    "[ERROR] Found functions with invalid argument mappings:\n"
    "\t- pkg.a.bad_one: x, y\n"
    "\t- pkg.a.bad_two: z\n"
    "\n"
    "[ERROR] Found deprecated wrappers with a misconfigured target:\n"
    "\t- pkg.b.notify_map: `TargetMode.NOTIFY` with `args_mapping`\n"
    "\t- pkg.b.remap_empty: `TargetMode.ARGS_REMAP` with an empty `args_mapping`\n"
    "\n"
    "[ERROR] Found inner-order @property @deprecated wrappers (setter/deleter unprotected):\n"
    "\t- pkg.c.Cls.prop: use @deprecated(...) above @property\n"
    "\t- pkg.c.Cls.other: use @deprecated(...) above @property\n"
    "\n"
    "[WARNING] Found functions with identity argument mappings (arg -> arg):\n"
    "\t- pkg.d.same: a\n"
    "\t- pkg.d.same2: b, c\n"
    "\n"
    "[WARNING] Found deprecated wrappers with NO EFFECT (zero impact):\n"
    "\t- pkg.e.noop: Empty mapping, Self reference\n"
    "\t- pkg.e.noop2: All identity mappings\n"
    "\n"
    "[WARNING] Found deprecated wrappers forming deprecation chains:\n"
    "\t- pkg.f.chain_t: target\n"
    "\t- pkg.f.chain_s: stacked\n"
    "\n"
    "[WARNING] Found args_mapping entries targeting POSITIONAL_ONLY constructor parameters"
    " (proxy falls back to setattr for these entries):\n"
    "\t- pkg.g.Pos: p\n"
    "\t- pkg.g.Pos2: q, r\n"
    "\n"
    "Issues were found in deprecated wrappers.\n"
)
_GOLDEN_CHAINS = (
    "\n"
    "[ERROR] Found deprecated wrappers forming deprecation chains:\n"
    "\t- pkg.f.chain_t: target\n"
    "\t- pkg.f.chain_s: stacked\n"
    "\n"
    "2 deprecation chain(s) found.\n"
)
_GOLDEN_EXPIRY = (
    "\n"
    "[ERROR] Found expired deprecated wrappers:\n"
    "\t- Callable `pkg.gone` was scheduled for removal in version 1.5 but still exists in version"
    " 2.0. Please delete this deprecated code.\n"
    "\t- Callable `pkg.gone2` was scheduled for removal in version 1.8 but still exists in version"
    " 2.0. Please delete this deprecated code.\n"
    "\n"
    "2 expired wrapper(s) found.\n"
)
_GOLDEN_POLICY = (
    "Policy: min-grace=None (flag)  message-required=True (built-in)\n"
    "\n"
    "[ERROR] Found deprecation policy violations:\n"
    "\t- [message-required] Callable `pkg.quiet` warns without naming a replacement; configure a"
    " `target`, an `args_mapping`/`attrs_mapping`, or a custom `message_template` so callers learn"
    " what to migrate to.\n"
    "\t- [message-required] Callable `pkg.quiet2` warns without naming a replacement; configure a"
    " `target`, an `args_mapping`/`attrs_mapping`, or a custom `message_template` so callers learn"
    " what to migrate to.\n"
    "\n"
    "2 policy violation(s) found.\n"
)
# ``all`` prints every check in turn; the policy section is advisory there, with a trailer pointing at ``policy``.
_GOLDEN_POLICY_ADVISORY = (
    "\n"
    "All deprecated wrappers look correct!\n"
    "No expired deprecated wrappers found.\n"
    "Policy: min-grace=0.3 (built-in)  message-required=True (built-in)\n"
    "\n"
    "[WARNING] Found deprecation policy violations:\n"
    "\t- [message-required] Callable `pkg.quiet` warns without naming a replacement; configure a"
    " `target`, an `args_mapping`/`attrs_mapping`, or a custom `message_template` so callers learn"
    " what to migrate to.\n"
    "\t- [message-required] Callable `pkg.quiet2` warns without naming a replacement; configure a"
    " `target`, an `args_mapping`/`attrs_mapping`, or a custom `message_template` so callers learn"
    " what to migrate to.\n"
    "\n"
    "2 policy violation(s) found.\n"
    "(advisory here — run `pydeprecate policy` to gate on it)\n"
    "No deprecation chains found.\n"
)


def _render_check() -> int:
    """Run ``check`` on a scan of :data:`_EVERY_SECTION` and return its exit code."""
    with _scanned(_EVERY_SECTION):
        return cmd_check(path="some_module")


def _render_chains() -> int:
    """Run ``chains`` on a scan of :data:`_EVERY_SECTION` and return its exit code."""
    with _scanned(_EVERY_SECTION):
        return cmd_chains(path="some_module")


def _render_expiry() -> int:
    """Run ``expiry`` on a scan of :data:`_TWO_EXPIRED` at version 2.0 and return its exit code."""
    with _scanned(_TWO_EXPIRED, "2.0"):
        return cmd_expiry(path="some_module", version="2.0")


def _render_policy() -> int:
    """Run ``policy`` with only ``message-required`` on a scan of :data:`_TWO_SILENT` and return its exit code."""
    with _scanned(_TWO_SILENT):
        return cmd_policy(path="some_module", min_grace=None)


def _render_policy_advisory() -> int:
    """Run ``all`` on a scan of :data:`_TWO_SILENT` at version 2.0, its table left out, and return its exit code."""
    with _scanned(_TWO_SILENT, "2.0"), patch("deprecate._cli.commands._status_table", return_value=""):
        return cmd_all(path="some_module", version="2.0")


class TestConsoleGolden:
    """The full console text of each check section, pinned byte for byte: order, headers, rows and count lines."""

    @pytest.mark.parametrize(
        ("render", "exit_code", "expected"),
        [
            pytest.param(_render_check, 1, _GOLDEN_CHECK, id="check"),
            pytest.param(_render_chains, 1, _GOLDEN_CHAINS, id="chains"),
            pytest.param(_render_expiry, 1, _GOLDEN_EXPIRY, id="expiry"),
            pytest.param(_render_policy, 1, _GOLDEN_POLICY, id="policy"),
            pytest.param(_render_policy_advisory, 0, _GOLDEN_POLICY_ADVISORY, id="policy-advisory"),
        ],
    )
    def test_stdout_is_unchanged(
        self, render: Callable[[], int], exit_code: int, expected: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Every section prints exactly the pinned text, so a refactor of the rendering cannot drift silently.

        CI logs and scripts read these sections; their order, headers and row format are a user-visible contract that
        the existing tests only checked by substring.

        """
        assert render() == exit_code
        assert capsys.readouterr().out == expected
