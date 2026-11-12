"""Unit tests for :mod:`deprecate._cli.static`: reading deprecation wrappers from source for ``--static``."""

import os
import sys
import textwrap
import warnings
from collections.abc import Iterator
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from deprecate import TargetMode
from deprecate._cli.static.checks import _policy_keywords, _report_unresolved, _resolved, _static_policy_findings
from deprecate._cli.static.consts import _GUIDANCE_KEYWORDS
from deprecate._cli.static.discovery import _locate_static, _StaticSource
from deprecate._cli.static.model import _StaticTarget
from deprecate._cli.static.rows import _read_static
from deprecate.audit import DeprecationWrapperInfo, find_deprecation_wrappers
from deprecate.audit._lifecycle import _expiry_findings
from deprecate.audit._policy import _build_policy_spec, _policy_findings

_ROOT = Path(__file__).resolve().parents[3]
#: Name of the throwaway package each source-snippet test writes, imports for comparison and then forgets.
_PKG = "staticprobe"
#: Name of the namespace package (no ``__init__.py``) the discovery tests split across two import-path entries.
_NAMESPACE = "staticnamespaceprobe"
#: The policy with only ``message-required`` on, so a target case is judged on guidance alone.
_GUIDANCE_ONLY = _build_policy_spec(None, True)
#: A module deprecating ``old`` with the ``target`` it imports from the package's ``consts`` module.
_IMPORTED_TARGET_USE = (
    "from deprecate import deprecated\nfrom .consts import REPL\n\n"
    "@deprecated(target=REPL, deprecated_in='1.0', remove_in='2.0')\ndef old(x):\n    return x\n"
)

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


def _write_namespace_portion(root: Path, module: str) -> None:
    """Write one directory of the :data:`_NAMESPACE` package under *root*, holding *module* with one expired wrapper."""
    pkg_dir = root / _NAMESPACE
    pkg_dir.mkdir(parents=True)
    (pkg_dir / f"{module}.py").write_text(
        "from deprecate import deprecated\n\n@deprecated(deprecated_in='1.0', remove_in='2.0')\ndef old():\n    pass\n"
    )


