"""Unit tests for :func:`deprecate._cli.cli`: the argparse entry point and its argv dispatch."""

import inspect
import json
import sys
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deprecate._cli import cli
from deprecate._cli.commands import cmd_all, cmd_chains, cmd_check, cmd_expiry, cmd_policy, cmd_status
from deprecate._cli.config import _FROM_PYPROJECT
from tests.unittests.cli._helpers import _EXPIRED_MSG, _POLICY_VIOLATION


def _called_with(function: Callable[..., int], mock: MagicMock) -> dict[str, object]:
    """Return every argument of the mocked ``cmd_*`` call by name, the omitted ones at their signature default."""
    args, kwargs = mock.call_args
    bound = inspect.signature(function).bind(*args, **kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)


# ---------------------------------------------------------------------------
# cli() entry point and backward compat
# ---------------------------------------------------------------------------


class TestCliEntryPoint:
    """Tests for the cli() entry point."""

    def test_no_subcommand_shows_help(self) -> None:
        """``cli()`` with no arguments prints help and returns without running a subcommand."""
        with patch("sys.argv", ["pydeprecate"]):
            cli()  # no SystemExit — the help is printed and the call returns

    def test_help_exits_zero(self) -> None:
        """``cli()`` with --help exits 0."""
        with patch("sys.argv", ["pydeprecate", "--help"]), pytest.raises(SystemExit) as exc_info:
            cli()
        assert exc_info.value.code == 0

    def test_check_subcommand_dispatches(self) -> None:
        """``cli()`` with check subcommand calls cmd_check and exits with the captured return code."""
        with (
            patch("sys.argv", ["pydeprecate", "check", "some_module"]),
            patch("deprecate._cli.commands.cmd_check", autospec=True, return_value=0) as mock_check,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        mock_check.assert_called_once()
        assert exc_info.value.code == 0

    def test_expiry_subcommand_dispatches(self) -> None:
        """``cli()`` with expiry subcommand calls cmd_expiry and exits with the captured return code."""
        with (
            patch("sys.argv", ["pydeprecate", "expiry", "some_module", "--version", "2.0"]),
            patch("deprecate._cli.commands.cmd_expiry", autospec=True, return_value=0) as mock_expiry,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        mock_expiry.assert_called_once()
        assert exc_info.value.code == 0

    def test_chains_subcommand_dispatches(self) -> None:
        """``cli()`` with chains subcommand calls cmd_chains and exits with the captured return code."""
        with (
            patch("sys.argv", ["pydeprecate", "chains", "some_module"]),
            patch("deprecate._cli.commands.cmd_chains", autospec=True, return_value=0) as mock_chains,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        mock_chains.assert_called_once()
        assert exc_info.value.code == 0

    def test_all_subcommand_dispatches(self) -> None:
        """``cli()`` with all subcommand calls cmd_all and exits with the captured return code."""
        with (
            patch("sys.argv", ["pydeprecate", "all", "some_module"]),
            patch("deprecate._cli.commands.cmd_all", autospec=True, return_value=0) as mock_all,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        mock_all.assert_called_once()
        assert exc_info.value.code == 0

    @pytest.mark.parametrize(
        ("expired", "expected_code"),
        [
            pytest.param([], 0, id="gates-pass"),
            pytest.param([_EXPIRED_MSG], 1, id="gate-fails"),
        ],
    )
    @patch("deprecate._cli.commands.generate_deprecation_table", side_effect=RuntimeError("table rendering failed"))
    @patch("deprecate._cli.commands._check_expiry_for_callables")
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_all_report_json_survives_table_render_failure(
        self,
        mock_find: MagicMock,
        mock_expiry: MagicMock,
        mock_gen: MagicMock,
        expired: list[str],
        expected_code: int,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``all --report-json`` still writes its report, with a null table, when the table cannot be rendered.

        ``all`` hands its scan to every sub-gate, so the report has no scan to rebuild the table from and a failed
        render leaves ``table`` null. The failure is printed once, as for a run without ``--report-json``, and the
        gates' own exit code is untouched; a second render attempt for the report would fail the same way and only
        add another stderr line.
        """
        mock_expiry.return_value = expired
        report = tmp_path / "report.json"
        with (
            patch("sys.argv", ["pydeprecate", "all", "some_module", "--version=1.0", f"--report-json={report}"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        err = capsys.readouterr().err
        assert exc_info.value.code == expected_code
        assert json.loads(report.read_text(encoding="utf-8"))["table"] is None
        assert "Could not render the deprecation table: table rendering failed" in err
        assert "for the JSON report" not in err

    def test_exit_code_propagated(self) -> None:
        """``cli()`` propagates the non-zero exit code from the subcommand."""
        with (
            patch("sys.argv", ["pydeprecate", "check", "some_module"]),
            patch("deprecate._cli.commands.cmd_check", autospec=True, return_value=1),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        assert exc_info.value.code == 1

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_unknown_flag_exits_nonzero(self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """``cli()`` with an unknown flag exits 2 before anything is scanned.

        A CI job invoking ``pydeprecate check pkg --bogusflag`` must fail loudly: a typo'd flag that is silently
        ignored lets the gate pass with exit 0 on unvalidated input.

        """
        with (
            patch("sys.argv", ["pydeprecate", "check", "some_module", "--bogusflag"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        assert exc_info.value.code == 2
        mock_find.assert_not_called()
        assert "unrecognized arguments: --bogusflag" in capsys.readouterr().err

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_misspelled_value_flag_exits_nonzero(self, mock_expiry: MagicMock) -> None:
        """``cli()`` with a misspelled ``--verison`` flag exits non-zero instead of dropping the value.

        A user running ``pydeprecate expiry pkg --verison 2.0`` intends to pin the comparison version;
        silently dropping the typo'd flag would check expiry against an auto-detected (wrong) version and
        report a false pass or false fail with no diagnostic.

        """
        with (
            patch("sys.argv", ["pydeprecate", "expiry", "some_module", "--verison", "2.0"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        assert exc_info.value.code == 2


class TestCliBoolFlags:
    """Tests for boolean flags placed anywhere in the argument list (e.g. by a pre-commit hook's ``args``)."""

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            pytest.param(["check", "--exit-zero", "some_module"], {"exit_zero": True}, id="dash-before-path"),
            pytest.param(["check", "--exit_zero", "some_module"], {"exit_zero": True}, id="underscore-before-path"),
            pytest.param(["check", "--norecursive", "some_module"], {"recursive": False}, id="negated-before-path"),
            pytest.param(["check", "--recursive", "some_module"], {"recursive": True}, id="plain-before-path"),
            pytest.param(["check", "--noexit-zero", "some_module"], {"exit_zero": False}, id="negated-dash"),
            pytest.param(["check", "--exit-zero", "False", "some_module"], {"exit_zero": False}, id="explicit-value"),
            pytest.param(["check", "--exit-zero", "false", "some_module"], {"exit_zero": False}, id="lowercase-false"),
            pytest.param(["check", "--exit-zero", "true", "some_module"], {"exit_zero": True}, id="lowercase-true"),
            pytest.param(["check", "--exit-zero", "0", "some_module"], {"exit_zero": False}, id="zero-value"),
            pytest.param(["check", "--recursive", "1", "some_module"], {"recursive": True}, id="one-value"),
            pytest.param(
                ["check", "--exit-zero=true", "some_module"], {"exit_zero": True}, id="attached-lowercase-true"
            ),
            pytest.param(["check", "--exit-zero=1", "some_module"], {"exit_zero": True}, id="attached-one"),
            pytest.param(["check", "--exit-zero=0", "some_module"], {"exit_zero": False}, id="attached-zero"),
            pytest.param(["check", "--recursive=FALSE", "some_module"], {"recursive": False}, id="attached-uppercase"),
            pytest.param(["check", "some_module", "--exit-zero"], {"exit_zero": True}, id="trailing-flag"),
            pytest.param(["--exit-zero", "some_module"], {"exit_zero": True}, id="implicit-check"),
        ],
    )
    def test_flag_before_path_keeps_path(self, argv: list[str], expected: dict[str, bool]) -> None:
        """A bare boolean flag never consumes the path that follows it.

        A pre-commit hook builds its command as ``entry + args``, so a config such as ``args: [--exit-zero,
        src/mypkg]`` puts the flag before the path. A parser that takes the token after a bare flag as the flag's
        value would bind ``exit_zero='src/mypkg'`` and silently scan the current directory instead (Fire did).

        """
        with (
            patch("sys.argv", ["pydeprecate", *argv]),
            patch("deprecate._cli.commands.cmd_check", autospec=True, return_value=0) as mock_check,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        assert exc_info.value.code == 0
        bound = _called_with(cmd_check, mock_check)
        assert bound["path"] == "some_module"
        assert {key: bound[key] for key in expected} == expected

    @pytest.mark.parametrize(
        ("literal", "expected"),
        [
            pytest.param("0", False, id="zero"),
            pytest.param("1", True, id="one"),
            pytest.param("false", False, id="lowercase-false"),
            pytest.param("TRUE", True, id="uppercase-true"),
        ],
    )
    def test_value_token_does_not_leak_into_version(self, literal: str, expected: bool) -> None:
        """A boolean literal after ``--exit-zero`` is the flag's value, not the ``version`` positional.

        A CI step ``pydeprecate expiry pkg --exit-zero 0`` meant ``exit_zero=False``; when the ``0`` stayed a free
        token it bound to ``version`` instead, which the boolean guard never inspects, so the expiry gate compared
        against version ``0`` and silently passed.

        """
        with (
            patch("sys.argv", ["pydeprecate", "expiry", "some_module", "--exit-zero", literal]),
            patch("deprecate._cli.commands.cmd_expiry", autospec=True, return_value=0) as mock_expiry,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        assert exc_info.value.code == 0
        bound = _called_with(cmd_expiry, mock_expiry)
        assert (bound["path"], bound["exit_zero"], bound["version"]) == ("some_module", expected, None)

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            pytest.param(["--message-required", "some_module"], True, id="bare-before-path"),
            pytest.param(["--nomessage-required", "some_module"], False, id="negated-before-path"),
            pytest.param(["--message_required", "false", "some_module"], False, id="explicit-false-before-path"),
            pytest.param(["some_module", "--message-required"], True, id="trailing-flag"),
            pytest.param(["some_module"], _FROM_PYPROJECT, id="omitted-keeps-pyproject-sentinel"),
        ],
    )
    def test_policy_message_required_in_any_order(self, argv: list[str], expected: object) -> None:
        """``policy --message-required <path>`` scans ``<path>``; an omitted flag still defers to ``pyproject.toml``.

        ``message_required`` is typed as a config-backed boolean rather than a plain ``bool``, so the normalizer
        used to skip it: Fire bound the path to the flag (``policy --message-required src/pkg`` exited 2) and
        ``--nomessage-required`` before the path left both tokens unconsumed. The omitted case must keep the
        sentinel default, otherwise the ``[tool.pydeprecate.policy]`` value could never apply.

        """
        with (
            patch("sys.argv", ["pydeprecate", "policy", *argv]),
            patch("deprecate._cli.commands.cmd_policy", autospec=True, return_value=0) as mock_policy,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        assert exc_info.value.code == 0
        bound = _called_with(cmd_policy, mock_policy)
        assert (bound["path"], bound["message_required"]) == ("some_module", expected)

    @pytest.mark.parametrize(
        ("subcommand", "command", "argv", "expected"),
        [
            pytest.param("all", cmd_all, ["--exit-zero", "0", "some_module"], {"exit_zero": False}, id="all-zero"),
            pytest.param(
                "all",
                cmd_all,
                ["--norecursive", "--exit-zero", "some_module"],
                {"recursive": False, "exit_zero": True},
                id="all-two-flags",
            ),
            pytest.param(
                "chains", cmd_chains, ["--norecursive", "some_module"], {"recursive": False}, id="chains-negated"
            ),
            pytest.param(
                "expiry",
                cmd_expiry,
                ["--recursive", "0", "--exit-zero", "some_module"],
                {"recursive": False, "exit_zero": True},
                id="expiry-two-flags",
            ),
            pytest.param(
                "policy",
                cmd_policy,
                ["--exit-zero", "--nomessage-required", "some_module"],
                {"exit_zero": True, "message_required": False},
                id="policy-two-flags",
            ),
        ],
    )
    def test_every_subcommand_binds_flags_before_path(
        self, subcommand: str, command: Callable[..., int], argv: list[str], expected: dict[str, bool]
    ) -> None:
        """Flags placed before the path bind to their own parameters for ``all``, ``chains``, ``expiry``, ``policy``.

        The normalizer derives its flag set from each subcommand's own signature, so a subcommand-specific
        parameter list (``policy``'s ``message_required``, ``expiry``'s ``version`` slot) must not shift the path
        or a value into the wrong parameter.

        """
        with (
            patch("sys.argv", ["pydeprecate", subcommand, *argv]),
            patch(f"deprecate._cli.commands.cmd_{subcommand}", autospec=True, return_value=0) as mock_command,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        assert exc_info.value.code == 0
        bound = _called_with(command, mock_command)
        assert {key: bound[key] for key in ("path", *expected)} == {"path": "some_module", **expected}

    def test_status_include_members_before_path(self) -> None:
        """The ``status`` subcommand's ``--noinclude-members`` flag is normalized like the shared flags.

        Every ``bool`` parameter of the dispatched subcommand is covered, not a fixed list of flag names, so a
        subcommand-specific switch placed before the path does not swallow it either.

        """
        with (
            patch("sys.argv", ["pydeprecate", "status", "--noinclude-members", "some_module"]),
            patch("deprecate._cli.commands.cmd_status", autospec=True, return_value=0) as mock_status,
            pytest.raises(SystemExit),
        ):
            cli()
        bound = _called_with(cmd_status, mock_status)
        assert (bound["path"], bound["include_members"]) == ("some_module", False)

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            pytest.param(["check", "-r", "some_module"], "unrecognized arguments: -r", id="short-flag"),
            pytest.param(["check", "some_module", "--exit-zero=yes"], "expects True or False", id="non-bool-value"),
        ],
    )
    def test_non_bool_value_exits_two(self, argv: list[str], message: str, capsys: pytest.CaptureFixture[str]) -> None:
        """A non-boolean value for a boolean flag, or an unknown short flag, is a usage error.

        Without this a stray value could make the run pass while scanning something other than what the user named;
        no short flags exist, so ``-r`` is unknown rather than a spelling of ``--recursive``.

        """
        with (
            patch("sys.argv", ["pydeprecate", *argv]),
            patch("deprecate._cli.commands.cmd_check", autospec=True, return_value=0) as mock_check,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        assert exc_info.value.code == 2
        mock_check.assert_not_called()
        assert message in capsys.readouterr().err

    @pytest.mark.parametrize("subcommand", ["check", "chains"])
    @pytest.mark.parametrize(
        "flags",
        [pytest.param((), id="defaults"), pytest.param(("--norecursive", "--noexit-zero"), id="explicit-booleans")],
    )
    def test_extra_path_rejected_before_dispatch(
        self, subcommand: str, flags: tuple[str, ...], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Explicit boolean flags never let a second path bind to a different option.

        Both flags are already bound, so Fire used to put pkg_b into exclude and dispatched the command.
        The CLI must reject that invocation without calling the command or scanning pkg_a.

        """
        with (
            patch("sys.argv", ["pydeprecate", subcommand, *flags, "pkg_a", "pkg_b"]),
            patch(f"deprecate._cli.commands.cmd_{subcommand}", autospec=True, return_value=0) as mock_command,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        assert exc_info.value.code == 2
        mock_command.assert_not_called()
        assert "one path per run" in capsys.readouterr().err


class TestInternalOptionRejection:
    """Underscore-prefixed parameters are in-process plumbing and are rejected at the command line."""

    @pytest.mark.parametrize(
        "option",
        [
            pytest.param("--_advisory=yes", id="non-bool-value"),
            pytest.param("--_advisory=True", id="bool-value"),
            pytest.param("--_advisory", id="bare"),
        ],
    )
    def test_internal_option_exits_two(self, option: str, capsys: pytest.CaptureFixture[str]) -> None:
        """``policy pkg --_advisory=<anything>`` exits 2 without running the subcommand.

        Fire exposed the keyword-only ``_advisory`` of ``cmd_policy`` as a flag, so a typed value used to reach it
        unvalidated and silently turned a gating policy run into an advisory one; the parser never declares it.

        """
        with (
            patch("sys.argv", ["pydeprecate", "policy", "some_module", option]),
            patch("deprecate._cli.commands.cmd_policy", autospec=True, return_value=0) as mock_policy,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        assert exc_info.value.code == 2
        mock_policy.assert_not_called()
        assert "--_advisory" in capsys.readouterr().err

    @patch("deprecate._cli.commands.cmd_status", return_value=0)
    @patch("deprecate._cli.commands._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[_POLICY_VIOLATION])
    def test_all_stays_advisory(
        self, mock_find: MagicMock, mock_expiry: MagicMock, mock_status: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``all pkg`` still runs its policy pass in advisory mode and exits 0 despite a violation.

        ``cmd_all`` passes ``_advisory=True`` in process, which the command-line rejection must not disturb: the
        violation prints as a warning with the advisory trailer and does not gate the run.

        """
        with (
            patch("sys.argv", ["pydeprecate", "all", "some_module", "--version=2.0"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        captured = capsys.readouterr()
        assert exc_info.value.code == 0
        assert "[WARNING] Found deprecation policy violations:" in captured.out
        assert "(advisory here — run `pydeprecate policy` to gate on it)" in captured.out


class TestCliEmptyExceptionMessage:
    """An exception with an empty message must still produce a non-blank stderr exit line."""

    def test_empty_message_exception_exits_with_type_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A subcommand raising a message-less exception exits with the exception type name, not a blank line.

        ``sys.exit(str(exc))`` on such an exception exited 1 with nothing printed, giving CI no clue what failed;
        prefixing the exception type guarantees a meaningful stderr line.
        """

        class _SilentError(Exception):
            pass

        monkeypatch.setattr(sys, "argv", ["pydeprecate", "check", "."])
        with (
            patch("deprecate._cli.commands.cmd_check", autospec=True, side_effect=_SilentError),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        assert "_SilentError" in str(exc_info.value.code)
