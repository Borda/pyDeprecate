"""Accepted findings for the CLI: the ``ignore`` list and the ``--baseline`` report that suppress findings.

An ignore entry names findings by pattern: ``<module-glob>:<symbol-glob>`` for every rule, or ``<rule>:<module-
glob>:<symbol-glob>`` for one rule (``*`` for any). A baseline is a ``--report-json`` file of layout version 2 or later;
its finding ids (active and suppressed) are accepted as they are. A finding matched by both counts as ignored.
Suppressed findings are never printed, counted or gated on; the report lists them under ``suppressed``.

"""

import json
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Optional

from deprecate.audit._findings import _Finding, _Rule

#: ``suppressed_by.kind`` of a finding an ignore entry matched.
_IGNORE = "ignore"
#: ``suppressed_by.kind`` of a finding the baseline holds.
_BASELINE = "baseline"
#: The rule part of an ignore entry that matches every rule.
_ANY_RULE = "*"
#: Oldest ``--report-json`` layout version a baseline may have: the first with finding ids.
_BASELINE_MIN_SCHEMA = 2


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


def _load_baseline(path: str) -> frozenset[str]:
    """Return the finding ids of the baseline report at *path*, from both its ``findings`` and ``suppressed`` arrays.

    Ids of the ``suppressed`` array count too, so a baseline regenerated from a run that already used it keeps the
    findings it suppressed.

    Raises:
        ValueError: When the file is missing or unreadable, is not JSON, or is not a report of layout version 2 or later
            with a ``findings`` array.

    """
    regenerate = (
        f"Baseline {path} is not a pyDeprecate schema-{_BASELINE_MIN_SCHEMA} report; regenerate it with --report-json"
    )
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise ValueError(f"{regenerate} ({err})") from err
    schema = data.get("schema") if isinstance(data, dict) else None
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < _BASELINE_MIN_SCHEMA:
        raise ValueError(regenerate)
    if not isinstance(data.get("findings"), list):
        raise ValueError(regenerate)
    ids = set()
    for key in ("findings", "suppressed"):
        for item in data.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                ids.add(item["id"])
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

        Ignore entries are checked first, so a finding both entries match counts as ignored.

        """
        for entry in self.ignore:
            if entry.matches(finding):
                self.used_entries.add(entry.text)
                return _IGNORE, entry.text
        if finding.id in self.baseline:
            self.used_ids.add(finding.id)
            return _BASELINE, finding.id
        return None

    def unmatched_ignore(self) -> int:
        """Return how many ignore entries matched no finding of the run."""
        return len({entry.text for entry in self.ignore} - self.used_entries)

    def unmatched_baseline(self, evaluated_rules: set[str]) -> int:
        """Return how many baseline ids of a rule the run evaluated matched no finding — the stale entries."""
        return sum(1 for item in self.baseline - self.used_ids if item.split(":", 1)[0] in evaluated_rules)