def _refusing(lister: Callable[..., Any], blocked: Path) -> Callable[..., Any]:
    """Wrap a directory lister (``os.scandir``, ``os.listdir``) so it raises ``PermissionError`` for *blocked* only."""

    def _list(path: Any, *args: Any) -> Any:  # noqa: ANN401
        if Path(path) == blocked:
            raise PermissionError(13, "Permission denied", str(path))
        return lister(path, *args)

    return _list


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
            pytest.param(
                "import deprecate\nimport deprecate.proxy", "deprecate.deprecated", id="package-imported-twice"
            ),
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
        assert [f.id for f in _static_policy_findings(_read_package(pkg), _GUIDANCE_ONLY)] == imported

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
        assert [f.id for f in _static_policy_findings(_read_package(pkg), _GUIDANCE_ONLY)] == imported

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
        findings = _static_policy_findings(_read_package(_write(tmp_path, {"__init__.py": source})), _GUIDANCE_ONLY)
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
        findings = _static_policy_findings(_read_package(_write(tmp_path, {"__init__.py": source})), _GUIDANCE_ONLY)
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
        findings = _static_policy_findings(_read_package(_write(tmp_path, {"__init__.py": source})), _GUIDANCE_ONLY)
        assert findings == []
        assert "not a literal or a name" in capsys.readouterr().err

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
        assert [(f.id, f.message) for f in _static_policy_findings(_read_package(pkg), _GUIDANCE_ONLY)] == imported

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
        assert [(f.id, f.message) for f in _static_policy_findings(_read_package(pkg), _GUIDANCE_ONLY)] == imported

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
        wrappers = _read_package(_write(tmp_path, {"__init__.py": source}))
        _static_policy_findings(wrappers, _GUIDANCE_ONLY)
        assert "`message_template` renders `remove_in`" in capsys.readouterr().err


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

    @pytest.mark.parametrize(
        ("lister", "blocked"),
        [pytest.param("scandir", "", id="package-root"), pytest.param("listdir", "sub", id="subpackage")],
    )
    def test_unlistable_directory_is_a_gap(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        lister: str,
        blocked: str,
    ) -> None:
        """A package directory the walk cannot list prints a ``module not audited`` gap naming it.

        A directory without read permission once looked like a package with no submodules, so every wrapper below it
        passed the audit unseen; the gap names the directory and the error, while the rest of the tree is still read.

        """
        pkg = _write(tmp_path, {"__init__.py": "", "sub/__init__.py": "", "m.py": ""})
        monkeypatch.setattr(os, lister, _refusing(getattr(os, lister), pkg / blocked))
        _read_package(pkg)
        assert f"module not audited: submodules of {pkg / blocked} not listed" in capsys.readouterr().err

    def test_module_without_source_is_a_gap(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A compiled extension module with no ``.py`` beside it is reported, since its wrappers cannot be read.

        Import mode would audit it; static mode has nothing to parse, so it says so.

        """
        pkg = _write(tmp_path, {"__init__.py": ""})
        (pkg / f"fast{EXTENSION_SUFFIXES[0]}").write_bytes(b"")  # the running interpreter's own extension suffix
        _read_package(pkg)
        assert f"module not audited: {_PKG}.fast: no Python source to read" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("files", "recursive", "exclude", "gap"),
        [
            pytest.param(
                {"__init__.py": "from .broken import thing\n"},
                False,
                (),
                f"{_PKG}.broken: could not read its source",
                id="no-recursive",
            ),
            pytest.param(
                {"__init__.py": "", "a.py": "from .broken import thing\n"},
                True,
                (f"{_PKG}.broken",),
                f"{_PKG}.broken: could not read its source",
                id="excluded-module-imported-by-a-walked-one",
            ),
            pytest.param(
                {"__init__.py": "from .compiled import thing\n"},
                False,
                (),
                f"{_PKG}.compiled: no Python source to read",
                id="imported-module-without-python-source",
            ),
        ],
    )
    def test_module_reached_only_through_an_import_is_a_gap(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        files: dict[str, str],
        recursive: bool,
        exclude: tuple[str, ...],
        gap: str,
    ) -> None:
        """A module the walk never reaches still reports why it was not audited when another module imports it.

        Under ``--no-recursive`` or ``--exclude`` the broken module is read only to follow the importer's names; the
        reason recorded there is the only signal that the importer's view is incomplete, so it must be printed.

        """
        pkg = _write(tmp_path, {**files, "broken.py": "def (:\n"})
        _read_package(pkg, recursive=recursive, exclude=exclude)
        assert f"module not audited: {gap}" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("recursive", "exclude"),
        [
            pytest.param(False, (), id="no-recursive"),
            pytest.param(True, (f"{_PKG}.core",), id="excluded-module-re-exported-by-a-walked-one"),
        ],
    )
    def test_structural_gaps_of_a_module_reached_only_through_an_import_are_printed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], recursive: bool, exclude: tuple[str, ...]
    ) -> None:
        """A shape the reader cannot place in a module the walk skips is still a gap when another module re-exports it.

        The package re-exports ``old`` from ``core``, which builds its wrapper through ``partial(deprecated, ...)``, a
        shape static mode does not read. Under ``--no-recursive`` or ``--exclude`` ``core`` is analyzed only to follow
        the re-export, and the gap that analysis records is the sole sign that ``old`` went unaudited.

        """
        core = (
            f"from functools import partial\n{_DEP}\n"
            "old = partial(deprecated, deprecated_in='1.0', remove_in='2.0')(print)\n"
        )
        pkg = _write(tmp_path, {"__init__.py": "from .core import old\n", "core.py": core})
        _read_package(pkg, recursive=recursive, exclude=exclude)
        assert "core.py:4: unrecognized use of `deprecated`" in capsys.readouterr().err

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

    def test_namespace_package_reads_every_portion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A namespace package split over two import-path entries is read from both, with no gap for its root.

        The import scan walks every directory of ``__path__`` and has no root module to report; reading only the
        first directory would drop the second one's wrappers silently, and a root gap would cry wolf.

        """
        _write_namespace_portion(tmp_path / "first", "alpha")
        _write_namespace_portion(tmp_path / "second", "beta")
        monkeypatch.syspath_prepend(str(tmp_path / "first"))
        monkeypatch.syspath_prepend(str(tmp_path / "second"))
        source = _locate_static(_NAMESPACE, None)
        assert isinstance(source, _StaticSource)
        found = _read_static(source, recursive=True, include_members=True, exclude=[])
        assert sorted(_rows(found)) == [f"{_NAMESPACE}.alpha:old", f"{_NAMESPACE}.beta:old"]
        assert "module not audited" not in capsys.readouterr().err

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
        assert [(i.function, i.api_type) for i in _read_package(pkg)] == imported

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
        wrappers = _read_package(_write(tmp_path, {"__init__.py": "", **files}))
        _report_unresolved(wrappers, ("remove_in", *_GUIDANCE_KEYWORDS))
        assert gap in capsys.readouterr().err

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
        wrappers = _read_package(_write(tmp_path, {"__init__.py": "", **files}))
        _report_unresolved(wrappers, ("deprecated_in", "remove_in", *_GUIDANCE_KEYWORDS))
        assert "not statically resolvable" not in capsys.readouterr().err

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
        assert _expiry_ids(_read_package(pkg, recursive=recursive)) == imported

    @pytest.mark.parametrize(
        ("source", "gap"),
        [
            pytest.param(
                # twice the interpreter's recursion limit: parses, but the analyzer's tree walk cannot follow it
                "x = a" + ".b" * (2 * sys.getrecursionlimit()) + "\n",
                f"module not audited: {_PKG}.deep: its source is nested too deeply to analyze",
                id="attribute-chain-too-deep-for-the-analyzer",
            ),
            pytest.param(
                # past the tokenizer's 200-level bracket limit: the parser refuses it before any analysis
                "x = " + "(" * 250 + "1" + ")" * 250 + "\n",
                f"module not audited: {_PKG}.deep: could not read its source",
                id="parentheses-too-deep-for-the-parser",
            ),
        ],
    )
    def test_module_too_deep_to_read_is_a_gap(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], source: str, gap: str
    ) -> None:
        """A module too deeply nested to analyze is a ``module not audited`` gap while its sibling is still read.

        A package ships one generated module with an enormous expression next to a module holding an expired
        wrapper. Skipping the generated module silently would hide whatever it holds, so the gap is printed, and the
        sibling wrapper must still be found. The analyzer-level case gives up on a tree it parsed; the parse-level
        case never gets a tree.

        """
        pkg = _write(
            tmp_path, {"__init__.py": "", "deep.py": source, "ok.py": f"{_DEP}@{_EXPIRED}\ndef old():\n    pass\n"}
        )
        assert _rows(_read_package(pkg)) == [f"{_PKG}.ok:old"]
        assert gap in capsys.readouterr().err


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
        wrappers = _read_package(_write(tmp_path, {"__init__.py": source}))
        _report_unresolved(wrappers, _GUIDANCE_KEYWORDS)
        assert "target" in capsys.readouterr().err

    def test_factory_rebound_through_globals_is_a_gap(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A factory overwritten through the module namespace cannot produce a settled wrapper.

        A compatibility module replaces its imported factory dynamically before applying a decorator.
        """
        source = (
            "from deprecate import deprecated\nglobals()['deprecated'] = lambda **kw: lambda f: f\n"
            "@deprecated(deprecated_in='1.0', remove_in='2.0')\ndef old(): pass\n"
        )
        wrappers = _read_package(_write(tmp_path, {"__init__.py": source}))
        _report_unresolved(wrappers, ("remove_in",))
        assert _expiry_ids(wrappers) == []
        assert "not statically resolvable" in capsys.readouterr().err

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
        assert _expiry_ids(_read_package(pkg)) == imported

    def test_rebound_base_module_is_not_assumed(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A dynamically replaced base module must not contribute its former class's deprecated members.

        A package replaces an imported base module through its globals before defining the subclass. The original
        module still has a deprecated method, but the subclass does not inherit it.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": "from . import base\nclass Other: pass\n"
                "globals()['base'] = type('M', (), {'Base': Other})\nclass Derived(base.Base): pass\n",
                "base.py": f"{_DEP}class Base:\n    @{_EXPIRED}\n    def old(self): pass\n",
            },
        )
        assert _rows(_read_package(pkg)) == [f"{_PKG}.base:Base.old"]
        assert "members inherited from `base.Base`" in capsys.readouterr().err


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
        assert [info.function for info in _read_package(pkg)] == imported == ["API.alias"]

    def test_suppressed_exception_in_with_reports_gap(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A context manager can suppress an exception before a wrapper is rebound.

        The scan must not silently assume the later assignment ran.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": f"from contextlib import suppress\n{_DEP}@{_EXPIRED}\ndef old(): pass\n"
                "with suppress(ValueError):\n    raise ValueError()\n    old = None\n"
            },
        )
        rows = _read_package(pkg)
        assert [info.function for info in rows] == ["old"] or "not statically resolvable" in capsys.readouterr().err

    def test_first_with_assignment_can_fail(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A failed assignment can be suppressed before it replaces a wrapper.

        An exception in the first statement leaves the original name live at runtime.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": f"from contextlib import suppress\n{_DEP}@{_EXPIRED}\ndef old(): pass\n"
                "with suppress(ValueError):\n    old = int('bad')\n"
            },
        )
        _read_package(pkg)
        assert "not statically resolvable" in capsys.readouterr().err

    def test_suppressed_call_does_not_create_phantom(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A suppressed error can prevent a later decorator from running.

        Static analysis must not report a wrapper that may never have been created.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": f"from contextlib import suppress\n{_DEP}"
                "with suppress(ValueError):\n    int('bad')\n"
                f"    @{_EXPIRED}\n    def old(): pass\n"
            },
        )
        assert _read_package(pkg) == []
        assert "not statically resolvable" in capsys.readouterr().err

    def test_module_wrapper_aliased_into_class(self, probe_package: Callable[..., Path]) -> None:
        """A class can expose a deprecated module-level function as a method.

        Class lookup falls back to globals when the class has not bound the name.
        """
        pkg = probe_package({"__init__.py": f"{_DEP}@{_EXPIRED}\ndef old(): pass\nclass API:\n    alias = old\n"})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            imported = [info.function for info in find_deprecation_wrappers(_PKG)]
        assert [info.function for info in _read_package(pkg)] == imported

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
        _static_policy_findings(_read_package(pkg), _GUIDANCE_ONLY)
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
        assert [f.id for f in _static_policy_findings(_read_package(pkg), _GUIDANCE_ONLY)] == imported
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
        assert [f.id for f in _static_policy_findings(_read_package(pkg), _GUIDANCE_ONLY)] == imported
        assert imported

    def test_excluded_root_has_no_rows(self, tmp_path: Path) -> None:
        """An excluded package root must not contribute its deprecated wrappers.

        A root-only scan should honor the same exclusion as an import scan.
        """
        pkg = _write(tmp_path, {"__init__.py": f"{_DEP}@{_EXPIRED}\ndef old(): pass\n"})
        assert _read_package(pkg, exclude=(_PKG,)) == []
