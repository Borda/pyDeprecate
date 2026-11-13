"""SARIF 2.1.0 output for the CLI: the ``--report-sarif`` file that code-scanning tools read.

The file holds one run built from the same run record as ``--report-json``: one result per active finding of every
scanned path (each naming its path), one rule per check rule the subcommand evaluates, the gaps as tool notifications.
Suppressed findings are left out; a finding whose wrapper has no source file is kept without a location and counted in
``run.properties.unlocated``; a run whose results all lack a location (only ``deprecated_instance`` findings, for
example) says so on stderr.

"""

import sys
from pathlib import Path, PurePath
from typing import Any, Optional
from urllib.parse import quote

from deprecate import __about__
from deprecate._cli.findings import _level
from deprecate._cli.report import (
    _location_root,
    _report_findings_with_sources,
    _report_suppressed,
    _run_mode,
    _RunRecord,
    _write_json_file,
)
from deprecate.audit._findings import _Rule

#: The SARIF version written and the JSON schema it validates against.
_SARIF_VERSION = "2.1.0"
_SARIF_SCHEMA = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
_INFORMATION_URI = "https://github.com/Borda/pyDeprecate"
#: The base id relative result locations are resolved against: the repository root of the directory the CLI ran in.
_SRCROOT = "%SRCROOT%"
#: Results GitHub code scanning accepts in one run; more are written, with an advisory.
_RESULTS_LIMIT = 25_000
#: ``partialFingerprints`` key carrying the stable finding id.
_FINGERPRINT = "pydeprecateFindingId/v1"

#: Short and full description of each rule, in the SARIF ``rules`` array.
_RULE_TEXT: dict[_Rule, tuple[str, str]] = {
    _Rule.INVALID_ARGS: (
        "args_mapping names an argument the wrapper does not have",
        "The wrapper's args_mapping maps an argument its signature does not have, so its callers are not migrated.",
    ),
    _Rule.MISCONFIGURED_TARGET: (
        "target contradicts the rest of the configuration",
        "target=False, TargetMode.NOTIFY with a mapping it ignores, or TargetMode.ARGS_REMAP without args_mapping.",
    ),
    _Rule.INNER_ORDER_PROPERTY: (
        "inner-order @property @deprecated leaves setter and deleter unprotected",
        "Only the getter warns; put @deprecated(...) above @property so every accessor warns.",
    ),
    _Rule.IDENTITY_MAPPING: (
        "args_mapping maps an argument to itself",
        "An args_mapping entry whose key equals its value has no effect.",
    ),
    _Rule.NO_EFFECT: (
        "deprecated wrapper has no effect on its callers",
        "An empty mapping, a self-reference or identity-only mappings leave callers unchanged.",
    ),
    _Rule.POSITIONAL_ONLY_MAPPING: (
        "args_mapping targets a positional-only constructor parameter",
        "The proxy forwards such entries positionally or falls back to setattr.",
    ),
    _Rule.CHAIN: (
        "deprecated wrapper forwards to another deprecated wrapper",
        "A deprecation chain; point the wrapper at the final, non-deprecated target.",
    ),
    _Rule.EXPIRED: (
        "deprecated wrapper is past its remove_in version",
        "The package version reached the wrapper's remove_in; delete the deprecated code.",
    ),
    _Rule.MIN_GRACE: (
        "deprecation window is shorter than the policy's minimum grace",
        "remove_in is too close to deprecated_in, or not one clean bump of a single version component.",
    ),
    _Rule.MESSAGE_REQUIRED: (
        "deprecation warns without naming a replacement",
        "Configure a target, a mapping or a custom message_template so callers learn what to migrate to.",
    ),
}


def _rule_name(rule: _Rule) -> str:
    """Return the PascalCase name of *rule* for the SARIF ``rules`` array.

    Examples:
        >>> _rule_name(_Rule.INNER_ORDER_PROPERTY)
        'InnerOrderProperty'

    """
    return "".join(part.capitalize() for part in rule.value.split("-"))


def _artifact_location(source: PurePath, root: PurePath) -> dict[str, str]:
    """Return the SARIF ``artifactLocation`` of an absolute *source* file: relative to *root* when under it.

    Examples:
        >>> from pathlib import PurePosixPath
        >>> _artifact_location(PurePosixPath("/repo/src/pkg.py"), PurePosixPath("/repo"))
        {'uri': 'src/pkg.py', 'uriBaseId': '%SRCROOT%'}
        >>> _artifact_location(PurePosixPath("/repo/my src/pkg#1.py"), PurePosixPath("/repo"))
        {'uri': 'my%20src/pkg%231.py', 'uriBaseId': '%SRCROOT%'}
        >>> _artifact_location(PurePosixPath("/elsewhere/pkg.py"), PurePosixPath("/repo"))
        {'uri': 'file:///elsewhere/pkg.py'}

    """
    try:
        # a URI reference: a space, `#`, `?` or `%` in a directory name is percent-encoded, as `as_uri()` does below
        return {"uri": quote(source.relative_to(root).as_posix(), safe="/"), "uriBaseId": _SRCROOT}
    except ValueError:
        return {"uri": source.as_uri()}


