"""Deprecated redirect module (Mode 2 fixture).

Attribute accesses on this module are forwarded to ``new_utils`` after emitting a
:class:`FutureWarning`.
"""

import deprecate
from tests.collection_modules import new_utils

deprecate.deprecated_module(__name__, target=new_utils, deprecated_in="1.0", remove_in="2.0")
