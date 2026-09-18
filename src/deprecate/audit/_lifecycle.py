"""Version arithmetic and the expiry gate: PEP 440 parsing, package-version lookup, deprecation status.

Everything here compares a wrapper's ``deprecated_in``/``remove_in`` against a version: the parsing helpers shared
by the policy lint and the report, :class:`DeprecationStatus`, and
:func:`~deprecate.audit.validate_deprecation_expiry`.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import enum
import importlib
import importlib.metadata
import re
import types
import warnings
from collections.abc import Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

if TYPE_CHECKING:
    from packaging.version import Version

from deprecate.audit._scan import find_deprecation_wrappers
from deprecate.audit._wrappers import DeprecationWrapperInfo, _format_subject, validate_deprecation_wrapper


class DeprecationStatus(str, enum.Enum):
    """Lifecycle status labels used in the deprecation report's *Current Status* column.

    Each member's value is the full display string (emoji + text) rendered in the table.
    Using a ``str`` enum means members compare equal to their string values and can be
    returned wherever a plain string is expected.

    Members are *declared* from least to most urgent for readability, but this enum is **not orderable by
    urgency**: because each value starts with an emoji, the inherited ``str`` ordering operators (``<``, ``>``)
    compare Unicode codepoints of those emoji, not deprecation urgency. Do not rely on ``status_a > status_b``
    to mean "more urgent" — compare members explicitly (``status is DeprecationStatus.PAST_REMOVAL_DATE``).
    The ordering operators are overridden to raise ``TypeError`` so accidental comparisons fail loudly.

    Examples:
        >>> DeprecationStatus.ACTIVE_WARNING.value
        '📢 Deprecation Active'
        >>> DeprecationStatus.PAST_REMOVAL_DATE is DeprecationStatus.PAST_REMOVAL_DATE
        True

    """

    SCHEDULED_DEPRECATION = "🕒 Scheduled Deprecation"  # current < deprecated_in
    NO_REMOVAL_TARGET = "ℹ️ No Removal Target"  # remove_in not set
    STATUS_UNKNOWN = "⚪ Status Unknown"  # current_version unavailable
    INVALID_REMOVAL_TARGET = "⚪ Invalid Removal Target"  # remove_in unparsable
    ACTIVE_WARNING = "📢 Deprecation Active"  # current < remove_in (different base)
    REMOVAL_IMMINENT = "⏰ Removal Imminent"  # pre-release dev/a/b of remove_in base
    REMOVE_BEFORE_RELEASE = "🔔 Remove Before Release"  # RC of the remove_in base release
    PAST_REMOVAL_DATE = "💥 Past Removal Date"  # current >= remove_in

    def __lt__(self, other: object) -> bool:
        """Raise TypeError — urgency ordering is not meaningful for emoji-valued status labels."""
        raise TypeError("'<' not supported between instances of 'DeprecationStatus' — compare by identity")

    def __le__(self, other: object) -> bool:
        """Raise TypeError — urgency ordering is not meaningful for emoji-valued status labels."""
        raise TypeError("'<=' not supported between instances of 'DeprecationStatus' — compare by identity")

    def __gt__(self, other: object) -> bool:
        """Raise TypeError — urgency ordering is not meaningful for emoji-valued status labels."""
        raise TypeError("'>' not supported between instances of 'DeprecationStatus' — compare by identity")

    def __ge__(self, other: object) -> bool:
        """Raise TypeError — urgency ordering is not meaningful for emoji-valued status labels."""
        raise TypeError("'>=' not supported between instances of 'DeprecationStatus' — compare by identity")


def _normalize_version_string(version: str) -> str:
    """Normalize non-standard version strings before PEP 440 parsing.

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


def _check_deprecated_wrapper_expiry(func: Union[Callable, types.ModuleType], current_version: str) -> None:
    """Check if a deprecated wrapper has passed its scheduled removal version.

    This is an internal helper function used by :func:`~deprecate.audit.validate_deprecation_expiry`.
    It verifies that deprecated code is actually removed when it reaches its scheduled removal deadline.

    The function validates that the wrapper is properly decorated, extracts the removal version from its metadata,
    and compares it against the current version using semantic versioning. If the current version is greater than or
    equal to the scheduled removal version, it raises an AssertionError indicating the code must be deleted.

    Args:
        func: The deprecated callable to check. Must have a ``__deprecation_config__`` attribute set by the
            ``@deprecated`` decorator.
        current_version: The current version of the package (e.g., "2.0.0"). Should follow PEP 440 versioning
            conventions.

    Raises:
        ValueError: If the wrapper has missing or invalid ``__deprecation_config__`` metadata (expected
            :class:`~deprecate._types.DeprecationConfig`).
        ValueError: If the ``remove_in`` field is missing from the deprecation metadata.
        AssertionError: If the current version is greater than or equal to the scheduled removal version, indicating
            the code should have been removed.

    """
    # First validate that the wrapper has proper deprecation metadata
    info = validate_deprecation_wrapper(func)

    # Extract the remove_in version from the metadata
    remove_in = info.deprecated_info.remove_in
    if not remove_in:
        raise ValueError(
            f"{_format_subject(info)} does not have a 'remove_in' version specified in its deprecation metadata."
        )

    # Parse both versions for proper semantic version comparison
    # Let ImportError propagate with its helpful install message
    try:
        current_ver = _parse_version(current_version)
    except ValueError as err:
        raise ValueError(f"Invalid current_version '{current_version}': {err}") from err

    try:
        remove_ver = _parse_version(remove_in)
    except ValueError as err:
        raise ValueError(f"Invalid remove_in '{remove_in}' for {_format_subject(info)}: {err}") from err

    # Check if the current version has reached or passed the removal deadline
    if current_ver >= remove_ver:
        raise AssertionError(
            f"{_format_subject(info)} was scheduled for removal in version {remove_in} "
            f"but still exists in version {current_version}. Please delete this deprecated code."
        )


