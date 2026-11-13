"""Unit tests for :mod:`deprecate._cli.sarif`: the SARIF 2.1.0 document of a CLI run."""

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Optional
from unittest import mock

import pytest

from deprecate._cli.report import (
    _build_report,
    _find_location_root,
    _Gap,
    _GapKind,
    _RunRecord,
    _ScannedPath,
    _ScanRecord,
    _SuppressedFinding,
)
from deprecate._cli.sarif import _artifact_location, _build_sarif, _rule_name, _write_sarif
from deprecate.audit import DeprecationWrapperInfo
from deprecate.audit._findings import _Check, _Finding, _make_finding, _Rule

#: The system temp directory, where pytest's ``tmp_path`` lives.
_TEMP_DIR = Path(tempfile.gettempdir()).resolve()
#: Whether a ``.git`` sits at or above the temp directory: the root walk would stop there, not at the test's own dirs.
_TEMP_IN_GIT_TREE = any(os.path.exists(directory / ".git") for directory in (_TEMP_DIR, *_TEMP_DIR.parents))
_skip_temp_in_git_tree = pytest.mark.skipif(_TEMP_IN_GIT_TREE, reason="the temp directory lies inside a git checkout")
#: An absolute ``GITHUB_WORKSPACE`` that holds no temporary directory, on any OS.
_FOREIGN_WORKSPACE = str(Path(_TEMP_DIR.anchor) / "pydeprecate-no-such-workspace")


def _finding(
    rule: _Rule, symbol: str, source_file: Optional[str], line: Optional[int] = 3, check: _Check = _Check.CHECK
) -> _Finding:
    """Build a finding of *rule* for ``pkg.<symbol>`` defined at *source_file*:*line*."""
    info = DeprecationWrapperInfo(module="pkg", function=symbol, source_file=source_file, source_line=line)
    return _make_finding(info, rule, check, f"{symbol} message")


def _record(command: str, *findings: _Finding, **extra: Any) -> _RunRecord:  # noqa: ANN401
    """Build the run record of *command* holding *findings* from the scan path ``src/pkg``."""
    return _RunRecord(
        command=command,
        findings=list(findings),
        finding_paths=["src/pkg"] * len(findings),
        scanned=[_ScannedPath("src/pkg", "pkg", "1.0", "user-provided")],
        **extra,
    )


def _run(document: dict[str, Any]) -> dict[str, Any]:
    """Return the single run of a SARIF document."""
    (run,) = document["runs"]
    return run


