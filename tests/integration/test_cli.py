"""Integration tests for the CLI — real subprocess invocations only."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

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

# Package whose remove_in="0.2" discriminates the PEP 440 string "0.10" from the float 0.1 that Fire's
# CLI parsing coerces an unquoted --version 0.10 into (0.1 < 0.2, so it would wrongly read as not expired).
_MYPKG_INIT_MULTI_DIGIT_MINOR = """\
from deprecate import deprecated


def new_fn(x: int) -> int:
    return x


@deprecated(target=new_fn, deprecated_in="0.1", remove_in="0.2")
def old_fn(x: int) -> int:
    pass
"""


# Package whose wrapper is deprecated and removed in the very same release — trips the ``min-grace`` rule.
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


def _make_pkg(tmp_path: Path, name: str = "mypkg", content: str = _MYPKG_INIT) -> Path:
    """Create a minimal importable package with one deprecated wrapper."""
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(content)
    return pkg


def _run_cli(
    *args: str, env: Optional[dict[str, str]] = None, cwd: Optional[Path] = None
) -> subprocess.CompletedProcess[str]:
    """Run 'python -m deprecate <args>' as a real subprocess, always decoding output as UTF-8.

    Without an explicit ``encoding``, ``text=True`` falls back to the platform's default locale
    encoding (``cp1252`` on Windows), which cannot decode the UTF-8 bytes this CLI always writes
    (``_ensure_utf8_streams`` reconfigures its own stdout/stderr to UTF-8 regardless of console
    codepage) — corrupting ``result.stdout``/``result.stderr`` or raising ``UnicodeDecodeError``
    inside the subprocess pipe-reader thread before ``subprocess.run`` ever returns.
    """
    return subprocess.run(
        [sys.executable, "-m", "deprecate", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env if env is not None else _cli_env(),
        cwd=cwd,
    )


class TestCliInvocation:
    """Tests for real CLI invocations via subprocess."""

    def test_no_args_shows_help(self) -> None:
        """CLI with no arguments prints help and exits 0 (Fire shows component help)."""
        result = _run_cli()
        assert result.returncode == 0
        assert "check" in (result.stdout + result.stderr).lower()

    def test_help(self) -> None:
        """CLI --help exits 0 and lists subcommands."""
        result = _run_cli("--help")
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "check" in combined.lower()

    def test_nonexistent_module(self) -> None:
        """CLI with a module that doesn't exist exits non-zero."""
        result = _run_cli("check", "nonexistent_module_xyz", env=_cli_env(COLUMNS="200"))
        assert result.returncode != 0


