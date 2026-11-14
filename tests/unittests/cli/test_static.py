"""CLI checks, gap output and import-scan parity for static rows."""

import json
import os
import sys
import warnings
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from deprecate._cli import cli
from deprecate._cli.report import _record_run
from deprecate._cli.static.checks import _report_unresolved, _static_policy_findings
from deprecate._gaps import _GapKind
from deprecate._static.checks import _resolved
from deprecate._static.consts import _GUIDANCE_KEYWORDS
from deprecate.audit import DeprecationWrapperInfo, find_deprecation_wrappers
from deprecate.audit._lifecycle import _expiry_findings
from deprecate.audit._policy import _build_policy_spec, _policy_findings
from tests.unittests.static._helpers import (
    _DEP,
    _EXPIRED,
    _NAMESPACE,
    _PKG,
    _ROOT,
    _line_of,
    _read_module,
    _read_package,
    _refusing,
    _rows,
    _write,
    _write_namespace_portion,
)
from tests.unittests.static._helpers import probe_package as probe_package

_GUIDANCE_ONLY = _build_policy_spec(None, True)


_IMPORTED_TARGET_USE = (
    "from deprecate import deprecated\nfrom .consts import REPL\n\n"
    "@deprecated(target=REPL, deprecated_in='1.0', remove_in='2.0')\ndef old(x):\n    return x\n"
)


def _expiry_ids(wrappers: list[DeprecationWrapperInfo]) -> list[str]:
    """Return the ids of the ``expired`` findings the wrappers whose removal version resolved produce at ``99.0``."""
    return [f.id for f in _expiry_findings(_resolved(wrappers, ("remove_in",)), "99.0")]


def _run_static_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    path: str,
    *options: str,
    command: str = "expiry",
) -> tuple[list[str], list[dict[str, Any]]]:
    """Run the real CLI and return its stderr lines and serialized gaps, preserving their order."""
    report = tmp_path / "gaps.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pydeprecate",
            command,
            path,
            "--static",
            *(["--version", "99.0"] if command == "expiry" else ["--min-grace", "0.3", "--message-required"]),
            "--exit-zero",
            "--report-json",
            str(report),
            *options,
        ],
    )
    with pytest.raises(SystemExit) as exited:
        cli()
    assert exited.value.code == 0
    return capsys.readouterr().err.splitlines(), json.loads(report.read_text())["gaps"]


