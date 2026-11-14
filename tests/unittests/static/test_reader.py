"""Unit tests for the static source reader."""

import sys
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path
from typing import Callable

import pytest

from deprecate import TargetMode
from deprecate._gaps import _FoundGap, _GapKind
from deprecate._static.discovery import _StaticSource
from deprecate._static.rows import _read_static
from tests.unittests.static._helpers import _DEP, _EXPIRED, _PKG, _read_package, _rows, _write
from tests.unittests.static._helpers import probe_package as probe_package


class TestStaticGapData:
    """Internal source reads collect gaps without writing to either output stream."""

    def test_rows_return_gaps_silently(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """An unread external base produces a row-building gap as data.

        A library caller reads a class whose base belongs to another distribution. The reader cannot audit its
        inherited members, so it returns the reason without printing a CLI diagnostic.
        """
        pkg = _write(tmp_path, {"__init__.py": "from external_library import Base\nclass Client(Base): pass\n"})
        wrappers, gaps = _read_static(
            _StaticSource("package", _PKG, pkg, pkg.parent), recursive=True, include_members=True, exclude=[]
        )
        assert wrappers == []
        assert gaps == [
            _FoundGap(
                _GapKind.STATIC_UNRESOLVED,
                f"{_PKG}.Client: members inherited from `Base` (imported from `external_library`, whose source is not"
                " read) are not audited",
            )
        ]
        captured = capsys.readouterr()
        assert captured.out == captured.err == ""

    def test_discovery_returns_gaps_silently(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Nested files produce a discovery gap without printing it.

        A library caller scans a directory of scripts with a nested helper. Discovery reports that the top-level
        scan omits that helper in the returned gaps, leaving presentation to its caller.
        """
        (tmp_path / "nested").mkdir()
        (tmp_path / "nested" / "helper.py").touch()
        wrappers, gaps = _read_static(
            _StaticSource("plain", str(tmp_path), tmp_path, None), recursive=True, include_members=True, exclude=[]
        )
        assert wrappers == []
        assert gaps == [
            _FoundGap(
                _GapKind.NESTED_FILES,
                "Skipping nested Python files in plain directory scan. Use an importable package layout with"
                " '__init__.py' files, or scan an importable module/package path instead.",
            )
        ]
        captured = capsys.readouterr()
        assert captured.out == captured.err == ""

    def test_reader_returns_gaps_silently(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A missing source reached through imports produces one reader gap without printing it.

        A package imports two names from the same compiled-only module. The read follows both references, but its
        per-module deduplication returns the missing-source reason only once and keeps the output streams empty.
        """
        pkg = _write(tmp_path, {"__init__.py": "from .compiled import first, second\n"})
        (pkg / f"compiled{EXTENSION_SUFFIXES[0]}").write_bytes(b"")
        wrappers, gaps = _read_static(
            _StaticSource("package", _PKG, pkg, pkg.parent), recursive=True, include_members=True, exclude=[]
        )
        assert wrappers == []
        assert gaps == [_FoundGap(_GapKind.MODULE_NOT_AUDITED, f"{_PKG}.compiled: no Python source to read")]
        captured = capsys.readouterr()
        assert captured.out == captured.err == ""


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
        found = _read_package(pkg)[0]
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
        found = _read_package(_write(tmp_path, {"__init__.py": source}))[0]
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
        assert _rows(_read_package(pkg)[0]) == [f"{_PKG}.api:old"]

    @pytest.mark.parametrize(
        ("body", "line"),
        [
            pytest.param("def build(fn):\n    return deprecated(deprecated_in='1.0')(fn)\n", 4, id="inside-function"),
            pytest.param("legacy = deprecated\n", 3, id="rebound-name"),
            pytest.param("import deprecate\nlegacy = deprecate.deprecated\n", 4, id="rebound-attribute"),
        ],
    )
    def test_unrecognized_use_is_a_gap(self, tmp_path: Path, body: str, line: int) -> None:
        """A factory used where the reader recognizes no wrapper shape is reported at its line, never ignored.

        Building wrappers inside a function is invisible to an import scan too, and a factory kept under another name
        could decorate anything later; a static audit cannot tell either from a wrapper it failed to read.

        """
        source = "from deprecate import deprecated\n\n" + body
        wrappers, gaps = _read_package(_write(tmp_path, {"__init__.py": source}))
        assert wrappers == []
        assert any(f"__init__.py:{line}: unrecognized use of `deprecate" in item.detail for item in gaps)


class TestBindingIdentity:
    """Rows follow the import scan's one-row-per-public-binding rule within the scanned tree."""

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
        found = _read_package(pkg)[0]
        assert [(info.module, info.function, info.deprecated_info.remove_in) for info in found] == [
            (f"{_PKG}.api", "Old", "2.5")
        ]


class TestResolvedSettings:
    """Each check sees exactly the wrappers whose settings it reads were settled from source."""

    def test_literal_dict_splat_resolves(self, tmp_path: Path) -> None:
        """Settings splatted from a module-level dict literal are read, as the project's own collections spell them.

        The unification pattern shares ``deprecated_in``/``remove_in`` through ``**_ARGS``; treating every splat as
        a gap would leave such packages unaudited.

        """
        source = (
            "from deprecate import TargetMode, deprecated\n\n_ARGS = {'deprecated_in': '1.0', 'remove_in': '2.0'}\n\n"
            "@deprecated(target=TargetMode.NOTIFY, **_ARGS)\ndef old(x):\n    return x\n"
        )
        (info,) = _read_package(_write(tmp_path, {"__init__.py": source}))[0]
        assert (info.deprecated_info.deprecated_in, info.deprecated_info.remove_in) == ("1.0", "2.0")
        assert info.deprecated_info.target is TargetMode.NOTIFY


class TestShapeParity:
    """Less common shapes yield the rows and policy findings of the import scan."""

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("def original(): pass\nalias = original\nalias = original\n", id="rebound-alias"),
            pytest.param("first = second\nsecond = first\nalias = first\n", id="cycle"),
        ],
    )
    def test_call_form_unsettled_alias_has_no_source_location(
        self, probe_package: Callable[..., Path], source: str
    ) -> None:
        """An ambiguous or cyclic alias never gives a wrapper a guessed source location.

        Static scanning can encounter unimportable or rebound source and must terminate without inventing a location.

        """
        pkg = probe_package({"__init__.py": f"{_DEP}{source}old = {_EXPIRED}(alias)\n"})
        wrappers = _read_package(pkg)[0]
        assert len(wrappers) == 1
        assert wrappers[0].source_file is None
        assert wrappers[0].source_line is None

    def test_unattributable_wrapper_is_a_gap(self, tmp_path: Path) -> None:
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
        wrappers, gaps = _read_package(pkg)
        assert wrappers == []
        assert any(f"{_PKG}.api.old: wraps an object imported from `{_PKG}.other`" in item.detail for item in gaps)

    def test_module_deprecating_another_module_is_a_gap(self, tmp_path: Path) -> None:
        """``deprecated_module`` naming another module is not this module's row; the call is reported instead.

        The import scan attributes it to the named module when that module is walked; static mode cannot tell when.

        """
        source = "from deprecate import deprecated_module\n\ndeprecated_module('elsewhere', deprecated_in='1.0')\n"
        wrappers, gaps = _read_package(_write(tmp_path, {"__init__.py": source}))
        assert wrappers == []
        assert any("unrecognized use of `deprecated_module`" in item.detail for item in gaps)


