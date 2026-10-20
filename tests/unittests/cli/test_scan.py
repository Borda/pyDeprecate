"""Unit tests for :mod:`deprecate._cli.scan`: resolving a path into deprecation wrappers."""

from pathlib import Path
from unittest.mock import patch

import pytest

from deprecate._cli.scan import _scan_path


class TestScanPath:
    """Tests for ``_scan_path`` resolving a package from a project-root directory."""

    def test_multiple_child_packages_raise(self, tmp_path: Path) -> None:
        """A directory holding two packages raises ``ValueError`` naming both instead of scanning neither.

        A flat-layout root with ``mypkg/`` and ``tests/`` used to fall through to the top-level-module scan, which
        imports only ``setup.py``-style files, so ``pydeprecate check`` reported a clean run on an unscanned project.

        """
        for name in ("mypkg", "tests"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "__init__.py").write_text("")
        with pytest.raises(ValueError, match=r"contains multiple packages \(mypkg, tests\)"):
            _scan_path(str(tmp_path))

    def test_directory_without_packages_scans_top_level_modules(self, tmp_path: Path) -> None:
        """A plain directory with no package keeps the top-level-module scan and finds no wrappers in a clean one.

        Only an ambiguous choice between several packages is an error; a scripts directory has nothing to choose.

        """
        (tmp_path / "plain_script.py").write_text("VALUE = 1\n")
        with patch("deprecate._cli.scan._scan_directory", return_value=[]) as mock_scan:
            assert _scan_path(str(tmp_path)) == []
        mock_scan.assert_called_once()