def _get_package_version(package_name: str) -> str:
    """Auto-detect the installed version of a package.

    This private helper function attempts to retrieve the version of an installed package using importlib.metadata,
    with a fallback to checking the package's ``__version__`` attribute. This is useful for automatically detecting
    the current version of a package when checking deprecation expiry.

    Args:
        package_name: Name of the package to get the version for (e.g., "numpy", "mypackage").

    Returns:
        The version string of the installed package.

    Raises:
        ImportError: If the package is not installed or version cannot be determined.

    """
    # Try importlib.metadata first (standard approach for installed packages)
    with suppress(Exception):
        return importlib.metadata.version(package_name)

    # Fall back to checking __version__ attribute
    with suppress(Exception):
        module = importlib.import_module(package_name)
        if hasattr(module, "__version__"):
            return module.__version__

    # If both methods fail, raise an informative error
    raise ImportError(
        f"Could not determine version for package '{package_name}'. "
        f"Ensure the package is installed and has version metadata."
    )


def _check_expiry_for_callables(results: list[DeprecationWrapperInfo], current_version: str) -> list[str]:
    """Apply expiry comparison to pre-scanned wrapper results.

    Shared implementation used by :func:`validate_deprecation_expiry` and the CLI's single-scan path. Keeps the
    error message format in one place.

    Args:
        results: Pre-scanned wrapper info list.
        current_version: Current package version string for comparison (PEP 440).

    Returns:
        List of expiry error messages for callables that have passed their removal deadline.

    Raises:
        ImportError: If the ``packaging`` library is not installed.

    """
    current_ver = _parse_version(current_version)
    expired = []
    for info in results:
        remove_in = info.deprecated_info.remove_in
        if not remove_in:
            continue
        try:
            remove_ver = _parse_version(remove_in)
        except ValueError:
            # An unparsable ``remove_in`` (e.g. a typo) would make the wrapper permanently un-expirable and the
            # batch gate would never flag it. Warn per skip so the misconfiguration is visible rather than silent
            # — the single-callable path raises; here a warning keeps the batch scan going for the rest.
            warnings.warn(
                f"{_format_subject(info)} has an unparsable `remove_in` version `{remove_in}`; "
                "its expiry cannot be checked until the version string is fixed.",
                stacklevel=2,
            )
            continue
        if current_ver >= remove_ver:
            expired.append(
                f"{_format_subject(info)} was scheduled for removal in version {remove_in}"
                f" but still exists in version {current_version}. Please delete this deprecated code."
            )
    return expired


