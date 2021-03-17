import os

__version__ = "0.1.0dev"
__docs__ = "Deprecation tooling"
__author__ = "Jiri Borovec"
__author_email__ = "jiri.borovec@fel.cvut.cz"
__homepage__ = "https://github.com/Borda/pyDeprecate"
__license__ = 'Apache-2.0'

_PATH_PACKAGE = os.path.realpath(os.path.dirname(__file__))
_PATH_PROJECT = os.path.dirname(_PATH_PACKAGE)

from deprecate.deprecation import deprecated  # noqa: F401 E402
