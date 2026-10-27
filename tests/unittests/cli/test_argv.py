"""Unit tests for :mod:`deprecate._cli.argv`: UTF-8 streams, option spelling and the subcommand parser."""

import argparse
import inspect
import io
import sys
from collections.abc import Callable
from types import SimpleNamespace
from typing import Optional
from unittest.mock import Mock

import pytest

from deprecate._cli.argv import (
    _cli_command,
    _ensure_utf8_streams,
    _normalize_options,
    _parse_bool,
    _parse_cli_args,
    _parse_switchable,
)
from deprecate._cli.commands import cmd_all, cmd_check, cmd_expiry, cmd_policy, cmd_status

_CHECK_BOOL_PARAMS = frozenset({"exit_zero", "recursive"})


class TestNormalizeOptions:
    """Table-driven tests of ``_normalize_options``, the argv respelling that runs before argparse parses."""

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            pytest.param([], [], id="empty-argv"),
            pytest.param(["pkg"], ["pkg"], id="path-only"),
            pytest.param(
                ["--exit-zero", "--norecursive", "pkg"],
                ["--exit-zero=true", "--norecursive", "pkg"],
                id="two-flags-before-path",
            ),
            pytest.param(
                ["pkg", "--exit_zero", "--recursive"],
                ["pkg", "--exit-zero=true", "--recursive=true"],
                id="two-flags-after-path",
            ),
            pytest.param(
                ["--exit-zero", "0", "--recursive", "false", "pkg"],
                ["--exit-zero=0", "--recursive=false", "pkg"],
                id="two-valued-flags",
            ),
            pytest.param(
                ["--exit-zero", "yes", "pkg"], ["--exit-zero=true", "yes", "pkg"], id="non-literal-stays-free"
            ),
            pytest.param(["--exit-zero", "--", "0"], ["--exit-zero=true", "--", "0"], id="separator-is-not-a-value"),
            pytest.param(["--", "--exit_zero", "0"], ["--", "--exit_zero", "0"], id="after-separator-untouched"),
            pytest.param(["--no_recursive", "false"], ["--no-recursive", "false"], id="negation-takes-no-value"),
            pytest.param(["--exit-zero=yes", "pkg"], ["--exit-zero=yes", "pkg"], id="attached-value-untouched"),
            pytest.param(["--min_grace=0.10"], ["--min-grace=0.10"], id="value-option-respelled"),
            pytest.param(["--report_json=a_b.json"], ["--report-json=a_b.json"], id="value-keeps-underscores"),
            pytest.param(["--verbose", "0"], ["--verbose", "0"], id="unknown-flag-untouched"),
            pytest.param(["--_advisory", "--_advisory=True"], ["--_advisory", "--_advisory=True"], id="internal"),
            pytest.param(["-r", "pkg"], ["-r", "pkg"], id="short-flag-untouched"),
        ],
    )
    def test_rewrites_check_argv(self, argv: list[str], expected: list[str]) -> None:
        """Option names use dashes and boolean flags carry their value; everything else is left for argparse.

        Respelling is what lets a pre-commit hook put a flag before the path (argparse would otherwise take the path
        as the optional value of a bare flag) and keeps the underscore spellings Fire accepted. An argument it does
        not recognise must pass through byte for byte so argparse still rejects typos and internal options.

        """
        assert _normalize_options(argv, _CHECK_BOOL_PARAMS) == expected

    @pytest.mark.parametrize("spelling", ["true", "True", "TRUE", "1", "false", "False", "FALSE", "0"])
    def test_literal_after_flag_is_its_value(self, spelling: str) -> None:
        """A literal after a bare flag is consumed as its value: ``--exit-zero 0`` is ``--exit-zero=0``.

        Left as a free token, ``0`` would bind to the next positional parameter of the subcommand — ``version`` for
        ``expiry`` — where nothing validates it.

        """
        assert _normalize_options(["--exit-zero", spelling, "pkg"], _CHECK_BOOL_PARAMS) == [
            f"--exit-zero={spelling}",
            "pkg",
        ]