class TestNoSilentMiss:
    """Shapes the source alone cannot settle print a gap; shapes it can settle give the import scan's findings."""

    @pytest.mark.parametrize(
        ("source", "gap"),
        [
            pytest.param(
                # twice the interpreter's recursion limit: parses, but the analyzer's tree walk cannot follow it
                "x = a" + ".b" * (2 * sys.getrecursionlimit()) + "\n",
                f"{_PKG}.deep: its source is nested too deeply to analyze",
                id="attribute-chain-too-deep-for-the-analyzer",
            ),
            pytest.param(
                # past the tokenizer's 200-level bracket limit: the parser refuses it before any analysis
                "x = " + "(" * 250 + "1" + ")" * 250 + "\n",
                f"{_PKG}.deep: could not read its source",
                id="parentheses-too-deep-for-the-parser",
            ),
        ],
    )
    def test_module_too_deep_to_read_is_a_gap(self, tmp_path: Path, source: str, gap: str) -> None:
        """A module too deeply nested to analyze is a ``module not audited`` gap while its sibling is still read.

        A package ships one generated module with an enormous expression next to a module holding an expired
        wrapper. Skipping the generated module silently would hide whatever it holds, so the gap is printed, and the
        sibling wrapper must still be found. The analyzer-level case gives up on a tree it parsed; the parse-level
        case never gets a tree.

        """
        pkg = _write(
            tmp_path, {"__init__.py": "", "deep.py": source, "ok.py": f"{_DEP}@{_EXPIRED}\ndef old():\n    pass\n"}
        )
        wrappers, gaps = _read_package(pkg)
        assert _rows(wrappers) == [f"{_PKG}.ok:old"]
        assert any(gap in item.detail for item in gaps)


