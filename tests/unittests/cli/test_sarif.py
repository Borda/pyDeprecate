"""Unit tests for :mod:`deprecate._cli.sarif`: the SARIF 2.1.0 document of a CLI run."""

import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Optional

import pytest

from deprecate._cli.report import _Gap, _GapKind, _RunRecord, _ScannedPath, _SuppressedFinding
from deprecate._cli.sarif import _artifact_location, _build_sarif, _rule_name, _write_sarif
from deprecate.audit import DeprecationWrapperInfo
from deprecate.audit._findings import _Check, _Finding, _make_finding, _Rule


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

    def test_written_file_is_utf8_json(self, tmp_path: Path) -> None:
        """``_write_sarif`` writes the document as UTF-8 JSON, creating missing parent directories.

        CI writes the SARIF file into a fresh output directory and uploads it as is.

        """
        target = tmp_path / "out" / "report.sarif"
        _write_sarif(str(target), _record("check"), 0)
        assert json.loads(target.read_text(encoding="utf-8"))["version"] == "2.1.0"


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
