"""Unit tests for :mod:`deprecate._cli.argv`: UTF-8 streams, boolean flags and argument binding."""

import inspect
import sys
from collections.abc import Callable
from typing import Optional

import pytest

from deprecate._cli.argv import _bool_params, _ensure_utf8_streams, _normalize_bool_flags
from deprecate._cli.commands import cmd_all, cmd_chains, cmd_check, cmd_expiry, cmd_policy, cmd_status

_CHECK_BOOL_PARAMS = frozenset({"exit_zero", "recursive"})


class TestNormalizeBoolFlags:
    """Table-driven tests of ``_normalize_bool_flags``, the argv rewrite that runs before Fire parses a subcommand."""

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            pytest.param([], [], id="empty-argv"),
            pytest.param(["pkg"], ["pkg"], id="path-only"),
            pytest.param(
                ["--exit-zero", "--norecursive", "pkg"],
                ["--exit_zero=True", "--recursive=False", "pkg"],
                id="two-flags-before-path",
            ),
            pytest.param(
                ["pkg", "--exit_zero", "--recursive"],
                ["pkg", "--exit_zero=True", "--recursive=True"],
                id="two-flags-after-path",
            ),
            pytest.param(
                ["--exit-zero", "0", "--recursive", "false", "pkg"],
                ["--exit_zero=False", "--recursive=False", "pkg"],
                id="two-valued-flags",
            ),
            pytest.param(
                ["--exit-zero", "yes", "pkg"], ["--exit_zero=True", "yes", "pkg"], id="non-literal-stays-free"
            ),
            pytest.param(["--exit-zero", "--", "0"], ["--exit_zero=True", "--", "0"], id="separator-is-not-a-value"),
            pytest.param(["--", "--exit-zero", "0"], ["--", "--exit-zero", "0"], id="after-separator-untouched"),
            pytest.param(
                ["--norecursive", "false", "pkg"], ["--recursive=False", "false", "pkg"], id="negation-takes-no-value"
            ),
            pytest.param(["--exit-zero=yes", "pkg"], ["--exit-zero=yes", "pkg"], id="attached-non-literal-untouched"),
            pytest.param(["--exit-zero=None"], ["--exit-zero=None"], id="attached-none-untouched"),
            pytest.param(["--verbose", "0"], ["--verbose", "0"], id="unknown-flag-untouched"),
            pytest.param(["--no-recursive", "pkg"], ["--no-recursive", "pkg"], id="dashed-negation-not-recognised"),
            pytest.param(["-r", "pkg"], ["-r", "pkg"], id="short-flag-untouched"),
        ],
    )
    def test_rewrites_check_argv(self, argv: list[str], expected: list[str]) -> None:
        """Boolean flags become ``--name=True|False`` wherever they sit; everything else is left for Fire or the guard.

        The rewrite is what lets a pre-commit hook put a flag before the path; any argument it does not recognise
        must pass through byte for byte so Fire still reports typos and the guard still rejects stray values.

        """
        assert _normalize_bool_flags(argv, _CHECK_BOOL_PARAMS) == expected

    @pytest.mark.parametrize(
        ("spelling", "expected"),
        [
            pytest.param("true", True, id="true"),
            pytest.param("True", True, id="capital-true"),
            pytest.param("TRUE", True, id="upper-true"),
            pytest.param("1", True, id="one"),
            pytest.param("false", False, id="false"),
            pytest.param("False", False, id="capital-false"),
            pytest.param("FALSE", False, id="upper-false"),
            pytest.param("0", False, id="zero"),
        ],
    )
    def test_literal_spellings_as_separate_token(self, spelling: str, expected: bool) -> None:
        """A literal after a bare flag is consumed as its value: ``--exit-zero 0`` is ``exit_zero=False``.

        Left as a free token, ``0`` would bind to the next positional parameter of the subcommand — ``version`` for
        ``expiry`` — where nothing validates it.

        """
        assert _normalize_bool_flags(["--exit-zero", spelling, "pkg"], _CHECK_BOOL_PARAMS) == [
            f"--exit_zero={expected}",
            "pkg",
        ]

    @pytest.mark.parametrize(
        ("spelling", "expected"),
        [
            pytest.param("true", True, id="true"),
            pytest.param("True", True, id="capital-true"),
            pytest.param("TRUE", True, id="upper-true"),
            pytest.param("1", True, id="one"),
            pytest.param("false", False, id="false"),
            pytest.param("False", False, id="capital-false"),
            pytest.param("FALSE", False, id="upper-false"),
            pytest.param("0", False, id="zero"),
        ],
    )
    def test_literal_spellings_attached(self, spelling: str, expected: bool) -> None:
        """``--exit-zero=<literal>`` accepts the same spellings and is rewritten to Fire's capitalised form.

        Fire parses only ``True``/``False`` as booleans, so ``=true`` would reach the subcommand as a string and
        ``=1`` as an integer.

        """
        assert _normalize_bool_flags([f"--exit-zero={spelling}", "pkg"], _CHECK_BOOL_PARAMS) == [
            f"--exit_zero={expected}",
            "pkg",
        ]

    def test_underscore_prefixed_parameters_are_not_flags(self) -> None:
        """``--_advisory`` is not rewritten even though ``cmd_policy._advisory`` is annotated ``bool``.

        Underscore parameters are ``cmd_all`` plumbing; the normalizer must leave them alone so the CLI boundary can
        reject them instead of treating a typed ``--_advisory`` as a user flag.

        """
        argv = ["--_advisory", "--_advisory=True", "pkg"]
        assert _normalize_bool_flags(argv, _bool_params(cmd_policy)) == argv


