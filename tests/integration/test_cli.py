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
