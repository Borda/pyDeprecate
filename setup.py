#!/usr/bin/env python
"""Copyright (C) 2020-2023 Jiri Borovec <...>."""

import os
import re
from importlib.util import module_from_spec, spec_from_file_location

# Always prefer setuptools over distutils
from setuptools import find_packages, setup

_PATH_ROOT = os.path.realpath(os.path.dirname(__file__))
_PATH_SOURCE = os.path.join(_PATH_ROOT, "src")


def _load_py_module(fname: str, pkg: str = "deprecate"):
    spec = spec_from_file_location(os.path.join(pkg, fname), os.path.join(_PATH_SOURCE, pkg, fname))
    py = module_from_spec(spec)
    spec.loader.exec_module(py)
    return py


ABOUT = _load_py_module("__about__.py")


def _load_readme_description(path_dir: str, homepage: str, version: str) -> str:
    """Load readme as description.

    >>> _load_readme_description(_PATH_ROOT, "",  "")
    '# pyDeprecate...'

    """
    path_readme = os.path.join(path_dir, "README.md")
    with open(path_readme, encoding="utf-8") as fp:
        text = fp.read()

    # https://github.com/Borda/pyDeprecate/raw/main/docs/source/_static/images/...png
    github_source_url = os.path.join(homepage, "raw", version)
    # replace relative repository path to absolute link to the release
    #  do not replace all "docs" as in the readme we replace some other sources with particular path to docs
    text = text.replace(".assets/", f"{os.path.join(github_source_url, '.assets/')}")

    # codecov badge
    text = text.replace("/branch/main/graph/badge.svg", f"/release/{version}/graph/badge.svg")
    # replace github badges for release ones
    text = text.replace("badge.svg?branch=main&event=push", f"badge.svg?tag={version}")

    skip_begin = r"<!-- following section will be skipped from PyPI description -->"
    skip_end = r"<!-- end skipping PyPI description -->"
    # todo: wrap content as commented description
    return re.sub(rf"{skip_begin}.+?{skip_end}", "<!--  -->", text, flags=re.IGNORECASE + re.DOTALL)


# https://packaging.python.org/discussions/install-requires-vs-requirements /
# keep the meta-data here for simplicity in reading this file... it's not obvious
# what happens and to non-engineers they won't know to look in init ...
# the goal of the project is simplicity for researchers, don't want to add too much
# engineer specific practices
setup(
    name="pyDeprecate",
    version=ABOUT.__version__,
    description=ABOUT.__docs__,
    author=ABOUT.__author__,
    author_email=ABOUT.__author_email__,
    url=ABOUT.__homepage__,
    license=ABOUT.__license__,
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    long_description=_load_readme_description(_PATH_ROOT, homepage=ABOUT.__source_code__, version=ABOUT.__version__),
    long_description_content_type="text/markdown",
    include_package_data=True,
    zip_safe=False,
    keywords=["python", "development", "deprecation"],
    python_requires=">=3.9",
    setup_requires=[],
    install_requires=[],
    project_urls={"Source Code": ABOUT.__source_code__, "Home page": ABOUT.__homepage__},
    classifiers=[
        "Environment :: Console",
        "Natural Language :: English",
        # How mature is this project? Common values are
        #   3 - Alpha, 4 - Beta, 5 - Production/Stable
        "Development Status :: 5 - Production/Stable",
        # Indicate who your project is intended for
        "Intended Audience :: Developers",
        # Pick your license as you wish
        # 'License :: OSI Approved :: BSD License',
        "Operating System :: OS Independent",
        # Specify the Python versions you support here. In particular, ensure
        # that you indicate whether you support Python 2, Python 3 or both.
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Topic :: Software Development :: Libraries",
    ],
)
