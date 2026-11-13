"""Accepted findings for the CLI: the ``ignore`` list and the ``--baseline`` report that suppress findings.

An ignore entry names findings by pattern: ``<module-glob>:<symbol-glob>`` for every rule, or ``<rule>:<module-
glob>:<symbol-glob>`` for one rule (``*`` for any). A baseline is a ``--report-json`` file with a ``findings`` array
(pyDeprecate 0.16 or later); the ids of its active findings, and of the suppressed findings the baseline itself
suppressed, are accepted as they are. A finding matched by both counts as ignored. Suppressed findings are never
printed, counted or gated on; the report lists them under ``suppressed``.

"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Optional

from deprecate.audit._findings import _Finding, _Rule

#: ``suppressed_by.kind`` of a finding an ignore entry matched.
_IGNORE = "ignore"
#: ``suppressed_by.kind`` of a finding the baseline holds.
_BASELINE = "baseline"
#: The rule part of an ignore entry that matches every rule.
_ANY_RULE = "*"
#: Oldest ``--report-json`` layout version a baseline may have. Finding ids were added to layout 1 without a bump, so a
#: baseline also needs the ``findings`` array, which a report of pyDeprecate 0.15 lacks.
_BASELINE_MIN_SCHEMA = 1


@dataclass(frozen=True)
class _IgnoreEntry:
    """One parsed ignore entry: the rule it applies to (or ``*``) and the module and symbol globs."""

    text: str
    rule: str
    module: str
    symbol: str

    def matches(self, finding: _Finding) -> bool:
        """Tell whether the entry names *finding*; each component is a case-sensitive ``fnmatch`` glob."""
        return (
            fnmatchcase(finding.rule.value, self.rule)
            and fnmatchcase(finding.module, self.module)
            and fnmatchcase(finding.symbol, self.symbol)
        )


def _parse_ignore_entry(text: str) -> _IgnoreEntry:
    """Parse one ignore entry.

    Raises:
        ValueError: For an entry that is not ``module:symbol`` or ``rule:module:symbol``, has an empty module part, or
            names an unknown rule.

    Examples:
        >>> _parse_ignore_entry("pkg.legacy:old_api")
        _IgnoreEntry(text='pkg.legacy:old_api', rule='*', module='pkg.legacy', symbol='old_api')
        >>> _parse_ignore_entry("expired:pkg.oldmod:").symbol
        ''

    """
    parts = text.split(":")
    if len(parts) == 2:
        rule, (module, symbol) = _ANY_RULE, parts
    elif len(parts) == 3:
        rule, module, symbol = parts
    else:
        raise ValueError("expected `module:symbol` or `rule:module:symbol`")
    if not module:
        raise ValueError("the module part is empty")
    known = [item.value for item in _Rule]
    if rule != _ANY_RULE and rule not in known:
        raise ValueError(f"unknown rule `{rule}`; expected `*` or one of {', '.join(f'`{name}`' for name in known)}")
    return _IgnoreEntry(text, rule, module, symbol)


def _parse_ignore(entries: list[str], source: str) -> list[_IgnoreEntry]:
    """Parse every ignore entry.

    Raises:
        ValueError: Naming the first bad entry and *source* (the ``--ignore`` flag or the ``pyproject.toml``).

    """
    parsed = []
    for entry in entries:
        try:
            parsed.append(_parse_ignore_entry(entry))
        except ValueError as err:
            raise ValueError(f"Invalid ignore entry `{entry}` from {source}: {err}.") from err
    return parsed


def _read_baseline(path: str, regenerate: str) -> object:
    """Return the parsed JSON document of the baseline file at *path*.

    Raises:
        ValueError: Saying the file cannot be read when it is missing or unreadable (with a hint to create a missing
            one), or naming *regenerate* when it is not JSON (a document nested too deeply to parse included).

    """
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as err:
        hint = "; create it with --report-json" if isinstance(err, FileNotFoundError) else ""
        raise ValueError(f"Baseline {path} cannot be read ({err.strerror or err}){hint}") from err
    except (ValueError, RecursionError) as err:
        raise ValueError(f"{regenerate} ({err})") from err


def _baseline_records(data: dict[str, Any], key: str, regenerate: str) -> list[dict[str, Any]]:
    """Return the finding records of the report's *key* array, each checked to be an object with a string ``id``.

    An absent array is empty. One that is not a list, or holds an entry that is not an object with a string ``id``, is
    refused, so a corrupted or hand-edited baseline never loses an accepted id silently.

    Raises:
        ValueError: Naming *regenerate* and the first malformed part.

    """
    records = data.get(key, [])
    if not isinstance(records, list):
        raise ValueError(f"{regenerate} (`{key}` is not a list)")
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise ValueError(f"{regenerate} (`{key}[{index}]` is not a finding with a string `id`)")
    return records


def _load_baseline(path: str) -> frozenset[str]:
    """Return the finding ids of the baseline report at *path*: its active findings and those its baseline suppressed.

    An id of the ``suppressed`` array counts only when its ``suppressed_by.kind`` is ``baseline``, so a baseline
    regenerated from a run that already used one keeps the findings that baseline suppressed. A finding an ignore entry
    suppressed is left out: it stays accepted only while the ignore entry does, never through a regenerated baseline.

    Raises:
        ValueError: When the file is missing or unreadable, or is not JSON; when it is not a pyDeprecate report (no
            integer ``schema`` of at least 1) or is one without a ``findings`` array (written before pyDeprecate 0.16);
            when ``findings`` or ``suppressed`` (which may be absent) is not a list, or holds an entry that is not an
            object with a string ``id``.

    """
    regenerate = f"Baseline {path} is not a pyDeprecate report; regenerate it with --report-json"
    data = _read_baseline(path, regenerate)
    if not isinstance(data, dict):
        raise ValueError(regenerate)
    schema = data.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < _BASELINE_MIN_SCHEMA:
        raise ValueError(regenerate)
    if "findings" not in data:
        raise ValueError(
            f"Baseline {path} has no `findings` list (a report written before pyDeprecate 0.16); regenerate it with"
            " --report-json"
        )
    ids = {record["id"] for record in _baseline_records(data, "findings", regenerate)}
    for record in _baseline_records(data, "suppressed", regenerate):
        suppressed_by = record.get("suppressed_by")
        if isinstance(suppressed_by, dict) and suppressed_by.get("kind") == _BASELINE:
            ids.add(record["id"])
    return frozenset(ids)


@dataclass
class _Suppressor:
    """The ignore entries and baseline ids of one run, and which of them matched a finding."""

    ignore: list[_IgnoreEntry] = field(default_factory=list)
    baseline: frozenset[str] = frozenset()
    used_entries: set[str] = field(default_factory=set)
    used_ids: set[str] = field(default_factory=set)

    def match(self, finding: _Finding) -> Optional[tuple[str, str]]:
        """Return how *finding* is suppressed — ``(kind, entry)`` — or ``None`` when it stays active.

        Ignore entries are checked first, so a finding both entries match counts as ignored; its baseline id still
        counts as matched, so it is never reported as stale.

        """
        if finding.id in self.baseline:
            self.used_ids.add(finding.id)
        for entry in self.ignore:
            if entry.matches(finding):
                self.used_entries.add(entry.text)
                return _IGNORE, entry.text
        if finding.id in self.baseline:
            return _BASELINE, finding.id
        return None

    def unmatched_ignore(self) -> int:
        """Return how many ignore entries matched no finding of the run."""
        return len({entry.text for entry in self.ignore} - self.used_entries)

    def unmatched_baseline(self, evaluated_rules: set[str], audited: Optional[Callable[[str], bool]] = None) -> int:
        """Return how many baseline ids the run expected to match matched no finding — the stale entries."""
        return len(self.evaluated_baseline(evaluated_rules, audited) - self.used_ids)

    def evaluated_baseline(
        self, evaluated_rules: set[str], audited: Optional[Callable[[str], bool]] = None
    ) -> set[str]:
        """Return the baseline ids a finding was expected to match: of a rule the run evaluated, in a module it audited.

        An id (``rule:module:symbol``) of a module the run did not audit — outside the scanned package, excluded, or
        one that failed to import — is not expected, so a baseline shared by several packages or a module that could
        not be imported never reads as stale. Without *audited* every module counts as audited.

        Examples:
            >>> suppressor = _Suppressor(baseline=frozenset({"expired:pkg.a:f", "expired:other:g", "chain:pkg.a:h"}))
            >>> sorted(suppressor.evaluated_baseline({"expired"}, lambda module: module.startswith("pkg")))
            ['expired:pkg.a:f']

        """
        expected = set()
        for item in self.baseline:
            rule, _, rest = item.partition(":")
            if rule in evaluated_rules and (audited is None or audited(rest.split(":", 1)[0])):
                expected.add(item)
        return expected
