"""Audit gaps as data: the parts of an audit that did not run, kept apart from how the CLI prints them.

The static source reader collects :class:`_FoundGap` records in the order it meets them and returns them with its rows;
:mod:`deprecate._cli.report` prints each one as a ``pydeprecate gap:`` line and records it for ``--report-json``, adding
the hint and the scan path. :func:`_nested_files_gap` holds the nested-files check shared by both scan modes, so its
message exists once. Nothing here prints, records a run or imports from :mod:`deprecate._cli`.

"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class _GapKind(str, Enum):
    """A part of the audit that did not run although the scan itself passed.

    Each gap is printed as an unwrapped stderr line ``pydeprecate gap: <kind>: <detail>`` — plain ``print``, independent
    of the warnings filters the audited package may install — and recorded for ``--report-json`` with its hint from
    :data:`_GAP_HINTS`.

    """

    #: A module the scan could not import (or whose module-level code exited), so none of its wrappers were audited;
    #: also ``<package>.*`` when a recursive walk of a package stopped early, leaving its submodules unaudited.
    MODULE_NOT_AUDITED = "module not audited"
    #: The package version could not be resolved, so removal deadlines were not checked.
    VERSION_UNRESOLVED = "version not resolved"
    #: The auto-detected package version is not a valid PEP 440 version, so removal deadlines were not checked.
    VERSION_INVALID = "version not PEP 440"
    #: A wrapper's ``remove_in`` is not a valid PEP 440 version, so its removal deadline (``expiry``) or its grace
    #: window (``policy``'s ``min-grace``) was not checked; ``all`` names each wrapper once.
    REMOVE_IN_INVALID = "remove_in not PEP 440"
    #: A wrapper's ``deprecated_in`` is not a valid PEP 440 version, so its grace window (``min-grace``) was not
    #: checked.
    DEPRECATED_IN_INVALID = "deprecated_in not PEP 440"
    #: A forward epoch change makes the grace window unmeasurable.
    EPOCH_CHANGE = "epoch change"
    #: The ``packaging`` library is missing, so version-based checks were skipped.
    PACKAGING_MISSING = "packaging missing"
    #: A plain-directory scan left the Python files in its subdirectories out.
    NESTED_FILES = "nested files not audited"
    #: The scanned package's import name was already taken by a copy from another directory, so that copy was audited.
    OTHER_COPY = "other copy audited"
    #: ``--static`` could not read a wrapper, or one of the settings a rule needs, from the source alone.
    STATIC_UNRESOLVED = "not statically resolvable"


@dataclass(frozen=True)
class _FoundGap:
    """One discovered audit omission, independent of a CLI run or its current scan path."""

    kind: _GapKind
    detail: str
    subject: Optional[tuple[str, str]] = None
    rules: Optional[frozenset[str]] = None


def _nested_files_gap(directory: Path) -> Optional[_FoundGap]:
    """Return a gap for Python files below a plain directory that its top-level scan leaves unaudited."""
    if any(fpy.is_file() and fpy.parent != directory for fpy in directory.rglob("*.py")):
        return _FoundGap(
            _GapKind.NESTED_FILES,
            "Skipping nested Python files in plain directory scan. Use an importable package layout with '__init__.py'"
            " files, or scan an importable module/package path instead.",
        )
    return None
