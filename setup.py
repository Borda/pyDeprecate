#!/usr/bin/env python
"""
Copyright (C) 2020-2021 Jiri Borovec <...>
"""
import os

# Always prefer setuptools over distutils
from setuptools import find_packages, setup

import deprecate

# https://packaging.python.org/guides/single-sourcing-package-version/
# http://blog.ionelmc.ro/2014/05/25/python-packaging/

_PATH_ROOT = os.path.dirname(__file__)


def _load_long_description(path_dir: str, version: str) -> str:
    path_readme = os.path.join(path_dir, "README.md")
    text = open(path_readme, encoding="utf-8").read()
    # codecov badge
    text = text.replace('/branch/main/graph/badge.svg', f'/release/{version}/graph/badge.svg')
    # replace github badges for release ones
    text = text.replace('badge.svg?branch=main&event=push', f'badge.svg?tag={version}')
    return text


# https://packaging.python.org/discussions/install-requires-vs-requirements /
# keep the meta-data here for simplicity in reading this file... it's not obvious
# what happens and to non-engineers they won't know to look in init ...
# the goal of the project is simplicity for researchers, don't want to add too much
# engineer specific practices
setup(
    name='pyDeprecate',
    version=deprecate.__version__,
    description=deprecate.__docs__,
    author=deprecate.__author__,
    author_email=deprecate.__author_email__,
    url=deprecate.__homepage__,
    license=deprecate.__license__,
    packages=find_packages(exclude=['tests', 'docs']),
    long_description=_load_long_description(_PATH_ROOT, version=deprecate.__version__),
    long_description_content_type='text/markdown',
    include_package_data=True,
    zip_safe=False,
    keywords=['python', 'development', 'deprecation'],
    python_requires='>=3.6',
    setup_requires=[],
    install_requires=[],
    project_urls={
        "Source Code": deprecate.__source_code__,
    },
    classifiers=[
        'Environment :: Console',
        'Natural Language :: English',
        # How mature is this project? Common values are
        #   3 - Alpha, 4 - Beta, 5 - Production/Stable
        'Development Status :: 3 - Alpha',
        # Indicate who your project is intended for
        'Intended Audience :: Developers',
        # Pick your license as you wish
        # 'License :: OSI Approved :: BSD License',
        'Operating System :: OS Independent',
        # Specify the Python versions you support here. In particular, ensure
        # that you indicate whether you support Python 2, Python 3 or both.
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
    ],
)
