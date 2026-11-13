"""Unit tests for :mod:`deprecate._cli.argv`: UTF-8 streams, option spelling and the subcommand parser."""

import argparse
import inspect
import io
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import Mock, patch

import pytest

from deprecate._cli.argv import (
    _cli_command,
    _ensure_utf8_streams,
    _normalize_options,
    _parse_bool,
    _parse_cli_args,
    _parse_switchable,
    _parses_as_version,
)
from deprecate._cli.commands import cmd_all, cmd_chains, cmd_check, cmd_expiry, cmd_policy

_CHECK_BOOL_PARAMS = frozenset({"exit_zero", "recursive"})


class TestNormalizeOptions:
    """Table-driven tests of ``_normalize_options``, the argv respelling that runs before argparse parses."""

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            pytest.param([], [], id="empty-argv"),
            pytest.param(["pkg"], ["pkg"], id="path-only"),
            pytest.param(
                ["--exit-zero", "--no-recursive", "pkg"],
                ["--exit-zero=true", "--no-recursive", "pkg"],
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
            pytest.param(["-r", "pkg"], ["-r", "pkg"], id="short-flag-untouched"),
        ],
    )
    def test_rewrites_check_argv(self, argv: list[str], expected: list[str]) -> None:
        """Option names use dashes and boolean flags carry their value; everything else is left for argparse.

        Respelling is what lets a pre-commit hook put a flag before the path (argparse would otherwise take the path
        as the optional value of a bare flag) and keeps the underscore spellings Fire accepted. An argument it does
        not recognise must pass through byte for byte so argparse still rejects typos.

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
            pytest.param("check", {"exit_zero", "include_members", "recursive", "strict"}, id="check"),
            pytest.param("expiry", {"exit_zero", "include_members", "recursive", "static", "strict"}, id="expiry"),
            pytest.param(
                "policy",
                {"exit_zero", "include_members", "message_required", "recursive", "static", "strict"},
                id="policy",
            ),
            pytest.param("chains", {"exit_zero", "include_members", "recursive", "strict"}, id="chains"),
            pytest.param("all", {"exit_zero", "include_members", "recursive", "strict"}, id="all"),
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
            pytest.param("check", False, id="check"),
            pytest.param("policy", False, id="policy"),
            pytest.param("chains", False, id="chains"),
            pytest.param("expiry", True, id="expiry"),
            pytest.param("all", True, id="all"),
            pytest.param("status", True, id="status"),
        ],
    )
    def test_takes_version(self, name: str, expected: bool) -> None:
        """Only the subcommands that compare against a version accept the legacy positional version."""
        assert _cli_command(name).takes_version is expected

    @pytest.mark.parametrize("name", ["check", "expiry", "policy", "chains", "all", "status"])
    def test_every_parameter_is_an_option(self, name: str) -> None:
        """Every parameter of a ``cmd_*`` function is a command-line operand or option, none is in-process plumbing.

        The parser is built from these signatures, so a parameter only ``cmd_all`` passes (pre-scanned wrappers, an
        advisory switch) would need hiding from the command line; such inputs belong to the private helpers instead.

        """
        params = inspect.signature(_cli_command(name).function).parameters
        assert [param for param in params if param.startswith("_")] == []

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
            pytest.param(["check", "pkg"], cmd_check, {"path": ["pkg"]}, id="path-only"),
            pytest.param(["pkg", "--exit-zero"], cmd_check, {"path": ["pkg"], "exit_zero": True}, id="implicit-check"),
            pytest.param(
                ["expiry", "pkg", "--no-recursive", "0.10"],
                cmd_expiry,
                {"path": ["pkg"], "version": "0.10", "recursive": False},
                id="flag-between-positionals",
            ),
            pytest.param(
                ["policy", "--min-grace=None", "pkg", "--message_required", "0"],
                cmd_policy,
                {"path": ["pkg"], "min_grace": None, "message_required": False},
                id="policy-rules",
            ),
            pytest.param(["all"], cmd_all, {}, id="nothing-typed"),
            pytest.param(
                ["expiry", "pkg", "--ignore", "a:b,c:d", "--ignore=e:f"],
                cmd_expiry,
                {"path": ["pkg"], "ignore": ["a:b,c:d", "e:f"]},
                id="ignore-repeated",
            ),
            pytest.param(
                ["check", "pkg", "--ignore="], cmd_check, {"path": ["pkg"], "ignore": [""]}, id="ignore-empty"
            ),
            pytest.param(
                ["chains", "pkg", "--baseline", "b.json"],
                cmd_chains,
                {"path": ["pkg"], "baseline": "b.json"},
                id="baseline",
            ),
            pytest.param(["all", "pkg", "--gate=policy"], cmd_all, {"path": ["pkg"], "gate": ["policy"]}, id="gate"),
            pytest.param(
                ["all", "pkg_a", "--exit-zero", "pkg_b", "pkg_c"],
                cmd_all,
                {"path": ["pkg_a", "pkg_b", "pkg_c"], "exit_zero": True},
                id="several-paths",
            ),
            pytest.param(["check", "pkg", "2.0"], cmd_check, {"path": ["pkg", "2.0"]}, id="no-version-to-take"),
            pytest.param(
                ["expiry", "pkg_a", "pkg_b", "2.0"],
                cmd_expiry,
                {"path": ["pkg_a", "pkg_b", "2.0"]},
                id="three-positionals-are-paths",
            ),
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
        command, kwargs, reports = parsed
        assert (command.function, kwargs, reports) == (expected_command, expected_kwargs, (None, None))

    def test_report_option_is_split_off(self) -> None:
        """``--report-json`` is the CLI's own option and never reaches the ``cmd_*`` function."""
        parsed = _parse_cli_args(["status", "pkg", "--report_json=out/r.json"])
        assert parsed is not None
        assert (parsed[1], parsed[2]) == ({"path": ["pkg"]}, ("out/r.json", None))

    @pytest.mark.parametrize(
        ("argv", "expected_kwargs"),
        [
            pytest.param(["check", "--", "--exit-zero"], {"path": ["--exit-zero"]}, id="option-spelling-is-a-path"),
            pytest.param(
                ["status", "--recursive=0", "--", "pkg", "0.2"],
                {"path": ["pkg"], "version": "0.2", "recursive": False},
                id="both-operands",
            ),
            pytest.param(["status", "pkg", "--", "0.2"], {"path": ["pkg"], "version": "0.2"}, id="version-operand"),
            # without the separator "--report-json" alone is a usage error (expects a value), so parsing proves it
            # was taken as an operand
            pytest.param(["check", "--", "--report-json"], {"path": ["--report-json"]}, id="report-option-is-a-path"),
            pytest.param(
                ["check", "--", "--report-json=out.json"], {"path": ["--report-json=out.json"]}, id="report-assignment"
            ),
            pytest.param(["check", "pkg_a", "--", "pkg_b"], {"path": ["pkg_a", "pkg_b"]}, id="paths-on-both-sides"),
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

    def test_unknown_option_is_named_before_any_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A mistyped option fails the parse by its own name, whatever the token after it binds to.

        In ``check pkg --verison 2.0`` the ``2.0`` meant for the unknown ``--verison`` reads as one more path, but the
        run never gets that far: the unknown option alone is the usage error, so the user sees the typo, not a complaint
        about a path called ``2.0``.

        """
        with pytest.raises(SystemExit):
            _parse_cli_args(["check", "pkg", "--verison", "2.0"])
        err = capsys.readouterr().err
        assert "unrecognized arguments: --verison" in err
        assert "No such path" not in err

    def test_empty_command_line_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A bare ``pydeprecate`` prints the subcommand list and runs nothing."""
        assert _parse_cli_args([]) is None
        assert "check" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            pytest.param(["check", "pkg", "--exit"], "unrecognized arguments: --exit", id="no-abbreviation"),
            pytest.param(["check", "pkg", "--exit-zero=yes"], "expects True or False", id="bad-bool"),
            pytest.param(["check", "pkg", "--no-exit-zero=1"], "ignored explicit argument", id="negation-value"),
            pytest.param(["check", "pkg", "--report-json"], "expected one argument", id="report-without-path"),
            pytest.param(["check", "pkg", "--report-json="], "expects a file path", id="report-empty-path"),
            pytest.param(["expiry", "pkg", "0.1", "--version=9.0"], "the version was given twice", id="version-twice"),
            pytest.param(
                ["check", "pkg", "--static"],
                "are supported by `expiry` and `policy` only, not `check`",
                id="static-on-check",
            ),
            pytest.param(
                ["chains", "pkg", "--no-static"],
                "are supported by `expiry` and `policy` only, not `chains`",
                id="no-static-on-chains",
            ),
            pytest.param(
                ["all", "pkg", "--nostatic"],
                "are supported by `expiry` and `policy` only, not `all`",
                id="nostatic-on-all",
            ),
            pytest.param(
                ["status", "pkg", "--static=false"],
                "are supported by `expiry` and `policy` only, not `status`",
                id="static-false-on-status",
            ),
            pytest.param(
                ["status", "--version=9.0", "pkg", "--", "v2"], "the version was given twice", id="twice-after-sep"
            ),
        ],
    )
    def test_usage_error_exits_two(self, argv: list[str], message: str, capsys: pytest.CaptureFixture[str]) -> None:
        """A malformed command line exits 2 with argparse's message before any subcommand runs.

        Option abbreviations are off, so ``--exit`` is a typo, never a silent ``--exit-zero``. ``--static`` is refused
        on a subcommand that has no static mode in every spelling, ``--no-static`` and ``--static=false`` included,
        and the message names both flags: a no-op that took the negative spelling quietly would hide that a CI line
        meant for ``expiry`` or ``policy`` was copied onto the wrong subcommand.

        """
        with pytest.raises(SystemExit) as exc_info:
            _parse_cli_args(argv)
        assert exc_info.value.code == 2
        assert message in capsys.readouterr().err


