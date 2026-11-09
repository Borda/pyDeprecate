"""Unit tests for :mod:`deprecate._cli.static`: reading deprecation wrappers from source for ``--static``."""

import sys
import textwrap
import warnings
from collections.abc import Iterator
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path
from typing import Callable, Optional

import pytest

from deprecate import TargetMode
from deprecate._cli.static import (
    _GUIDANCE_KEYWORDS,
    _locate_static,
    _policy_keywords,
    _read_static,
    _report_unresolved,
    _resolved,
    _static_policy_findings,
    _StaticSource,
    _StaticTarget,
)
from deprecate.audit import DeprecationWrapperInfo, find_deprecation_wrappers
from deprecate.audit._lifecycle import _expiry_findings
from deprecate.audit._policy import _build_policy_spec, _policy_findings
from deprecate.proxy import deprecated_instance

_ROOT = Path(__file__).resolve().parents[3]
#: Name of the throwaway package each source-snippet test writes, imports for comparison and then forgets.
_PKG = "staticprobe"
#: The policy with only ``message-required`` on, so a target case is judged on guidance alone.
_GUIDANCE_ONLY = _build_policy_spec(None, True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(root: Path, files: dict[str, str]) -> Path:
    """Write *files* (``relative path -> source``) under *root* and return the package directory it fills."""
    for relative, source in files.items():
        path = root / _PKG / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source))
    return root / _PKG


def _read_package(pkg: Path, *, recursive: bool = True, exclude: tuple[str, ...] = ()) -> list[DeprecationWrapperInfo]:
    """Read every wrapper of the package directory *pkg* with the static reader."""
    source = _StaticSource("package", pkg.name, pkg, pkg.parent)
    return _read_static(source, recursive=recursive, include_members=True, exclude=list(exclude))


def _read_module(name: str) -> list[DeprecationWrapperInfo]:
    """Read one module of this repository's test tree with the static reader, as a single source file."""
    path = _ROOT.joinpath(*name.split(".")).with_suffix(".py")
    return _read_static(_StaticSource("module", name, path, _ROOT), recursive=False, include_members=True, exclude=[])


def _line_of(module: str, text: str) -> int:
    """Return the 1-based number of the first line of *module*'s source that contains *text*."""
    lines = _ROOT.joinpath(*module.split(".")).with_suffix(".py").read_text().splitlines()
    return next(number for number, line in enumerate(lines, start=1) if text in line)


def _expiry_ids(wrappers: list[DeprecationWrapperInfo]) -> list[str]:
    """Return the ids of the ``expired`` findings the wrappers whose removal version resolved produce at ``99.0``."""
    return [f.id for f in _expiry_findings(_resolved(wrappers, ("remove_in",)), "99.0")]


def _rows(wrappers: list[DeprecationWrapperInfo]) -> list[str]:
    """Return ``module:function`` for each wrapper, in order."""
    return [f"{info.module}:{info.function}" for info in wrappers]


@pytest.fixture
def probe_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Path]]:
    """Write a throwaway package on an import path, forgetting every module it imported at teardown."""
    monkeypatch.syspath_prepend(str(tmp_path))
    yield lambda files: _write(tmp_path, files)
    for name in [name for name in sys.modules if name == _PKG or name.startswith(f"{_PKG}.")]:
        del sys.modules[name]


