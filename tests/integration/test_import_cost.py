"""Import-cost tests: ``import deprecate`` loads only the decorator core, the audit tooling loads on first access.

Libraries import ``deprecate`` at module import time just to decorate a few functions, so every module the package pulls
in is paid by every downstream ``import``.  The audit tooling (``deprecate.audit``) and its ``importlib.metadata``,
``zipfile``, ``json`` and ``tempfile`` dependencies are needed only by CI scans and the CLI.  Each check runs in a fresh
isolated interpreter (``python -I``) because the test process itself has already imported everything.

"""

import json
import subprocess
import sys

import pytest

import deprecate

#: Modules that a plain ``import deprecate`` must not load.
_HEAVY_MODULES = ("importlib.metadata", "zipfile", "json", "tempfile")


def _run_isolated(code: str) -> str:
    """Run ``code`` in a fresh isolated interpreter and return its stripped stdout."""
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True, timeout=60)
    return result.stdout.strip()


class TestLazyAuditImport:
    """``import deprecate`` defers the audit tooling until one of its names is used."""

    def test_plain_import_skips_audit_and_metadata(self) -> None:
        """A bare ``import deprecate`` loads no audit module and none of the heavy stdlib modules.

        A library that only decorates its own functions imports ``deprecate`` at the top of every module.  The audit
        package and ``importlib.metadata`` (with the ``zipfile``/``json``/``tempfile`` modules it drags in) are dead
        weight for that library, so they must stay unloaded until the library or its CI asks for the audit tools.

        """
        code = (
            "import sys, json\n"
            "before = set(sys.modules)\n"
            "import deprecate\n"
            "print(json.dumps(sorted(set(sys.modules) - before)))\n"
        )
        # ``json`` is imported by the probe itself before the snapshot, so its absence here is checked separately below
        loaded = json.loads(_run_isolated(code))
        assert [name for name in loaded if name.startswith("deprecate.audit")] == []
        assert [name for name in loaded if name in _HEAVY_MODULES] == []

    def test_plain_import_skips_json(self) -> None:
        """``json`` stays unloaded after ``import deprecate`` when nothing else imported it first.

        The previous check needs ``json`` to report its result, so this variant reports through a plain ``print`` of
        booleans instead, covering the one heavy module the first probe could not observe.

        """
        code = "import sys\nimport deprecate\nprint('json' in sys.modules, 'deprecate.audit' in sys.modules)\n"
        assert _run_isolated(code) == "False False"

    @pytest.mark.parametrize("name", ["find_deprecation_wrappers", "audit", "DeprecatedCallableInfo"])
    def test_audit_name_resolves_on_access(self, name: str) -> None:
        """Accessing an audit name on the package loads the audit tooling and returns the real object.

        A CI script written against the documented top-level API, such as ``deprecate.find_deprecation_wrappers``, or a
        legacy one using the ``DeprecatedCallableInfo`` alias, keeps working: the first attribute access imports
        ``deprecate.audit`` and hands back the same object the subpackage exposes.

        """
        code = (
            "import sys\n"
            "import deprecate\n"
            "assert 'deprecate.audit' not in sys.modules\n"
            f"value = getattr(deprecate, {name!r})\n"
            "assert 'deprecate.audit' in sys.modules\n"
            "import deprecate.audit as audit\n"
            f"expected = audit if {name!r} == 'audit' else getattr(audit, {name!r})\n"
            "print(value is expected)\n"
        )
        assert _run_isolated(code) == "True"

    def test_star_import_binds_every_public_name(self) -> None:
        """``from deprecate import *`` binds every name in ``__all__``, audit names included.

        Interactive sessions and quick scripts use the star import; the lazy loader must serve the audit names through
        it exactly as the eager imports did.

        """
        code = (
            "ns = {}\n"
            "exec('from deprecate import *', ns)\n"
            "import deprecate\n"
            "print(sorted(set(deprecate.__all__) - set(ns)))\n"
        )
        assert _run_isolated(code) == "[]"

    def test_dir_lists_audit_names(self) -> None:
        """``dir(deprecate)`` lists the lazily served audit names before they are loaded.

        Editors, ``help()`` and API-listing tools enumerate a module with ``dir()``; the audit names must appear there
        even while the audit tooling has not been imported yet.

        """
        listed = set(dir(deprecate))
        assert {"audit", "find_deprecation_wrappers", "validate_deprecation_expiry"} <= listed
        assert set(deprecate.__all__) <= listed

    def test_unknown_name_raises_attribute_error(self) -> None:
        """An unknown attribute still raises ``AttributeError`` naming the module and the attribute.

        ``hasattr`` checks and typos on ``deprecate.`` must keep failing the standard way rather than returning a
        placeholder from the lazy loader.

        """
        with pytest.raises(AttributeError, match="module 'deprecate' has no attribute 'no_such_name'"):
            _ = deprecate.no_such_name


class TestProxyPackageImport:
    """``import deprecate`` alone gives proxies their full protocol forwarding."""

    def test_installs_forwarding_dunders(self) -> None:
        """A proxy built right after a bare ``import deprecate`` forwards operators and the context protocol.

        The operator and context-manager forwarders live in their own module of the proxy package and are installed
        onto the proxy class when that module is imported. A program that never imports the module itself must still
        get a proxy that supports ``+`` and ``with``, so the package has to load it on its own.

        """
        code = (
            "import deprecate\n"
            "proxy = deprecate.deprecated_instance([1, 2], deprecated_in='1.0', remove_in='2.0', stream=None)\n"
            "print(proxy + [3], hasattr(type(proxy), '__enter__'))\n"
        )
        assert _run_isolated(code) == "[1, 2, 3] True"