class TestLegacyPositionalVersion:
    """The second positional of ``expiry``, ``all`` and ``status`` is the deprecated version only when it is no path."""

    @pytest.mark.parametrize(
        ("argv", "version"),
        [
            pytest.param(["expiry", "pkg", "2.0"], "2.0", id="expiry-release"),
            pytest.param(["expiry", "pkg", "v2"], "v2", id="expiry-v-prefix"),
            pytest.param(["status", "pkg", "1.0.0rc1"], "1.0.0rc1", id="status-pre-release"),
            pytest.param(["all", "--no-recursive", "pkg", "0.10"], "0.10", id="all-trailing-zero"),
        ],
    )
    def test_version_shaped_second_positional_is_the_version(
        self, argv: list[str], version: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``expiry pkg 2.0`` keeps meaning version 2.0, with a notice that names ``--version`` and removal at 1.0.

        CI scripts written before several paths were accepted pass the version as the second positional. It names no
        path and no module and parses as PEP 440, so it is still the version, and the run says how to spell it from now.

        """
        parsed = _parse_cli_args(argv)
        assert parsed is not None
        err = capsys.readouterr().err
        assert (parsed[1]["path"], parsed[1]["version"]) == (["pkg"], version)
        assert f"pass `--version {version}` instead" in err
        assert "will stop working in 1.0" in err

    @pytest.mark.parametrize(
        "second",
        [
            pytest.param("json", id="findable-module"),
            pytest.param("pkg_b", id="missing-path-stays-a-path"),
            pytest.param("src/pkg_b", id="mistyped-directory"),
        ],
    )
    def test_second_positional_that_is_no_version_is_a_path(
        self, second: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A second positional that is a module or no version at all is a second path, never a version.

        ``all pkg_a pkg_b`` scans two packages; a typo in the second one must reach the scan as a path, so the run
        reports it as no such path instead of comparing against a version called ``pkg_b``.

        """
        parsed = _parse_cli_args(["all", "pkg_a", second])
        assert parsed is not None
        assert parsed[1] == {"path": ["pkg_a", second]}
        assert "deprecated" not in capsys.readouterr().err

    def test_existing_directory_named_like_a_version_is_a_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A directory called ``2.0`` in the working directory is scanned, not read as the version.

        A monorepo may keep release branches checked out as directories named after the release. The path that exists
        wins over the version spelling.

        """
        (tmp_path / "2.0").mkdir()
        monkeypatch.chdir(tmp_path)
        parsed = _parse_cli_args(["status", "pkg", "2.0"])
        assert parsed is not None
        assert parsed[1] == {"path": ["pkg", "2.0"]}
        assert "deprecated" not in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("2.0", True, id="release"),
            pytest.param("v2", True, id="v-prefix"),
            pytest.param("1.0.0rc1", True, id="pre-release"),
            pytest.param("src/pkg_b", False, id="path"),
            pytest.param("pkg_b", False, id="name"),
        ],
    )
    def test_version_shape_stands_in_without_packaging(self, text: str, expected: bool) -> None:
        """Without ``packaging`` a version-shaped token still counts as a version, and a name or path does not.

        A base install has no PEP 440 parser, yet ``expiry pkg 2.0`` must keep its meaning there too; only tokens that
        start like a release number qualify.

        """
        with patch("deprecate._cli.argv._parse_version", side_effect=ImportError("No module named 'packaging'")):
            assert _parses_as_version(text) is expected


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
