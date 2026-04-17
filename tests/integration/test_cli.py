"""Integration tests for the CLI — real subprocess invocations only."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SRC_DIR = str(Path(__file__).resolve().parent.parent.parent / "src")
_JSONARGPARSE_AVAILABLE = importlib.util.find_spec("jsonargparse") is not None


def _cli_env(**extra: str) -> dict[str, str]:
    """Build env dict with PYTHONPATH pointing at src/ so subprocess can find deprecate."""
    existing_pythonpath = os.environ.get("PYTHONPATH")
    pythonpath = f"{_SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else _SRC_DIR
    return {**os.environ, "PYTHONPATH": pythonpath, **extra}


@pytest.mark.skipif(not _JSONARGPARSE_AVAILABLE, reason="requires jsonargparse (pip install 'pyDeprecate[cli]')")
class TestCliInvocation:
    """Tests for real CLI invocations via subprocess."""

    def test_no_args(self, tmp_path: Path) -> None:
        """Test real CLI invocation via subprocess with no arguments."""
        pkg_dir = tmp_path / "mypkg"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").touch()
        (pkg_dir / "module_a.py").touch()

        result = subprocess.run(
            [sys.executable, "-m", "deprecate"],
            capture_output=True,
            text=True,
            env=_cli_env(),
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert "Scanning path" in result.stdout

    def test_help(self) -> None:
        """Test real CLI invocation prints help text."""
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "--help"],
            capture_output=True,
            text=True,
            env=_cli_env(),
        )
        assert result.returncode == 0
        assert "path" in result.stdout.lower()

    def test_nonexistent_module(self) -> None:
        """Test real CLI invocation with a module that doesn't exist."""
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "nonexistent_module_xyz"],
            capture_output=True,
            text=True,
            env=_cli_env(COLUMNS="200"),
        )
        assert result.returncode != 0