class TestStaticGapOutput:
    """Pin the CLI gap text, ordering and JSON schema before the reader's gap seam changes."""

    @pytest.mark.parametrize("command", ["expiry", "policy"])
    def test_collection_lines(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
    ) -> None:
        """Structural uncertainty precedes the per-wrapper settings the selected check cannot resolve.

        The existing static-forms collection mixes unsupported factory use with unsettled removal dates and a
        computed target. Locate it as a top-level module because static CLI lookup refuses dotted module names.
        """
        monkeypatch.chdir(_ROOT / "tests" / "collections")
        monkeypatch.syspath_prepend(str(_ROOT / "tests" / "collections"))
        lines, _ = _run_static_cli(tmp_path, monkeypatch, capsys, "static_forms", command=command)
        expected = [
            "pydeprecate gap: not statically resolvable: static_forms.py:237: unrecognized use of `deprecated`",
            "pydeprecate gap: not statically resolvable: static_forms.py:213: "
            "`remove_in` is `REMOVAL`, not a literal (Callable `static_forms.ambiguous_removal` not checked)",
            "pydeprecate gap: not statically resolvable: static_forms.py:225: "
            "arguments come from `**dict(deprecated_in='1.0', remove_in='2.0')` "
            "(Callable `static_forms.called_splat` not checked)",
            "pydeprecate gap: not statically resolvable: static_forms.py:219: "
            "`remove_in` is `f'{_MAJOR}.0'`, not a literal (Callable `static_forms.formatted_removal` not checked)",
        ]
        extra = {
            "expiry": [],
            "policy": [
                "pydeprecate gap: not statically resolvable: static_forms.py:231: "
                "`target` is `_REPLACEMENTS['double']`, not a literal or a name "
                "(Callable `static_forms.looked_up_target` not checked)"
            ],
        }
        assert lines == expected + extra[command]

    def test_collection_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The report preserves structural and setting gaps in stderr order without serializing internal subjects.

        Consumers of the existing static-forms expiry report receive a path and remedy for every gap, including
        structural gaps with no wrapper subject and settings gaps that internally identify a wrapper.
        """
        monkeypatch.chdir(_ROOT / "tests" / "collections")
        monkeypatch.syspath_prepend(str(_ROOT / "tests" / "collections"))
        _, gaps = _run_static_cli(tmp_path, monkeypatch, capsys, "static_forms")
        assert gaps == [
            {
                "kind": "not statically resolvable",
                "detail": "static_forms.py:237: unrecognized use of `deprecated`",
                "hint": "run without `--static` for a full audit, or use literal version strings",
                "path": "static_forms",
            },
            {
                "kind": "not statically resolvable",
                "detail": "static_forms.py:213: `remove_in` is `REMOVAL`, not a literal "
                "(Callable `static_forms.ambiguous_removal` not checked)",
                "hint": "run without `--static` for a full audit, or use literal version strings",
                "path": "static_forms",
            },
            {
                "kind": "not statically resolvable",
                "detail": "static_forms.py:225: arguments come from "
                "`**dict(deprecated_in='1.0', remove_in='2.0')` (Callable `static_forms.called_splat` not checked)",
                "hint": "run without `--static` for a full audit, or use literal version strings",
                "path": "static_forms",
            },
            {
                "kind": "not statically resolvable",
                "detail": "static_forms.py:219: `remove_in` is `f'{_MAJOR}.0'`, not a literal "
                "(Callable `static_forms.formatted_removal` not checked)",
                "hint": "run without `--static` for a full audit, or use literal version strings",
                "path": "static_forms",
            },
        ]

    def test_policy_collection_has_no_gaps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Literal deprecations produce findings without introducing audit gaps.

        The existing policy collection is the settled counterpart to static_forms; pin its empty gap output as well
        so the seam refactor cannot accidentally introduce diagnostics on a fully readable collection.
        """
        monkeypatch.syspath_prepend(str(_ROOT / "tests" / "collections"))
        lines, gaps = _run_static_cli(tmp_path, monkeypatch, capsys, "policy", command="policy")
        assert lines == []
        assert gaps == []

    @pytest.mark.parametrize(
        ("lister", "blocked", "expected"),
        [
            pytest.param(
                "scandir",
                "",
                "pydeprecate gap: module not audited: staticprobe.*: submodules of staticprobe not listed: "
                "listing denied",
                id="package-root",
            ),
            pytest.param(
                "listdir",
                "sub",
                "pydeprecate gap: module not audited: staticprobe.sub: submodules of staticprobe/sub not listed: "
                "listing denied",
                id="subpackage",
            ),
        ],
    )
    def test_directory_listing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        lister: str,
        blocked: str,
        expected: str,
    ) -> None:
        """An unlistable root or child directory reports the exact scope left unaudited.

        Reuse the existing discovery fixture and OS listing failure boundary through the CLI, rather than merely
        checking a substring from the reader. The report must retain the same gap and scan path.
        """
        pkg = _write(tmp_path, {"__init__.py": "", "sub/__init__.py": "", "m.py": ""})
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            os, lister, _refusing(getattr(os, lister), pkg / blocked, PermissionError("listing denied"))
        )
        lines, gaps = _run_static_cli(tmp_path, monkeypatch, capsys, "staticprobe")
        assert lines == [expected.replace("/", os.sep)]
        assert gaps[0]["path"] == "staticprobe"

    @pytest.mark.parametrize("blocked", [False, True])
    def test_namespace_portions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], blocked: bool
    ) -> None:
        """A namespace reports only the portion whose directory cannot be walked.

        Reuse the two-portion discovery fixture in import-path order. A readable namespace has no root gap;
        refusing the first portion must produce exactly one gap while the other portion remains readable.
        """
        _write_namespace_portion(tmp_path / "first", "alpha")
        _write_namespace_portion(tmp_path / "second", "beta")
        monkeypatch.syspath_prepend(str(tmp_path / "first"))
        monkeypatch.syspath_prepend(str(tmp_path / "second"))
        monkeypatch.chdir(tmp_path)
        refused = tmp_path / "second" / _NAMESPACE if blocked else tmp_path / "absent"
        monkeypatch.setattr(os, "scandir", _refusing(os.scandir, refused, PermissionError("listing denied")))
        lines, _ = _run_static_cli(tmp_path, monkeypatch, capsys, _NAMESPACE)
        expected = {
            False: [],
            True: [
                "pydeprecate gap: module not audited: staticnamespaceprobe.*: submodules of "
                "second/staticnamespaceprobe not listed: listing denied"
            ],
        }
        assert lines == [line.replace("/", os.sep) for line in expected[blocked]]

    def test_plain_directory_nested_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A plain-directory scan announces the nested Python files it leaves out.

        The existing discovery fixture has one readable top-level module and one nested module. Pin the complete
        advisory even though the top-level scan succeeds, so discovery cannot silently drop it during the move.
        """
        (tmp_path / "nested").mkdir()
        (tmp_path / "nested" / "inner.py").write_text("X = 1\n")
        (tmp_path / "mod.py").write_text("X = 1\n")
        lines, _ = _run_static_cli(tmp_path, monkeypatch, capsys, str(tmp_path))
        assert lines == [
            "pydeprecate gap: nested files not audited: Skipping nested Python files in plain directory scan. "
            "Use an importable package layout with '__init__.py' files, "
            "or scan an importable module/package path instead."
        ]

    def test_reader_and_rows_interleave(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Parse failures encountered through imports precede structural and row-resolution gaps.

        A minimal package combines a broken imported module, an unsupported factory call, an external base and a
        re-export cycle. A null byte makes the real parser failure independent of Python's syntax-error offsets.
        These tiny sources supplement the existing fixtures to pin the cross-module emission order in one run.
        """
        _write(
            tmp_path,
            {
                "__init__.py": "from .broken import thing\nfrom .a import loop\n"
                "from external_package import Base\nfrom functools import partial\nfrom deprecate import deprecated\n"
                "class Child(Base): pass\nfactory = partial(deprecated, remove_in='2.0')\n",
                "a.py": "from .b import loop\n",
                "b.py": "from .a import loop\n",
                "broken.py": "\x00",
            },
        )
        monkeypatch.chdir(tmp_path)
        lines, _ = _run_static_cli(tmp_path, monkeypatch, capsys, "staticprobe")
        parse_error = (
            "ValueError('source code string cannot contain null bytes')"
            if sys.version_info < (3, 11)
            else "SyntaxError('source code string cannot contain null bytes')"
        )
        expected = [
            "pydeprecate gap: module not audited: staticprobe.broken: could not read its source: " + parse_error,
            "pydeprecate gap: not statically resolvable: staticprobe/__init__.py:7: unrecognized use of `deprecated`",
            "pydeprecate gap: not statically resolvable: staticprobe.Child: members inherited from `Base` "
            "(imported from `external_package`, whose source is not read) are not audited",
            "pydeprecate gap: not statically resolvable: staticprobe.loop: "
            "is re-exported in an import cycle; not followed",
            "pydeprecate gap: not statically resolvable: staticprobe.a.loop: "
            "is re-exported in an import cycle; not followed",
            "pydeprecate gap: not statically resolvable: staticprobe.b.loop: "
            "is re-exported in an import cycle; not followed",
        ]
        assert lines == [line.replace("/", os.sep) for line in expected]

    def test_imported_object_attribution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An imported object of unknown kind cannot be assigned a wrapper row silently.

        Reuse the existing attribution fixture: the defining module builds an object with a call, and another
        module wraps it. The CLI must explain why it cannot choose between class and function attribution.
        """
        _write(
            tmp_path,
            {
                "__init__.py": "",
                "other.py": "def make():\n    return 1\n\nthing = make()\n",
                "api.py": "from deprecate import deprecated\nfrom .other import thing\n\n"
                "old = deprecated(deprecated_in='1.0', remove_in='2.0')(thing)\n",
            },
        )
        monkeypatch.chdir(tmp_path)
        lines, _ = _run_static_cli(tmp_path, monkeypatch, capsys, "staticprobe")
        assert lines == [
            "pydeprecate gap: not statically resolvable: staticprobe.api.old: wraps an object imported from "
            "`staticprobe.other` that the source does not show to be a class or a function, "
            "so its row cannot be attributed"
        ]

    @pytest.mark.parametrize(
        "options",
        [
            pytest.param(("--no-recursive",), id="no-recursive"),
            pytest.param(("--exclude", "staticprobe.core"), id="excluded-reexport"),
        ],
    )
    def test_import_only_structural_gaps(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        options: tuple[str, ...],
    ) -> None:
        """Structural gaps in an imported but unwalked module are flushed after the walked module's gaps.

        Reuse the existing partial-factory re-export fixture under no-recursive and exclude. A second unsupported
        use in the package root distinguishes the normal emission from the deferred unwalked-module emission.
        """
        _write(
            tmp_path,
            {
                "__init__.py": "from .core import old\nfrom functools import partial\n"
                "from deprecate import deprecated\n"
                "factory = partial(deprecated, remove_in='2.0')\n",
                "core.py": "from functools import partial\nfrom deprecate import deprecated\n\n"
                "old = partial(deprecated, deprecated_in='1.0', remove_in='2.0')(print)\n",
            },
        )
        monkeypatch.chdir(tmp_path)
        lines, _ = _run_static_cli(tmp_path, monkeypatch, capsys, "staticprobe", *options)
        expected = [
            "pydeprecate gap: not statically resolvable: staticprobe/__init__.py:4: unrecognized use of `deprecated`",
            "pydeprecate gap: not statically resolvable: staticprobe/core.py:4: unrecognized use of `deprecated`",
        ]
        assert lines == [line.replace("/", os.sep) for line in expected]

    @pytest.mark.parametrize(
        "options",
        [
            pytest.param((), id="walked-extension"),
            pytest.param(("--no-recursive",), id="import-only-extension"),
            pytest.param(("--exclude", "staticprobe.compiled"), id="excluded-extension"),
        ],
    )
    def test_missing_python_source(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        options: tuple[str, ...],
    ) -> None:
        """An extension with no Python source reports one gap whether walked or reached only through an import.

        The existing discovery fixtures cover extensions and imports into unwalked modules. Combine those shapes
        to pin the missing-source message and deduplication when both traversal and an import encounter it.
        """
        pkg = _write(tmp_path, {"__init__.py": "from .compiled import thing\n"})
        (pkg / f"compiled{EXTENSION_SUFFIXES[0]}").write_bytes(b"")
        monkeypatch.chdir(tmp_path)
        lines, _ = _run_static_cli(tmp_path, monkeypatch, capsys, "staticprobe", *options)
        assert lines == ["pydeprecate gap: module not audited: staticprobe.compiled: no Python source to read"]


class TestCollectionParity:
    """Static and import scans of the same module agree finding by finding, except where a gap says why."""

    @pytest.mark.parametrize(
        ("module", "exempt"),
        [
            pytest.param("tests.collections.policy", set(), id="policy"),
            pytest.param(
                "tests.collections.static_forms",
                {"ambiguous_removal", "formatted_removal", "called_splat", "partial_built"},
                id="static-forms",
            ),
        ],
    )
    def test_expiry_findings_match(self, module: str, exempt: set[str]) -> None:
        """Every ``expired`` finding of an import scan has a byte-equal static twin unless a gap explains its absence.

        A static read that worded a finding differently, or named another symbol, would split a baseline between the
        two modes; the exempt wrappers are the ones the collection spells with settings the source does not settle.

        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = {f.id: f.message for f in _expiry_findings(find_deprecation_wrappers(module, False), "99.0")}
        static = {f.id: f.message for f in _expiry_findings(_resolved(_read_module(module)[0], ("remove_in",)), "99.0")}
        assert static == {fid: msg for fid, msg in imported.items() if fid.rsplit(":", 1)[1] not in exempt}

    @pytest.mark.parametrize("module", ["tests.collections.policy", "tests.collections.static_forms"])
    def test_policy_findings_match(self, module: str) -> None:
        """Both policy rules produce the same findings, in the same order, from source as from the imported module.

        The policy collection spells every wrapper with literal settings, and the static-forms collection adds the
        shapes static mode has to recognize: positional settings, module aliases, members, proxies, configured and
        aliased decorators, dict splats and ``with`` blocks.

        """
        spec = _build_policy_spec("0.3", True)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [(f.id, f.message) for f in _policy_findings(find_deprecation_wrappers(module, False), spec)]
        assert [(f.id, f.message) for f in _static_policy_findings(_read_module(module)[0], spec)] == imported

    def test_rows_match_import_scan(self) -> None:
        """The static read names and orders the static-forms wrappers as the import scan does, ``api_type`` included.

        The one wrapper built through ``partial(deprecated, ...)`` has no row; its factory use is reported as a gap.

        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = find_deprecation_wrappers("tests.collections.static_forms", recursive=False)
        static = _read_module("tests.collections.static_forms")[0]
        expected = [(i.function, i.api_type) for i in imported if i.function != "partial_built"]
        assert [(info.function, info.api_type) for info in static] == expected

    def test_source_locations_match_import_scan(self) -> None:
        """Each static row names the file and first line the import scan records for the same wrapper.

        ``expiry --static --report-sarif`` uploads results GitHub code scanning only shows with a location, and a
        report's ``location`` is what a reviewer clicks; a static read that dropped or shifted it would leave a static
        CI gate with alerts nobody can place. A wrapper the source cannot place gives ``None`` in both modes.

        """
        module = "tests.collections.static_forms"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = find_deprecation_wrappers(module, recursive=False)
        static = _read_module(module)[0]
        expected = [(i.function, i.source_file, i.source_line) for i in imported if i.function != "partial_built"]
        assert [(info.function, info.source_file, info.source_line) for info in static] == expected

    def test_every_missing_finding_has_its_gap(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Each import-scan finding the static read lacks is named by a gap, and no other finding is missing.

        Dropping the exempt wrappers from a comparison proves nothing on its own: a wrapper that vanished silently would
        pass it too. Here every absent finding must map to the gap that explains it — by subject for an unsettled
        setting, by file and line for a factory use static mode does not follow.

        """
        module = "tests.collections.static_forms"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = {f.symbol for f in _expiry_findings(find_deprecation_wrappers(module, False), "99.0")}
        wrappers, gaps = _read_module(module)
        _report_unresolved(wrappers, {"expired": ("remove_in",)})
        static = {f.symbol for f in _expiry_findings(_resolved(wrappers, ("remove_in",)), "99.0")}
        err = capsys.readouterr().err
        partial_line = _line_of(module, "= partial(deprecated")
        expected = {
            "ambiguous_removal": f"`{module}.ambiguous_removal` not checked",
            "formatted_removal": f"`{module}.formatted_removal` not checked",
            "called_splat": f"`{module}.called_splat` not checked",
            "partial_built": f"static_forms.py:{partial_line}: unrecognized use of `deprecated`",
        }
        explained = {
            symbol: expected.get(symbol, "") in err or any(expected.get(symbol, "") in item.detail for item in gaps)
            for symbol in imported - static
        }
        assert explained == dict.fromkeys(expected, True)

    def test_unsettled_forms_are_gaps(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Each form the source does not settle prints a gap naming its file, line and reason.

        A gap is what keeps a static audit honest: the wrapper is left out of the rule that needs the unsettled value,
        and the line says so instead of the wrapper silently passing.

        """
        wrappers, gaps = _read_module("tests.collections.static_forms")
        _report_unresolved(wrappers, {"expired": ("remove_in",), "message-required": ("target",)})
        err = capsys.readouterr().err
        assert "`remove_in` is `REMOVAL`, not a literal" in err
        assert "`remove_in` is `f'{_MAJOR}.0'`, not a literal" in err
        assert "arguments come from `**dict(deprecated_in='1.0', remove_in='2.0')`" in err
        assert "`target` is `_REPLACEMENTS['double']`, not a literal or a name" in err
        assert "static_forms.py" in err
        assert any("unrecognized use of `deprecated`" in item.detail for item in gaps)


class TestTargetSemantics:
    """``message-required`` reaches the same verdict from source as from the imported wrapper."""

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(
                "REPL = None\n\n@deprecated(target=REPL, deprecated_in='1.0', remove_in='2.0')\n"
                "def old(x):\n    return x\n",
                id="name-bound-to-none",
            ),
            pytest.param(
                "def new(x):\n    return x\n\n@deprecated(target=new, deprecated_in='1.0', remove_in='2.0')\n"
                "def old(x):\n    return x\n",
                id="function-target",
            ),
            pytest.param(
                "@deprecated(target=lambda x: x, deprecated_in='1.0', remove_in='2.0')\ndef old(x):\n    return x\n",
                id="lambda-target",
            ),
            pytest.param(
                "@deprecated(deprecated_in='1.0', remove_in='2.0', args_mapping={'a': 'b'})\ndef old(a=0, b=0):\n"
                "    return b\n",
                id="auto-with-mapping",
            ),
            pytest.param(
                "@deprecated(TargetMode.NOTIFY, '1.0', '2.0', message_template='Use `new` instead.')\ndef old(x):\n"
                "    return x\n",
                id="notify-with-template",
            ),
            pytest.param(
                "@deprecated(True, deprecated_in='1.0', remove_in='2.0', message_template='Use `New`.')\nclass Old:\n"
                "    pass\n",
                id="legacy-true-on-class",
            ),
            pytest.param(
                "class New:\n    pass\n\n@deprecated(target=New, deprecated_in='1.0', remove_in='2.0')\nclass Old:\n"
                "    pass\n",
                id="class-target-on-class",
            ),
            pytest.param(
                "def new(x):\n    return x\n\n@deprecated(target=new, deprecated_in='1.0', remove_in='2.0')\n"
                "class Old:\n    pass\n",
                id="function-target-on-class-ignored",
            ),
            pytest.param(
                "class Old:\n    colour = 'red'\n    color = 'red'\n\nLegacy = deprecated_class(deprecated_in='1.0',"
                " remove_in='2.0', attrs_mapping={'color': 'colour'})(Old)\n",
                id="class-attrs-mapping",
            ),
            pytest.param(
                "def source(x):\n    return x\n\n"
                "old = deprecated_callable(deprecated_in='1.0', remove_in='2.0')(source)\n",
                id="strict-callable-default-notify",
            ),
        ],
    )
    def test_guidance_matches_import_scan(self, probe_package: Callable[..., Path], body: str) -> None:
        """The static read and the import scan agree on whether each wrapper names a replacement.

        Each case is a different path through the factories' target normalisation: legacy sentinels, the front door's
        inferred mode, a class route that ignores a function target, a proxy whose mapping resolves the mode.

        """
        header = "from deprecate import TargetMode, deprecated, deprecated_callable, deprecated_class\n\n"
        pkg = probe_package({"__init__.py": header + body})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [f.id for f in _policy_findings(find_deprecation_wrappers(_PKG), _GUIDANCE_ONLY)]
        static = [f.id for f in _static_policy_findings(_read_package(pkg)[0], _GUIDANCE_ONLY)]
        assert static == imported

    @pytest.mark.parametrize(
        "files",
        [
            pytest.param({"consts.py": "REPL = None\n", "m.py": _IMPORTED_TARGET_USE}, id="constant-none"),
            pytest.param(
                {
                    "consts.py": "from deprecate import TargetMode\n\nREPL = TargetMode.NOTIFY\n",
                    "m.py": _IMPORTED_TARGET_USE,
                },
                id="target-mode-member",
            ),
            pytest.param(
                {"consts.py": "def REPL(x):\n    return x\n", "m.py": _IMPORTED_TARGET_USE}, id="function-replacement"
            ),
            pytest.param(
                {"consts.py": "def new(x):\n    return x\n\nREPL = new\n", "m.py": _IMPORTED_TARGET_USE},
                id="alias-of-a-function",
            ),
            pytest.param(
                {
                    "base.py": "REPL = None\n",
                    "consts.py": "from .base import REPL\n",
                    "m.py": _IMPORTED_TARGET_USE,
                },
                id="constant-re-exported-through-a-module",
            ),
            pytest.param(
                {
                    "consts.py": "REPL = None\n",
                    "m.py": "from deprecate import deprecated\nfrom .consts import REPL\n\nclass Old:\n"
                    "    @deprecated(target=REPL, deprecated_in='1.0', remove_in='2.0')\n    def run(self, x):\n"
                    "        return x\n",
                },
                id="constant-on-a-class-member",
            ),
        ],
    )
    def test_imported_target_matches_import_scan(
        self, probe_package: Callable[..., Path], files: dict[str, str]
    ) -> None:
        """A ``target`` imported from another module of the package is judged by what that module binds it to.

        A project keeps its sentinel values in a constants module and passes one as ``target=``: ``None`` or
        ``TargetMode.NOTIFY`` names no replacement, so ``message-required`` must report the wrapper as the import scan
        does, while an imported function or an alias of one is still a replacement and passes. Reading every imported
        name as a replacement let the first two pass unseen.

        """
        pkg = probe_package({"__init__.py": "", **files})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [f.id for f in _policy_findings(find_deprecation_wrappers(_PKG), _GUIDANCE_ONLY)]
        assert [f.id for f in _static_policy_findings(_read_package(pkg)[0], _GUIDANCE_ONLY)] == imported

    def test_unsettled_target_skips_only_guidance(self, tmp_path: Path) -> None:
        """A target the source does not settle leaves the wrapper out of ``message-required`` but not ``min-grace``.

        One unsettled setting must not hide a violation another rule can prove: the grace window here is one minor,
        which ``min-grace`` still reports.

        """
        source = (
            "from deprecate import deprecated\n\nTABLE = {}\n\n"
            "@deprecated(target=TABLE['x'], deprecated_in='1.0', remove_in='1.1')\ndef old(x):\n    return x\n"
        )
        wrappers = _read_package(_write(tmp_path, {"__init__.py": source}))[0]
        findings = _static_policy_findings(wrappers, _build_policy_spec("0.3", True))
        assert [f.rule.value for f in findings] == ["min-grace"]

    def test_instance_replacement_matches_import_scan(self, probe_package: Callable[..., Path]) -> None:
        """A data replacement given to ``deprecated_instance`` is judged by ``message-required`` as at runtime.

        A non-callable replacement needs an ``attrs_mapping`` or a custom message to count as guidance, so static mode
        must not treat it as a forwarding callable.

        """
        body = "NEW = {'a': 1}\nold = deprecated_instance({'a': 1}, target=NEW, deprecated_in='1.0', remove_in='2.0')\n"
        pkg = probe_package({"__init__.py": "from deprecate import deprecated_instance\n\n" + body})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [f.id for f in _policy_findings(find_deprecation_wrappers(_PKG), _GUIDANCE_ONLY)]
        assert [f.id for f in _static_policy_findings(_read_package(pkg)[0], _GUIDANCE_ONLY)] == imported

    @pytest.mark.parametrize(
        ("target", "extra"),
        [
            pytest.param("[1, 2]", "", id="list-display"),
            pytest.param("Plain()", "", id="same-file-class-instance"),
            pytest.param("Plain()", ", message_template='use the new object'", id="template-guides"),
        ],
    )
    def test_instance_data_target_needs_a_message(self, tmp_path: Path, target: str, extra: str) -> None:
        """A data replacement counts as guidance only with a message, never as a forwarding callable.

        A team serves a new configuration object under the old name. The warning cannot name a plain data object, so
        only a hand-written template tells callers where to go, and the other wrappers are flagged.

        """
        source = (
            "from deprecate import deprecated_instance\n\nclass Plain:\n    pass\n\n"
            f"old = deprecated_instance(Plain(), target={target}, deprecated_in='1.0', remove_in='2.0'{extra})\n"
        )
        findings = _static_policy_findings(_read_package(_write(tmp_path, {"__init__.py": source}))[0], _GUIDANCE_ONLY)
        assert [f.rule.value for f in findings] == ([] if extra else ["message-required"])

    def test_instance_target_of_callable_class_stays_a_gap(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An instance of a class that defines ``__call__`` may be a forwarding callable, so it is reported, not judged.

        The replacement object is built from a class the same file defines with ``__call__``: calling it would forward,
        which a data reading would misjudge as a missing message.

        """
        source = (
            "from deprecate import deprecated_instance\n\nclass Handler:\n    def __call__(self):\n        return 1\n\n"
            "old = deprecated_instance(Handler(), target=Handler(), deprecated_in='1.0', remove_in='2.0')\n"
        )
        findings = _static_policy_findings(_read_package(_write(tmp_path, {"__init__.py": source}))[0], _GUIDANCE_ONLY)
        assert findings == []
        assert "`target` is `Handler()`, not a literal or a name" in capsys.readouterr().err

    def test_dict_target_of_a_callable_wrapper_stays_a_gap(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A dict ``target`` on a function wrapper is still reported: only a served object may take a data target.

        Reading the dict as data is specific to ``deprecated_instance``; on ``@deprecated`` the runtime rejects it, so
        the source cannot be judged.

        """
        source = (
            "from deprecate import deprecated\n\n"
            "@deprecated(target={'a': 1}, deprecated_in='1.0', remove_in='2.0')\ndef old(x):\n    return x\n"
        )
        findings = _static_policy_findings(_read_package(_write(tmp_path, {"__init__.py": source}))[0], _GUIDANCE_ONLY)
        assert findings == []
        assert "not a literal or a name" in capsys.readouterr().err


class TestBindingIdentity:
    """Rows follow the import scan's one-row-per-public-binding rule within the scanned tree."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param(
                "def _impl(x):\n    return x\n\n_old = deprecated(deprecated_in='1.0', remove_in='2.0')(_impl)\n"
                "public = _old\n",
                ["public"],
                id="private-wrapper-public-alias",
            ),
            pytest.param(
                "@deprecated(deprecated_in='1.0', remove_in='2.0')\ndef zeta(x):\n    return x\n\nalpha = zeta\n"
                "omega = zeta\n",
                ["zeta"],
                id="binding-named-like-the-wrapper-wins",
            ),
            pytest.param(
                "def _impl(x):\n    return x\n\n_old = deprecated(deprecated_in='1.0', remove_in='2.0')(_impl)\n"
                "z_old = _old\na_old = _old\n",
                ["a_old"],
                id="no-binding-named-like-the-wrapper-takes-the-alphabetically-first",
            ),
            pytest.param(
                "def zeta(x):\n    return x\n\nzeta = deprecated(deprecated_in='1.0', remove_in='2.0')(zeta)\n"
                "alpha = zeta\n",
                ["zeta"],
                id="expression-wrapper-canonical-binding",
            ),
            pytest.param(
                "def zeta(x):\n    return x\n\nsource = zeta\nzeta = deprecated(deprecated_in='1.0', "
                "remove_in='2.0')(source)\nalpha = zeta\n",
                ["zeta"],
                id="expression-wrapper-aliased-source",
            ),
            pytest.param(
                "class API:\n    def zeta(self):\n        return 1\n\n"
                "    zeta = deprecated(deprecated_in='1.0', remove_in='2.0')(zeta)\n    alpha = zeta\n",
                ["API.alpha", "API.zeta"],
                id="expression-wrapper-class-members",
            ),
            pytest.param(
                "class Base:\n    @deprecated(deprecated_in='1.0', remove_in='2.0')\n    def old(self):\n"
                "        return 1\n\nclass Sub(Base):\n    pass\n",
                ["Base.old", "Sub.old"],
                id="inherited-member",
            ),
            pytest.param(
                "class Base:\n    @deprecated(deprecated_in='1.0', remove_in='2.0')\n    def old(self):\n"
                "        return 1\n\nclass Sub(Base):\n    def old(self):\n        return 2\n",
                ["Base.old"],
                id="overridden-member",
            ),
            pytest.param(
                "class Long:\n    @deprecated(deprecated_in='1.0', remove_in='2.0')\n    def old(self):\n"
                "        return 1\n\nShort = Long\n",
                ["Long.old", "Short.old"],
                id="class-alias-repeats-members",
            ),
            pytest.param(
                "class Holder:\n    @deprecated(deprecated_in='1.0', remove_in='2.0')\n    @property\n"
                "    def size(self):\n        return 1\n\n    @size.setter\n    def size(self, value):\n        pass\n",
                ["Holder.size"],
                id="setter-keeps-deprecated-getter",
            ),
            pytest.param(
                "from typing import cast\n\ndef impl(x):\n    return x\n\nold = deprecated(deprecated_in='1.0',"
                " remove_in='2.0')(impl)\naaa = cast(object, old)\n",
                ["aaa"],
                id="cast-alias",
            ),
        ],
    )
    def test_rows_follow_binding_rules(self, probe_package: Callable[..., Path], source: str, expected: list) -> None:
        """Static rows carry the same names the import scan reports for aliases, subclasses and accessors.

        A finding id carries the binding name; a mode switch that renamed rows would orphan every baseline entry.

        """
        pkg = probe_package({"__init__.py": "from deprecate import deprecated\n\n" + source})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [info.function for info in find_deprecation_wrappers(_PKG)]
        assert [info.function for info in _read_package(pkg)[0]] == imported == expected

    def test_reexports_attributed_like_import_scan(self, probe_package: Callable[..., Path]) -> None:
        """A re-exported function stays with its defining module; a re-exported proxy goes to the first module walked.

        ``functools.wraps`` gives a function wrapper the module it was defined in, so the package root skips it; a
        proxy has no such module, so the root, walked first, claims it.

        """
        pkg = probe_package(
            {
                "__init__.py": "from .core import Old, old_fn\n",
                "core.py": "from deprecate import deprecated, deprecated_class\n\n"
                "@deprecated(deprecated_in='1.0', remove_in='2.0')\ndef old_fn(x):\n    return x\n\n"
                "@deprecated_class(deprecated_in='1.0', remove_in='2.0')\nclass Old:\n    pass\n",
            }
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = _rows(find_deprecation_wrappers(_PKG))
        assert _rows(_read_package(pkg)[0]) == imported == [f"{_PKG}:Old", f"{_PKG}.core:old_fn"]

    def test_reexports_reported_on_package_without_submodules(self, probe_package: Callable[..., Path]) -> None:
        """Without recursion the package root reports what it re-exports, as the import scan does.

        ``--no-recursive`` never visits ``core``, so the import scan turns attribution off and lists the function under
        the package; static mode follows the import into ``core``'s source for the same row.

        """
        pkg = probe_package(
            {
                "__init__.py": "from .core import old_fn\n",
                "core.py": "from deprecate import deprecated\n\n@deprecated(deprecated_in='1.0', remove_in='2.0')\n"
                "def old_fn(x):\n    return x\n",
            }
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = _rows(find_deprecation_wrappers(_PKG, recursive=False))
        assert _rows(_read_package(pkg, recursive=False)[0]) == imported == [f"{_PKG}:old_fn"]

    def test_deprecated_module_row(self, probe_package: Callable[..., Path]) -> None:
        """``deprecated_module`` called in a module's body reports the module itself, first.

        A module row has no symbol; its message-required verdict comes from a replacement module, a mapping or a
        custom message.

        """
        pkg = probe_package(
            {
                "__init__.py": "",
                "old.py": "from deprecate import deprecated_module\n\nX = 1\n\n"
                "deprecated_module(__name__, deprecated_in='1.0', remove_in='2.0', message_template='Use `new`.')\n",
            }
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [(i.module, i.function, i.api_type) for i in find_deprecation_wrappers(_PKG)]
        static = _read_package(pkg)[0]
        assert [(i.module, i.function, i.api_type) for i in static] == imported == [(f"{_PKG}.old", "", "module")]
        assert _static_policy_findings(static, _GUIDANCE_ONLY) == []

    @pytest.mark.parametrize(
        "target",
        [
            pytest.param("True", id="constant"),
            pytest.param("helper", id="function"),
            pytest.param("Helper", id="class"),
            pytest.param("json", id="imported-module"),
        ],
    )
    def test_module_target_guidance_matches_import_scan(self, probe_package: Callable[..., Path], target: str) -> None:
        """A module wrapper's ``message-required`` verdict from source matches the import scan for each target kind.

        A module, function or class passed as ``target`` is named in the warning ``deprecated_module`` renders, which
        import mode counts as guidance; a constant such as ``True`` has no name and gives none. Reading every
        non-``None`` target as a module let a module with no guidance pass the rule in static mode while import mode
        reported it.

        """
        pkg = probe_package(
            {
                "__init__.py": "",
                "old.py": "import json\nfrom deprecate import deprecated_module\n\ndef helper():\n    pass\n\n"
                f"class Helper:\n    pass\n\ndeprecated_module(__name__, target={target}, deprecated_in='1.0',"
                " remove_in='2.0')\n",
            }
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [(f.id, f.message) for f in _policy_findings(find_deprecation_wrappers(_PKG), _GUIDANCE_ONLY)]
        assert [(f.id, f.message) for f in _static_policy_findings(_read_package(pkg)[0], _GUIDANCE_ONLY)] == imported

    @pytest.mark.parametrize(
        "template",
        [
            pytest.param("Use `new`.", id="plain-text"),
            pytest.param("%(source_name)s moved to `new`.", id="placeholder-in-custom-text"),
            pytest.param("%(target_name)s", id="placeholder-that-renders-empty"),
            pytest.param(
                "The `%(source_name)s` module was deprecated since v%(deprecated_in)s."
                " It will be removed in v%(remove_in)s.",
                id="text-that-renders-to-the-built-in-notice",
            ),
        ],
    )
    def test_module_template_guidance_matches_import_scan(
        self, probe_package: Callable[..., Path], template: str
    ) -> None:
        """A module wrapper's ``message-required`` verdict reads the rendered template, as the import scan does.

        ``deprecated_module`` renders its ``message_template`` at decoration time and stores the result, so a
        template that renders empty (``%(target_name)s`` with no ``target``) or to exactly the built-in notice gives
        callers no guidance, however different the raw text looks. Judging the raw text let such a module pass the
        rule in static mode while import mode reported it.

        """
        pkg = probe_package(
            {
                "__init__.py": "",
                "old.py": "from deprecate import deprecated_module\n\n"
                f"deprecated_module(__name__, deprecated_in='1.0', remove_in='2.0', message_template={template!r})\n",
            }
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [(f.id, f.message) for f in _policy_findings(find_deprecation_wrappers(_PKG), _GUIDANCE_ONLY)]
        assert [(f.id, f.message) for f in _static_policy_findings(_read_package(pkg)[0], _GUIDANCE_ONLY)] == imported

    def test_module_template_using_an_unsettled_version_is_a_gap(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A module template that renders a version the source does not settle is reported, not judged on its raw text.

        The rendered notice depends on the version: ``TABLE['x']`` may hold anything, so whether the stored text
        equals the built-in notice is unknown and the module's guidance is left unjudged, with a gap naming why.

        """
        source = (
            "from deprecate import deprecated_module\n\nTABLE = {}\n\n"
            "deprecated_module(__name__, deprecated_in='1.0', remove_in=TABLE['x'], message_template='%(remove_in)s')\n"
        )
        wrappers = _read_package(_write(tmp_path, {"__init__.py": source}))[0]
        _static_policy_findings(wrappers, _GUIDANCE_ONLY)
        assert "`message_template` renders `remove_in`" in capsys.readouterr().err


class TestResolvedSettings:
    """Each check sees exactly the wrappers whose settings it reads were settled from source."""

    @pytest.mark.parametrize(
        ("grace", "message_required", "expected"),
        [
            pytest.param("0.3", True, ("deprecated_in", "remove_in", *_GUIDANCE_KEYWORDS), id="both"),
            pytest.param(None, True, _GUIDANCE_KEYWORDS, id="guidance-only"),
            pytest.param("0.3", False, ("deprecated_in", "remove_in"), id="grace-only"),
            pytest.param(None, False, (), id="disabled"),
        ],
    )
    def test_only_enabled_rules_report_unsettled_settings(
        self, tmp_path: Path, grace: Optional[str], message_required: bool, expected: tuple[str, ...]
    ) -> None:
        """Only the settings an enabled rule reads can make a wrapper a gap for ``policy``.

        A version held in a variable must not be reported when ``min-grace`` is switched off.

        """
        source = (
            "from deprecate import deprecated\n\n"
            "@deprecated(deprecated_in=VERSION, remove_in=VERSION, target=TARGET['x'], args_mapping=MAPPING, "
            "attrs_mapping=MAPPING, message_template=MESSAGE)\ndef old(): pass\n"
        )
        wrappers = _read_package(_write(tmp_path, {"__init__.py": source}))[0]
        with _record_run() as record:
            _static_policy_findings(wrappers, _build_policy_spec(grace, message_required))
        assert [gap.detail.split("`")[1] for gap in record.gaps] == list(expected)

    @pytest.mark.parametrize(
        ("body", "reason"),
        [
            pytest.param(
                "ARGS = ('1.0', '2.0')\n\n@deprecated(None, *ARGS)", "arguments come from `*ARGS`", id="star-args"
            ),
            pytest.param(
                "V = '2.0'\nV = '3.0'\n\n@deprecated(deprecated_in='1.0', remove_in=V)",
                "`remove_in` is `V`, not a literal",
                id="rebound-version",
            ),
            pytest.param(
                "R = None\nR = print\n\n@deprecated(target=R, deprecated_in='1.0', remove_in='2.0')",
                "`target` is `R`, which is bound more than once",
                id="rebound-target",
            ),
            pytest.param(
                "R = dict(a=1)\n\n@deprecated(target=R, deprecated_in='1.0', remove_in='2.0')",
                "`target` is `R`, which is not bound to a literal or a definition",
                id="target-bound-to-call",
            ),
            pytest.param(
                "M = dict(a='b')\n\n@deprecated(deprecated_in='1.0', remove_in='2.0', args_mapping=M)",
                "`args_mapping` is `M`, not a literal",
                id="mapping-from-call",
            ),
        ],
    )
    def test_unsettled_setting_reason(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], body: str, reason: str
    ) -> None:
        """Each way a setting escapes the source is named in the gap, so the author knows what to spell literally.

        The gap is the whole remedy for a static audit: the line, the setting and why it could not be read.

        """
        source = f"from deprecate import deprecated\n\n{body}\ndef old(a=0):\n    return a\n"
        wrappers = _read_package(_write(tmp_path, {"__init__.py": source}))[0]
        _report_unresolved(
            wrappers, {"min-grace": ("deprecated_in", "remove_in"), "message-required": _GUIDANCE_KEYWORDS}
        )
        assert reason in capsys.readouterr().err


class TestShapeParity:
    """Less common shapes yield the rows and policy findings of the import scan."""

    @pytest.mark.parametrize(
        ("source", "definition"),
        [
            pytest.param("def original(): pass\nalias = original\n", "def original", id="function-alias"),
            pytest.param("def original(): pass\nfirst = original\nalias = first\n", "def original", id="alias-chain"),
            pytest.param("original = lambda: None\nalias = original\n", "original = lambda", id="named-lambda"),
        ],
    )
    def test_call_form_alias_source_location_matches_import(
        self, probe_package: Callable[..., Path], source: str, definition: str
    ) -> None:
        """A wrapper around an alias points to the original callable in both scans.

        An audit baseline should retain its source location when a callable is wrapped through an intermediate name.

        """
        pkg = probe_package({"__init__.py": f"{_DEP}{source}old = {_EXPIRED}(alias)\n"})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [(i.function, i.source_file, i.source_line) for i in find_deprecation_wrappers(_PKG)]
        static = [(i.function, i.source_file, i.source_line) for i in _read_package(pkg)[0]]
        line = next(i for i, text in enumerate((pkg / "__init__.py").read_text().splitlines(), 1) if definition in text)
        assert len(imported) == 1
        assert imported[0][2] == line
        assert static == imported

    def test_source_locations_match_per_shape(self, probe_package: Callable[..., Path]) -> None:
        """Every wrapper shape gets the file and first line the import scan records, or no location in both modes.

        A package mixing a deprecated module, decorated and call-form functions, a lambda, decorated and call-form
        classes, a ``deprecated_instance`` and deprecated property and classmethod members is read both ways. A
        report or SARIF result from ``--static`` must point at the line an import run points at, so a baseline or a
        code-scanning alert does not move when a CI job switches modes.

        """
        pkg = probe_package(
            {
                "__init__.py": """
                from deprecate import deprecated, deprecated_class, deprecated_instance, deprecated_module

                deprecated_module(__name__, deprecated_in="1.0", remove_in="2.0")

                def new_fn(x):
                    return x

                @deprecated(target=new_fn, deprecated_in="1.0", remove_in="2.0")
                def decorated(x):
                    pass

                def plain(x):
                    return x

                called = deprecated(target=new_fn, deprecated_in="1.0", remove_in="2.0")(plain)
                lam = deprecated(target=None, deprecated_in="1.0", remove_in="2.0")(lambda x: x)

                class NewThing:
                    pass

                @deprecated_class(target=NewThing, deprecated_in="1.0", remove_in="2.0")
                class OldThing:
                    pass

                OldAlias = deprecated_class(target=NewThing, deprecated_in="1.0", remove_in="2.0")(NewThing)
                CONFIG = deprecated_instance({"a": 1}, deprecated_in="1.0", remove_in="2.0")

                class Holder:
                    @property
                    @deprecated(target=None, deprecated_in="1.0", remove_in="2.0")
                    def prop(self):
                        return 1

                    @classmethod
                    @deprecated(target=None, deprecated_in="1.0", remove_in="2.0")
                    def cm(cls):
                        return 1
            """
            }
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [(i.function, i.source_file, i.source_line) for i in find_deprecation_wrappers(_PKG)]
        static = [(i.function, i.source_file, i.source_line) for i in _read_package(pkg)[0]]
        assert static == imported

    @pytest.mark.parametrize(
        "files",
        [
            pytest.param(
                {
                    "__init__.py": "",
                    "sub/__init__.py": "",
                    "sub/mod.py": "from deprecate import deprecated\n\n@deprecated(deprecated_in='1.0')\n"
                    "def old(x):\n    return x\n",
                },
                id="subpackage",
            ),
            pytest.param(
                {
                    "__init__.py": "from .core import *\n",
                    "core.py": "from deprecate import deprecated_class\n\n"
                    "@deprecated_class(deprecated_in='1.0', remove_in='2.0')\nclass Old:\n    pass\n",
                },
                id="star-reexport-of-proxy",
            ),
            pytest.param(
                {
                    "__init__.py": "",
                    "core.py": "def impl(x):\n    return x\n\nclass Impl:\n    pass\n",
                    "api.py": "from deprecate import deprecated\nfrom .core import Impl, impl\n\n"
                    "old = deprecated(deprecated_in='1.0', remove_in='2.0')(impl)\n"
                    "Old = deprecated(deprecated_in='1.0', remove_in='2.0')(Impl)\n",
                },
                id="wrapping-imported-function-and-class",
            ),
            pytest.param(
                {
                    "__init__.py": "from deprecate import deprecated, deprecated_class, deprecated_instance\n\n"
                    "class Holder:\n"
                    "    def impl(self):\n        return 1\n\n"
                    "    legacy = deprecated(deprecated_in='1.0', remove_in='2.0')(impl)\n\n"
                    "    @deprecated_class(deprecated_in='1.0', remove_in='2.0')\n    class Inner:\n        pass\n\n"
                    "class Plain:\n    pass\n\n"
                    "proxied = deprecated_instance(Plain, deprecated_in='1.0', remove_in='2.0')\n",
                },
                id="class-body-assign-nested-class-instance-of-class",
            ),
            pytest.param(
                {
                    "__init__.py": "import json\nfrom json import dumps\nfrom deprecate import deprecated\n\n"
                    "old_dumps = deprecated(deprecated_in='1.0', remove_in='2.0')(dumps)\n"
                    "with open(__file__) as handle:\n    first, second = 1, 2\n",
                },
                id="external-object-with-as-and-unpacking",
            ),
            pytest.param(
                {
                    "__init__.py": "from .shared import legacy\n",
                    "shared.py": "from deprecate import deprecated\n\n"
                    "legacy = deprecated(deprecated_in='1.0', remove_in='2.0')\n",
                    "api.py": "from . import legacy\n\n@legacy\ndef old(x):\n    return x\n",
                },
                id="configured-decorator-through-package-root",
            ),
        ],
    )
    def test_rows_and_findings_match(self, probe_package: Callable[..., Path], files: dict[str, str]) -> None:
        """Rows, their order and every policy finding agree with an import scan of the same package.

        These shapes exercise the attribution rules: a function wrapper takes the module of what it wraps, a proxy the
        first module that binds it, and a class body binds members by assignment as well as by definition.

        """
        pkg = probe_package(files)
        spec = _build_policy_spec("0.3", True)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = find_deprecation_wrappers(_PKG)
            imported_findings = [f.id for f in _policy_findings(imported, spec)]
        static = _read_package(pkg)[0]
        assert (_rows(static), [f.id for f in _static_policy_findings(static, spec)]) == (
            _rows(imported),
            imported_findings,
        )

    @pytest.mark.parametrize(
        "files",
        [
            pytest.param(
                {
                    "__init__.py": "from dataclasses import dataclass as record\n"
                    "from deprecate import deprecated_class\n\n"
                    "@deprecated_class(deprecated_in='1.0', remove_in='2.0')\n@record\nclass Old:\n    x: int = 0\n"
                },
                id="aliased-decorator",
            ),
            pytest.param(
                {
                    "__init__.py": "import dataclasses as dc\nfrom deprecate import deprecated_class\n\n"
                    "@deprecated_class(deprecated_in='1.0', remove_in='2.0')\n@dc.dataclass(frozen=True)\n"
                    "class Old:\n    x: int = 0\n"
                },
                id="aliased-module",
            ),
            pytest.param(
                {
                    "__init__.py": "from deprecate import deprecated_class\n\ndef dataclass(cls):\n    return cls\n\n"
                    "@deprecated_class(deprecated_in='1.0', remove_in='2.0')\n@dataclass\nclass Old:\n    x: int = 0\n"
                },
                id="local-function-named-dataclass",
            ),
            pytest.param(
                {
                    "__init__.py": "",
                    "core.py": "from dataclasses import dataclass as record\n\n@record\nclass Impl:\n    x: int = 0\n",
                    "api.py": "from deprecate import deprecated_class\nfrom .core import Impl\n\n"
                    "Old = deprecated_class(deprecated_in='1.0', remove_in='2.0')(Impl)\n",
                },
                id="dataclass-defined-in-another-module",
            ),
        ],
    )
    def test_dataclass_label_matches_import_scan(
        self, probe_package: Callable[..., Path], files: dict[str, str]
    ) -> None:
        """A proxied class is labelled ``dataclass`` exactly when the import scan's runtime check says it is one.

        The decorator name alone misleads both ways: an alias of ``dataclasses.dataclass`` builds a dataclass under
        another name, and a project's own function named ``dataclass`` builds none; the name is resolved through the
        imports of the module that defines the class, which may not be the module that wraps it.

        """
        pkg = probe_package(files)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [(i.function, i.api_type) for i in find_deprecation_wrappers(_PKG)]
        assert [(i.function, i.api_type) for i in _read_package(pkg)[0]] == imported


class TestNoSilentMiss:
    """Shapes the source alone cannot settle print a gap; shapes it can settle give the import scan's findings."""

    @pytest.mark.parametrize(
        ("files", "gap"),
        [
            pytest.param(
                {
                    "m.py": f"{_DEP}_dep = {_EXPIRED}\ntry:\n    @_dep\n    def old():\n        pass\n"
                    "except Exception:\n    pass\n"
                },
                "m.py:4: unrecognized use of `_dep`",
                id="configured-decorator-in-try",
            ),
            pytest.param(
                {"a.py": "from .broken import thing\n", "broken.py": "def (:\n"},
                f"{_PKG}.broken: could not read its source",
                id="unparsable-module-first-read-through-an-import",
            ),
            pytest.param(
                {"m.py": f"{_DEP}from extlib import Base\n\nclass Child(Base):\n    pass\n"},
                f"{_PKG}.m.Child: members inherited from `Base` (imported from `extlib`, whose source is not read)",
                id="base-class-outside-the-tree",
            ),
            pytest.param(
                {
                    "consts.py": "REPL = None\n",
                    "m.py": f"{_DEP}from .consts import *\n\n"
                    "@deprecated(target=REPL, deprecated_in='1.0', remove_in='2.0')\ndef old():\n    pass\n",
                },
                "`target` is `REPL`, which a star import may bind",
                id="target-from-a-star-import",
            ),
            pytest.param(
                {
                    "m.py": f"{_DEP}KW = {{'deprecated_in': '1.0', 'remove_in': '9.0'}}\nKW['remove_in'] = '2.0'\n\n"
                    "@deprecated(**KW)\ndef old():\n    pass\n"
                },
                "arguments come from `**KW`",
                id="splat-of-a-mutated-dict",
            ),
            pytest.param(
                {
                    "m.py": f"{_DEP}KW = {{'deprecated_in': '1.0', 'remove_in': '9.0'}}\nALIAS = KW\n"
                    "ALIAS['remove_in'] = '2.0'\n\n@deprecated(**KW)\ndef old():\n    pass\n"
                },
                "arguments come from `**KW`",
                id="splat-of-a-dict-changed-through-an-alias",
            ),
            pytest.param(
                {
                    "m.py": f"{_DEP}def _bump(settings):\n    settings['remove_in'] = '2.0'\n\n"
                    "KW = {'deprecated_in': '1.0', 'remove_in': '9.0'}\n_bump(KW)\n\n"
                    "@deprecated(**KW)\ndef old():\n    pass\n"
                },
                "arguments come from `**KW`",
                id="splat-of-a-dict-a-helper-call-changes",
            ),
            pytest.param(
                {
                    "m.py": f"{_DEP}REMOVE = '9.0'\nglobals()['REMOVE'] = '2.0'\n\n"
                    "@deprecated(deprecated_in='1.0', remove_in=REMOVE)\ndef old():\n    pass\n"
                },
                "`remove_in` is `REMOVE`, not a literal",
                id="version-rebound-through-globals",
            ),
            pytest.param(
                {
                    "m.py": f"import sys\n{_DEP}REMOVE = '9.0'\nsetattr(sys.modules[__name__], 'REMOVE', '2.0')\n\n"
                    "@deprecated(deprecated_in='1.0', remove_in=REMOVE)\ndef old():\n    pass\n"
                },
                "`remove_in` is `REMOVE`, not a literal",
                id="version-rebound-through-setattr-on-the-module",
            ),
            pytest.param(
                {
                    "m.py": f"{_DEP}REMOVE = '9.0'\nglobals().update(REMOVE='2.0')\n\n"
                    "@deprecated(deprecated_in='1.0', remove_in=REMOVE)\ndef old():\n    pass\n"
                },
                "unrecognized use of `deprecated`",
                id="version-in-a-module-that-updates-its-globals",
            ),
            pytest.param(
                {
                    "__init__.py": "from .core import *\n",
                    "core.py": f"{_DEP}__all__ = ['old'] + []\n\n@{_EXPIRED}\ndef old():\n    pass\n",
                },
                f"`from {_PKG}.core import *`: `{_PKG}.core` has an `__all__` that is not one literal list of names",
                id="star-import-of-a-computed-all",
            ),
            pytest.param(
                {
                    "__init__.py": "from .core import *\n",
                    "core.py": f"{_DEP}__all__ = []\n__all__.append('old')\n\n@{_EXPIRED}\ndef old():\n    pass\n",
                },
                f"`from {_PKG}.core import *`: `{_PKG}.core` has an `__all__` that is not one literal list of names",
                id="star-import-of-an-all-appended-to",
            ),
            pytest.param(
                {
                    "m.py": "try:\n    from deprecate import deprecated\nexcept ImportError:\n    deprecated = None\n\n"
                    f"@{_EXPIRED}\ndef old():\n    pass\n"
                },
                "m.py:6: unrecognized use of `deprecated`",
                id="factory-name-bound-twice",
            ),
            pytest.param(
                {"a.py": "from .b import old\n", "b.py": "from .a import old\n"},
                f"{_PKG}.a.old: is re-exported in an import cycle; not followed",
                id="wrapper-re-exported-in-an-import-cycle",
            ),
            pytest.param(
                {"a.py": "from .b import legacy\n\n@legacy\ndef old():\n    pass\n", "b.py": "from .a import legacy\n"},
                "a.py:3: `legacy` is re-exported in an import cycle; not followed",
                id="configured-decorator-re-exported-in-an-import-cycle",
            ),
            pytest.param(
                {"m.py": f"from extcompat import deprecated\n\n@{_EXPIRED}\ndef old():\n    pass\n"},
                "m.py:3: `deprecated` is called with `remove_in` but comes from a module static mode does not read",
                id="factory-re-exported-by-a-module-outside-the-tree",
            ),
            pytest.param(
                {"m.py": f"from extcompat import *\n\n@{_EXPIRED}\ndef old():\n    pass\n"},
                "m.py:3: `deprecated` is called with `remove_in` but comes from a module static mode does not read",
                id="factory-from-a-star-import-of-a-module-outside-the-tree",
            ),
            pytest.param(
                {"m.py": f"{_DEP}def command(f):\n    return f\n\n@command\n@{_EXPIRED}\ndef old():\n    pass\n"},
                "m.py:6: `@command` is applied over the deprecation wrapper and may replace it",
                id="unknown-decorator-stacked-over-a-function-wrapper",
            ),
            pytest.param(
                {
                    "m.py": "from deprecate import deprecated_class\n\ndef register(c):\n    return c\n\n"
                    "@register\n@deprecated_class(deprecated_in='1.0', remove_in='2.0')\nclass Old:\n    pass\n"
                },
                "m.py:7: `@register` is applied over the deprecation wrapper and may replace it",
                id="unknown-decorator-stacked-over-a-class-proxy",
            ),
            pytest.param(
                {
                    "m.py": f"{_DEP}KW = {{'deprecated_in': '1.0', 'remove_in': '9.0'}}\n\nclass Holder:\n"
                    "    KW = {'deprecated_in': '1.0', 'remove_in': '2.0'}\n\n"
                    "    @deprecated(**KW)\n    def old(self):\n        pass\n"
                },
                "arguments come from `**KW`",
                id="splat-of-a-dict-the-class-body-shadows",
            ),
            pytest.param(
                {
                    "m.py": f"{_DEP}M = {{'a': 'b'}}\n\nclass Holder:\n    M = None\n\n"
                    "    @deprecated(deprecated_in='1.0', remove_in='2.0', args_mapping=M)\n"
                    "    def old(self, a=0):\n        pass\n"
                },
                "`args_mapping` is `M`, not a literal",
                id="mapping-the-class-body-shadows",
            ),
            pytest.param(
                {
                    "m.py": f"import sys\n{_DEP}\n@{_EXPIRED}\ndef old():\n    pass\n\n"
                    "if sys.version_info >= (3,):\n    def old():\n        pass\n"
                },
                "m.py:8: `old` may be rebound by this `if` statement",
                id="wrapper-rebound-inside-if",
            ),
            pytest.param(
                {
                    "m.py": f"{_DEP}\nclass API:\n    @{_EXPIRED}\n    def old(self):\n        pass\n\n"
                    "    if False:\n        def old(self):\n            pass\n"
                },
                "m.py:8: `old` may be rebound by this `if` statement",
                id="class-member-replaced-inside-if",
            ),
            pytest.param(
                {
                    "m.py": f"{_DEP}\nclass API:\n    @{_EXPIRED}\n    def old(self):\n        pass\n\n"
                    "    try:\n        old = None\n    except NameError:\n        pass\n"
                },
                "m.py:8: `old` may be rebound by this `try` statement",
                id="class-member-replaced-inside-try",
            ),
            pytest.param(
                {
                    "m.py": "from deprecate import deprecated_module\n\n"
                    "deprecated_module(__name__, deprecated_in='1.0', remove_in='2.0', message_template='%(nope)s')\n"
                },
                "m.py:3: `message_template` cannot be rendered",
                id="module-template-the-factory-rejects",
            ),
            pytest.param(
                {
                    "m.py": f"{_DEP}\nclass Holder:\n    deprecated = staticmethod(lambda **kw: lambda f: f)\n\n"
                    f"    @{_EXPIRED}\n    def old(self):\n        pass\n"
                },
                "m.py:6: `deprecated` is rebound in the class body",
                id="factory-name-the-class-body-rebinds",
            ),
            pytest.param(
                {
                    "m.py": "from deprecate import deprecated_instance\n\n"
                    "class Holder:\n    deprecated_instance = dict\n"
                    "    SETTINGS = deprecated_instance({}, deprecated_in='1.0', remove_in='2.0')\n"
                },
                "m.py:5: `deprecated_instance` is rebound in the class body",
                id="instance-factory-name-the-class-body-rebinds",
            ),
            pytest.param(
                {
                    "m.py": "from deprecate import deprecated_module\n\n"
                    "deprecated_module(__name__, target='json', deprecated_in='1.0', remove_in='2.0')\n"
                },
                "`target` is `'json'`, not a literal or a name",
                id="module-target-given-as-an-expression",
            ),
            pytest.param(
                {
                    "consts.py": "import os\n\nREPL = os.environ.get('REPL')\n",
                    "m.py": f"{_DEP}from .consts import REPL\n\n@deprecated(target=REPL, deprecated_in='1.0',"
                    " remove_in='2.0')\ndef old():\n    pass\n",
                },
                f"m.py:4: `target` is `REPL`, imported from `{_PKG}.consts`, which does not settle its value",
                id="imported-target-computed-at-runtime",
            ),
        ],
    )
    def test_unsettled_shape_is_a_gap(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], files: dict[str, str], gap: str
    ) -> None:
        """A wrapper the static read cannot place prints a gap naming why, instead of passing the audit unseen.

        Each case once gave neither a row nor a gap: a configured decorator inside ``try``, a broken file first parsed
        through another module's import, a base class from another distribution, a target or a splat whose value
        depends on runtime, a star import of a computed or in-place mutated ``__all__``, a factory name the module
        rebinds, a deprecated class member that an ``if`` or ``try`` in the class body may replace (the import scan
        reports it whenever that statement does not run), and a re-export chain longer than the reader follows.

        """
        wrappers, gaps = _read_package(_write(tmp_path, {"__init__.py": "", **files}))
        _report_unresolved(wrappers, {"expired": ("remove_in",), "message-required": _GUIDANCE_KEYWORDS})
        assert gap in capsys.readouterr().err or any(gap in item.detail for item in gaps)

    @pytest.mark.parametrize(
        "files",
        [
            pytest.param(
                {
                    "m.py": "from functools import partial\n\n"
                    "SETTINGS = dict(deprecated_in='1.0', remove_in='2.0')\nLATER = partial(print, remove_in='2.0')\n"
                },
                id="remove-in-passed-to-a-builtin-or-standard-library-callee",
            ),
            pytest.param(
                {"__init__.py": "from . import sub\n", "sub.py": f"{_DEP}@{_EXPIRED}\ndef old():\n    pass\n"},
                id="package-init-imports-its-own-submodule-relatively",
            ),
            pytest.param(
                {"__init__.py": f"from {_PKG} import sub\n", "sub.py": f"{_DEP}@{_EXPIRED}\ndef old():\n    pass\n"},
                id="package-init-imports-its-own-submodule-absolutely",
            ),
            pytest.param(
                {
                    "m.py": f"{_DEP}UNSET = None\n\n"
                    "@deprecated(deprecated_in=None, remove_in=UNSET)\ndef old():\n    pass\n"
                },
                id="versions-given-as-none-inline-and-through-a-name",
            ),
            pytest.param(
                {
                    "m.py": f"{_DEP}KW = {{'deprecated_in': '1.0', 'remove_in': '2.0'}}\nM = {{'a': 'b'}}\n"
                    "COPY = {**KW, 'remove_in': '3.0'}\nprint(**KW)\n\n"
                    "@deprecated(args_mapping=M, **KW)\ndef old(a=0):\n    pass\n"
                },
                id="dict-only-copied-or-given-to-a-factory",
            ),
            pytest.param(
                {
                    "m.py": f"{_DEP}\nclass API:\n    @{_EXPIRED}\n    def old(self):\n        pass\n\n"
                    "    def old(self):\n        pass\n"
                },
                id="class-member-replaced-by-a-plain-definition",
            ),
            pytest.param(
                {
                    "m.py": f"{_DEP}\nclass API:\n    @{_EXPIRED}\n    def old(self):\n        pass\n\n"
                    "    with open(__file__):\n        def old(self):\n            pass\n"
                },
                id="class-member-replaced-in-a-with-body-that-always-runs",
            ),
        ],
    )
    def test_settled_source_prints_no_gap(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], files: dict[str, str]
    ) -> None:
        """Source the reader settles in full prints no ``not statically resolvable`` gap at all.

        A gap the source does not warrant is noise that teaches users to skim past the channel: a builtin or
        standard-library call that happens to take a ``remove_in`` keyword, for example, can never hold a factory.

        """
        wrappers, gaps = _read_package(_write(tmp_path, {"__init__.py": "", **files}))
        _report_unresolved(
            wrappers, {"min-grace": ("deprecated_in", "remove_in"), "message-required": _GUIDANCE_KEYWORDS}
        )
        assert "not statically resolvable" not in capsys.readouterr().err
        assert not any(item.kind is _GapKind.STATIC_UNRESOLVED for item in gaps)

    @pytest.mark.parametrize(
        ("files", "recursive"),
        [
            pytest.param(
                {
                    "a.py": f"{_DEP}from {_PKG}.b import helper\n_dep = {_EXPIRED}\n\n@helper\ndef x():\n    pass\n",
                    "b.py": f"def helper(f):\n    return f\n\nfrom {_PKG}.a import _dep\n\n"
                    "@_dep\ndef old():\n    pass\n",
                },
                True,
                id="configured-decorator-across-an-import-cycle",
            ),
            pytest.param(
                {
                    "compat2.py": _DEP,
                    "compat.py": "from .compat2 import deprecated\n",
                    "mod.py": f"from .compat import deprecated\n\n@{_EXPIRED}\ndef old():\n    pass\n",
                },
                True,
                id="factory-re-exported-twice",
            ),
            pytest.param(
                {
                    "m0.py": f"{_DEP}@{_EXPIRED}\ndef old():\n    pass\n",
                    **{f"m{n}.py": f"from .m{n - 1} import old\n" for n in range(1, 7)},
                    "__init__.py": "from .m6 import old\n",
                },
                True,
                id="wrapper-re-exported-through-a-long-chain",
            ),
            pytest.param(
                {
                    "deep.py": "x = " + "+".join(["1"] * 3000) + "\n",
                    "ok.py": f"{_DEP}@{_EXPIRED}\ndef old():\n    pass\n",
                },
                True,
                id="deeply-nested-expression-next-to-a-wrapper",
            ),
            pytest.param(
                {
                    "m.py": f"{_DEP}class A:\n    def old(self):\n        pass\n\nclass B:\n    @{_EXPIRED}\n"
                    "    def old(self):\n        pass\n\nclass C(A, B):\n    pass\n"
                },
                True,
                id="plain-member-of-an-earlier-base-hides-a-deprecated-one",
            ),
            pytest.param(
                {
                    "__init__.py": "from .core import *\n",
                    "core.py": f"{_DEP}__all__ = []\n\n@{_EXPIRED}\ndef old():\n    pass\n",
                },
                False,
                id="star-import-honours-all",
            ),
            pytest.param(
                {
                    "__init__.py": "def old():\n    pass\n\nfrom .core import *\n",
                    "core.py": f"{_DEP}@{_EXPIRED}\ndef old():\n    pass\n",
                },
                False,
                id="star-import-after-a-local-binding-wins",
            ),
            pytest.param(
                {
                    "__init__.py": "from .core import *\n\ndef old():\n    pass\n",
                    "core.py": f"{_DEP}@{_EXPIRED}\ndef old():\n    pass\n",
                },
                False,
                id="local-binding-after-a-star-import-wins",
            ),
            pytest.param(
                {
                    "__init__.py": "from .a import *\nfrom .b import *\n",
                    "a.py": f"{_DEP}@{_EXPIRED}\ndef old():\n    pass\n",
                    "b.py": "def old():\n    pass\n",
                },
                False,
                id="later-star-import-of-a-plain-name-wins",
            ),
            pytest.param(
                {
                    "__init__.py": "from .b import *\nfrom .a import *\n",
                    "a.py": f"{_DEP}@{_EXPIRED}\ndef old():\n    pass\n",
                    "b.py": "def old():\n    pass\n",
                },
                False,
                id="later-star-import-of-a-wrapper-wins",
            ),
            pytest.param(
                {
                    "cfg.py": f"{_DEP}dep = {_EXPIRED}\n",
                    "x.py": "def dep(f):\n    return f\n\nfrom .cfg import *\n",
                    "m.py": "from .x import dep\n\n@dep\ndef old():\n    pass\n",
                },
                True,
                id="configured-decorator-star-imported-over-a-local-binding",
            ),
            pytest.param(
                {
                    "m.py": f"from typing import TYPE_CHECKING\n{_DEP}\nclass API:\n    @{_EXPIRED}\n"
                    "    def old(self):\n        pass\n\n"
                    "    if TYPE_CHECKING:\n        def old(self):\n            pass\n"
                },
                True,
                id="member-redefined-under-type-checking-keeps-its-wrapper",
            ),
            pytest.param(
                {"m.py": f"import abc\n{_DEP}\nclass K(abc.ABC):\n    @{_EXPIRED}\n    def old(self):\n        pass\n"},
                True,
                id="standard-library-base",
            ),
            pytest.param(
                {
                    "m.py": f"import abc\nimport functools\nfrom functools import lru_cache\n{_DEP}\n"
                    f"@functools.lru_cache(maxsize=None)\n@{_EXPIRED}\ndef cached():\n    pass\n\n"
                    f"@lru_cache\n@{_EXPIRED}\ndef bare_cached():\n    pass\n\n"
                    f"class K(abc.ABC):\n    @property\n    @{_EXPIRED}\n    def size(self):\n        return 1\n\n"
                    f"    @classmethod\n    @abc.abstractmethod\n    @{_EXPIRED}\n    def make(cls):\n        pass\n"
                },
                True,
                id="transparent-decorators-stacked-over-the-factory",
            ),
        ],
    )
    def test_settled_shape_matches_import_scan(
        self, probe_package: Callable[..., Path], files: dict[str, str], recursive: bool
    ) -> None:
        """Shapes the source settles give exactly the import scan's ``expired`` findings: none missing, none extra.

        Covers an import cycle between modules, a factory re-exported through two modules, a module holding a long
        ``1 + 1 + ...`` chain that the reader handles without a gap, a plain member that shadows an inherited deprecated
        one, a star import filtered by ``__all__``, star imports bound in statement order (a later star import, or a
        local binding after one, wins, also for a configured decorator), a member redefined under
        ``if TYPE_CHECKING:`` (that body never runs, so the deprecated member stays), and a standard-library base
        class, which carries no deprecated member and so needs no gap. A module the reader cannot walk at all is a gap,
        not a parity case; see ``test_module_too_deep_to_read_is_a_gap``.

        """
        pkg = probe_package({"__init__.py": "", **files})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [f.id for f in _expiry_findings(find_deprecation_wrappers(_PKG, recursive), "99.0")]
        assert _expiry_ids(_read_package(pkg, recursive=recursive)[0]) == imported


class TestDynamicSourceBindings:
    """Dynamic bindings cannot be treated as settled replacements or factories."""

    @pytest.mark.parametrize("value", [pytest.param("None", id="none"), pytest.param("TargetMode.NOTIFY", id="notify")])
    def test_attribute_target_is_not_assumed_callable(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], value: str
    ) -> None:
        """A class attribute holding a non-callable target must not hide missing guidance.

        A project centralizes its deprecation policy on a class, so static analysis must report uncertainty if it
        cannot resolve the attribute rather than treating it as a replacement function.
        """
        source = (
            f"from deprecate import deprecated, TargetMode\nclass Settings:\n    target = {value}\n"
            "@deprecated(target=Settings.target, deprecated_in='1.0', remove_in='2.0')\ndef old(): pass\n"
        )
        wrappers = _read_package(_write(tmp_path, {"__init__.py": source}))[0]
        _report_unresolved(wrappers, {"message-required": _GUIDANCE_KEYWORDS})
        assert "target" in capsys.readouterr().err

    def test_factory_rebound_through_globals_is_a_gap(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A factory overwritten through the module namespace cannot produce a settled wrapper.

        A compatibility module replaces its imported factory dynamically before applying a decorator.
        """
        source = (
            "from deprecate import deprecated\nglobals()['deprecated'] = lambda **kw: lambda f: f\n"
            "@deprecated(deprecated_in='1.0', remove_in='2.0')\ndef old(): pass\n"
        )
        wrappers, gaps = _read_package(_write(tmp_path, {"__init__.py": source}))
        _report_unresolved(wrappers, {"expired": ("remove_in",)})
        assert _expiry_ids(wrappers) == []
        assert any(item.kind is _GapKind.STATIC_UNRESOLVED for item in gaps)

    def test_module_qualified_base_matches_import_scan(self, probe_package: Callable[..., Path]) -> None:
        """A subclass inherits deprecated methods from an imported in-tree module.

        A package exposes a subclass using its base module's qualified name, and both class bindings must be audited.
        """
        pkg = probe_package(
            {
                "__init__.py": "from . import base\nclass Derived(base.Base): pass\n",
                "base.py": f"{_DEP}class Base:\n    @{_EXPIRED}\n    def old(self): pass\n",
            }
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [f.id for f in _expiry_findings(find_deprecation_wrappers(_PKG, True), "99.0")]
        assert _expiry_ids(_read_package(pkg)[0]) == imported


class TestStaticRegressionBindings:
    """Exercise static scans of bindings whose runtime values differ from their syntax."""

    def test_class_member_alias_survives_rebinding(self, probe_package: Callable[..., Path]) -> None:
        """An old method remains exposed through an alias after its original name is replaced.

        Both scanners must identify the remaining class attribute as the deprecated wrapper.
        """
        pkg = probe_package(
            {
                "__init__.py": f"{_DEP}class API:\n    @{_EXPIRED}\n    def old(self): pass\n"
                "    alias = old\n    old = None\n"
            }
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [info.function for info in find_deprecation_wrappers(_PKG)]
        assert [info.function for info in _read_package(pkg)[0]] == imported == ["API.alias"]

    def test_module_wrapper_aliased_into_class(self, probe_package: Callable[..., Path]) -> None:
        """A class can expose a deprecated module-level function as a method.

        Class lookup falls back to globals when the class has not bound the name.
        """
        pkg = probe_package({"__init__.py": f"{_DEP}@{_EXPIRED}\ndef old(): pass\nclass API:\n    alias = old\n"})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [info.function for info in find_deprecation_wrappers(_PKG)]
        assert [info.function for info in _read_package(pkg)[0]] == imported

    def test_unknown_proxy_callability_reports_gap(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A factory can return non-callable data to a deprecated instance proxy.

        Without knowing whether the object can be called, the mapping cannot establish guidance.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": "from deprecate import deprecated_instance\n"
                "def factory(): return {}\n"
                "old = deprecated_instance(factory(), args_mapping={'old': 'new'}, "
                "deprecated_in='1.0', remove_in='2.0')\n"
            },
        )
        _static_policy_findings(_read_package(pkg)[0], _GUIDANCE_ONLY)
        assert "callability" in capsys.readouterr().err

    def test_class_target_alias_uses_class_scope(self, probe_package: Callable[..., Path]) -> None:
        """A class-local alias of NOTIFY must not masquerade as migration guidance.

        The static policy check must agree with the imported class's policy result.
        """
        pkg = probe_package(
            {
                "__init__.py": f"from deprecate import TargetMode\n{_DEP}class API:\n"
                "    MODE = TargetMode.NOTIFY\n    REPL = MODE\n"
                "    @deprecated(target=REPL, deprecated_in='1.0', remove_in='2.0')\n"
                "    def old(self): pass\n"
            }
        )
        imported = [f.id for f in _policy_findings(find_deprecation_wrappers(_PKG), _GUIDANCE_ONLY)]
        assert [f.id for f in _static_policy_findings(_read_package(pkg)[0], _GUIDANCE_ONLY)] == imported
        assert imported

    def test_noncallable_instance_mapping_is_inert(self, probe_package: Callable[..., Path]) -> None:
        """A call mapping on a dictionary proxy cannot guide callers.

        Both policy paths must report the missing migration guidance.
        """
        pkg = probe_package(
            {
                "__init__.py": "from deprecate import deprecated_instance\n"
                "old = deprecated_instance({}, args_mapping={'old': 'new'}, "
                "deprecated_in='1.0', remove_in='2.0')\n"
            }
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [f.id for f in _policy_findings(find_deprecation_wrappers(_PKG), _GUIDANCE_ONLY)]
        assert [f.id for f in _static_policy_findings(_read_package(pkg)[0], _GUIDANCE_ONLY)] == imported
        assert imported

    def test_conditional_callable_target_is_unresolved(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A conditional call method may make a target callable.

        The scanner must not classify that instance as definitely non-callable data.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": "from deprecate import deprecated_instance\n"
                "class Target:\n    if True:\n        def __call__(self): pass\n"
                "old = deprecated_instance({}, target=Target(), deprecated_in='1.0', remove_in='2.0')\n"
            },
        )
        _static_policy_findings(_read_package(pkg)[0], _GUIDANCE_ONLY)
        assert "not statically resolvable" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "nested",
        [
            pytest.param("def run(self):\n        def __call__(self): pass", id="nested-function"),
            pytest.param("class Inner:\n        def __call__(self): pass", id="nested-class"),
        ],
    )
    def test_nested_call_method_does_not_make_target_callable(self, tmp_path: Path, nested: str) -> None:
        """A call method in a nested scope does not make the enclosing class callable.

        Static policy must still flag a data replacement that provides no migration guidance.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": f"from deprecate import deprecated_instance\nclass Target:\n    {nested}\n"
                "old = deprecated_instance({}, target=Target(), deprecated_in='1.0', remove_in='2.0')\n"
            },
        )
        findings = _static_policy_findings(_read_package(pkg)[0], _GUIDANCE_ONLY)
        assert [finding.id for finding in findings] == [f"message-required:{_PKG}:old"]

    def test_comprehension_target_does_not_make_class_callable(self, tmp_path: Path) -> None:
        """A comprehension variable is local to the comprehension, not a class attribute.

        A data target with a comprehension variable named like the call method still needs guidance.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": "from deprecate import deprecated_instance\n"
                "class Target:\n    values = [__call__ for __call__ in range(2)]\n"
                "old = deprecated_instance({}, target=Target(), deprecated_in='1.0', remove_in='2.0')\n"
            },
        )
        findings = _static_policy_findings(_read_package(pkg)[0], _GUIDANCE_ONLY)
        assert [finding.id for finding in findings] == [f"message-required:{_PKG}:old"]

    def test_imported_call_method_makes_class_callability_unresolved(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An imported class-body binding can make instances callable.

        An imported call method must not be treated as proof that a replacement is plain data.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": "from deprecate import deprecated_instance\n"
                "class Target:\n    from math import sin as __call__\n"
                "old = deprecated_instance({}, target=Target(), deprecated_in='1.0', remove_in='2.0')\n"
            },
        )
        assert _static_policy_findings(_read_package(pkg)[0], _GUIDANCE_ONLY) == []
        assert "not statically resolvable" in capsys.readouterr().err