class TestBoolParams:
    """Contract of ``_bool_params`` across every subcommand: which parameters are command-line boolean flags."""

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            pytest.param(cmd_check, {"exit_zero", "recursive"}, id="check"),
            pytest.param(cmd_expiry, {"exit_zero", "recursive"}, id="expiry"),
            pytest.param(cmd_policy, {"exit_zero", "message_required", "recursive"}, id="policy"),
            pytest.param(cmd_chains, {"exit_zero", "recursive"}, id="chains"),
            pytest.param(cmd_all, {"exit_zero", "recursive"}, id="all"),
            pytest.param(cmd_status, {"include_members", "recursive"}, id="status"),
        ],
    )
    def test_cli_visible_boolean_flags(self, command: Callable[..., int], expected: set[str]) -> None:
        """Each subcommand exposes exactly its documented boolean flags to the normalizer and the guard.

        A flag missing here is order-dependent on the command line (the path after it is swallowed) and escapes the
        exit-2 guard; ``message_required`` is the case that was missed when it was typed as a config-backed flag.

        """
        assert _bool_params(command) == frozenset(expected)

    @pytest.mark.parametrize(
        ("command", "hidden"),
        [
            pytest.param(cmd_expiry, "_version_explicit", id="expiry"),
            pytest.param(cmd_policy, "_advisory", id="policy"),
            pytest.param(cmd_status, "_version_explicit", id="status"),
        ],
    )
    def test_underscore_boolean_is_not_a_flag(self, command: Callable[..., int], hidden: str) -> None:
        """A ``bool``-annotated underscore parameter stays out of the flag set.

        ``cmd_all`` passes ``_advisory=True`` and ``_version_explicit`` to its siblings in process; they are not
        part of the command-line surface, so the filter on the leading underscore is what keeps them out.

        """
        assert inspect.signature(command).parameters[hidden].annotation is bool
        assert hidden not in _bool_params(command)

    @pytest.mark.parametrize(
        ("command", "other_flag"),
        [
            pytest.param(cmd_policy, "min_grace", id="min-grace"),
            pytest.param(cmd_policy, "exclude", id="policy-exclude"),
            pytest.param(cmd_check, "exclude", id="check-exclude"),
        ],
    )
    def test_config_backed_non_boolean_flags_are_excluded(self, command: Callable[..., int], other_flag: str) -> None:
        """Config-backed flags that are not booleans (``min_grace``, ``exclude``) are never treated as boolean.

        They share the ``pyproject.toml`` sentinel default with ``message_required`` but take strings, numbers or
        lists, so rewriting ``--exclude pkg`` into ``--exclude=True`` would break them.

        """
        assert other_flag in inspect.signature(command).parameters
        assert other_flag not in _bool_params(command)


class _NoneEncodingStream:
    """Stream stub whose ``encoding`` attribute is present but ``None`` (fixture)."""

    encoding = None

    def __init__(self) -> None:
        self.reconfigured_to: Optional[str] = None

    def reconfigure(self, *, encoding: str) -> None:
        """Record the encoding a caller reconfigured the stream to."""
        self.reconfigured_to = encoding


class TestEnsureUtf8StreamsNoneEncoding:
    """A stream whose ``encoding`` is ``None`` must not crash the UTF-8 reconfigure pass."""

    def test_none_encoding_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A ``None`` encoding is tolerated (treated as UTF-8, so reconfigure is skipped) rather than crashing.

        ``getattr(stream, "encoding", "utf-8")`` returned ``None`` (the attribute exists but is ``None``) and the
        subsequent ``.lower()`` raised ``AttributeError``; the ``or "utf-8"`` guard folds ``None`` to ``"utf-8"``
        so the stream is considered already-UTF-8 and left untouched instead of blowing up.
        """
        stream = _NoneEncodingStream()
        monkeypatch.setattr(sys, "stdout", stream)
        monkeypatch.setattr(sys, "stderr", stream)
        _ensure_utf8_streams()
        assert stream.reconfigured_to is None
