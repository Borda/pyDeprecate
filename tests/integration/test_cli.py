"""Integration tests for the CLI — real subprocess invocations only."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SRC_DIR = str(Path(__file__).resolve().parent.parent.parent / "src")
_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None
_MYPKG_INIT = """\
from deprecate import deprecated


def new_fn(x: int) -> int:
    return x


@deprecated(target=new_fn, deprecated_in="1.0", remove_in="9.0", args_mapping={"old": "x"})
def old_fn(old: int) -> int:
    pass
"""

# Package with an invalid args_mapping (target param does not exist in new_fn).
# cmd_check exits 1 for this package without --exit-zero.
_MYPKG_INIT_INVALID = """\
from deprecate import deprecated


def new_fn(x: int) -> int:
    return x


@deprecated(target=new_fn, deprecated_in="1.0", remove_in="9.0", args_mapping={"old": "nonexistent"})
def old_fn(old: int) -> int:
    pass
"""


# Package whose wrapper is deprecated and removed in the very same release — trips the ``min-grace`` rule
# alone (``1.0`` is a major release, so the removal-cadence rule stays satisfied).
_MYPKG_INIT_AGGRESSIVE = """\
from deprecate import deprecated


def new_fn(x: int) -> int:
    return x


@deprecated(target=new_fn, deprecated_in="1.0", remove_in="1.0", args_mapping={"old": "x"})
def old_fn(old: int) -> int:
    pass