class TestCliSubcommands:
    """Integration tests for the four CLI subcommands via subprocess."""

    def test_check_subcommand_explicit(self, tmp_path: Path) -> None:
        """'pydeprecate check <path>' scans and exits 0 for a clean package."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli("check", str(pkg), cwd=tmp_path)
        assert result.returncode == 0
        assert "Scanning:" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_expiry_subcommand_no_expired(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <path> --version 1.0' exits 0 when nothing is expired."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli("expiry", str(pkg), "--version", "1.0", cwd=tmp_path)
        assert result.returncode == 0
        assert "No expired" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_expiry_subcommand_expired(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <path> --version 9.0' exits 1 when wrapper is past remove_in."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli("expiry", str(pkg), "--version", "9.0", cwd=tmp_path)
        assert result.returncode == 1

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    @pytest.mark.parametrize(
        ("version_arg", "returncode", "expected_text"),
        [
            pytest.param("0.10", 0, "No expired", id="unquoted-coerced-to-float"),
            pytest.param('"0.10"', 1, "expired wrapper", id="quoted-preserves-string"),
        ],
    )
    def test_expiry_multi_digit_minor_version_requires_quoting(
        self, tmp_path: Path, version_arg: str, returncode: int, expected_text: str
    ) -> None:
        """'pydeprecate expiry <path> --version <arg>' behaves differently for a bare vs. quoted "0.10".

        skills/prune/SKILL.md warns that CLI argument parsing can coerce "0.10" to 0.1 and that "shell
        quoting alone does not guarantee preservation" — confirmed here: Fire infers a bare
        ``--version 0.10`` as the float 0.1 before it ever reaches this package's own ``str(version)``
        normalization, so a wrapper with ``remove_in="0.2"`` is (wrongly) reported as not yet expired.
        Passing the value as an embedded string literal (``'"0.10"'``) is the escape the skill alludes
        to: it reaches ``validate_deprecation_expiry`` with "0.10" intact, and PEP 440 ordering correctly
        reports the same wrapper as expired against "0.2". Pinning both outcomes in one test documents
        the coercion bug and the workaround as a single, comparable contract.
        """
        pkg = _make_pkg(tmp_path, content=_MYPKG_INIT_MULTI_DIGIT_MINOR)
        result = _run_cli("expiry", str(pkg), "--version", version_arg, cwd=tmp_path)
        assert result.returncode == returncode, result.stdout + result.stderr
        assert expected_text in result.stdout, result.stdout + result.stderr

    def test_chains_subcommand_no_chains(self, tmp_path: Path) -> None:
        """'pydeprecate chains <path>' exits 0 for a package with no deprecation chains."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli("chains", str(pkg), cwd=tmp_path)
        assert result.returncode == 0
        assert "No deprecation chains" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_all_subcommand_clean(self, tmp_path: Path) -> None:
        """'pydeprecate all <path> --version 1.0' exits 0 when all checks pass."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli("all", str(pkg), "--version", "1.0", cwd=tmp_path)
        assert result.returncode == 0

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_status_subcommand_exits_0(self, tmp_path: Path) -> None:
        """'pydeprecate status <path> --version 1.0' exits 0 and prints a markdown table."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli("status", str(pkg), "--version", "1.0", cwd=tmp_path)
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
        result = _run_cli("expiry", str(broken_pkg), "--version", "9.0", cwd=tmp_path)
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
        result = _run_cli("all", str(plain), cwd=tmp_path)
        assert result.returncode == 0

    def test_status_plain_directory_exits_0(self, tmp_path: Path) -> None:
        """'pydeprecate status <plaindir>' exits 0 on a plain dir; status generation is never a pass/fail gate."""
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "mod.py").write_text("x = 1\n")
        result = _run_cli("status", str(plain), cwd=tmp_path)
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
        result = _run_cli("check", str(plain), cwd=tmp_path)
        assert result.returncode == 0
        assert "Skipping nested Python files" in result.stderr

    def test_help_lists_subcommands(self) -> None:
        """'pydeprecate --help' output includes the six subcommand names."""
        result = _run_cli("--help")
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
        result = _run_cli("policy", str(pkg), cwd=tmp_path)
        assert result.returncode == 0
        assert "No deprecation policy violations" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_policy_subcommand_reports_violation(self, tmp_path: Path) -> None:
        """'pydeprecate policy <path>' exits 1 and names the broken rule for an aggressive removal schedule.

        The package deprecates and removes inside the same `1.x` line, which is the schedule a reviewer is
        meant to catch before release: callers get no version they can upgrade through.
        """
        pkg = _make_pkg(tmp_path, name="aggressivepkg", content=_MYPKG_INIT_AGGRESSIVE)
        result = _run_cli("policy", str(pkg), cwd=tmp_path)
        assert result.returncode == 1
        assert "min-grace" in (result.stdout or ""), result

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_policy_subcommand_rule_can_be_disabled(self, tmp_path: Path) -> None:
        """'--min-grace=None' drops the grace-window rule so a same-line removal passes the gate.

        A project that ships removals inside a release line still wants the remaining rules; without a working
        opt-out flag the whole subcommand would be unusable for it.
        """
        pkg = _make_pkg(tmp_path, name="aggressivepkg2", content=_MYPKG_INIT_AGGRESSIVE)
        result = _run_cli("policy", str(pkg), "--min-grace=None", cwd=tmp_path)
        assert result.returncode == 0

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    @pytest.mark.parametrize(
        "flag",
        [
            pytest.param("--min-grace=0.2", id="float-minor-delta"),
            pytest.param("--min-grace=1", id="int-major-delta"),
        ],
    )
    def test_policy_subcommand_accepts_numeric_grace_delta(self, flag: str, tmp_path: Path) -> None:
        """An unquoted numeric '--min-grace' reaches the rule as a number and still enforces the window.

        Fire converts ``0.2`` and ``1`` on the command line into a ``float`` and an ``int`` before the
        subcommand sees them; the grace rule must accept those as the delta they spell instead of rejecting
        them as malformed (exit 2) or silently skipping the rule (exit 0).
        """
        pkg = _make_pkg(tmp_path, name="aggressivepkg3", content=_MYPKG_INIT_AGGRESSIVE)
        result = _run_cli("policy", str(pkg), flag, cwd=tmp_path)
        assert result.returncode == 1
        assert "min-grace" in (result.stdout or ""), result

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_policy_subcommand_reads_pyproject_table(self, tmp_path: Path) -> None:
        """'pydeprecate policy <path>' applies ``[tool.pydeprecate.policy]`` from the project's ``pyproject.toml``.

        The aggressive package fails the built-in window; with the project declaring ``min-grace = false`` next
        to it, the same bare invocation passes and the header attributes the setting to ``pyproject.toml`` —
        the shape a repository uses so CI and every developer run the one policy without repeating flags.
        """
        pkg = _make_pkg(tmp_path, name="aggressivepkg4", content=_MYPKG_INIT_AGGRESSIVE)
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmin-grace = false\n")
        result = _run_cli("policy", str(pkg), cwd=tmp_path)
        assert result.returncode == 0, result
        assert "min-grace=None (pyproject.toml)" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_policy_flag_overrides_pyproject_table(self, tmp_path: Path) -> None:
        """A typed '--min-grace' beats the value ``pyproject.toml`` declares for the same rule.

        The project disabled the window in its config; a maintainer re-enabling it for one run from the command
        line must see the violation, otherwise the flag would silently lose to the file.
        """
        pkg = _make_pkg(tmp_path, name="aggressivepkg5", content=_MYPKG_INIT_AGGRESSIVE)
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmin-grace = false\n")
        result = _run_cli("policy", str(pkg), "--min-grace=0.1", cwd=tmp_path)
        assert result.returncode == 1, result
        assert "min-grace=0.1 (flag)" in result.stdout

    def test_policy_malformed_pyproject_value_exits_two(self, tmp_path: Path) -> None:
        """A malformed ``min-grace`` in ``pyproject.toml`` exits 2 and names the file, before any scan.

        A usage error sourced from the file must point the reader at the file, not at a flag they never typed.
        """
        pkg = _make_pkg(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate.policy]\nmin-grace = "bogus"\n')
        result = _run_cli("policy", str(pkg), cwd=tmp_path)
        assert result.returncode == 2, result
        assert "pyproject.toml" in result.stderr

    def test_all_subcommand_reads_pyproject_table(self, tmp_path: Path) -> None:
        """'pydeprecate all <path>' runs its advisory policy pass with the ``pyproject.toml`` settings.

        ``all`` never fails on policy, but its printed advisory must reflect the project's declared rules,
        otherwise the summary a developer reads locally disagrees with the dedicated ``policy`` gate in CI.
        """
        pkg = _make_pkg(tmp_path)
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmessage-required = false\n")
        result = _run_cli("all", str(pkg), cwd=tmp_path)
        assert result.returncode == 0, result
        assert "message-required=False (pyproject.toml)" in result.stdout

    def test_policy_subcommand_invalid_min_grace_exits_two(self, tmp_path: Path) -> None:
        """'--min-grace=bogus' exits 2 and names the accepted spellings instead of scanning anything.

        A typo'd grace-window delta must be caught by `_build_policy_spec()`'s upfront validation before any
        package scanning starts, so the user gets a usage error (exit 2) naming the `1` / `0.1` / `0.0.1`
        spellings rather than a scan failure, a stack trace, or a silently-ignored flag.
        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli("policy", str(pkg), "--min-grace=bogus", cwd=tmp_path)
        assert result.returncode == 2
        combined = result.stdout + result.stderr
        assert "min_grace" in combined
        assert "0.0.1" in combined

    def test_subcommand_help(self) -> None:
        """'pydeprecate expiry --help' shows expiry-specific options."""
        result = _run_cli("expiry", "--help")
        assert result.returncode == 0
        assert "version" in (result.stdout + result.stderr).lower()

    def test_check_no_recursive_flag(self, tmp_path: Path) -> None:
        """'pydeprecate check <path> --norecursive' is accepted and exits 0."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli("check", str(pkg), "--norecursive", cwd=tmp_path)
        assert result.returncode == 0

    def test_check_exit_zero_dash_form(self, tmp_path: Path) -> None:
        """'--exit-zero' (dash form) forces exit 0 even when invalid args are found."""
        pkg = tmp_path / "badpkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(_MYPKG_INIT_INVALID)
        result = _run_cli("check", str(pkg), "--exit-zero", cwd=tmp_path)
        assert result.returncode == 0

    def test_check_exit_zero_underscore_form(self, tmp_path: Path) -> None:
        """Fire also accepts '--exit_zero' (underscore) as an alias for '--exit-zero'."""
        pkg = tmp_path / "badpkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(_MYPKG_INIT_INVALID)
        result = _run_cli("check", str(pkg), "--exit_zero", cwd=tmp_path)
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
        result = _run_cli("check", str(pkg), "--bogusflag", env=_cli_env(COLUMNS="200"), cwd=tmp_path)
        assert result.returncode != 0
        assert "Could not consume arg" in result.stderr + result.stdout

    def test_expiry_misspelled_version_flag_exits_nonzero(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <path> --verison 9.0' (typo) exits non-zero instead of dropping the value.

        A user pinning the comparison version with a typo'd flag must get a hard error; silently dropping
        the flag would run the expiry gate against an auto-detected (wrong) version — false pass or false
        fail with zero diagnostics.

        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli("expiry", str(pkg), "--verison", "9.0", env=_cli_env(COLUMNS="200"), cwd=tmp_path)
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
        result = _run_cli("expiry", "mypkg", env=env, cwd=decoy)
        assert "9.9.9" not in result.stdout + result.stderr
        assert result.returncode == 0
