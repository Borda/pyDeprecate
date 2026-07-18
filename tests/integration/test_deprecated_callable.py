"""Tests for the strict callable-only alias ``deprecated_callable``.

``deprecated_callable`` is the strict sibling of ``deprecated``: on any callable source it behaves
identically (same forwarding, warning, and ``__deprecated__`` metadata), but applying it to a class
is a decoration-time ``TypeError`` rather than a silent delegation to ``deprecated_class``. These
tests pin both halves of that contract — behavioural parity with ``deprecated`` on callables, and the
loud rejection of class sources.
"""

from typing import cast

import pytest

from deprecate import deprecated_callable
from deprecate._types import _DeprecatedCallable
from tests.collection_deprecate import (
    HolderWithDeprecatedCallableMethod,
    deprecated_callable_double,
    deprecated_double_twin,
    make_deprecated_callable_on_class,
)
from tests.collection_targets import double_value


class TestCallableParity:
    """``deprecated_callable`` matches ``deprecated`` for callable sources."""

    def test_forwards_to_target(self) -> None:
        """A function wrapped with the strict alias forwards to its target and warns.

        A team renames ``double_value`` and wraps the old name with ``@deprecated_callable`` instead of
        ``@deprecated`` to be explicit that only a callable is ever allowed here; calling the old name
        must still return the replacement's result and surface the deprecation.
        """
        with pytest.warns(FutureWarning):
            result = deprecated_callable_double(5)
        assert result == 10

    def test_metadata_matches_deprecated_twin(self) -> None:
        """The alias records the same forwarding config as an identically-configured ``deprecated`` twin.

        Audit tooling discovers wrappers through ``__deprecated__``; the strict alias must populate that
        contract exactly as ``deprecated`` does so a migration to the alias is invisible to the audit
        pipeline. Only the source ``name`` legitimately differs between the two wrapper functions.
        """
        alias_cfg = cast(_DeprecatedCallable, deprecated_callable_double).__deprecated__
        twin_cfg = cast(_DeprecatedCallable, deprecated_double_twin).__deprecated__
        assert alias_cfg.target is double_value
        assert twin_cfg.target is double_value
        assert alias_cfg.deprecated_in == twin_cfg.deprecated_in
        assert alias_cfg.remove_in == twin_cfg.remove_in

    def test_classmethod_source_forwards(self) -> None:
        """The strict alias handles the descriptor path — a deprecated classmethod still forwards.

        Descriptors (``classmethod`` here) route through the recursive ``packing`` descriptor handler; the
        alias adds one wrapper frame, so this guards that the extra frame does not break forwarding for the
        trickier callable kinds, not just plain functions.
        """
        with pytest.warns(FutureWarning):
            result = HolderWithDeprecatedCallableMethod.old_double(6)
        assert result == 12


class TestClassSourceRejected:
    """``deprecated_callable`` refuses class sources at decoration time."""

    def test_raises_type_error(self) -> None:
        """Decorating a class raises ``TypeError`` — the strict form never delegates to ``deprecated_class``.

        The whole reason the alias exists is to make ``@deprecated`` on a class impossible at a call site
        that must only ever wrap callables; a maintainer who reaches for it on a class should be corrected
        immediately at import, not warned-and-delegated.
        """
        with pytest.raises(TypeError):
            make_deprecated_callable_on_class()

    def test_error_message_names_alternatives(self) -> None:
        """The rejection message points the user to ``deprecated_class`` and ``deprecated``.

        A loud error is only useful if it says what to do instead; the message must name both the
        class-specific decorator and the auto-dispatching one so the fix is obvious without opening docs.
        """
        with pytest.raises(TypeError, match=r"deprecated_class.*deprecated"):
            make_deprecated_callable_on_class()

    def test_error_names_the_class(self) -> None:
        """The rejection message includes the offending class name for a locatable error.

        When a codebase has many decorators, an error naming ``RejectedClass`` tells the maintainer exactly
        which definition to change rather than forcing a hunt through the traceback.
        """
        with pytest.raises(TypeError, match="RejectedClass"):
            make_deprecated_callable_on_class()


def test_alias_is_distinct_callable() -> None:
    """``deprecated_callable`` is its own public function, not an alias object of ``deprecated``.

    Phase 1 requires the strict form to carry its own ``__name__``/``__qualname__`` so warning texts and
    tracebacks read ``deprecated_callable``; this guards that it was exported as a genuine function.
    """
    assert deprecated_callable.__name__ == "deprecated_callable"
    assert callable(deprecated_callable)