def validate_deprecation_expiry(
    module: Union[Any, str],  # noqa: ANN401
    current_version: Optional[str] = None,
    recursive: bool = True,
    include_members: bool = True,
    *,
    exclude: Optional[Sequence[str]] = None,
) -> list[str]:
    """Check all deprecated callables in a module/package for expired removal deadlines.

    This enforcement tool scans an entire module or package for deprecated functions and checks if any have passed
    their scheduled removal version. It's designed for CI/CD pipelines to automatically detect and report zombie code
    across a codebase.

    The function uses :func:`~deprecate.audit.find_deprecation_wrappers` to discover all deprecated wrappers,
    then checks each one against
    the current version. Any wrappers that have reached or passed their removal deadline are collected and reported.

    Args:
        module: A Python module or package to scan. Can be:
            - Imported module object (e.g., ``import my_package; validate_deprecation_expiry(my_package, "2.0")``)
            - String module path (e.g., ``validate_deprecation_expiry("my_package.submodule", "2.0")``)
        current_version: The current version of your package to compare against removal deadlines (e.g., ``"2.0.0"``).
            If None, attempts to auto-detect the version using the package name from the module path (e.g.,
            ``"mypackage"`` extracts ``mypackage`` as package name).
        recursive: If True (default), recursively scan submodules. If False, only scan the top-level module.
        include_members: If True (default), also scan deprecated class members (methods, constructors,
            classmethods, staticmethods, properties) — matching the discovery default of
            :func:`~deprecate.audit.find_deprecation_wrappers`, so the enforcement gate sees everything
            discovery and reporting see.
        exclude: Glob patterns over full dotted module names to leave out of the scan, as in
            :func:`~deprecate.audit.find_deprecation_wrappers` (e.g. ``["my_package.tests"]``); ``None`` excludes
            nothing.

    Returns:
        List of error messages for callables that have expired (past their removal deadline).
        Empty list if all deprecated callables are still within their deprecation period.

    Example:
        >>> # Check a specific module with version before any deadlines
        >>> from deprecate import validate_deprecation_expiry
        >>> expired = validate_deprecation_expiry("tests.collection_deprecate", "0.1", recursive=False)
        >>> len(expired)
        0

        >>> # Check with version past some removal deadlines
        >>> expired = validate_deprecation_expiry("tests.collection_deprecate", "0.5", recursive=False)
        >>> print(len(expired))  # Some functions and class members have remove_in <= "0.5"
        31

    !!! note
        - Skips callables without a ``remove_in`` field (warnings only, no removal deadline)
        - Skips callables that cannot be imported or accessed
        - Emits a ``UserWarning`` (rather than silently skipping) for callables with an unparsable ``remove_in``
        - Uses semantic versioning comparison (e.g., "1.2.3" vs "2.0.0")
        - Intended for automated checks in CI/CD pipelines
        - Can be integrated into test suites or pre-commit hooks

    """
    # Determine module name for auto-version detection
    module_name = module if isinstance(module, str) else getattr(module, "__name__", None)

    # Auto-detect version if not provided
    if current_version is None:
        if not module_name:
            raise ValueError(
                "Cannot auto-detect version: module object has no __name__ attribute. "
                "Please provide current_version explicitly."
            )
        # Extract package name (first component of module path)
        package_name = module_name.split(".")[0]
        current_version = _get_package_version(package_name)

    # Validate current_version upfront for fail-fast feedback before the module scan.
    try:
        _parse_version(current_version)
    except ValueError as err:
        raise ValueError(f"Invalid current_version '{current_version}': {err}") from err

    # Handle string module path
    if isinstance(module, str):
        module = importlib.import_module(module)

    return _check_expiry_for_callables(
        find_deprecation_wrappers(module, recursive=recursive, include_members=include_members, exclude=exclude),
        current_version,
    )


def _safe_parse_version(version: str) -> Optional["Version"]:
    """Best-effort version parser for report status evaluation."""
    if not version:
        return None
    try:
        return _parse_version(version)
    except (ImportError, ValueError):
        return None


def _format_version(version: Optional[str], *, missing: str = "—") -> str:
    """Format version values with a stable ``v`` prefix for report output."""
    if not version:
        return missing
    # Strip a single left-anchored leading ``v``/``V`` (``lstrip`` would remove *all* leading v's).
    return f"v{re.sub(r'^[vV]', '', version)}"


def _get_deprecation_status(info: DeprecationWrapperInfo, current_version: Optional["Version"]) -> DeprecationStatus:
    """Classify one deprecated symbol into a report lifecycle status."""
    if current_version is None:
        return (
            DeprecationStatus.NO_REMOVAL_TARGET
            if not info.deprecated_info.remove_in
            else DeprecationStatus.STATUS_UNKNOWN
        )

    deprecated_in = _safe_parse_version(info.deprecated_info.deprecated_in)
    if deprecated_in is not None and current_version < deprecated_in:
        return DeprecationStatus.SCHEDULED_DEPRECATION

    remove_in = info.deprecated_info.remove_in
    if not remove_in:
        return DeprecationStatus.NO_REMOVAL_TARGET

    remove_version = _safe_parse_version(remove_in)
    if remove_version is None:
        return DeprecationStatus.INVALID_REMOVAL_TARGET

    if current_version >= remove_version:
        return DeprecationStatus.PAST_REMOVAL_DATE

    # Pre-release of the same base release as remove_in gets an elevated status:
    # dev/alpha/beta → REMOVAL_IMMINENT; rc → REMOVE_BEFORE_RELEASE.
    # base_version strips pre/post/dev/local markers so "1.8" == "1.8.0" compare equal.
    if current_version.is_prerelease:
        try:
            _VersionType = type(current_version)  # noqa: N806
            same_base = _VersionType(current_version.base_version) == _VersionType(remove_version.base_version)
        except Exception:
            same_base = False
        if same_base:
            if current_version.pre is not None and current_version.pre[0] == "rc":
                return DeprecationStatus.REMOVE_BEFORE_RELEASE
            return DeprecationStatus.REMOVAL_IMMINENT

    return DeprecationStatus.ACTIVE_WARNING