class TestParseBool:
    """The value of a boolean flag is ``true``, ``false``, ``1`` or ``0`` in any case, nothing else."""

    @pytest.mark.parametrize(
        ("spelling", "expected"),
        [
            pytest.param("true", True, id="true"),
            pytest.param("TRUE", True, id="upper-true"),
            pytest.param("1", True, id="one"),
            pytest.param("False", False, id="capital-false"),
            pytest.param("0", False, id="zero"),
        ],
    )
    def test_accepts_literal(self, spelling: str, expected: bool) -> None:
        """Every documented spelling reads as the boolean it names, so CI configs written for Fire keep working."""
        assert _parse_bool(spelling) is expected

    @pytest.mark.parametrize("spelling", ["yes", "", "None"])
    def test_rejects_other_spelling(self, spelling: str) -> None:
        """Any other value is a usage error rather than a truthy string that would silently pass the run.

        argparse turns the ``ArgumentTypeError`` into exit 2 with the message naming the flag.

        """
        with pytest.raises(argparse.ArgumentTypeError, match="expects True or False"):
            _parse_bool(spelling)


class TestParseSwitchable:
    """A config-backed option such as ``--min-grace`` is switched off with ``None`` or ``false``, else kept as text."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("None", None, id="none"),
            pytest.param("FALSE", None, id="false"),
            pytest.param("0.10", "0.10", id="delta-keeps-trailing-zero"),
            pytest.param("pkg.tests,*._legacy*", "pkg.tests,*._legacy*", id="pattern-list"),
        ],
    )
    def test_reads_value(self, text: str, expected: Optional[str]) -> None:
        """``--min-grace=None`` disables the rule as before, and ``0.10`` stays the string ``"0.10"``.

        Fire parsed ``0.10`` as the float ``0.1``, so ten or more steps needed extra quoting; option values now stay
        strings, which keeps the trailing zero without it.

        """
        assert _parse_switchable(text) == expected


class TestCliCommand:
    """Contract of ``_cli_command`` across every subcommand: positionals and command-line boolean flags."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            pytest.param("check", {"exit_zero", "include_members", "recursive"}, id="check"),
            pytest.param("expiry", {"exit_zero", "include_members", "recursive"}, id="expiry"),
            pytest.param("policy", {"exit_zero", "include_members", "message_required", "recursive"}, id="policy"),
            pytest.param("chains", {"exit_zero", "include_members", "recursive"}, id="chains"),
            pytest.param("all", {"exit_zero", "include_members", "recursive"}, id="all"),
            pytest.param("status", {"include_members", "recursive"}, id="status"),
        ],
    )
    def test_cli_visible_boolean_flags(self, name: str, expected: set[str]) -> None:
        """Each subcommand exposes exactly its documented boolean flags to the respelling and the parser.

        A flag missing here would take the path after it as its value; ``message_required`` is the case that was
        missed when it was typed as a config-backed flag.

        """
        assert _cli_command(name).bool_params == frozenset(expected)

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            pytest.param("check", ("path",), id="check"),
            pytest.param("policy", ("path",), id="policy"),
            pytest.param("expiry", ("path", "version"), id="expiry"),
            pytest.param("all", ("path", "version"), id="all"),
            pytest.param("status", ("path", "version"), id="status"),
        ],
    )
    def test_positionals(self, name: str, expected: tuple[str, ...]) -> None:
        """Only the path and, where the subcommand compares against one, the version are positional operands."""
        assert _cli_command(name).positionals == expected

    @pytest.mark.parametrize(
        ("command", "name", "hidden"),
        [
            pytest.param(cmd_expiry, "expiry", "_version_explicit", id="expiry"),
            pytest.param(cmd_policy, "policy", "_advisory", id="policy"),
            pytest.param(cmd_status, "status", "_version_explicit", id="status"),
        ],
    )
    def test_underscore_boolean_is_not_a_flag(self, command: Callable[..., int], name: str, hidden: str) -> None:
        """A ``bool``-annotated underscore parameter stays out of the flag set.

        ``cmd_all`` passes ``_advisory=True`` and ``_version_explicit`` to its siblings in process; they are not
        part of the command-line surface, so the filter on the leading underscore is what keeps them out.

        """
        assert inspect.signature(command).parameters[hidden].annotation is bool
        assert hidden not in _cli_command(name).bool_params

    @pytest.mark.parametrize(
        ("command", "name", "other_flag"),
        [
            pytest.param(cmd_policy, "policy", "min_grace", id="min-grace"),
            pytest.param(cmd_policy, "policy", "exclude", id="policy-exclude"),
            pytest.param(cmd_check, "check", "exclude", id="check-exclude"),
        ],
    )
    def test_config_backed_non_boolean_flags_are_excluded(
        self, command: Callable[..., int], name: str, other_flag: str
    ) -> None:
        """Config-backed flags that are not booleans (``min_grace``, ``exclude``) are never treated as boolean.

        They share the ``pyproject.toml`` sentinel default with ``message_required`` but take strings, so respelling
        ``--exclude pkg`` into ``--exclude=true`` would break them.

        """
        assert other_flag in inspect.signature(command).parameters
        assert other_flag not in _cli_command(name).bool_params


