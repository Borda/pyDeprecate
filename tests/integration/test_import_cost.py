"""Import-cost tests: ``import deprecate`` loads only the decorator core, the audit tooling loads on first access.

Libraries import ``deprecate`` at module import time just to decorate a few functions, so every module the package pulls
in is paid by every downstream ``import``.  The audit tooling (``deprecate.audit``) and its ``importlib.metadata``,
``zipfile``, ``json`` and ``tempfile`` dependencies are needed only by CI scans and the CLI.  Each check runs in a fresh
isolated interpreter (``python -I``) because the test process itself has already imported everything. The probes of
``TestLazyAuditImport`` carry the ``without_coverage`` marker and run as ``pytest -m without_coverage``, without
``--cov``: coverage 7.10.x, the newest release for Python 3.9, imports ``json`` in every child interpreter, which these
probes would read as a regression in ``deprecate``.

"""

import json

import pytest

import deprecate
from tests._helpers import run_isolated_python

#: Modules that a plain ``import deprecate`` must not load.
_HEAVY_MODULES = ("importlib.metadata", "zipfile", "json", "tempfile")


@pytest.mark.without_coverage
class TestLazyAuditImport:
    """``import deprecate`` defers the audit tooling until one of its names is used."""

    @pytest.mark.subprocess
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
        loaded = json.loads(run_isolated_python(code))
        assert [name for name in loaded if name.startswith("deprecate.audit")] == []
        assert [name for name in loaded if name in _HEAVY_MODULES] == []

    @pytest.mark.subprocess
    def test_plain_import_skips_json(self) -> None:
        """``json`` stays unloaded after ``import deprecate`` when nothing else imported it first.

        The previous check needs ``json`` to report its result, so this variant reports through a plain ``print`` of
        booleans instead, covering the one heavy module the first probe could not observe.

        """
        code = "import sys\nimport deprecate\nprint('json' in sys.modules, 'deprecate.audit' in sys.modules)\n"
        assert run_isolated_python(code) == "False False"

    @pytest.mark.subprocess
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
        assert run_isolated_python(code) == "True"

    @pytest.mark.subprocess
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
        assert run_isolated_python(code) == "[]"

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


@pytest.mark.subprocess
def test_proxy_carries_wrapped_object_dunders() -> None:
    """A proxy built right after a bare ``import deprecate`` carries the dunders its wrapped object has.

    Each proxy class is generated from the protocols of the object it wraps, so a program that never imports the
    proxy's helper modules itself must still get ``+`` on a list and ``with`` on a lock, and must not get a context
    protocol on an object that has none.

    """
    code = (
        "import deprecate, threading\n"
        "proxy = deprecate.deprecated_instance([1, 2], deprecated_in='1.0', remove_in='2.0', stream=None)\n"
        "lock = deprecate.deprecated_instance(threading.Lock(), deprecated_in='1.0', remove_in='2.0', stream=None)\n"
        "print(proxy + [3], hasattr(type(lock), '__enter__'), hasattr(type(proxy), '__enter__'))\n"
    )
    # A list has no context protocol, so its proxy carries no ``__enter__``; a lock does, so its proxy carries one.
    assert run_isolated_python(code) == "[1, 2, 3] True False"