class TestDynamicSourceBindings:
    """Dynamic bindings cannot be treated as settled replacements or factories."""

    def test_rebound_base_module_is_not_assumed(self, tmp_path: Path) -> None:
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
        wrappers, gaps = _read_package(pkg)
        assert _rows(wrappers) == [f"{_PKG}.base:Base.old"]
        assert any("members inherited from `base.Base`" in item.detail for item in gaps)


class TestStaticRegressionBindings:
    """Exercise static scans of bindings whose runtime values differ from their syntax."""

    def test_suppressed_exception_in_with_reports_gap(self, tmp_path: Path) -> None:
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
        rows, gaps = _read_package(pkg)
        assert [info.function for info in rows] == ["old"] or any(
            item.kind is _GapKind.STATIC_UNRESOLVED for item in gaps
        )

    def test_first_with_assignment_can_fail(self, tmp_path: Path) -> None:
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
        _, gaps = _read_package(pkg)
        assert any(item.kind is _GapKind.STATIC_UNRESOLVED for item in gaps)

    def test_suppressed_call_does_not_create_phantom(self, tmp_path: Path) -> None:
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
        wrappers, gaps = _read_package(pkg)
        assert wrappers == []
        assert any(item.kind is _GapKind.STATIC_UNRESOLVED for item in gaps)

    def test_excluded_root_has_no_rows(self, tmp_path: Path) -> None:
        """An excluded package root must not contribute its deprecated wrappers.

        A root-only scan should honor the same exclusion as an import scan.
        """
        pkg = _write(tmp_path, {"__init__.py": f"{_DEP}@{_EXPIRED}\ndef old(): pass\n"})
        assert _read_package(pkg, exclude=(_PKG,))[0] == []

    def test_suppressed_import_does_not_create_phantom(self, tmp_path: Path) -> None:
        """A suppressed missing optional import prevents a later wrapper from being created.

        Static scanning must report the uncertainty instead of claiming the skipped wrapper exists.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": f"from contextlib import suppress\n{_DEP}"
                "with suppress(ImportError):\n    import unavailable_optional_dependency\n"
                f"    @{_EXPIRED}\n    def old(): pass\n"
            },
        )
        wrappers, gaps = _read_package(pkg)
        assert wrappers == []
        assert any(item.kind is _GapKind.STATIC_UNRESOLVED for item in gaps)

    def test_explicit_none_names_current_module(self, tmp_path: Path) -> None:
        """An explicit None module name has the same meaning as an omitted name.

        Projects passing it explicitly still need their module removal deadline audited.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": "from deprecate import deprecated_module\n"
                "deprecated_module(None, deprecated_in='1.0', remove_in='2.0')\n"
            },
        )
        assert [row.function for row in _read_package(pkg)[0]] == [""]

    def test_plain_directory_sibling_reexport_is_not_silent(self, tmp_path: Path) -> None:
        """An excluded sibling can still be re-exported by a scanned module.

        A directory scan must report the wrapper or a gap when it cannot follow the import.
        """
        (tmp_path / "a.py").write_text("from b import old\n")
        (tmp_path / "b.py").write_text(f"{_DEP}@{_EXPIRED}\ndef old(): pass\n")
        rows, gaps = _read_static(
            _StaticSource("plain", str(tmp_path), tmp_path, None), recursive=True, include_members=True, exclude=["b"]
        )
        assert [(row.module, row.function) for row in rows] == [("a", "old")] or (
            any("import from `b` is not audited" in item.detail for item in gaps)
        )

    def test_unknown_earlier_base_does_not_claim_inherited_wrapper(self, tmp_path: Path) -> None:
        """An external base may shadow a later local deprecated method.

        The scanner must retain the unknown-base gap without reporting an uncertain inherited row.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": f"{_DEP}from external_package import External\n"
                f"class Local:\n    @{_EXPIRED}\n    def old(self): pass\n"
                "class Child(External, Local): pass\n"
            },
        )
        wrappers, gaps = _read_package(pkg)
        assert "Child.old" not in [row.function for row in wrappers]
        assert any(item.kind is _GapKind.STATIC_UNRESOLVED for item in gaps)

    def test_import_inside_function_does_not_interrupt_with_body(self, tmp_path: Path) -> None:
        """An import inside an uncalled function does not interrupt a module-level context manager.

        A wrapper defined after the function is created normally and must remain visible.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": f"from contextlib import nullcontext\n{_DEP}"
                "with nullcontext():\n    def helper():\n        import os\n"
                f"    @{_EXPIRED}\n    def old(): pass\n"
            },
        )
        assert [row.function for row in _read_package(pkg)[0]] == ["old"]

    def test_function_local_sibling_import_has_no_reexport_gap(self, tmp_path: Path) -> None:
        """A function-local import does not create a module re-export.

        An uncalled helper importing a sibling should not report a missing module-level wrapper.
        """
        (tmp_path / "a.py").write_text("def helper():\n    from b import old\n")
        (tmp_path / "b.py").write_text("old = 1\n")
        _, gaps = _read_static(
            _StaticSource("plain", str(tmp_path), tmp_path, None), recursive=True, include_members=True, exclude=["b"]
        )
        assert not any(item.kind is _GapKind.STATIC_UNRESOLVED for item in gaps)

    def test_shared_unknown_base_preserves_visible_member(self, tmp_path: Path) -> None:
        """A shared external base follows both local branches in the C3 order.

        A method on the later local branch stays visible ahead of their shared unknown ancestor.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": f"{_DEP}from external_package import External\n"
                "class A(External): pass\n"
                f"class B(External):\n    @{_EXPIRED}\n    def old(self): pass\n"
                "class Child(A, B): pass\n"
            },
        )
        assert "Child.old" in [row.function for row in _read_package(pkg)[0]]

    def test_plain_directory_sibling_package_reexport_is_not_silent(self, tmp_path: Path) -> None:
        """A sibling package can be re-exported from a scanned top-level module.

        An excluded sibling package must still produce a gap when its exported wrapper cannot be read.
        """
        (tmp_path / "a.py").write_text("from b import old\n")
        (tmp_path / "b").mkdir()
        (tmp_path / "b" / "__init__.py").write_text(f"{_DEP}@{_EXPIRED}\ndef old(): pass\n")
        _, gaps = _read_static(
            _StaticSource("plain", str(tmp_path), tmp_path, None), recursive=True, include_members=True, exclude=["b"]
        )
        assert any(item.kind is _GapKind.STATIC_UNRESOLVED for item in gaps)

    @pytest.mark.parametrize(
        "helper",
        [
            pytest.param("def helper():\n        return object().method()", id="function-body"),
            pytest.param("async def helper():\n        return object().method()", id="async-function-body"),
            pytest.param("helper = lambda: object().method()", id="lambda-body"),
        ],
    )
    def test_deferred_call_does_not_interrupt_with_body(self, tmp_path: Path, helper: str) -> None:
        """A call in a deferred function body cannot stop the surrounding context manager.

        An uncalled helper before a deprecated function must not hide that live wrapper from a static scan.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": f"from contextlib import nullcontext\n{_DEP}with nullcontext():\n    {helper}\n"
                f"    @{_EXPIRED}\n    def old(): pass\n"
            },
        )
        assert [row.function for row in _read_package(pkg)[0]] == ["old"]

    @pytest.mark.parametrize(
        "helper",
        [
            pytest.param("def helper(value=fail()): pass", id="default-argument"),
            pytest.param("@fail()\n    def helper(): pass", id="decorator"),
        ],
    )
    def test_definition_time_call_can_interrupt_with_body(self, tmp_path: Path, helper: str) -> None:
        """A function default or decorator executes before a later wrapper is bound.

        A suppressing context manager may stop at that call, so the later wrapper must remain unresolved.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": f"from contextlib import suppress\n{_DEP}with suppress(Exception):\n    {helper}\n"
                f"    @{_EXPIRED}\n    def old(): pass\n"
            },
        )
        wrappers, gaps = _read_package(pkg)
        assert wrappers == []
        assert any(item.kind is _GapKind.STATIC_UNRESOLVED for item in gaps)

    def test_generator_body_does_not_interrupt_with_body(self, tmp_path: Path) -> None:
        """A generator expression defers its element expression until iteration.

        Creating an unconsumed generator cannot prevent a later wrapper from being bound.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": f"from contextlib import nullcontext\n{_DEP}with nullcontext():\n"
                "    values = (fail() for item in (1, 2))\n"
                f"    @{_EXPIRED}\n    def old(): pass\n"
            },
        )
        assert [row.function for row in _read_package(pkg)[0]] == ["old"]

    def test_generator_outer_iterable_can_interrupt_with_body(self, tmp_path: Path) -> None:
        """A generator expression evaluates its first iterable immediately.

        A failing iterable call can be suppressed before a later wrapper is created.
        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": f"from contextlib import suppress\n{_DEP}with suppress(Exception):\n"
                "    values = (item for item in fail())\n"
                f"    @{_EXPIRED}\n    def old(): pass\n"
            },
        )
        wrappers, gaps = _read_package(pkg)
        assert wrappers == []
        assert any(item.kind is _GapKind.STATIC_UNRESOLVED for item in gaps)
