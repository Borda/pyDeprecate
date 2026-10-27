"""Regression tests for packaging consistency between ``pyproject.toml`` and the ``setup.py`` shim."""

import re
import runpy
import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from deprecate import __about__

if sys.version_info >= (3, 11):
    import tomllib
else:  # the `audit` extra installs the backport on Python 3.9-3.10
    import tomli as tomllib

_ROOT = Path(__file__).parent.parent.parent
_README = _ROOT / "README.md"
_SETUP = _ROOT / "setup.py"
_PYPROJECT = _ROOT / "pyproject.toml"
#: The only arguments ``setup.py`` may pass: the README rewrite that a static ``readme`` field cannot express.
_SHIM_KWARGS = {"long_description", "long_description_content_type"}


def _setup_kwargs() -> dict[str, object]:
    """Run ``setup.py`` against a stub ``setuptools`` and return the keyword arguments it passes to ``setup()``."""
    builder = Mock()
    with patch.dict(sys.modules, {"setuptools": builder}):
        runpy.run_path(str(_SETUP))
    builder.setup.assert_called_once()
    return dict(builder.setup.call_args.kwargs)


def _project_table() -> dict[str, Any]:
    """Return the ``[project]`` table of ``pyproject.toml``."""
    return tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]


def test_setup_shim_sets_only_the_long_description() -> None:
    """``setup.py`` passes nothing but the long description, which ``[project]`` leaves dynamic.

    All other metadata moved to ``pyproject.toml``. A field set in both places is resolved by setuptools in a way a
    reader of either file does not see, and a field added back to ``setup.py`` would silently fork the metadata.

    """
    project = _project_table()
    assert set(_setup_kwargs()) == _SHIM_KWARGS
    assert "readme" not in project
    assert "readme" in project["dynamic"]


def test_project_description_matches_about() -> None:
    """``[project].description`` is the same one-line summary as ``deprecate.__about__.__docs__``.

    The summary is static in ``pyproject.toml`` and also exposed by the package at runtime; the two copies must not
    drift apart when one of them is edited.

    """
    assert _project_table()["description"] == __about__.__docs__


def test_long_description_resolves_brand_images() -> None:
    """Resolve both banner variants in the package description without rewriting unrelated links.

    PyPI renders the packaged README without the repository's relative URL base. The dark source and light fallback
    must therefore point to the same release as the package while preserving the author's external profile link.

    """
    builder = Mock()
    with patch.dict(sys.modules, {"setuptools": builder}):
        namespace = runpy.run_path(str(_SETUP))

    builder.setup.assert_called_once()
    description = builder.setup.call_args.kwargs["long_description"]
    about = namespace["ABOUT"]
    brand_url = f"{about.__source_code__}/raw/v{about.__version__}/docs/assets/brand"
    assert f'srcset="{brand_url}/banner-dark.svg"' in description
    assert f'src="{brand_url}/banner-light.svg"' in description
    assert '"docs/assets/brand/' not in description
    assert "[Jiri Borovec](https://github.com/Borda)" in description


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


def test_setup_images_present_in_readme() -> None:
    """Every entry in _README_IMAGE_FILES must actually appear in README.

    Catches stale entries left in _README_IMAGE_FILES after a screenshot is renamed or removed.

    """
    readme_images = _parse_readme_image_files()
    setup_images = _parse_setup_image_files()
    stale = setup_images - readme_images
    assert not stale, (
        f"_README_IMAGE_FILES in setup.py contains {stale!r} "
        f"but those filenames do not appear in README.md under docs/assets/images/. "
        f"Remove them from _README_IMAGE_FILES to avoid broken PyPI image links."
    )
