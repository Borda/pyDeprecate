#!/usr/bin/env python

import os

# Always prefer setuptools over distutils
from setuptools import find_packages, setup

import deprecate

# https://packaging.python.org/guides/single-sourcing-package-version/
# http://blog.ionelmc.ro/2014/05/25/python-packaging/

_PATH_ROOT = os.path.dirname(__file__)


def _load_long_description() -> str:
    url = os.path.join(deprecate.__homepage__, 'raw', deprecate.__version__, 'docs')
    text = open('README.md', encoding='utf-8').read()
    # replace relative repository path to absolute link to the release
    text = text.replace('](docs', f']({url}')
    # SVG images are not readable on PyPI, so replace them  with PNG
    text = text.replace('.svg', '.png')
    return text


# https://packaging.python.org/discussions/install-requires-vs-requirements /
# keep the meta-data here for simplicity in reading this file... it's not obvious
# what happens and to non-engineers they won't know to look in init ...
# the goal of the project is simplicity for researchers, don't want to add too much
# engineer specific practices
setup(
    name='deprecate',
    version=deprecate.__version__,
    description=deprecate.__docs__,
    author=deprecate.__author__,
    author_email=deprecate.__author_email__,
    url=deprecate.__homepage__,
    license=deprecate.__license__,
    packages=find_packages(exclude=['tests', 'docs']),
    long_description=_load_long_description(),
    long_description_content_type='text/markdown',
    include_package_data=True,
    zip_safe=False,
    keywords=['python', 'development', 'deprecation'],
    python_requires='>=3.6',
    setup_requires=[],
    install_requires=[],
    project_urls={
        "Source Code": deprecate.__homepage__,
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
    ],
)