class TestSarifDocument:
    """Shape of the SARIF document: required keys, rules, results and their locations."""

    def test_required_keys_at_each_level(self, tmp_path: Path) -> None:
        """The document carries the SARIF version, schema, tool driver, results and invocation.

        GitHub code scanning rejects a file without ``$schema``, ``version`` 2.1.0, a driver name and ``results``.

        """
        finding = _finding(_Rule.INVALID_ARGS, "old_fn", str(tmp_path / "src" / "pkg.py"))
        document = _build_sarif(_record("check", finding), 1, root=tmp_path)
        run = _run(document)
        assert document["version"] == "2.1.0"
        assert document["$schema"].endswith("sarif-schema-2.1.0.json")
        assert run["tool"]["driver"]["name"] == "pyDeprecate"
        assert run["invocations"][0]["exitCode"] == 1
        assert run["originalUriBaseIds"]["%SRCROOT%"]["uri"] == tmp_path.as_uri() + "/"

    def test_rules_are_those_the_subcommand_evaluates_at_its_level(self, tmp_path: Path) -> None:
        """Each rule the subcommand evaluates is listed once with its level there; ``ruleIndex`` points at it.

        ``chains`` evaluates only the chain rule, as an error, so its run lists exactly that rule.

        """
        finding = _finding(_Rule.CHAIN, "old_fn", str(tmp_path / "pkg.py"), check=_Check.CHAINS)
        run = _run(_build_sarif(_record("chains", finding), 1, root=tmp_path))
        rules = run["tool"]["driver"]["rules"]
        assert [(rule["id"], rule["name"], rule["defaultConfiguration"]["level"]) for rule in rules] == [
            ("chain", "Chain", "error")
        ]
        assert (run["results"][0]["ruleId"], run["results"][0]["ruleIndex"]) == ("chain", 0)

    def test_result_carries_level_message_and_fingerprint(self, tmp_path: Path) -> None:
        """A result has the finding's level in this run, its message and its stable id as a partial fingerprint.

        Code scanning matches results across runs by fingerprint; the finding id survives code moves and rewording.

        """
        finding = _finding(_Rule.NO_EFFECT, "old_fn", str(tmp_path / "pkg.py"))
        (result,) = _run(_build_sarif(_record("check", finding), 0, root=tmp_path))["results"]
        assert result["level"] == "warning"
        assert result["message"] == {"text": "old_fn message"}
        assert result["partialFingerprints"] == {"pydeprecateFindingId/v1": "no-effect:pkg:old_fn"}
        assert result["properties"]["path"] == "src/pkg"

    def test_location_relative_to_root_with_line(self, tmp_path: Path) -> None:
        """A wrapper under the root is located by a ``%SRCROOT%``-relative POSIX URI and its line.

        Code scanning shows a result only with a location it can resolve inside the repository.

        """
        finding = _finding(_Rule.INVALID_ARGS, "old_fn", str(tmp_path / "src" / "pkg.py"), line=12)
        (result,) = _run(_build_sarif(_record("check", finding), 1, root=tmp_path))["results"]
        assert result["locations"][0]["physicalLocation"] == {
            "artifactLocation": {"uri": "src/pkg.py", "uriBaseId": "%SRCROOT%"},
            "region": {"startLine": 12},
        }

    def test_module_finding_starts_at_line_one(self, tmp_path: Path) -> None:
        """A finding with a file but no line (a deprecated module) points at line 1 of that file.

        A SARIF region needs a start line; the module's first line is where its deprecation applies.

        """
        finding = _finding(_Rule.EXPIRED, "", str(tmp_path / "oldmod.py"), line=None, check=_Check.EXPIRY)
        (result,) = _run(_build_sarif(_record("expiry", finding), 1, root=tmp_path))["results"]
        assert result["locations"][0]["physicalLocation"]["region"] == {"startLine": 1}

    def test_unlocated_finding_is_kept_and_counted(self, tmp_path: Path) -> None:
        """A finding without a source file is a result without ``locations`` and counted as unlocated.

        A ``deprecated_instance`` data object has no file; dropping its finding would hide it from the SARIF file.

        """
        finding = _finding(_Rule.EXPIRED, "OLD_CONFIG", None, line=None, check=_Check.EXPIRY)
        run = _run(_build_sarif(_record("expiry", finding), 1, root=tmp_path))
        assert "locations" not in run["results"][0]
        assert run["properties"]["unlocated"] == 1

    def test_suppressed_findings_are_omitted_and_counted(self, tmp_path: Path) -> None:
        """Suppressed findings produce no result; the run's properties count them.

        An accepted finding must not reappear as an alert in code scanning, yet the count keeps it visible.

        """
        accepted = _finding(_Rule.EXPIRED, "old_fn", str(tmp_path / "pkg.py"), check=_Check.EXPIRY)
        record = _record("expiry", suppressed=[_SuppressedFinding(accepted, "src/pkg", "ignore", "pkg:old_fn")])
        run = _run(_build_sarif(record, 0, root=tmp_path))
        assert run["results"] == []
        assert run["properties"]["suppressed"] == 1

    def test_gaps_become_tool_notifications(self, tmp_path: Path) -> None:
        """Each gap is a warning notification of the invocation, identified by its kind.

        A partial audit must stay visible next to the results, as it is on stderr and in the JSON report.

        """
        record = _record("check", gaps=[_Gap(_GapKind.MODULE_NOT_AUDITED, "pkg.extra: ImportError()", "src/pkg")])
        notifications = _run(_build_sarif(record, 0, root=tmp_path))["invocations"][0]["toolExecutionNotifications"]
        assert notifications == [
            {
                "level": "warning",
                "message": {"text": "pkg.extra: ImportError()"},
                "descriptor": {"id": "module not audited"},
            }
        ]

    @pytest.mark.parametrize(
        ("scan", "mode"),
        [
            pytest.param(_ScanRecord("src/pkg", [], "1.0", static=True), "static", id="static-scan"),
            pytest.param(_ScanRecord("src/pkg", [], "1.0"), "import", id="import-scan"),
            pytest.param(None, "import", id="no-recorded-scan"),
        ],
    )
    def test_mode_matches_the_json_report(self, tmp_path: Path, scan: Optional[_ScanRecord], mode: str) -> None:
        """``run.properties.mode`` names how the gate read the package, exactly as the JSON report's ``mode`` does.

        ``policy --static --report-json=r.json --report-sarif=r.sarif`` writes both files from one source-only scan;
        a SARIF file claiming an import-mode scan would contradict the report written next to it. A run that recorded
        no scan, such as ``all``, read the package by importing it.

        """
        record = _record("policy", scan=scan, table="| pkg |")
        sarif_mode = _run(_build_sarif(record, 0, root=tmp_path))["properties"]["mode"]
        assert (sarif_mode, _build_report(record, 0)["mode"]) == (mode, mode)

    @pytest.mark.parametrize("static", [True, False])
    def test_run_with_only_unlocated_results_warns(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], static: bool
    ) -> None:
        """A run whose every result lacks a location says on stderr that code scanning will show none of them.

        A run of only ``deprecated_instance`` findings fails the gate, yet a wrapped value has no source line, so the
        uploaded file shows no alert. Static and import runs place wrappers alike, so the notice names no mode.

        """
        finding = _finding(_Rule.EXPIRED, "old_fn", None, line=None, check=_Check.EXPIRY)
        record = _record("expiry", finding, scan=_ScanRecord("src/pkg", [], "1.0", static=static), table="| pkg |")
        run = _run(_build_sarif(record, 1, root=tmp_path))
        err = capsys.readouterr().err
        assert run["properties"]["unlocated"] == 1
        assert "none of the run's 1 SARIF result(s) has a source location; GitHub code scanning" in err
        assert "displays only located results, so it shows none of them" in err
        assert "--static" not in err

    def test_run_with_one_located_result_stays_quiet(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """No advisory when at least one result has a location code scanning can display.

        An import run that mixes a ``deprecated_instance`` finding with a located wrapper uploads an alert GitHub
        shows; a notice there would be noise in every CI log.

        """
        unlocated = _finding(_Rule.EXPIRED, "OLD_CONFIG", None, line=None, check=_Check.EXPIRY)
        located = _finding(_Rule.EXPIRED, "old_fn", str(tmp_path / "pkg.py"), check=_Check.EXPIRY)
        _build_sarif(_record("expiry", unlocated, located), 1, root=tmp_path)
        assert capsys.readouterr().err == ""

    def test_results_over_the_limit_are_written_with_an_advisory(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A run over the code-scanning result limit keeps every result and says on stderr that GitHub rejects it.

        GitHub code scanning refuses a run with more than 25,000 results; the limit is lowered here so two results
        cross it. Dropping results silently would hide findings, so all are written and the user is told to narrow.

        """
        monkeypatch.setattr("deprecate._cli.sarif._RESULTS_LIMIT", 1)
        first = _finding(_Rule.NO_EFFECT, "a_fn", str(tmp_path / "pkg.py"))
        second = _finding(_Rule.NO_EFFECT, "b_fn", str(tmp_path / "pkg.py"))
        run = _run(_build_sarif(_record("check", first, second), 0, root=tmp_path))
        assert len(run["results"]) == 2
        assert "the SARIF run holds 2 results, more than the 1 GitHub code scanning accepts" in capsys.readouterr().err

    def test_run_without_results_stays_quiet(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A clean ``--static`` run has no result to locate, so it prints no advisory.

        A passing gate must not leave a warning-looking line in the CI log.

        """
        record = _record("expiry", scan=_ScanRecord("src/pkg", [], "1.0", static=True), table="| pkg |")
        _build_sarif(record, 0, root=tmp_path)
        assert capsys.readouterr().err == ""

    def test_written_file_is_utf8_json(self, tmp_path: Path) -> None:
        """``_write_sarif`` writes the document as UTF-8 JSON, creating missing parent directories.

        CI writes the SARIF file into a fresh output directory and uploads it as is.

        """
        target = tmp_path / "out" / "report.sarif"
        _write_sarif(str(target), _record("check"), 0)
        assert json.loads(target.read_text(encoding="utf-8"))["version"] == "2.1.0"


class TestWriteSarifFailure:
    """``_write_sarif`` turns every failure into a stderr advisory and leaves no partial file for CI to upload."""

    @mock.patch("deprecate._cli.sarif._build_sarif", autospec=True, side_effect=ValueError("bad value"))
    def test_build_error_is_an_advisory(
        self, mock_build: mock.MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A ``ValueError`` while building the document prints the advisory and raises nothing.

        ``cli()`` writes the SARIF file after the gate ran; an exception escaping here would turn a decided exit code
        into a traceback.

        """
        target = tmp_path / "report.sarif"
        _write_sarif(str(target), _record("check"), 1)
        mock_build.assert_called_once()
        assert f"Could not write the SARIF report {target}: bad value" in capsys.readouterr().err
        assert not target.exists()

    def test_unencodable_message_leaves_no_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A finding text with a lone surrogate cannot be UTF-8 encoded: advisory, and no empty file is created.

        A symbol name decoded with ``surrogateescape`` reaches the message as is. Writing such text used to truncate the
        target first and then fail, leaving a 0-byte file the GitHub Action would still publish for upload.

        """
        target = tmp_path / "report.sarif"
        finding = _finding(_Rule.NO_EFFECT, "old_\udcff", str(tmp_path / "pkg.py"))
        _write_sarif(str(target), _record("check", finding), 0)
        assert "Could not write the SARIF report" in capsys.readouterr().err
        assert not target.exists()

    def test_unwritable_target_is_an_advisory(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A target whose parent is a file cannot be written; the ``OSError`` becomes the advisory.

        A CI step passing a wrong output path must still keep the gate's own exit code.

        """
        blocker = tmp_path / "blocker"
        blocker.write_text("", encoding="utf-8")
        _write_sarif(str(blocker / "report.sarif"), _record("check"), 0)
        assert "Could not write the SARIF report" in capsys.readouterr().err

    @mock.patch("deprecate._cli.report.os.replace", autospec=True, side_effect=OSError("disk full"))
    def test_failed_write_keeps_the_previous_file(
        self, mock_replace: mock.MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A write failing before the file is complete leaves the previous file intact and no temporary file behind.

        The GitHub Action publishes the SARIF path whenever the file exists; a truncated or half-written file there
        would reach ``upload-sarif`` as corrupt JSON. The document goes to a temporary sibling replaced in one step.

        """
        target = tmp_path / "report.sarif"
        target.write_text("previous run\n", encoding="utf-8")
        _write_sarif(str(target), _record("check"), 0)
        mock_replace.assert_called_once()
        assert "Could not write the SARIF report" in capsys.readouterr().err
        assert target.read_text(encoding="utf-8") == "previous run\n"
        assert sorted(tmp_path.iterdir()) == [target]

    def test_non_ascii_text_round_trips_as_utf8(self, tmp_path: Path) -> None:
        """A non-ASCII symbol is written as UTF-8 text, not as ASCII escapes, and reads back unchanged.

        Code scanning shows the message to people; a package with accented or CJK names must read the same in the alert.

        """
        target = tmp_path / "report.sarif"
        finding = _finding(_Rule.NO_EFFECT, "café_fn", str(tmp_path / "pkg.py"))
        _write_sarif(str(target), _record("check", finding), 0)
        assert "café_fn message".encode() in target.read_bytes()
        assert _run(json.loads(target.read_bytes()))["results"][0]["message"] == {"text": "café_fn message"}

    def test_rewrite_replaces_the_file_and_leaves_no_temporary(self, tmp_path: Path) -> None:
        """Writing over an earlier SARIF file replaces it whole, and only the target remains in the directory.

        A re-run in the same CI workspace must not accumulate temporary siblings next to the uploaded file.

        """
        target = tmp_path / "report.sarif"
        target.write_text("previous run\n", encoding="utf-8")
        _write_sarif(str(target), _record("check"), 0)
        assert json.loads(target.read_bytes())["version"] == "2.1.0"
        assert sorted(tmp_path.iterdir()) == [target]


class TestArtifactLocation:
    """``_artifact_location`` turns an absolute source path into a SARIF artifact location on any OS."""

    @pytest.mark.parametrize(
        ("source", "root", "expected"),
        [
            pytest.param(
                PurePosixPath("/repo/src/pkg.py"),
                PurePosixPath("/repo"),
                {"uri": "src/pkg.py", "uriBaseId": "%SRCROOT%"},
                id="posix-under-root",
            ),
            pytest.param(
                PurePosixPath("/repo/my src/pkg#1.py"),
                PurePosixPath("/repo"),
                {"uri": "my%20src/pkg%231.py", "uriBaseId": "%SRCROOT%"},
                id="posix-percent-encoded",
            ),
            pytest.param(
                PurePosixPath("/other/pkg.py"),
                PurePosixPath("/repo"),
                {"uri": "file:///other/pkg.py"},
                id="posix-outside",
            ),
            pytest.param(
                PureWindowsPath("C:\\\\repo\\\\src\\\\pkg.py"),
                PureWindowsPath("C:\\\\repo"),
                {"uri": "src/pkg.py", "uriBaseId": "%SRCROOT%"},
                id="windows-under-root",
            ),
            pytest.param(
                PureWindowsPath("D:\\\\other\\\\pkg.py"),
                PureWindowsPath("C:\\\\repo"),
                {"uri": "file:///D:/other/pkg.py"},
                id="windows-outside",
            ),
            pytest.param(
                PureWindowsPath("C:\\repo\\src\\pkg.py"),
                PureWindowsPath("C:\\repo"),
                {"uri": "src/pkg.py", "uriBaseId": "%SRCROOT%"},
                id="windows-single-backslash",
            ),
            pytest.param(
                PureWindowsPath("\\\\server\\share\\repo\\src\\pkg.py"),
                PureWindowsPath("\\\\server\\share\\repo"),
                {"uri": "src/pkg.py", "uriBaseId": "%SRCROOT%"},
                id="windows-unc-under-root",
            ),
            pytest.param(
                PurePosixPath("/repo/src/模块/über.py"),
                PurePosixPath("/repo"),
                {"uri": "src/%E6%A8%A1%E5%9D%97/%C3%BCber.py", "uriBaseId": "%SRCROOT%"},
                id="non-ascii-utf8-percent-encoded",
            ),
            pytest.param(
                PurePosixPath("/repo/100%/pkg.py"),
                PurePosixPath("/repo"),
                {"uri": "100%25/pkg.py", "uriBaseId": "%SRCROOT%"},
                id="literal-percent-escaped",
            ),
        ],
    )
    def test_location(self, source: Any, root: Any, expected: dict[str, str]) -> None:  # noqa: ANN401
        """Under the root the URI is relative POSIX with ``%SRCROOT%``; elsewhere it is an absolute ``file:`` URI.

        A Windows runner must produce forward-slash URIs, or code scanning cannot match them to repository files.

        """
        assert _artifact_location(source, root) == expected

    def test_rule_name_is_pascal_case(self) -> None:
        """Rule names are the PascalCase of the slug, as SARIF tooling expects a readable name."""
        assert _rule_name(_Rule.POSITIONAL_ONLY_MAPPING) == "PositionalOnlyMapping"


class TestLocationRoot:
    """Report and SARIF locations are relative to the repository root, wherever in the checkout the CLI runs."""

    @pytest.mark.parametrize(
        "make_marker", [pytest.param(Path.mkdir, id="git-directory"), pytest.param(Path.touch, id="worktree-git-file")]
    )
    def test_nearest_git_entry_is_the_root(self, tmp_path: Path, make_marker: Callable[[Path], None]) -> None:
        """A run in a sub-directory of a checkout anchors at the directory holding ``.git``, before the workspace.

        GitHub resolves relative SARIF paths against the repository root, so ``cd src && pydeprecate check pkg`` must
        still name ``src/pkg/...``. A worktree or submodule marks its root with a ``.git`` file instead of a directory.

        """
        repo = tmp_path / "repo"
        (repo / "src" / "pkg").mkdir(parents=True)
        make_marker(repo / ".git")
        assert _find_location_root(repo / "src" / "pkg", str(tmp_path)) == repo

    @_skip_temp_in_git_tree
    def test_workspace_holding_cwd_is_the_root(self, tmp_path: Path) -> None:
        """Without ``.git``, ``GITHUB_WORKSPACE`` is the root when the run happens inside it.

        ``actions/checkout`` falls back to a tarball without ``.git`` when git is missing on the runner; the workspace
        is then the only marker of where the repository starts.

        """
        cwd = tmp_path / "ws" / "sub"
        cwd.mkdir(parents=True)
        assert _find_location_root(cwd, str(tmp_path / "ws")) == tmp_path / "ws"

    @_skip_temp_in_git_tree
    @pytest.mark.parametrize(
        "workspace",
        [
            pytest.param(None, id="no-workspace"),
            pytest.param("", id="empty-workspace"),
            pytest.param("ws", id="relative-workspace"),
            pytest.param(_FOREIGN_WORKSPACE, id="workspace-not-holding-cwd"),
        ],
    )
    def test_without_repository_the_working_directory_is_the_root(
        self, tmp_path: Path, workspace: Optional[str]
    ) -> None:
        """Outside a checkout, with no workspace holding the run, locations stay relative to the working directory.

        Unit tests and ad-hoc runs work in temporary directories while CI sets ``GITHUB_WORKSPACE`` to the checkout;
        a workspace elsewhere, or a relative one, must not turn every location absolute.

        """
        assert _find_location_root(tmp_path, workspace) == tmp_path

    def test_sarif_and_report_share_the_root_from_a_subdirectory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Run from ``<repo>/src``, the SARIF URI, its ``%SRCROOT%`` base and the JSON location all start at ``<repo>``.

        The Action annotates from the JSON report and code scanning reads the SARIF file of the same run; both must
        name the file the way the repository does, not relative to the directory the step happened to run in.

        """
        (tmp_path / ".git").mkdir()
        (tmp_path / "src").mkdir()
        monkeypatch.chdir(tmp_path / "src")
        monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)
        record = _record("check", _finding(_Rule.INVALID_ARGS, "old_fn", str(tmp_path / "src" / "pkg.py"), line=5))
        run = _run(_build_sarif(record, 1))
        report_location = _build_report(record, 1)["findings"][0]["location"]
        assert run["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/pkg.py"
        assert run["originalUriBaseIds"]["%SRCROOT%"]["uri"] == tmp_path.as_uri() + "/"
        assert report_location == {"file": "src/pkg.py", "line": 5}

    def test_result_outside_the_root_is_counted_on_stderr(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A wrapper file outside the repository root keeps its absolute URI and is counted in a stderr advisory.

        A module-name scan of an installed copy locates findings in ``site-packages``; code scanning cannot show such
        a result, and the run says so instead of uploading alerts that silently never appear.

        """
        repo = tmp_path / "repo"
        repo.mkdir()
        inside = _finding(_Rule.NO_EFFECT, "a_fn", str(repo / "pkg.py"))
        outside = _finding(_Rule.NO_EFFECT, "b_fn", str(tmp_path / "site-packages" / "pkg.py"))
        run = _run(_build_sarif(_record("check", inside, outside), 0, root=repo))
        assert "pydeprecate: 1 SARIF result(s) lie outside the repository root" in capsys.readouterr().err
        assert run["properties"]["unlocated"] == 0