# ---------------------------------------------------------------------------
# Parity with the import scan on real collections
# ---------------------------------------------------------------------------


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
        static = {f.id: f.message for f in _expiry_findings(_resolved(_read_module(module), ("remove_in",)), "99.0")}
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
        assert [(f.id, f.message) for f in _static_policy_findings(_read_module(module), spec)] == imported

    def test_rows_match_import_scan(self) -> None:
        """The static read names and orders the static-forms wrappers as the import scan does, ``api_type`` included.

        The one wrapper built through ``partial(deprecated, ...)`` has no row; its factory use is reported as a gap.

        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = find_deprecation_wrappers("tests.collections.static_forms", recursive=False)
        static = _read_module("tests.collections.static_forms")
        expected = [(i.function, i.api_type) for i in imported if i.function != "partial_built"]
        assert [(info.function, info.api_type) for info in static] == expected

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
        wrappers = _read_module(module)
        _report_unresolved(wrappers, ("remove_in",))
        static = {f.symbol for f in _expiry_findings(_resolved(wrappers, ("remove_in",)), "99.0")}
        err = capsys.readouterr().err
        partial_line = _line_of(module, "= partial(deprecated")
        expected = {
            "ambiguous_removal": f"`{module}.ambiguous_removal` not checked",
            "formatted_removal": f"`{module}.formatted_removal` not checked",
            "called_splat": f"`{module}.called_splat` not checked",
            "partial_built": f"static_forms.py:{partial_line}: unrecognized use of `deprecated`",
        }
        explained = {symbol: expected.get(symbol, "") in err for symbol in imported - static}
        assert explained == dict.fromkeys(expected, True)

    def test_unsettled_forms_are_gaps(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Each form the source does not settle prints a gap naming its file, line and reason.

        A gap is what keeps a static audit honest: the wrapper is left out of the rule that needs the unsettled value,
        and the line says so instead of the wrapper silently passing.

        """
        wrappers = _read_module("tests.collections.static_forms")
        _report_unresolved(wrappers, ("remove_in", "target"))
        err = capsys.readouterr().err
        assert "`remove_in` is `REMOVAL`, not a literal" in err
        assert "`remove_in` is `f'{_MAJOR}.0'`, not a literal" in err
        assert "arguments come from `**dict(deprecated_in='1.0', remove_in='2.0')`" in err
        assert "`target` is `_REPLACEMENTS['double']`, not a literal or a name" in err
        assert "static_forms.py" in err
        assert "unrecognized use of `deprecated`" in err


# ---------------------------------------------------------------------------
# Recognizing pyDeprecate names
# ---------------------------------------------------------------------------


class TestFactoryNames:
    """Every way a module can import a pyDeprecate factory makes its wrappers visible."""

    @pytest.mark.parametrize(
        "header",
        [
            pytest.param("from deprecate import deprecated", id="from-package"),
            pytest.param("from deprecate import deprecated as deprecated", id="explicit-as"),
            pytest.param("from deprecate.deprecation import deprecated", id="from-defining-module"),
            pytest.param("from deprecate import *", id="star-import"),
        ],
    )
    def test_import_forms_bind_the_factory(self, tmp_path: Path, header: str) -> None:
        """A ``@deprecated`` function is found whatever import statement bound the decorator name.

        Projects import the factory from the package root, from its defining module, through a star import, or keep
        a reference under another name; each must still lead to the wrapper.

        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": f"{header}\n\n@deprecated(deprecated_in='1.0', remove_in='2.0')\n"
                "def old(x):\n    return x\n"
            },
        )
        found = _read_package(pkg)
        assert [(info.function, info.deprecated_info.remove_in) for info in found] == [("old", "2.0")]

    @pytest.mark.parametrize(
        ("header", "decorator"),
        [
            pytest.param("import deprecate", "deprecate.deprecated", id="module"),
            pytest.param("import deprecate as d", "d.deprecated", id="module-alias"),
            pytest.param("import deprecate.proxy", "deprecate.proxy.deprecated_class", id="submodule"),
            pytest.param("from deprecate import proxy", "proxy.deprecated_class", id="from-submodule"),
        ],
    )
    def test_attribute_chains_name_the_factory(self, tmp_path: Path, header: str, decorator: str) -> None:
        """A factory reached through a module attribute chain is recognized.

        ``import deprecate as d`` with ``@d.deprecated(...)`` is as common as the bare name; the reader follows the
        chain to the pyDeprecate module and the object it exports.

        """
        source = f"{header}\n\n@{decorator}(deprecated_in='1.0', remove_in='2.0')\nclass Old:\n    pass\n"
        found = _read_package(_write(tmp_path, {"__init__.py": source}))
        assert [info.function for info in found] == ["Old"]

    def test_in_tree_factory_reexport(self, tmp_path: Path) -> None:
        """A factory a project re-exports from its own compatibility module is followed one hop.

        ``mypkg/_compat.py`` imports ``deprecated`` and the rest of the package imports it from there; without the hop
        the decorator name would look foreign and its wrappers would vanish.

        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": "",
                "_compat.py": "from deprecate import deprecated\n",
                "api.py": "from ._compat import deprecated\n\n@deprecated(deprecated_in='1.0', remove_in='2.0')\n"
                "def old(x):\n    return x\n",
            },
        )
        assert _rows(_read_package(pkg)) == [f"{_PKG}.api:old"]

    @pytest.mark.parametrize(
        ("body", "line"),
        [
            pytest.param("def build(fn):\n    return deprecated(deprecated_in='1.0')(fn)\n", 4, id="inside-function"),
            pytest.param("legacy = deprecated\n", 3, id="rebound-name"),
            pytest.param("import deprecate\nlegacy = deprecate.deprecated\n", 4, id="rebound-attribute"),
        ],
    )
    def test_unrecognized_use_is_a_gap(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], body: str, line: int
    ) -> None:
        """A factory used where the reader recognizes no wrapper shape is reported at its line, never ignored.

        Building wrappers inside a function is invisible to an import scan too, and a factory kept under another name
        could decorate anything later; a static audit cannot tell either from a wrapper it failed to read.

        """
        source = "from deprecate import deprecated\n\n" + body
        assert _read_package(_write(tmp_path, {"__init__.py": source})) == []
        assert f"__init__.py:{line}: unrecognized use of `deprecate" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The target each factory stores, judged by ``message-required``