def _sarif_result(finding: dict[str, Any], source: Optional[str], rule_index: int, root: PurePath) -> dict[str, Any]:
    """Return the SARIF result of one report finding; *source* is its wrapper's absolute file, if any."""
    result: dict[str, Any] = {
        "ruleId": finding["rule"],
        "ruleIndex": rule_index,
        "level": finding["level"],
        "message": {"text": finding["message"]},
        "partialFingerprints": {_FINGERPRINT: finding["id"]},
        "properties": {
            "check": finding["check"],
            "module": finding["subject"]["module"],
            "symbol": finding["subject"]["symbol"],
            "api_type": finding["subject"]["api_type"],
            "deprecated_in": finding["deprecated_in"],
            "remove_in": finding["remove_in"],
            "path": finding["path"],
        },
    }
    if source is not None:
        line = (finding["location"] or {}).get("line") or 1
        result["locations"] = [
            {
                "physicalLocation": {
                    "artifactLocation": _artifact_location(PurePath(source), root),
                    "region": {"startLine": line},
                }
            }
        ]
    return result


def _is_outside(result: dict[str, Any]) -> bool:
    """Tell whether a SARIF *result* is located outside the root: by an absolute URI without the ``%SRCROOT%`` base.

    Examples:
        >>> inside = {"physicalLocation": {"artifactLocation": {"uri": "src/a.py", "uriBaseId": "%SRCROOT%"}}}
        >>> outside = {"physicalLocation": {"artifactLocation": {"uri": "file:///elsewhere/pkg.py"}}}
        >>> _is_outside({"locations": [inside]}), _is_outside({"locations": [outside]}), _is_outside({})
        (False, True, False)

    """
    locations = result.get("locations")
    if not locations:  # an unlocated result is counted apart, in `unlocated`
        return False
    return "uriBaseId" not in locations[0]["physicalLocation"]["artifactLocation"]


def _print_advisories(total: int, *, unlocated: int, outside: int, root: Path) -> None:
    """Print on stderr what keeps GitHub code scanning from showing the run's results; the file is written as built.

    Args:
        total: Number of results in the run.
        unlocated: Results without a location, which code scanning does not display.
        outside: Results located outside *root*, by an absolute ``file`` URI no repository file matches.
        root: The repository root relative locations resolve against.

    """
    if outside:
        print(
            f"pydeprecate: {outside} SARIF result(s) lie outside the repository root {root}, so their absolute file URI"
            " matches no repository file in GitHub code scanning; scan the package in the checkout, not an installed"
            " copy.",
            file=sys.stderr,
        )
    if total > _RESULTS_LIMIT:
        print(
            f"pydeprecate: the SARIF run holds {total} results, more than the {_RESULTS_LIMIT} GitHub code"
            " scanning accepts; narrow the scan or accept known findings with --ignore or --baseline.",
            file=sys.stderr,
        )
    if total and unlocated == total:
        # with every result unlocated, an upload shows nothing while the gate may still fail: say so instead
        print(
            f"pydeprecate: none of the run's {total} SARIF result(s) has a source location; GitHub code scanning"
            " displays only located results, so it shows none of them.",
            file=sys.stderr,
        )


def _build_sarif(record: _RunRecord, exit_code: int, root: Optional[Path] = None) -> dict[str, Any]:
    """Return the SARIF 2.1.0 document of the run.

    Args:
        record: The run record the subcommand filled.
        exit_code: The process exit code (0 or 1).
        root: Directory relative locations resolve against; by default the repository root of the working directory
            (:func:`~deprecate._cli.report._location_root`), the base of the ``--report-json`` locations too.

    """
    base = root if root is not None else _location_root()
    command = record.command or ""
    # a rule's default level is its highest in the run: `--gate` raises a rule to an error for the paths it names
    gates = frozenset().union(*record.gates_by_path.values())
    # every rule the subcommand evaluates, in `_Rule` order, with its level there; the others have no level
    rule_levels = {rule: level for rule in _Rule if (level := _level(rule, command, gates)) is not None}
    rules = list(rule_levels)
    rule_index = {rule.value: index for index, rule in enumerate(rules)}
    findings = _report_findings_with_sources(record, base)
    results = [_sarif_result(finding, source, rule_index[finding["rule"]], base) for finding, source in findings]
    unlocated = sum(1 for _, source in findings if source is None)
    outside = sum(1 for result in results if _is_outside(result))
    mode = _run_mode(record)
    _print_advisories(len(results), unlocated=unlocated, outside=outside, root=base)
    return {
        "$schema": _SARIF_SCHEMA,
        "version": _SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "pyDeprecate",
                        "version": __about__.__version__,
                        "informationUri": _INFORMATION_URI,
                        "rules": [
                            {
                                "id": rule.value,
                                "name": _rule_name(rule),
                                "shortDescription": {"text": _RULE_TEXT[rule][0]},
                                "fullDescription": {"text": _RULE_TEXT[rule][1]},
                                "defaultConfiguration": {"level": rule_levels[rule].value},
                            }
                            for rule in rules
                        ],
                    }
                },
                "originalUriBaseIds": {_SRCROOT: {"uri": base.as_uri() + "/"}},
                "results": results,
                "invocations": [
                    {
                        "executionSuccessful": True,
                        "exitCode": exit_code,
                        "toolExecutionNotifications": [
                            {"level": "warning", "message": {"text": gap.detail}, "descriptor": {"id": gap.kind.value}}
                            for gap in record.gaps
                        ],
                    }
                ],
                "properties": {
                    "suppressed": len(_report_suppressed(record, base)),
                    "unlocated": unlocated,
                    "mode": mode,
                    "paths": [scanned.path for scanned in record.scanned],
                },
            }
        ],
    }


def _write_sarif(path: str, record: _RunRecord, exit_code: int) -> None:
    """Write the run's ``--report-sarif`` file; a failure only prints an advisory and never changes the exit code.

    Args:
        path: File to write; missing parent directories are created.
        record: The run record the subcommand filled.
        exit_code: The process exit code (0 or 1; the file is never written for 2 or 3).

    """
    _write_json_file(path, lambda: _build_sarif(record, exit_code), "SARIF report")
