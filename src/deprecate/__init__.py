"""Deprecation package."""

import os

_PATH_PACKAGE = os.path.realpath(os.path.dirname(__file__))
_PATH_PROJECT = os.path.dirname(_PATH_PACKAGE)

from deprecate.deprecation import deprecated  # noqa: E402, F401
from deprecate.utils import void  # noqa: E402, F401