# ---------------------------------------------------------------------------


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
        static = [f.id for f in _static_policy_findings(_read_package(pkg), _GUIDANCE_ONLY)]
        assert static == imported

    def test_unsettled_target_skips_only_guidance(self, tmp_path: Path) -> None:
        """A target the source does not settle leaves the wrapper out of ``message-required`` but not ``min-grace``.

        One unsettled setting must not hide a violation another rule can prove: the grace window here is one minor,
        which ``min-grace`` still reports.

        """
        source = (
            "from deprecate import deprecated\n\nTABLE = {}\n\n"
            "@deprecated(target=TABLE['x'], deprecated_in='1.0', remove_in='1.1')\ndef old(x):\n    return x\n"
        )
        wrappers = _read_package(_write(tmp_path, {"__init__.py": source}))
        findings = _static_policy_findings(wrappers, _build_policy_spec("0.3", True))
        assert [f.rule.value for f in findings] == ["min-grace"]
        assert set(_policy_keywords(_build_policy_spec("0.3", True))) >= set(_GUIDANCE_KEYWORDS)

    @pytest.mark.skipif(
        "target" not in __import__("inspect").signature(deprecated_instance).parameters,
        reason="`deprecated_instance` has no `target` here; the case guards the merge that adds it",
    )
    def test_instance_replacement_matches_import_scan(self, probe_package: Callable[..., Path]) -> None:
        """A data replacement given to ``deprecated_instance`` is judged by ``message-required`` as at runtime.

        Once ``deprecated_instance`` accepts ``target``, a non-callable replacement needs an ``attrs_mapping`` or a
        custom message to count as guidance; static mode must stop treating it as a forwarding callable then.

        """
        body = "NEW = {'a': 1}\nold = deprecated_instance({'a': 1}, target=NEW, deprecated_in='1.0', remove_in='2.0')\n"
        pkg = probe_package({"__init__.py": "from deprecate import deprecated_instance\n\n" + body})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [f.id for f in _policy_findings(find_deprecation_wrappers(_PKG), _GUIDANCE_ONLY)]
        assert [f.id for f in _static_policy_findings(_read_package(pkg), _GUIDANCE_ONLY)] == imported

    def test_static_target_is_never_called(self) -> None:
        """A replacement read from source is a named placeholder that refuses to run.

        It stands in for the real callable only so the policy rule and the report can see that a replacement is named;
        calling it would mean static mode executed something it never imported.

        """
        target = _StaticTarget("pkg.new")
        with pytest.raises(TypeError, match="was read from source by `--static`"):
            target()
        assert (target.__qualname__, target.__module__) == ("pkg.new", "")


