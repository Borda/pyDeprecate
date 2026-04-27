"""Regression tests for setup.py packaging consistency."""

import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
_README = _ROOT / "README.md"
_SETUP = _ROOT / "setup.py"


def _parse_readme_image_files() -> set[str]:
    """Extract all docs/assets/images/*.png basenames from README.md."""
    text = _README.read_text(encoding="utf-8")
    return {Path(m).name for m in re.findall(r"docs/assets/images/([^\s)\"]+)", text)}


def _parse_setup_image_files() -> set[str]:
    """Extract _README_IMAGE_FILES tuple values from setup.py."""
    text = _SETUP.read_text(encoding="utf-8")
    match = re.search(r"_README_IMAGE_FILES\s*=\s*\(([^)]+)\)", text)
    assert match, "_README_IMAGE_FILES tuple not found in setup.py"
    return {s.strip().strip("\"'") for s in match.group(1).split(",") if s.strip()}


def test_readme_images_covered_by_setup() -> None:
    """Every docs/assets/images/ reference in README must be in _README_IMAGE_FILES.

    Prevents silent broken PyPI long-description image links when new screenshots are added.
    """
    readme_images = _parse_readme_image_files()
    setup_images = _parse_setup_image_files()
    missing = readme_images - setup_images
    assert not missing, (
        f"README references {missing!r} under docs/assets/images/ "
        f"but they are not in _README_IMAGE_FILES in setup.py. "
        f"Add them to _README_IMAGE_FILES to ensure PyPI renders them correctly."
    )
