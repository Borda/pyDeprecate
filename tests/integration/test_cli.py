"""Integration tests for the CLI — real subprocess invocations only."""

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


def _cli_env(**extra: str) -> dict[str, str]:
    """Build env dict with PYTHONPATH pointing at src/ so subprocess can find deprecate."""
    existing_pythonpath = os.environ.get("PYTHONPATH")
    pythonpath = f"{_SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else _SRC_DIR
    return {**os.environ, "PYTHONPATH": pythonpath, **extra}


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
        result = subprocess.run([sys.executable, "-m", "deprecate"], capture_output=True, text=True, env=_cli_env())
        assert result.returncode == 0
        assert "check" in (result.stdout + result.stderr).lower()

    def test_help(self) -> None:
        """CLI --help exits 0 and lists subcommands."""
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "--help"], capture_output=True, text=True, env=_cli_env()
        )
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "check" in combined.lower()

    def test_nonexistent_module(self) -> None:
        """CLI with a module that doesn't exist exits non-zero."""
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "check", "nonexistent_module_xyz"],
            capture_output=True,
            text=True,
            env=_cli_env(COLUMNS="200"),
        )
        assert result.returncode != 0


class TestCliSubcommands:
    """Integration tests for the four CLI subcommands via subprocess."""

    def test_check_subcommand_explicit(self, tmp_path: Path) -> None:
        """'pydeprecate check <path>' scans and exits 0 for a clean package."""
        pkg = _make_pkg(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "check", str(pkg)],
            capture_output=True,
            text=True,
            env=_cli_env(),
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert "Scanning:" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_expiry_subcommand_no_expired(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <path> --version 1.0' exits 0 when nothing is expired."""
        pkg = _make_pkg(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "expiry", str(pkg), "--version", "1.0"],
            capture_output=True,
            text=True,
            env=_cli_env(),
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert "No expired" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_expiry_subcommand_expired(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <path> --version 9.0' exits 1 when wrapper is past remove_in."""
        pkg = _make_pkg(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "expiry", str(pkg), "--version", "9.0"],
            capture_output=True,
            text=True,
            env=_cli_env(),
            cwd=tmp_path,
        )
        assert result.returncode == 1

    def test_chains_subcommand_no_chains(self, tmp_path: Path) -> None:
        """'pydeprecate chains <path>' exits 0 for a package with no deprecation chains."""
        pkg = _make_pkg(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "chains", str(pkg)],
            capture_output=True,
            text=True,
            env=_cli_env(),
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert "No deprecation chains" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_all_subcommand_clean(self, tmp_path: Path) -> None:
        """'pydeprecate all <path> --version 1.0' exits 0 when all checks pass."""
        pkg = _make_pkg(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "all", str(pkg), "--version", "1.0"],
            capture_output=True,
            text=True,
            env=_cli_env(),
            cwd=tmp_path,
        )
        assert result.returncode == 0

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_status_subcommand_exits_0(self, tmp_path: Path) -> None:
        """'pydeprecate status <path> --version 1.0' exits 0 and prints a markdown table."""
        pkg = _make_pkg(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "status", str(pkg), "--version", "1.0"],
            capture_output=True,
            text=True,
            env=_cli_env(),
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert "Original API" in result.stdout

    def test_all_plain_directory_exits_0(self, tmp_path: Path) -> None:
        """'pydeprecate all <plaindir>' exits 0 when every check passes on a plain dir without __init__.py.

        cmd_check already scans plain directories; cmd_all appends a status table afterwards. Resolving the
        module name for that advisory table must not turn a clean run into exit 1 on a directory that has no
        importable package — the table is a display artifact, not a pass/fail gate.
        """
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "mod.py").write_text("x = 1\n")
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "all", str(plain)],
            capture_output=True,
            text=True,
            env=_cli_env(),
            cwd=tmp_path,
        )
        assert result.returncode == 0

    def test_status_plain_directory_exits_0(self, tmp_path: Path) -> None:
        """'pydeprecate status <plaindir>' exits 0 on a plain dir; status generation is never a pass/fail gate."""
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "mod.py").write_text("x = 1\n")
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "status", str(plain)],
            capture_output=True,
            text=True,
            env=_cli_env(),
            cwd=tmp_path,
        )
        assert result.returncode == 0

    def test_help_lists_subcommands(self) -> None:
        """'pydeprecate --help' output includes the five subcommand names."""
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "--help"], capture_output=True, text=True, env=_cli_env()
        )
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        for name in ("check", "expiry", "chains", "all", "status"):
            assert name in combined, f"subcommand '{name}' missing from --help output"

    def test_subcommand_help(self) -> None:
        """'pydeprecate expiry --help' shows expiry-specific options."""
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "expiry", "--help"], capture_output=True, text=True, env=_cli_env()
        )
        assert result.returncode == 0
        assert "version" in (result.stdout + result.stderr).lower()

    def test_check_no_recursive_flag(self, tmp_path: Path) -> None:
        """'pydeprecate check <path> --norecursive' is accepted and exits 0."""
        pkg = _make_pkg(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "check", str(pkg), "--norecursive"],
            capture_output=True,
            text=True,
            env=_cli_env(),
            cwd=tmp_path,
        )
        assert result.returncode == 0

    def test_check_exit_zero_dash_form(self, tmp_path: Path) -> None:
        """'--exit-zero' (dash form) forces exit 0 even when invalid args are found."""
        pkg = tmp_path / "badpkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(_MYPKG_INIT_INVALID)
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "check", str(pkg), "--exit-zero"],
            capture_output=True,
            text=True,
            env=_cli_env(),
            cwd=tmp_path,
        )
        assert result.returncode == 0

    def test_check_exit_zero_underscore_form(self, tmp_path: Path) -> None:
        """Fire also accepts '--exit_zero' (underscore) as an alias for '--exit-zero'."""
        pkg = tmp_path / "badpkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(_MYPKG_INIT_INVALID)
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "check", str(pkg), "--exit_zero"],
            capture_output=True,
            text=True,
            env=_cli_env(),
            cwd=tmp_path,
        )
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
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "check", str(pkg), "--bogusflag"],
            capture_output=True,
            text=True,
            env=_cli_env(COLUMNS="200"),
            cwd=tmp_path,
        )
        assert result.returncode != 0
        assert "Could not consume arg" in result.stderr + result.stdout

    def test_expiry_misspelled_version_flag_exits_nonzero(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <path> --verison 9.0' (typo) exits non-zero instead of dropping the value.

        A user pinning the comparison version with a typo'd flag must get a hard error; silently dropping
        the flag would run the expiry gate against an auto-detected (wrong) version — false pass or false
        fail with zero diagnostics.

        """
        pkg = _make_pkg(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "expiry", str(pkg), "--verison", "9.0"],
            capture_output=True,
            text=True,
            env=_cli_env(COLUMNS="200"),
            cwd=tmp_path,
        )
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
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "expiry", "mypkg"],
            capture_output=True,
            text=True,
            env=env,
            cwd=decoy,
        )
        assert "9.9.9" not in result.stdout + result.stderr
        assert result.returncode == 0
