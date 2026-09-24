"""PEP 440 version parsing and current-version detection shared by the escalation and audit machinery.

:func:`_normalize_version_string` and :func:`_parse_version` originated in ``deprecate.audit._lifecycle`` and moved here
so the core (non-audit) warning-escalation path (:mod:`deprecate.messaging`, :mod:`deprecate.routine`,
:mod:`deprecate.proxy`, :mod:`deprecate.module`) can reuse the exact same PEP 440 comparison semantics without importing
the ``audit`` subpackage, which itself imports from the core and would create a cycle. ``deprecate.audit._lifecycle``
re-imports both names from here, so every existing call site and test that reaches them via
``deprecate.audit._lifecycle`` keeps working unchanged.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import functools
import importlib
import importlib.metadata
import re
import warnings
from contextlib import suppress
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from packaging.version import Version


def _normalize_version_string(version: str) -> str:
    r"""Normalize non-standard version strings before PEP 440 parsing.

    Newer ``packaging`` (>=22) is strict PEP 440 and rejects real-world strings that omit trailing digits
    on pre/post/dev release labels (e.g. ``"1.8.0.dev"``, ``"1.8.0dev"``, ``"1.8.0.post"``). This helper
    performs the minimum normalization needed to make such strings parseable, then defers everything else
    (label aliasing like ``alpha`` -> ``a``, case folding, separator handling) to ``packaging.Version``.

    The transformation is conservative:

    1. Strip a single leading ``v`` or ``V`` prefix (left-anchored, so only one leading ``v`` is removed).
    2. Append ``0`` to bare pre/post/dev labels that lack a trailing digit. Labels recognized:
       ``dev``, ``rc``, ``a``, ``b``, ``c``, ``alpha``, ``beta``, ``preview``, ``post``.

    The label normalization runs only over the *public* part of the version — any PEP 440 local segment
    (everything after ``+``) is split off first and re-attached verbatim, so a legitimate local like
    ``1.2.3+cuda`` is never mangled into ``1.2.3+cuda0`` by the trailing-``a`` label rule.

    No other transformations are applied — case, separators, and label aliases pass through unchanged
    so ``packaging.Version`` can apply its own canonicalization.

    Args:
        version: Raw version string, possibly missing trailing digits on labels.

    Returns:
        Normalized version string ready to be passed to ``packaging.version.Version``.

    Examples:
        >>> _normalize_version_string("1.8.0.dev")
        '1.8.0.dev0'
        >>> _normalize_version_string("1.8.0dev")
        '1.8.0dev0'
        >>> _normalize_version_string("1.8.0.post")
        '1.8.0.post0'
        >>> _normalize_version_string("v1.2.3")
        '1.2.3'
        >>> _normalize_version_string("1.8.0.RC1")
        '1.8.0.RC1'
        >>> _normalize_version_string("1.2.3")
        '1.2.3'
        >>> _normalize_version_string("1.2.3+cuda")
        '1.2.3+cuda'
        >>> _normalize_version_string("v1.8.0.dev+local.a")
        '1.8.0.dev0+local.a'

    """
    # Split off any PEP 440 local segment (after ``+``) so the label regex never touches it; labels like
    # ``post``/``dev`` never appear in a local segment, and running the rule over it mangles legit locals.
    public, plus, local = version.partition("+")
    # Strip a single left-anchored leading ``v``/``V`` (``lstrip("vV")`` would strip *all* leading v's).
    normalized = re.sub(r"^[vV]", "", public)
    # Append ``0`` to bare pre/post/dev labels with no trailing digit. The ordering of the alternatives
    # matters: longer labels (``alpha``, ``beta``, ``preview``) must come before their single-letter
    # forms (``a``, ``b``) so the regex prefers the longer match.
    # Use a negative lookahead for ``[0-9]`` to detect "no trailing digit"; ``(?=$|[^A-Za-z0-9])``
    # ensures the label is a whole token (e.g. ``dev`` but not ``develop``).
    pattern = re.compile(
        r"(?P<sep>\.?)(?P<label>alpha|beta|preview|post|dev|rc|a|b|c)(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    normalized = pattern.sub(lambda m: f"{m.group('sep')}{m.group('label')}0", normalized)
    return f"{normalized}{plus}{local}"


def _parse_version(version_string: str) -> "Version":
    """Parse a version string using the packaging library (PEP 440 compliant).

    This function requires the 'packaging' library, which is available as an optional dependency via the 'audit'
    extra: ``pip install pyDeprecate[audit]``

    The packaging library provides robust PEP 440 version parsing and comparison, supporting pre-releases
    (alpha/beta/rc), stable releases, post-releases, and development releases with proper ordering.

    Inputs are first passed through :func:`_normalize_version_string`, which appends ``0`` to bare
    pre/post/dev labels (e.g. ``"1.8.0.dev"`` becomes ``"1.8.0.dev0"``) so non-canonical-but-common
    strings parse successfully under strict ``packaging`` (>=22).

    Args:
        version_string: Version string (e.g., "1.2.3", "2.0", "1.5.0a1", "1.5.0rc1", "1.5.0.post1").

    Returns:
        packaging.version.Version object that supports comparison operations.

    Raises:
        ImportError: If the packaging library is not installed.
        ValueError: If the version string is not valid per PEP 440
            (wraps ``packaging.version.InvalidVersion`` with additional context).

    Example:
        >>> import importlib; importlib.import_module("packaging")  # doctest: +ELLIPSIS
        <module 'packaging' ...>
        >>> v1 = _parse_version("1.2.3")
        >>> v2 = _parse_version("2.0")
        >>> v1 < v2
        True
        >>> _parse_version("1.5.0a1") < _parse_version("1.5.0")
        True
        >>> _parse_version("1.8.0.dev") < _parse_version("1.8.0")
        True
        >>> _parse_version("1.8.0.post") > _parse_version("1.8.0")
        True

    !!! note
        Install the audit extra to use version comparison features:
        ``pip install pyDeprecate[audit]``

    """
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError as err:
        raise ImportError(
            "Version comparison requires the 'packaging' library. Install with: pip install pyDeprecate[audit]"
        ) from err

    try:
        return Version(_normalize_version_string(version_string))
    except InvalidVersion as err:
        raise ValueError(
            f"Failed to parse version '{version_string}'. Expected PEP 440 format "
            f"(e.g., '1.2.3', '2.0', '1.5.0a1'). Error: {err}"
        ) from err


def _detect_current_version(module_name: str) -> Optional[str]:
    """Best-effort lookup of the installed version of *module_name*'s top-level package.

    Mirrors ``deprecate.audit._lifecycle._get_package_version``'s two-fallback strategy
    (``importlib.metadata`` first, then the module's own ``__version__``), but never raises — callers
    on the opt-in escalation path (:func:`_resolve_escalation_note`) must degrade silently to a
    phase-less message rather than break normal decoration when the version cannot be determined.

    The lookup is cached per top-level package name (see :func:`_cached_package_version`): the
    installed version of a package cannot change mid-process, so repeated ``escalate=True``
    decorations across many symbols of the same package must not each pay an uncached
    ``importlib.metadata`` distribution scan.

    Args:
        module_name: Dotted module name (e.g. ``"mypackage.sub"``); only its first component is used.

    Returns:
        The detected version string, or ``None`` when it cannot be determined by either strategy.

    Examples:
        >>> _detect_current_version("deprecate") is not None
        True
        >>> _detect_current_version("no_such_package_xyz") is None
        True

    """
    return _cached_package_version(module_name.split(".")[0])


@functools.cache
def _cached_package_version(package_name: str) -> Optional[str]:
    """Cached ``importlib.metadata`` / ``__version__`` lookup for one top-level package name."""
    with suppress(Exception):
        return importlib.metadata.version(package_name)
    with suppress(Exception):
        module = importlib.import_module(package_name)
        version = getattr(module, "__version__", None)
        if isinstance(version, str):
            return version
    return None


#: Opening text of the past-removal escalation tier.  Emitters match against it (via
#: :func:`_is_past_removal_note`) to decide whether the base message's "will be removed" clause should
#: switch to past tense, so the tier is recognised from the note alone — no second config field, and no
#: re-deriving version state at emission time.  The note itself is built from this constant, so the two
#: cannot drift apart.
_PAST_REMOVAL_NOTE_PREFIX = " Past its planned removal in v"


def _is_past_removal_note(escalation_note: str) -> bool:
    """Return whether *escalation_note* is the past-removal tier (as opposed to ``""`` or "removal imminent").

    Examples:
        >>> _is_past_removal_note("")
        False
        >>> _is_past_removal_note(_compute_escalation_note("2.0", "1.0", "2.0"))
        True
        >>> _is_past_removal_note(_compute_escalation_note("2.0rc1", "1.0", "2.0"))
        False

    """
    return escalation_note.startswith(_PAST_REMOVAL_NOTE_PREFIX)


def _compute_escalation_note(current_version: Optional[str], deprecated_in: str, remove_in: str) -> str:
    """Return a message suffix that ramps urgency as *current_version* nears *remove_in*.

    Purely additive text appended to the existing warning message — never changes the warning
    category (always floored at the configured ``stream``, per the Ft-2 design decision that a
    Pending→Deprecation→Future category ladder would *downgrade* visibility below today's default
    ``FutureWarning`` for most of the deprecation window).

    Returns ``""`` (no escalation) when:

    - ``current_version`` or ``remove_in`` is missing (nothing to compare against), or
    - ``remove_in`` fails to parse (an unparsable *typo* should not crash decoration), or
    - the current version is comfortably mid-window (the base message already states the version
      window; no ramp needed yet).

    Args:
        current_version: Detected current package version, or ``None`` when undetectable.
        deprecated_in: The wrapper's configured ``deprecated_in`` (unused in the current two-phase
            ramp but accepted for symmetry with the audit report's richer status classification and
            forward compatibility with a future ``pending`` phase).
        remove_in: The wrapper's configured ``remove_in`` version.

    Returns:
        A leading-space-prefixed suffix ready to append to a rendered warning message, or ``""``.

    Examples:
        >>> _compute_escalation_note("1.5", "1.0", "2.0")
        ''
        >>> _compute_escalation_note("2.0", "1.0", "2.0")
        ' Past its planned removal in v2.0 — check the upstream release notes; it may be dropped in any release.'
        >>> _compute_escalation_note("2.0rc1", "1.0", "2.0")
        ' Removal imminent in v2.0 — this is the last chance to migrate before it ships.'
        >>> _compute_escalation_note(None, "1.0", "2.0")
        ''
        >>> _compute_escalation_note("1.5", "1.0", "")
        ''

    """
    del deprecated_in  # accepted for API symmetry/forward-compat; not used by the current two-phase ramp
    if not remove_in or not current_version:
        return ""
    try:
        current = _parse_version(current_version)
        remove_ver = _parse_version(remove_in)
    except (ImportError, ValueError):
        return ""
    if current >= remove_ver:
        # Deliberately neutral about cause: this branch can only be reached when the symbol still exists
        # past the version its own package promised to delete it in, which is an upstream schedule slip,
        # not evidence the caller is late (had the removal actually happened, the call would raise instead
        # of warning). Point at the release notes rather than blaming whoever triggered the warning.
        return (
            f"{_PAST_REMOVAL_NOTE_PREFIX}{remove_in} — check the upstream release notes;"
            " it may be dropped in any release."
        )
    if current.is_prerelease:
        same_base = False
        with suppress(Exception):
            _version_type = type(current)
            same_base = _version_type(current.base_version) == _version_type(remove_ver.base_version)
        if same_base:
            return f" Removal imminent in v{remove_in} — this is the last chance to migrate before it ships."
    return ""


def _resolve_escalation_note(
    escalate: bool,
    module_name: str,
    deprecated_in: str,
    remove_in: str,
    *,
    stacklevel: int,
) -> str:
    """Resolve the decoration-time escalation note for an ``escalate=True`` wrapper.

    Computed once at decoration time (not per-call) since the installed package version does not
    change during a process's lifetime — mirrors how ``__deprecated__``'s static message is rendered
    once by :func:`~deprecate.messaging._render_static_deprecation_message`.

    Args:
        escalate: The wrapper's ``escalate`` argument. ``False`` short-circuits to ``""`` with no
            version detection or ``packaging`` probe at all.
        module_name: Dotted module name of the deprecated source, used to detect the current
            installed version of its top-level package.
        deprecated_in: The wrapper's configured ``deprecated_in``.
        remove_in: The wrapper's configured ``remove_in``.
        stacklevel: Forwarded to :func:`warnings.warn` for the missing-``packaging`` notice so it
            points at the user's decoration site.

    Returns:
        The computed escalation note (possibly ``""``).

    Warns:
        UserWarning: When ``escalate=True`` but the ``packaging`` library is not installed —
            escalation degrades to a phase-less message (the base template still renders normally).

    """
    if not escalate:
        return ""
    try:
        import packaging  # noqa: F401
    except ImportError:
        warnings.warn(
            "`escalate=True` requires the `packaging` library (install `pyDeprecate[audit]`);"
            " message escalation is disabled for this deprecation.",
            UserWarning,
            stacklevel=stacklevel,
        )
        return ""
    current_version = _detect_current_version(module_name)
    return _compute_escalation_note(current_version, deprecated_in, remove_in)