class TestParseCliArgs:
    """``_parse_cli_args`` maps a command line onto the subcommand and the keyword arguments the user typed."""

    @pytest.mark.parametrize(
        ("argv", "expected_command", "expected_kwargs"),
        [
            pytest.param(["check", "pkg"], cmd_check, {"path": "pkg"}, id="path-only"),
            pytest.param(["pkg", "--exit-zero"], cmd_check, {"path": "pkg", "exit_zero": True}, id="implicit-check"),
            pytest.param(
                ["expiry", "pkg", "--norecursive", "0.10"],
                cmd_expiry,
                {"path": "pkg", "version": "0.10", "recursive": False},
                id="flag-between-positionals",
            ),
            pytest.param(
                ["policy", "--min-grace=None", "pkg", "--message_required", "0"],
                cmd_policy,
                {"path": "pkg", "min_grace": None, "message_required": False},
                id="policy-rules",
            ),
            pytest.param(["all"], cmd_all, {}, id="nothing-typed"),
        ],
    )
    def test_binds_typed_options_only(
        self, argv: list[str], expected_command: object, expected_kwargs: dict[str, object]
    ) -> None:
        """Only typed options reach the ``cmd_*`` call, so every omitted one keeps its signature default.

        The ``pyproject.toml``-backed defaults depend on that: passing ``min_grace=None`` for an omitted flag would
        switch the rule off instead of reading the project's table.

        """
        parsed = _parse_cli_args(argv)
        assert parsed is not None
        command, kwargs, report_path = parsed
        assert (command.function, kwargs, report_path) == (expected_command, expected_kwargs, None)

    def test_report_option_is_split_off(self) -> None:
        """``--report-json`` is the CLI's own option and never reaches the ``cmd_*`` function."""
        parsed = _parse_cli_args(["status", "pkg", "--report_json=out/r.json"])
        assert parsed is not None
        assert (parsed[1], parsed[2]) == ({"path": "pkg"}, "out/r.json")

    @pytest.mark.parametrize(
        ("argv", "expected_kwargs"),
        [
            pytest.param(["check", "--", "--exit-zero"], {"path": "--exit-zero"}, id="option-spelling-is-a-path"),
            pytest.param(
                ["status", "--recursive=0", "--", "pkg", "0.2"],
                {"path": "pkg", "version": "0.2", "recursive": False},
                id="both-operands",
            ),
            pytest.param(["status", "pkg", "--", "0.2"], {"path": "pkg", "version": "0.2"}, id="version-operand"),
        ],
    )
    def test_separator_ends_options(self, argv: list[str], expected_kwargs: dict[str, object]) -> None:
        """Everything after ``--`` binds to the positional operands and is never parsed as an option.

        ``parse_known_intermixed_args`` on Python 3.9 still parsed ``check -- --exit-zero`` as the ``--exit-zero`` flag,
        scanning ``.`` with the gate disabled, while newer Pythons took it as the path; both now read it as the path.

        """
        parsed = _parse_cli_args(argv)
        assert parsed is not None
        assert parsed[1] == expected_kwargs

    def test_unknown_option_value_gets_no_path_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The value of a mistyped option is not called a second path.

        In ``check pkg --verison 2.0`` the ``2.0`` belongs to the unknown ``--verison``, so telling the user to pass one
        path per run points at the wrong mistake.

        """
        with pytest.raises(SystemExit):
            _parse_cli_args(["check", "pkg", "--verison", "2.0"])
        err = capsys.readouterr().err
        assert "unrecognized arguments: --verison 2.0" in err
        assert "one path per run" not in err

    def test_empty_command_line_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A bare ``pydeprecate`` prints the subcommand list and runs nothing."""
        assert _parse_cli_args([]) is None
        assert "check" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            pytest.param(["check", "pkg_a", "pkg_b"], "pass one path per run", id="second-path"),
            pytest.param(["check", "pkg", "--_advisory"], "unrecognized arguments: --_advisory", id="internal"),
            pytest.param(["check", "pkg", "--exit"], "unrecognized arguments: --exit", id="no-abbreviation"),
            pytest.param(["check", "pkg", "--exit-zero=yes"], "expects True or False", id="bad-bool"),
            pytest.param(["check", "pkg", "--no-exit-zero=1"], "ignored explicit argument", id="negation-value"),
            pytest.param(["check", "pkg", "--report-json"], "expected one argument", id="report-without-path"),
            pytest.param(["check", "pkg", "--report-json="], "expects a file path", id="report-empty-path"),
            pytest.param(["expiry", "pkg", "0.1", "--version=9.0"], "the version was given twice", id="version-twice"),
            pytest.param(["check", "--", "pkg_a", "pkg_b"], "pass one path per run", id="operands-after-separator"),
        ],
    )
    def test_usage_error_exits_two(self, argv: list[str], message: str, capsys: pytest.CaptureFixture[str]) -> None:
        """A malformed command line exits 2 with argparse's message before any subcommand runs.

        Option abbreviations are off, so ``--exit`` is a typo, never a silent ``--exit-zero``.

        """
        with pytest.raises(SystemExit) as exc_info:
            _parse_cli_args(argv)
        assert exc_info.value.code == 2
        assert message in capsys.readouterr().err


