"""Unit tests for the caller-frame walk (:mod:`deprecate._frames`)."""

from __future__ import annotations

import sys
import warnings
from collections.abc import Callable
from typing import Any

import pytest

from deprecate import _frames
from deprecate._frames import _MAX_FRAMES, _caller_stacklevel, _is_internal

#: Body of a one-frame helper: the frame that would call ``warnings.warn``.
_HELPER_SRC = "def helper():\n    return _caller_stacklevel()\n"
#: Body of a helper that recurses ``depth`` times before measuring, to build an arbitrarily deep internal stack.
_RECURSIVE_SRC = (
    "def recurse(depth):\n    if depth:\n        return recurse(depth - 1)\n    return _caller_stacklevel()\n"
)
#: Body of an emitter that warns with the computed level, the way every pyDeprecate warn site does.
_EMIT_SRC = "def emit():\n    warnings.warn('probe', UserWarning, stacklevel=_caller_stacklevel())\n"
#: Body of a pass-through frame; compiled under an import-bootstrap filename it mimics ``importlib._bootstrap``.
_TRAMPOLINE_SRC = "def trampoline(fn):\n    return fn()\n"
#: Body of an internal frame that calls its argument, so an internal frame sits above the trampoline.
_OUTER_SRC = "def outer(fn):\n    return fn()\n"
#: Filename ``warnings.warn`` treats as an import-machinery frame (contains ``importlib`` and ``_bootstrap``).
_BOOTSTRAP_FILENAME = "<frozen importlib._bootstrap>"


def _namespaced(module_name: str, source: str, func_name: str, filename: str = "<frames-test>") -> Callable[..., Any]:
    """Compile ``source`` into a fresh namespace whose ``__name__`` is ``module_name`` and return ``func_name``.

    Frames of the returned function carry ``module_name`` in ``f_globals``, which is what the walk inspects, so a
    test can place a frame "inside" any package without creating a real module.

    """
    namespace: dict[str, Any] = {
        "__name__": module_name,
        "_caller_stacklevel": _caller_stacklevel,
        "warnings": warnings,
    }
    exec(compile(source, filename, "exec"), namespace)  # noqa: S102
    return namespace[func_name]


class TestIsInternal:
    """``_is_internal`` matches the ``deprecate`` package by full name only."""

    @pytest.mark.parametrize(
        ("module_name", "expected"),
        [
            pytest.param("deprecate", True, id="package-root"),
            pytest.param("deprecate.x", True, id="submodule"),
            pytest.param("deprecate.audit._scan", True, id="nested-submodule"),
            pytest.param("deprecate_x", False, id="underscore-sibling"),
            pytest.param("deprecated", False, id="prefix-sibling"),
            pytest.param("mydeprecate.x", False, id="suffix-match"),
            pytest.param("", False, id="empty"),
        ],
    )
    def test_matches_full_package_name(self, module_name: str, expected: bool) -> None:
        """A frame is internal only when its module is ``deprecate`` or a ``deprecate.`` submodule.

        A user package named ``deprecate_x`` or ``deprecated`` shares the text prefix but is a different package; a
        plain ``startswith("deprecate")`` would skip its frames and attribute its warnings to whoever called it.

        """
        frame = _namespaced(module_name, "def current():\n    return sys._getframe()\n", "current")
        frame.__globals__["sys"] = sys
        assert _is_internal(frame()) is expected

    def test_missing_module_name_is_external(self) -> None:
        """A frame whose globals carry no ``__name__`` (raw ``exec`` code) counts as external.

        Code run through ``exec`` with a bare globals dict has no module name; treating it as internal would walk
        past it and point the warning at an unrelated frame further up.

        """
        namespace: dict[str, Any] = {"sys": sys}
        exec(compile("def current():\n    return sys._getframe()\n", "<raw>", "exec"), namespace)  # noqa: S102
        assert _is_internal(namespace["current"]()) is False

    @pytest.mark.parametrize("module_name", [None, 42])
    def test_non_string_module_name_is_external(self, module_name: object) -> None:
        """A frame whose globals bind ``__name__`` to a non-string counts as external instead of raising.

        ``exec`` code may set ``__name__`` to any object. The predicate runs on every frame of every emitted warning,
        so an ``AttributeError`` there would break each deprecated call made from such code.

        """
        namespace: dict[str, Any] = {"sys": sys, "__name__": module_name}
        exec(compile("def current():\n    return sys._getframe()\n", "<raw>", "exec"), namespace)  # noqa: S102
        assert _is_internal(namespace["current"]()) is False