# ---------------------------------------------------------------------------
# Rows: which bindings and members report a wrapper
# ---------------------------------------------------------------------------


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
                "@deprecated(deprecated_in='1.0', remove_in='2.0')\ndef zeta(x):\n    return x\n\nalpha = zeta\n",
                ["alpha"],
                id="alphabetically-first-binding",
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
        assert [info.function for info in _read_package(pkg)] == imported == expected

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
        assert _rows(_read_package(pkg)) == imported == [f"{_PKG}:Old", f"{_PKG}.core:old_fn"]

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
        assert _rows(_read_package(pkg, recursive=False)) == imported == [f"{_PKG}:old_fn"]

    def test_imported_configured_decorator(self, tmp_path: Path) -> None:
        """A decorator configured in one module and applied in another is read with the settings it was given.

        Projects hoist a shared ``deprecated_class(...)`` instance into one module and apply it across the package; its
        versions are spelled where it is configured, not where it is applied.

        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": "",
                "shared.py": "from deprecate import deprecated_class\n\n"
                "legacy = deprecated_class(deprecated_in='1.0', remove_in='2.5')\n",
                "api.py": "from .shared import legacy\n\n@legacy\nclass Old:\n    pass\n",
            },
        )
        found = _read_package(pkg)
        assert [(info.module, info.function, info.deprecated_info.remove_in) for info in found] == [
            (f"{_PKG}.api", "Old", "2.5")
        ]

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
        static = _read_package(pkg)
        assert [(i.module, i.function, i.api_type) for i in static] == imported == [(f"{_PKG}.old", "", "module")]
        assert _static_policy_findings(static, _GUIDANCE_ONLY) == []


# ---------------------------------------------------------------------------
# Discovery: which files are read
# ---------------------------------------------------------------------------


class TestDiscovery:
    """The static walk reads the files the import walk would import, and reports the ones it cannot read."""

    def test_excluded_module_is_never_read(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """An excluded submodule is skipped before reading: its syntax error never surfaces.

        ``exclude`` keeps a module out of the audit; reading it anyway could fail the run on a file the user asked
        to leave alone.

        """
        pkg = _write(tmp_path, {"__init__.py": "", "skip.py": "def broken(:\n", "keep.py": "X = 1\n"})
        _read_package(pkg, exclude=(f"{_PKG}.skip",))
        assert "module not audited" not in capsys.readouterr().err

    def test_unreadable_module_is_a_gap(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A file that does not parse is a ``module not audited`` gap, and the rest of the package is still read.

        Import mode reports a submodule that fails to import the same way; one broken file never aborts the audit.

        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": "",
                "broken.py": "def broken(:\n",
                "good.py": "from deprecate import deprecated\n\n@deprecated(deprecated_in='1.0')\ndef old(x):\n"
                "    return x\n",
            },
        )
        assert _rows(_read_package(pkg)) == [f"{_PKG}.good:old"]
        assert f"module not audited: {_PKG}.broken: could not read its source" in capsys.readouterr().err

    def test_module_without_source_is_a_gap(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A compiled extension module with no ``.py`` beside it is reported, since its wrappers cannot be read.

        Import mode would audit it; static mode has nothing to parse, so it says so.

        """
        pkg = _write(tmp_path, {"__init__.py": ""})
        (pkg / f"fast{EXTENSION_SUFFIXES[0]}").write_bytes(b"")  # the running interpreter's own extension suffix
        _read_package(pkg)
        assert f"module not audited: {_PKG}.fast: no Python source to read" in capsys.readouterr().err

    def test_plain_directory_reads_top_level_files(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A plain directory reads its top-level modules by file name and reports nested files as not audited.

        This mirrors the import scan of a directory without ``__init__.py``, which only imports top-level modules.

        """
        (tmp_path / "nested").mkdir()
        (tmp_path / "nested" / "inner.py").write_text("X = 1\n")
        (tmp_path / "mod.py").write_text(
            "from deprecate import deprecated\n\n@deprecated(deprecated_in='1.0')\ndef old(x):\n    return x\n"
        )
        source = _locate_static(str(tmp_path), None)
        assert isinstance(source, _StaticSource)
        found = _read_static(source, recursive=True, include_members=True, exclude=[])
        assert _rows(found) == ["mod:old"]
        assert "nested files not audited" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("path", "message"),
        [
            pytest.param("os.path", "would import its parent packages", id="dotted"),
            pytest.param("no_such_module_xyz", "No such path or importable module", id="missing"),
            pytest.param("sys", "has no Python source", id="builtin"),
        ],
    )
    def test_module_names_without_readable_source(self, path: str, message: str) -> None:
        """A module name static mode cannot read without importing is a usage error, not a silent empty scan.

        Locating a dotted name imports its parents, and a built-in has no source; both would make the scan either run
        code or audit nothing.

        """
        located = _locate_static(path, None)
        assert isinstance(located, str)
        assert message in located

    def test_top_level_module_name_located_without_import(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A top-level module name is located on the import path without running the module.

        The module raises when imported; locating it for a static read must not trigger that.

        """
        (tmp_path / "standalone_static_mod.py").write_text("raise RuntimeError('imported')\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        located = _locate_static("standalone_static_mod", None)
        assert isinstance(located, _StaticSource)
        assert (located.kind, located.location.name) == ("module", "standalone_static_mod.py")
        assert "standalone_static_mod" not in sys.modules


# ---------------------------------------------------------------------------
# Rule settings
# ---------------------------------------------------------------------------


class TestResolvedSettings:
    """Each check sees exactly the wrappers whose settings it reads were settled from source."""

    @pytest.mark.parametrize(
        ("grace", "message_required", "expected"),
        [
            pytest.param("0.3", True, ("deprecated_in", "remove_in", *_GUIDANCE_KEYWORDS), id="both"),
            pytest.param(None, True, _GUIDANCE_KEYWORDS, id="guidance-only"),
            pytest.param("0.3", False, ("deprecated_in", "remove_in"), id="grace-only"),
        ],
    )
    def test_policy_keywords_follow_enabled_rules(
        self, grace: Optional[str], message_required: bool, expected: tuple[str, ...]
    ) -> None:
        """Only the settings an enabled rule reads can make a wrapper a gap for ``policy``.

        A version held in a variable must not be reported when ``min-grace`` is switched off.

        """
        assert _policy_keywords(_build_policy_spec(grace, message_required)) == expected

    def test_literal_dict_splat_resolves(self, tmp_path: Path) -> None:
        """Settings splatted from a module-level dict literal are read, as the project's own collections spell them.

        The unification pattern shares ``deprecated_in``/``remove_in`` through ``**_ARGS``; treating every splat as
        a gap would leave such packages unaudited.

        """
        source = (
            "from deprecate import TargetMode, deprecated\n\n_ARGS = {'deprecated_in': '1.0', 'remove_in': '2.0'}\n\n"
            "@deprecated(target=TargetMode.NOTIFY, **_ARGS)\ndef old(x):\n    return x\n"
        )
        (info,) = _read_package(_write(tmp_path, {"__init__.py": source}))
        assert (info.deprecated_info.deprecated_in, info.deprecated_info.remove_in) == ("1.0", "2.0")
        assert info.deprecated_info.target is TargetMode.NOTIFY

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
        wrappers = _read_package(_write(tmp_path, {"__init__.py": source}))
        _report_unresolved(wrappers, ("deprecated_in", "remove_in", *_GUIDANCE_KEYWORDS))
        assert reason in capsys.readouterr().err


class TestShapeParity:
    """Less common shapes yield the rows and policy findings of the import scan."""

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
        static = _read_package(pkg)
        assert (_rows(static), [f.id for f in _static_policy_findings(static, spec)]) == (
            _rows(imported),
            imported_findings,
        )

    def test_unattributable_wrapper_is_a_gap(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A wrapper around an imported object the source cannot classify is reported rather than guessed.

        Whether the import scan lists it depends on the object being a class (a proxy, kept) or a function (attributed
        to its own module, dropped); without that, a silent choice would risk losing a row.

        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": "",
                "other.py": "def make():\n    return 1\n\nthing = make()\n",
                "api.py": "from deprecate import deprecated\nfrom .other import thing\n\n"
                "old = deprecated(deprecated_in='1.0', remove_in='2.0')(thing)\n",
            },
        )
        assert _read_package(pkg) == []
        assert f"{_PKG}.api.old: wraps an object imported from `{_PKG}.other`" in capsys.readouterr().err

    def test_module_deprecating_another_module_is_a_gap(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``deprecated_module`` naming another module is not this module's row; the call is reported instead.

        The import scan attributes it to the named module when that module is walked; static mode cannot tell when.

        """
        source = "from deprecate import deprecated_module\n\ndeprecated_module('elsewhere', deprecated_in='1.0')\n"
        assert _read_package(_write(tmp_path, {"__init__.py": source})) == []
        assert "unrecognized use of `deprecated_module`" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# No silent miss: every wrapper the import scan finds is a static row or a gap
# ---------------------------------------------------------------------------

_DEP = "from deprecate import deprecated\n"
_EXPIRED = "deprecated(deprecated_in='1.0', remove_in='2.0')"


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
                    "__init__.py": "from .core import *\n",
                    "core.py": f"{_DEP}__all__ = ['old'] + []\n\n@{_EXPIRED}\ndef old():\n    pass\n",
                },
                f"`from {_PKG}.core import *`: `{_PKG}.core` has an `__all__` that is not one literal list of names",
                id="star-import-of-a-computed-all",
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
                {
                    "m0.py": f"{_DEP}@{_EXPIRED}\ndef old():\n    pass\n",
                    **{f"m{n}.py": f"from .m{n - 1} import old\n" for n in range(1, 7)},
                    "__init__.py": "from .m6 import old\n",
                },
                f"{_PKG}.old: re-exported through more than 5 modules; not followed",
                id="re-export-chain-past-the-hop-limit",
            ),
        ],
    )
    def test_unsettled_shape_is_a_gap(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], files: dict[str, str], gap: str
    ) -> None:
        """A wrapper the static read cannot place prints a gap naming why, instead of passing the audit unseen.

        Each case once gave neither a row nor a gap: a configured decorator inside ``try``, a broken file first parsed
        through another module's import, a base class from another distribution, a target or a splat whose value
        depends on runtime, a star import of a computed ``__all__``, a factory name the module rebinds, and a re-export
        chain longer than the reader follows.

        """
        wrappers = _read_package(_write(tmp_path, {"__init__.py": "", **files}))
        _report_unresolved(wrappers, ("remove_in", *_GUIDANCE_KEYWORDS))
        assert gap in capsys.readouterr().err

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
                {"m.py": f"import abc\n{_DEP}\nclass K(abc.ABC):\n    @{_EXPIRED}\n    def old(self):\n        pass\n"},
                True,
                id="standard-library-base",
            ),
        ],
    )
    def test_settled_shape_matches_import_scan(
        self, probe_package: Callable[..., Path], files: dict[str, str], recursive: bool
    ) -> None:
        """Shapes the source settles give exactly the import scan's ``expired`` findings: none missing, none extra.

        Covers an import cycle between modules, a factory re-exported through two modules, a module too deeply nested
        for a recursive tree walk, a plain member that shadows an inherited deprecated one, a star import filtered by
        ``__all__``, and a standard-library base class, which carries no deprecated member and so needs no gap.

        """
        pkg = probe_package({"__init__.py": "", **files})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [f.id for f in _expiry_findings(find_deprecation_wrappers(_PKG, recursive), "99.0")]
        assert _expiry_ids(_read_package(pkg, recursive=recursive)) == imported