class _NoneEncodingStream:
    """Stream stub whose ``encoding`` attribute is present but ``None`` (fixture)."""

    encoding = None

    def __init__(self) -> None:
        self.reconfigured_to: Optional[str] = None

    def reconfigure(self, *, encoding: str) -> None:
        """Record the encoding a caller reconfigured the stream to."""
        self.reconfigured_to = encoding


class TestEnsureUtf8Streams:
    """CLI output streams are prepared for Unicode without breaking unsupported streams."""

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

    def test_reconfigures_non_utf8_streams(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both output streams switch to UTF-8 when their existing encoding cannot represent all CLI output.

        A console using a legacy encoding must be able to write the Unicode status symbols after initialization.
        """
        with (
            io.TextIOWrapper(io.BytesIO(), encoding="cp1252") as stdout,
            io.TextIOWrapper(io.BytesIO(), encoding="cp1252") as stderr,
            monkeypatch.context() as patch,
        ):
            patch.setattr(sys, "stdout", stdout)
            patch.setattr(sys, "stderr", stderr)
            _ensure_utf8_streams()
            assert stdout.encoding == "utf-8"
            assert stderr.encoding == "utf-8"
            stdout.write("✓")
            stdout.flush()
            assert stdout.buffer.getvalue() == "✓".encode()

    def test_stream_without_reconfigure_is_left_untouched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-UTF-8 stream without reconfigure does not prevent the other stream from being prepared.

        Capture wrappers can omit reconfigure even when they report a legacy encoding.
        """
        stream = SimpleNamespace(encoding="cp1252")
        with io.TextIOWrapper(io.BytesIO(), encoding="cp1252") as stderr, monkeypatch.context() as patch:
            patch.setattr(sys, "stdout", stream)
            patch.setattr(sys, "stderr", stderr)
            _ensure_utf8_streams()
            assert stream.encoding == "cp1252"
            assert stderr.encoding == "utf-8"

    def test_reconfigure_failure_does_not_block_other_stream(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A stream that rejects reconfiguration does not stop the other output stream being prepared.

        Some console streams cannot change encoding after output has started; the CLI must still initialize.
        """
        stream = SimpleNamespace(encoding="cp1252", reconfigure=Mock(side_effect=ValueError("already written")))
        with io.TextIOWrapper(io.BytesIO(), encoding="cp1252") as stderr, monkeypatch.context() as patch:
            patch.setattr(sys, "stdout", stream)
            patch.setattr(sys, "stderr", stderr)
            _ensure_utf8_streams()
            stream.reconfigure.assert_called_once_with(encoding="utf-8")
            assert stderr.encoding == "utf-8"