"""


def _cli_env(**extra: str) -> dict[str, str]:
    """Build env dict with PYTHONPATH pointing at src/ so subprocess can find deprecate."""
    existing_pythonpath = os.environ.get("PYTHONPATH")
    pythonpath = f"{_SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else _SRC_DIR
    return {**os.environ, "PYTHONPATH": pythonpath, **extra}


def _run_cli(
    args: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m deprecate <args>`` with an explicit UTF-8 decode.

    ``subprocess.run(..., text=True)`` without an explicit ``encoding=`` decodes the child's output
    with ``locale.getpreferredencoding()``, which resolves to cp1252 on Windows. The CLI deliberately
    emits UTF-8 box-drawing characters, so a cp1252 decode raises ``UnicodeDecodeError`` inside
    CPython's subprocess reader thread; the thread dies silently and ``communicate()`` then yields
    ``stdout=None``, turning any ``"x" in result.stdout`` assertion into a bare ``TypeError`` instead
    of a readable test failure. Forcing ``encoding="utf-8"`` (with ``errors="replace"`` as a last-resort
    fallback) keeps every subprocess invocation in this file platform-independent.
    """
    return subprocess.run(
        [sys.executable, "-m", "deprecate", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd,
    )


def _make_pkg(tmp_path: Path, name: str = "mypkg") -> Path:
    """Create a minimal importable package with one deprecated wrapper."""
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(_MYPKG_INIT)
    return pkg


class TestCliInvocation:
    """Tests for real CLI invocations via subprocess."""

    def test_no_args_shows_help(self) -> None:
        """CLI with no arguments prints help and exits 0 (Fire shows component help)."""
        result = _run_cli([], env=_cli_env())
        assert result.returncode == 0
        assert "check" in (result.stdout + result.stderr).lower()

    def test_help(self) -> None:
        """CLI --help exits 0 and lists subcommands."""
        result = _run_cli(["--help"], env=_cli_env())
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "check" in combined.lower()

    def test_nonexistent_module(self) -> None:
        """CLI with a module that doesn't exist exits non-zero."""
        result = _run_cli(["check", "nonexistent_module_xyz"], env=_cli_env(COLUMNS="200"))
        assert result.returncode != 0


class TestCliSubcommands:
    """Integration tests for the four CLI subcommands via subprocess."""

    def test_check_subcommand_explicit(self, tmp_path: Path) -> None:
        """'pydeprecate check <path>' scans and exits 0 for a clean package."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli(["check", str(pkg)], env=_cli_env(), cwd=tmp_path)
        assert result.returncode == 0
        assert "Scanning:" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_expiry_subcommand_no_expired(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <path> --version 1.0' exits 0 when nothing is expired."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli(["expiry", str(pkg), "--version", "1.0"], env=_cli_env(), cwd=tmp_path)
        assert result.returncode == 0
        assert "No expired" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_expiry_subcommand_expired(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <path> --version 9.0' exits 1 when wrapper is past remove_in."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli(["expiry", str(pkg), "--version", "9.0"], env=_cli_env(), cwd=tmp_path)
        assert result.returncode == 1

    def test_chains_subcommand_no_chains(self, tmp_path: Path) -> None:
        """'pydeprecate chains <path>' exits 0 for a package with no deprecation chains."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli(["chains", str(pkg)], env=_cli_env(), cwd=tmp_path)
        assert result.returncode == 0
        assert "No deprecation chains" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_all_subcommand_clean(self, tmp_path: Path) -> None:
        """'pydeprecate all <path> --version 1.0' exits 0 when all checks pass."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli(["all", str(pkg), "--version", "1.0"], env=_cli_env(), cwd=tmp_path)
        assert result.returncode == 0

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_status_subcommand_exits_0(self, tmp_path: Path) -> None:
        """'pydeprecate status <path> --version 1.0' exits 0 and prints a markdown table."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli(["status", str(pkg), "--version", "1.0"], env=_cli_env(), cwd=tmp_path)
        assert result.returncode == 0
        assert "Original API" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_expiry_broken_user_import_propagates(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <path> --version 9.0' exits non-zero when the scanned package has a broken import.

        When ``validate_deprecation_expiry`` imports the user's module and the module raises an ImportError
        (e.g. ``from _nonexistent_module_xyz_ import something``), the CLI must not silently swallow that
        error and exit 0 with a misleading "Could not determine version" message.  The real broken-import
        error must reach the user so they can fix their package, not get confused about version detection.

        """
        broken_pkg = tmp_path / "broken_pkg"
        broken_pkg.mkdir()
        (broken_pkg / "__init__.py").write_text("from _nonexistent_module_xyz_ import something\n")
        result = _run_cli(["expiry", str(broken_pkg), "--version", "9.0"], env=_cli_env(), cwd=tmp_path)
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "_nonexistent_module_xyz_" in combined
        assert "Could not determine the current package version" not in combined

    def test_all_plain_directory_exits_0(self, tmp_path: Path) -> None:
        """'pydeprecate all <plaindir>' exits 0 when every check passes on a plain dir without __init__.py.

        cmd_check already scans plain directories; cmd_all appends a status table afterwards. Resolving the
        module name for that advisory table must not turn a clean run into exit 1 on a directory that has no
        importable package — the table is a display artifact, not a pass/fail gate.
        """
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "mod.py").write_text("x = 1\n")
        result = _run_cli(["all", str(plain)], env=_cli_env(), cwd=tmp_path)
        assert result.returncode == 0

    def test_status_plain_directory_exits_0(self, tmp_path: Path) -> None:
        """'pydeprecate status <plaindir>' exits 0 on a plain dir; status generation is never a pass/fail gate."""
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "mod.py").write_text("x = 1\n")
        result = _run_cli(["status", str(plain)], env=_cli_env(), cwd=tmp_path)
        assert result.returncode == 0

    def test_check_plain_directory_warns_nested_files_on_stderr(self, tmp_path: Path) -> None:
        """'pydeprecate check <plaindir>' warns on stderr when the directory contains nested .py files.

        Plain directories are scanned one level deep only. When nested ``.py`` files are present in
        sub-directories they are silently skipped, but users must be informed via stderr so they know
        to switch to an importable package layout if they want full recursive coverage.

        """
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "mod.py").write_text("x = 1\n")
        sub = plain / "sub"
        sub.mkdir()
        (sub / "nested.py").write_text("y = 2\n")
        result = _run_cli(["check", str(plain)], env=_cli_env(), cwd=tmp_path)
        assert result.returncode == 0
        assert "Skipping nested Python files" in result.stderr

    def test_help_lists_subcommands(self) -> None:
        """'pydeprecate --help' output includes the six subcommand names."""
        result = _run_cli(["--help"], env=_cli_env())
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        for name in ("check", "expiry", "policy", "chains", "all", "status"):
            assert name in combined, f"subcommand '{name}' missing from --help output"

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_policy_subcommand_clean_package(self, tmp_path: Path) -> None:
        """'pydeprecate policy <path>' exits 0 for a package whose wrapper respects the default policy.

        The fixture package deprecates in `1.0` with a forwarding target and schedules removal at the `9.0`
        major — the disciplined shape the default rules are written to wave through without any flags.
        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli(["policy", str(pkg), "--version", "1.0"], env=_cli_env(), cwd=tmp_path)
        assert result.returncode == 0
        assert "No deprecation policy violations" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_policy_subcommand_reports_violation(self, tmp_path: Path) -> None:
        """'pydeprecate policy <path>' exits 1 and names the broken rule for an aggressive removal schedule.

        The package deprecates and removes inside the same `1.x` line, which is the schedule a reviewer is
        meant to catch before release: callers get no version they can upgrade through.
        """
        pkg = tmp_path / "aggressivepkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(_MYPKG_INIT_AGGRESSIVE)
        result = _run_cli(["policy", str(pkg), "--version", "1.0"], env=_cli_env(), cwd=tmp_path)
        assert result.returncode == 1
        assert "min-grace" in (result.stdout or ""), result

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_policy_subcommand_rule_can_be_disabled(self, tmp_path: Path) -> None:
        """'--min-grace=None' drops the grace-window rule so a same-line removal passes the gate.

        A project that ships removals inside a release line still wants the remaining rules; without a working
        opt-out flag the whole subcommand would be unusable for it.
        """
        pkg = tmp_path / "aggressivepkg2"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(_MYPKG_INIT_AGGRESSIVE)
        result = _run_cli(
            ["policy", str(pkg), "--version", "1.0", "--min-grace=None"],
            env=_cli_env(),
            cwd=tmp_path,
        )
        assert result.returncode == 0

    def test_policy_subcommand_invalid_remove_only_at_exits_two(self, tmp_path: Path) -> None:
        """'--remove-only-at=bogus' exits 2 and names the accepted spellings instead of scanning anything.

        A typo'd removal-cadence level must be caught by `_build_policy_spec()`'s upfront validation before
        any package scanning starts, so the user gets a usage error (exit 2) naming `major`/`minor`/`patch`
        rather than a scan failure, a stack trace, or a silently-ignored flag.
        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli(
            ["policy", str(pkg), "--remove-only-at=bogus"],
            env=_cli_env(),
            cwd=tmp_path,
        )
        assert result.returncode == 2
        combined = result.stdout + result.stderr
        assert "major" in combined
        assert "minor" in combined
        assert "patch" in combined

    def test_subcommand_help(self) -> None:
        """'pydeprecate expiry --help' shows expiry-specific options."""
        result = _run_cli(["expiry", "--help"], env=_cli_env())
        assert result.returncode == 0
        assert "version" in (result.stdout + result.stderr).lower()

    def test_check_no_recursive_flag(self, tmp_path: Path) -> None:
        """'pydeprecate check <path> --norecursive' is accepted and exits 0."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli(["check", str(pkg), "--norecursive"], env=_cli_env(), cwd=tmp_path)
        assert result.returncode == 0

    def test_check_exit_zero_dash_form(self, tmp_path: Path) -> None:
        """'--exit-zero' (dash form) forces exit 0 even when invalid args are found."""
        pkg = tmp_path / "badpkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(_MYPKG_INIT_INVALID)
        result = _run_cli(["check", str(pkg), "--exit-zero"], env=_cli_env(), cwd=tmp_path)
        assert result.returncode == 0

    def test_check_exit_zero_underscore_form(self, tmp_path: Path) -> None:
        """Fire also accepts '--exit_zero' (underscore) as an alias for '--exit-zero'."""
        pkg = tmp_path / "badpkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(_MYPKG_INIT_INVALID)
        result = _run_cli(["check", str(pkg), "--exit_zero"], env=_cli_env(), cwd=tmp_path)
        assert result.returncode == 0


class TestCliArgumentValidation:
    """Unknown or misspelled flags and version auto-detection must fail safe, never silently mislead."""

    def test_check_unknown_flag_exits_nonzero(self, tmp_path: Path) -> None:
        """'pydeprecate check <path> --bogusflag' exits non-zero with a diagnostic.

        A CI pipeline invoking the CLI with an unknown flag must fail the job: exiting inside the Fire
        trace would suppress Fire's "Could not consume arg" check, silently ignoring the flag and letting
        the gate pass with exit 0 on unvalidated input.

        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli(["check", str(pkg), "--bogusflag"], env=_cli_env(COLUMNS="200"), cwd=tmp_path)
        assert result.returncode != 0
        assert "Could not consume arg" in result.stderr + result.stdout

    def test_expiry_misspelled_version_flag_exits_nonzero(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <path> --verison 9.0' (typo) exits non-zero instead of dropping the value.

        A user pinning the comparison version with a typo'd flag must get a hard error; silently dropping
        the flag would run the expiry gate against an auto-detected (wrong) version — false pass or false
        fail with zero diagnostics.

        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli(["expiry", str(pkg), "--verison", "9.0"], env=_cli_env(COLUMNS="200"), cwd=tmp_path)
        assert result.returncode != 0
        assert "Could not consume arg" in result.stderr + result.stdout

    def test_expiry_module_name_ignores_unrelated_cwd_pyproject(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <module_name>' run from an unrelated project must not steal its version.

        Scanning an importable module *name* (not a filesystem path) from a directory that happens to
        contain another project's ``pyproject.toml`` must not auto-detect that project's version — doing
        so compares deprecation deadlines against a foreign version and flips the CI gate arbitrarily.

        """
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_pkg(proj)
        decoy = tmp_path / "otherproj"
        decoy.mkdir()
        (decoy / "pyproject.toml").write_text('[project]\nname = "fakeproj"\nversion = "9.9.9"\n')
        env = _cli_env()
        env["PYTHONPATH"] = f"{proj}{os.pathsep}{env['PYTHONPATH']}"
        result = _run_cli(["expiry", "mypkg"], env=env, cwd=decoy)
        assert "9.9.9" not in result.stdout + result.stderr
        assert result.returncode == 0
