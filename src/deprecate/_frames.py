"""Caller-frame walk that points every pyDeprecate warning at user code.

Every warning pyDeprecate raises — deprecation notices at call time and configuration notices at decoration time —
must report the first frame outside the ``deprecate`` package, whatever internal path led to the warn call (stacked
wrappers, the class front door, proxy dunders, descriptor accessors).  Counting those frames by hand at each site broke
whenever a helper was extracted; :func:`_caller_stacklevel` measures the distance at the moment of warning instead.

The module is private and dependency-free so every other ``deprecate`` module can import it without cycles.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import sys
from types import FrameType
from typing import Optional

#: Upper bound on the frames :func:`_caller_stacklevel` walks.  It sits far above any internal call chain and only
#: stops a runaway walk through a pathological internal recursion.
_MAX_FRAMES = 64

#: The package whose frames are skipped.  Matched by full name so a user package such as ``deprecate_x`` is external.
_PACKAGE = "deprecate"


def _is_internal(frame: FrameType) -> bool:
    """Tell whether ``frame`` runs code of the ``deprecate`` package.

    The module name comes from the frame's globals, not from ``__module__``: :func:`functools.wraps` copies the user's
    ``__module__`` onto every wrapper closure, while the closure's globals still belong to the ``deprecate`` module that
    defined it.

    Args:
        frame: The frame to classify.

    Returns:
        ``True`` when the frame's module is ``deprecate`` or a ``deprecate.`` submodule.

    Examples:
        >>> _is_internal(sys._getframe())
        True

    """
    name = frame.f_globals.get("__name__", "")
    # ``exec`` code may bind ``__name__`` to any object; a non-string name is never this package.
    return isinstance(name, str) and (name == _PACKAGE or name.startswith(_PACKAGE + "."))


def _is_import_bootstrap(frame: FrameType) -> bool:
    """Tell whether :func:`warnings.warn` skips ``frame`` when it counts ``stacklevel`` frames.

    ``warnings.warn`` treats a frame whose filename contains both ``importlib`` and ``_bootstrap`` as import machinery
    and steps over it without spending a level; the walk below must do the same to agree with it.

    Args:
        frame: The frame to classify.

    Returns:
        ``True`` for an ``importlib._bootstrap`` / ``importlib._bootstrap_external`` frame.

    """
    filename = frame.f_code.co_filename
    return "importlib" in filename and "_bootstrap" in filename


def _caller_stacklevel() -> int:
    """Return the ``stacklevel`` that makes a ``warnings.warn`` issued from the calling frame point at user code.

    The caller is the frame that is about to call :func:`warnings.warn` (or a ``stream``): level ``1`` is that frame.
    The walk steps outwards while the frame belongs to the ``deprecate`` package and returns the level of the first
    frame outside it, never more than :data:`_MAX_FRAMES`.  Import-machinery frames are stepped over without counting,
    exactly as ``warnings.warn`` does.  The walk runs only when a warning is actually emitted.

    The level is relative to the frame that calls the stream, so it is right for :func:`warnings.warn`, a C-level
    :func:`functools.partial` of it, or :func:`logging.warning`.  A pure-Python stream that itself calls
    ``warnings.warn(msg, stacklevel=stacklevel)`` adds its own frame and lands one frame short.

    Returns:
        A ``stacklevel`` value of at least ``1``.

    Examples:
        >>> 1 <= _caller_stacklevel() <= _MAX_FRAMES
        True

    """
    try:
        frame: Optional[FrameType] = sys._getframe(1)
    except ValueError:
        return 1
    level = 1
    while frame is not None and level < _MAX_FRAMES and _is_internal(frame):
        frame = frame.f_back
        while frame is not None and _is_import_bootstrap(frame):
            frame = frame.f_back
        level += 1
    return level
