"""Unit tests for the :mod:`deprecate.audit` public surface and its top-level mirror."""

import pytest

import deprecate
import deprecate.audit

# ---------------------------------------------------------------------------
# Top-level package surface
# ---------------------------------------------------------------------------


class TestTopLevelExports:
    """The audit classes a policy caller configures with are mirrored at the package top level.

    Lives next to the :mod:`deprecate.audit` surface it mirrors, the same way ``tests/unittests/test_proxy.py``
    pins ``DeprecationProxy`` next to the code it belongs to.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "ChainType",
            "DeprecationStatus",
            "DeprecationWrapperInfo",
            "GraceWindow",
            "PolicyRule",
            "TableStyle",
            "VersionBump",
        ],
    )
    def test_audit_class_mirrored(self, name: str) -> None:
        """Each audit class is the very same object at ``deprecate.<name>`` and is listed in ``__all__``.

        ``from deprecate import VersionBump`` failed while ``ChainType`` and ``TableStyle`` imported fine, so a
        caller spelling ``min_grace=GraceWindow(3, VersionBump.MINOR)`` had to know which policy names were
        mirrored and which lived only under ``deprecate.audit``. Identity rather than equality pins that the top
        level re-exports the audit object instead of redefining it.
        """
        assert getattr(deprecate, name) is getattr(deprecate.audit, name)
        assert name in deprecate.__all__

    def test_grace_window_spec_stays_audit_only(self) -> None:
        """``GraceWindowSpec`` is deliberately not mirrored: a typing alias, not a class callers instantiate.

        Keeping it under ``deprecate.audit`` only is the documented split; a stray top-level re-export would widen
        the public surface nobody asked for and no other test would notice.
        """
        assert not hasattr(deprecate, "GraceWindowSpec")
        assert "GraceWindowSpec" not in deprecate.__all__