class TestCallerStacklevel:
    """``_caller_stacklevel`` returns the ``stacklevel`` that lands ``warnings.warn`` on the first external frame."""

    def test_direct_call_from_external_frame(self) -> None:
        """Called straight from user code, the level is ``1`` — the caller itself is already external."""
        assert _caller_stacklevel() == 1

    def test_internal_helper_steps_one_frame(self) -> None:
        """A helper inside ``deprecate.x`` measures ``2``: its own frame, then this test frame.

        This is the shape of every decoration-time warn site: a pyDeprecate function called from the user's
        decorator line.

        """
        helper = _namespaced("deprecate.x", _HELPER_SRC, "helper")
        assert helper() == 2

    def test_package_root_is_internal(self) -> None:
        """Frames of the ``deprecate`` package module itself are skipped like any submodule."""
        helper = _namespaced("deprecate", _HELPER_SRC, "helper")
        assert helper() == 2

    def test_underscore_sibling_module_is_external(self) -> None:
        """A helper in a user module named ``deprecate_x`` is external, so it measures ``1``.

        A third-party package that happens to start with ``deprecate`` must keep its own warning location.

        """
        helper = _namespaced("deprecate_x", _HELPER_SRC, "helper")
        assert helper() == 1

    def test_walk_is_bounded(self) -> None:
        """An internal stack deeper than ``_MAX_FRAMES`` stops the walk at ``_MAX_FRAMES``.

        The walk runs on every emitted warning; the bound keeps a pathological recursion inside pyDeprecate from
        turning one warning into an unbounded stack scan.

        """
        recurse = _namespaced("deprecate.x", _RECURSIVE_SRC, "recurse")
        assert recurse(_MAX_FRAMES + 36) == _MAX_FRAMES

    def test_walk_below_bound_counts_every_frame(self) -> None:
        """Ten internal frames above an external caller give level ``11``: the count is exact below the bound."""
        recurse = _namespaced("deprecate.x", _RECURSIVE_SRC, "recurse")
        assert recurse(9) == 11

    def test_unavailable_frame_falls_back_to_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the interpreter cannot provide the caller frame, the level falls back to ``1``.

        ``sys._getframe`` raises ``ValueError`` when the requested depth does not exist; the helper must still
        return a valid ``stacklevel`` rather than propagate the error out of a warn site.

        """

        def _no_frame(depth: int = 0) -> Any:  # noqa: ANN401
            raise ValueError("call stack is not deep enough")

        monkeypatch.setattr(_frames.sys, "_getframe", _no_frame)
        level = _caller_stacklevel()
        monkeypatch.undo()
        assert level == 1


class TestAgreesWithWarnings:
    """The computed level, passed to ``warnings.warn``, reports the first external frame."""

    def test_internal_emitter_points_at_caller(self) -> None:
        """A warning from an internal emitter carries this test's file and the calling line.

        This is the end-to-end contract every warn site relies on: whatever the internal depth, the location is
        the user's line.

        """
        emit = _namespaced("deprecate.x", _EMIT_SRC, "emit")
        expected_line = sys._getframe().f_lineno + 2
        with pytest.warns(UserWarning, match="probe") as record:
            emit()
        assert (record[0].filename, record[0].lineno) == (__file__, expected_line)

    def test_import_bootstrap_frame_is_not_counted(self) -> None:
        """An import-machinery frame between two internal frames is stepped over without counting.

        ``warnings.warn`` skips frames whose filename contains ``importlib`` and ``_bootstrap`` when it walks
        ``stacklevel`` frames up.  A warning raised while ``deprecate`` itself is importing has such a frame in the
        middle of its internal chain; if the walk counted it, the warning would land on the internal frame above
        the bootstrap frame instead of the user's line.

        """
        outer = _namespaced("deprecate.x", _OUTER_SRC, "outer")
        trampoline = _namespaced("importlib._bootstrap", _TRAMPOLINE_SRC, "trampoline", filename=_BOOTSTRAP_FILENAME)
        emit = _namespaced("deprecate.x", _EMIT_SRC, "emit")
        expected_line = sys._getframe().f_lineno + 2
        with pytest.warns(UserWarning, match="probe") as record:
            outer(lambda: trampoline(emit))
        assert (record[0].filename, record[0].lineno) == (__file__, expected_line)
