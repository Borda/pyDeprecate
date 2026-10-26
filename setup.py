#!/usr/bin/env python
"""Copyright (C) 2020-2026 Jiri Borovec <...>.

All package metadata lives in ``pyproject.toml``. This shim only supplies the PyPI long description: the README with its
relative links pointed at the release tag, which a static ``readme`` field cannot express.

"""

import os
import re
from importlib.util import module_from_spec, spec_from_file_location
from typing import Any

from setuptools import setup

_PATH_ROOT = os.path.realpath(os.path.dirname(__file__))
_PATH_SOURCE = os.path.join(_PATH_ROOT, "src")
_README_IMAGE_FILES = ("path-illustration.webp", "demo-docs-mkdocs.png", "demo-docs-sphinx.png")


def _load_py_module(fname: str, pkg: str = "deprecate") -> Any:  # noqa: ANN401
    spec = spec_from_file_location(os.path.join(pkg, fname), os.path.join(_PATH_SOURCE, pkg, fname))
    py = module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(py)  # type: ignore[union-attr]
    return py


ABOUT = _load_py_module("__about__.py")


def _load_readme_description(path_dir: str, codebase_url: str, version: str) -> str:
    """Load readme as description.

    >>> _load_readme_description(_PATH_ROOT, "",  "")
    '<picture>...'

    """
    path_readme = os.path.join(path_dir, "README.md")
    with open(path_readme, encoding="utf-8") as fp:
        text = fp.read()

    github_source_url = f"{codebase_url}/raw/{version}"
    # replace relative repository path to absolute link to the release
    for image in _README_IMAGE_FILES:
        text = text.replace(
            f"(docs/assets/images/{image})",
            f"({github_source_url}/docs/assets/images/{image})",
        )
    # brand assets are referenced from HTML <picture> attributes, not markdown parentheses
    text = text.replace('"docs/assets/brand/', f'"{github_source_url}/docs/assets/brand/')
    text = text.replace(".github/", f"{github_source_url}/.github/")

    # codecov badge
    text = text.replace("/branch/main/graph/badge.svg", f"/release/{version}/graph/badge.svg")
    # replace github badges for release ones
    text = text.replace("badge.svg?branch=main&event=push", f"badge.svg?tag={version}")

    skip_begin = r"<!-- following section will be skipped from PyPI description -->"
    skip_end = r"<!-- end skipping PyPI description -->"
    # todo: wrap content as commented description
    return re.sub(rf"{skip_begin}.+?{skip_end}", "<!--  -->", text, flags=re.IGNORECASE + re.DOTALL)


setup(
    long_description=_load_readme_description(
        _PATH_ROOT, codebase_url=ABOUT.__source_code__, version=f"v{ABOUT.__version__}"
    ),
    long_description_content_type="text/markdown",
)
