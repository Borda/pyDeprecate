"""Run one CLI scan in a separate Python interpreter and return serializable audit results.

Purpose: Prevent one scan's imported modules from supplying stale bindings to another scan in the same CLI run.

Scope: Scan one requested path, derive check findings and optional table output, and leave cross-path suppression and
aggregation to the caller. Imported wrappers and arbitrary target objects never cross the process boundary.

Usage: Invoke this private module with the response-file path as its first argument and a JSON request on standard
input. The caller supplies the import path, scan options, path, requested checks, and reporting settings.

Outputs: Write a UTF-8 JSON response containing scan status, per-check findings, gaps, captured output, version, and
optional rendered table to the specified private file.

Failure: An unhandled scan or protocol error exits without a successful response; the caller must report the failure
rather than treating it as a clean audit.

Used by: The CLI's multi-path scan orchestration, which owns output ordering, suppression, reports, and final exit.

"""

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryFile
from typing import Any, Optional

from deprecate._cli.commands import _chain_derive, _check_derive, _expiry_derive, _policy_derive, _status_table
from deprecate._cli.config import _FROM_PYPROJECT
from deprecate._cli.report import _record_run, _render_recorded_table, _ScanRecord
from deprecate._cli.scan import _scan_for_command, _ScanError
from deprecate.audit import TableStyle
from deprecate.audit._findings import _Finding
from deprecate.audit._policy import _build_policy_spec


@contextmanager
def _capture() -> Iterator[dict[str, str]]:
    """Capture phase output while exposing normal binary buffers and file descriptors to imported code."""
    with (
        TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stdout,
        TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr,
    ):
        captured = {"stdout": "", "stderr": ""}
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                yield captured
        finally:
            stdout.flush()
            stderr.flush()
            stdout.seek(0)
            stderr.seek(0)
            captured.update(stdout=stdout.read(), stderr=stderr.read())


def _derive(name: str, scan: _ScanRecord, policy: Optional[dict[str, Any]]) -> Optional[list[_Finding]]:
    """Derive one check from a worker-local scan before any wrapper crosses the process boundary."""
    if name == "check":
        return _check_derive(scan.wrappers)
    if name == "expiry":
        return _expiry_derive(scan.wrappers, scan.version)
    if name == "chains":
        return _chain_derive(scan.wrappers)
    if policy is None:
        raise ValueError("policy settings are required for a policy scan")
    return _policy_derive(scan.wrappers, _build_policy_spec(policy["grace"], policy["message"]))


def _run(request: dict[str, Any]) -> dict[str, Any]:
    """Scan one requested path and derive the requested check results without suppressing them."""
    sys.path[:] = request["sys_path"]
    command = request["command"]
    path = request["path"]
    exclusions = request["exclude"]
    source = request["exclude_source"]
    config = ({"exclude": exclusions}, "pyproject.toml") if source == "pyproject.toml" else ({}, None)
    exclude = exclusions if source == "flag" else _FROM_PYPROJECT
    phases: dict[str, dict[str, str]] = {}
    checks: dict[str, Any] = {}
    with _record_run() as record:
        record.wants_report = request["wants_report"]
        try:
            with _capture() as captured:
                scan = _scan_for_command(
                    path,
                    recursive=request["recursive"],
                    exclude=exclude,
                    config=config,
                    include_members=request["include_members"],
                    version=request["version"],
                    version_explicit=request["version"] is not None,
                    resolve_version=request["resolve_version"],
                    covered=request["covered"],
                )
        except _ScanError as exc:
            phases["scan"] = captured
            return {"status": "scan-error", "message": str(exc), "phases": phases}
        phases["scan"] = captured
        if scan is None:
            return {"status": "usage", "phases": phases}
        for name in ("check", "expiry", "policy", "chains"):
            if command != name and command != "all":
                continue
            with _capture() as captured:
                findings = _derive(name, scan, request["policy"])
            phases[name] = captured
            checks[name] = None if findings is None else [asdict(finding) for finding in findings]
        table = None
        table_error = None
        if command in ("all", "status") or request["wants_report"]:
            with _capture() as captured:
                try:
                    if command in ("all", "status"):
                        table = _status_table(
                            scan.wrappers,
                            path,
                            scan.version,
                            style=TableStyle(request["style"]),
                            version_explicit=request["version"] is not None,
                        )
                    else:
                        table = _render_recorded_table(record)
                except Exception as exc:
                    table_error = str(exc)
            phases["table"] = captured
        return {
            "status": "ok",
            "path": path,
            "version": scan.version,
            "wrapper_count": len(scan.wrappers),
            "checks": checks,
            "phases": phases,
            "gaps": [
                {
                    "kind": gap.kind.value,
                    "detail": gap.detail,
                    "path": gap.path,
                    "subject": gap.subject,
                    "rules": sorted(gap.rules) if gap.rules is not None else None,
                }
                for gap in record.gaps
            ],
            "table": table,
            "table_error": table_error,
        }


def main() -> None:
    """Read a JSON request from stdin and write its response to the caller's private path."""
    request = json.load(sys.stdin)
    response = _run(request)
    Path(sys.argv[1]).write_text(json.dumps(response), encoding="utf-8")


if __name__ == "__main__":
    main()
