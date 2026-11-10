"""Unit tests for :mod:`deprecate._cli.suppress`: ignore entries, the baseline file and what they suppress."""

import json
from pathlib import Path

import pytest

from deprecate._cli.suppress import _load_baseline, _parse_ignore, _parse_ignore_entry, _Suppressor
from deprecate.audit import DeprecationWrapperInfo
from deprecate.audit._findings import _Check, _Finding, _make_finding, _Rule


def _finding(rule: _Rule, module: str = "pkg.mod", symbol: str = "old_fn") -> _Finding:
    """Build a finding of *rule* for ``module.symbol``."""
    return _make_finding(DeprecationWrapperInfo(module=module, function=symbol), rule, _Check.CHECK, "message")


def _write_report(path: Path, payload: object) -> str:
    """Write *payload* as JSON to *path* and return the path as a string."""
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


class TestIgnoreEntryGrammar:
    """An ignore entry is ``module:symbol`` or ``rule:module:symbol``; anything else is refused."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("pkg.legacy:old_api", ("*", "pkg.legacy", "old_api"), id="any-rule"),
            pytest.param("min-grace:pkg.mod:Thing.meth", ("min-grace", "pkg.mod", "Thing.meth"), id="one-rule"),
            pytest.param("expired:pkg.oldmod:", ("expired", "pkg.oldmod", ""), id="module-subject"),
            pytest.param("*:pkg.compat.*:*", ("*", "pkg.compat.*", "*"), id="globs"),
        ],
    )
    def test_valid_entry(self, text: str, expected: tuple[str, str, str]) -> None:
        """Each documented spelling parses into its rule, module glob and symbol glob.

        Teams accept a whole legacy symbol, one rule of a method, a deprecated module or a whole subpackage; each must
        read as written.

        """
        entry = _parse_ignore_entry(text)
        assert (entry.rule, entry.module, entry.symbol) == expected

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            pytest.param("pkg.legacy", "expected `module:symbol` or `rule:module:symbol`", id="no-colon"),
            pytest.param("a:b:c:d", "expected `module:symbol` or `rule:module:symbol`", id="too-many-colons"),
            pytest.param(":old_api", "the module part is empty", id="empty-module"),
            pytest.param("expird:pkg:old", "unknown rule `expird`", id="unknown-rule"),
        ],
    )
    def test_invalid_entry(self, text: str, message: str) -> None:
        """A malformed entry is refused with the reason, never silently matching nothing.

        A typo in a rule slug would otherwise leave the finding active while the author believes it is accepted.

        """
        with pytest.raises(ValueError, match=message):
            _parse_ignore_entry(text)

    def test_error_names_entry_and_source(self) -> None:
        """The error for a list names the offending entry and where the list came from.

        An entry from ``pyproject.toml`` and one from ``--ignore`` need different fixes, so the message says which.

        """
        with pytest.raises(ValueError, match=r"Invalid ignore entry `bad` from `--ignore`: "):
            _parse_ignore(["pkg:ok", "bad"], "`--ignore`")


class TestSuppressorMatch:
    """Which findings an ignore entry or the baseline suppresses, and how each match is reported."""

    @pytest.mark.parametrize(
        ("entry", "finding", "suppressed"),
        [
            pytest.param("pkg.mod:old_fn", _finding(_Rule.EXPIRED), True, id="any-rule-exact"),
            pytest.param("expired:pkg.mod:old_fn", _finding(_Rule.MIN_GRACE), False, id="other-rule"),
            pytest.param("*:pkg.*:*", _finding(_Rule.CHAIN), True, id="glob-crosses-dots"),
            pytest.param("expired:pkg.oldmod:", _finding(_Rule.EXPIRED, "pkg.oldmod", ""), True, id="module-subject"),
            pytest.param("pkg.mod:Old_fn", _finding(_Rule.EXPIRED), False, id="case-sensitive"),
        ],
    )
    def test_ignore_entry(self, entry: str, finding: _Finding, suppressed: bool) -> None:
        """An ignore entry matches the rule, module and symbol of a finding by case-sensitive glob.

        ``*`` crosses dots as in ``exclude``, and an empty symbol names a deprecated module itself.

        """
        suppressor = _Suppressor(ignore=_parse_ignore([entry], "test"))
        assert (suppressor.match(finding) is not None) is suppressed

    def test_ignore_wins_over_baseline(self) -> None:
        """A finding both an ignore entry and the baseline name counts as ignored.

        The ignore list is the deliberate, reviewed decision; the baseline only records a snapshot.

        """
        finding = _finding(_Rule.EXPIRED)
        suppressor = _Suppressor(ignore=_parse_ignore(["pkg.mod:old_fn"], "test"), baseline=frozenset({finding.id}))
        assert suppressor.match(finding) == ("ignore", "pkg.mod:old_fn")

    def test_ignored_baseline_id_is_not_stale(self) -> None:
        """A baseline id whose finding an ignore entry suppressed still counts as matched, never as stale.

        A team that ignores a finding it also baselined must not be told to regenerate a baseline that is still right.

        """
        finding = _finding(_Rule.EXPIRED)
        suppressor = _Suppressor(ignore=_parse_ignore(["pkg.mod:old_fn"], "test"), baseline=frozenset({finding.id}))
        suppressor.match(finding)
        assert suppressor.unmatched_baseline({"expired"}) == 0

    @pytest.mark.parametrize(
        ("symbol", "expected"),
        [
            pytest.param("old_fn", ("baseline", "expired:pkg.mod:old_fn"), id="same-id"),
            pytest.param("renamed_fn", None, id="renamed-symbol"),
        ],
    )
    def test_baseline_matches_exact_id(self, symbol: str, expected: object) -> None:
        """The baseline suppresses exactly the ids it holds; a renamed symbol is a new finding.

        Ids carry no line or version, so moved code stays accepted, while renaming the wrapper is a new problem.

        """
        suppressor = _Suppressor(baseline=frozenset({"expired:pkg.mod:old_fn"}))
        assert suppressor.match(_finding(_Rule.EXPIRED, symbol=symbol)) == expected

    def test_unmatched_entries_are_counted(self) -> None:
        """Ignore entries and baseline ids that matched nothing are counted, baseline ids only for evaluated rules.

        A stale baseline entry for an expiry finding is reported only when the expiry check ran; an unused ignore
        entry is counted for the report.

        """
        suppressor = _Suppressor(
            ignore=_parse_ignore(["pkg.mod:old_fn", "pkg.other:gone"], "test"),
            baseline=frozenset({"expired:pkg.x:a", "chain:pkg.y:b"}),
        )
        suppressor.match(_finding(_Rule.EXPIRED))
        assert suppressor.unmatched_ignore() == 1
        assert suppressor.unmatched_baseline({"expired"}) == 1


class TestLoadBaseline:
    """A baseline is a schema-2 report; its ids come from both the ``findings`` and the ``suppressed`` arrays."""

    def test_reads_ids_from_findings_and_suppressed(self, tmp_path: Path) -> None:
        """Ids of active and suppressed findings are both accepted.

        A baseline regenerated from a run that already used it lists the accepted findings under ``suppressed``; they
        must stay accepted.

        """
        path = _write_report(
            tmp_path / "baseline.json",
            {"schema": 2, "findings": [{"id": "expired:pkg:a"}], "suppressed": [{"id": "chain:pkg:b"}]},
        )
        assert _load_baseline(path) == frozenset({"expired:pkg:a", "chain:pkg:b"})

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"schema": 1, "table": None, "gaps": []}, id="schema-1"),
            pytest.param({"schema": "2", "findings": []}, id="schema-not-int"),
            pytest.param({"schema": 2}, id="no-findings"),
            pytest.param([], id="not-an-object"),
        ],
    )
    def test_rejects_non_schema_two_report(self, tmp_path: Path, payload: object) -> None:
        """A file that is not a schema-2 report is refused with the regenerate hint.

        A schema-1 report has no ids, so it could never match a finding; accepting it would suppress nothing silently.

        """
        path = _write_report(tmp_path / "baseline.json", payload)
        with pytest.raises(ValueError, match="is not a pyDeprecate schema-2 report; regenerate it with --report-json"):
            _load_baseline(path)

    def test_rejects_missing_file(self, tmp_path: Path) -> None:
        """A baseline path that names no file is refused, naming the regenerate hint.

        A typo in the path must stop the run instead of running with an empty baseline.

        """
        with pytest.raises(ValueError, match="regenerate it with --report-json"):
            _load_baseline(str(tmp_path / "missing.json"))

    def test_rejects_malformed_json(self, tmp_path: Path) -> None:
        """A baseline file that is not JSON is refused, naming the regenerate hint.

        A truncated file left by an interrupted CI step must not read as an empty baseline.

        """
        path = tmp_path / "baseline.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="regenerate it with --report-json"):
            _load_baseline(str(path))

    def test_later_schema_is_accepted(self, tmp_path: Path) -> None:
        """A report of a later layout version is read, since layout bumps keep the ``findings`` ids.

        A baseline written by a newer pyDeprecate must keep working for an older one in the same repository.

        """
        path = _write_report(tmp_path / "baseline.json", {"schema": 3, "findings": [{"id": "expired:pkg:a"}]})
        assert _load_baseline(path) == frozenset({"expired:pkg:a"})
