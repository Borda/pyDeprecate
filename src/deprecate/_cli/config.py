"""``pyproject.toml`` configuration for the CLI: read ``[tool.pydeprecate]`` and resolve flag defaults."""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from deprecate._cli._pkg import _ConfigReadError, _read_pydeprecate_config
from deprecate._cli.report import _is_package_available, _print
from deprecate.audit import PolicyRule
from deprecate.audit._lifecycle import _parse_version
from deprecate.audit._policy import (
    _DEFAULT_MESSAGE_REQUIRED,
    _DEFAULT_MIN_GRACE,
    _GRACE_WINDOW_TABLE_SPELLING,
    _build_policy_spec,
)
from deprecate.audit._version import _iter_pyproject_paths

if TYPE_CHECKING:
    from deprecate.audit._policy import _PolicySpec


def _is_missing_packaging_import_error(error: ImportError) -> bool:
    """Return True when an ImportError was caused by a missing packaging dependency.

    Args:
        error: The ImportError raised while trying to inspect expiry information.

    Returns:
        True if the error indicates that the ``packaging`` library is unavailable.

    """
    for exc in (error, getattr(error, "__cause__", None)):
        if exc is None:
            continue
        name = getattr(exc, "name", None)
        if isinstance(name, str) and (name == "packaging" or name.startswith("packaging.")):
            return True
    return "No module named 'packaging'" in str(error)


def _validate_user_version(version: Optional[str], *, explicit: bool = True) -> Optional[int]:
    """Reject a malformed user-supplied ``--version`` before any subcommand does real scan work.

    Applies only to a value the user actually typed — ``expiry``, ``status``, and ``all`` previously
    let a bad ``--version`` fall through to whichever internal call happened to parse it first, exiting
    1 (via the top-level exception handler) in some subcommands and 2 (a validated-argument error, like
    a bad ``--min-grace``) in others. Validating it once, up front, in all three makes a malformed
    ``--version`` exit 2 everywhere. An *auto-detected* version that happens to be malformed is not a
    usage error and is untouched — it keeps its existing advisory/exception handling deeper in the scan.

    Args:
        version: The raw ``--version`` string the user supplied, or ``None`` when omitted (auto-detect).
        explicit: Whether *version* was typed by the user. ``cmd_all`` hands its already-resolved version
            down to ``cmd_expiry``/``cmd_status``, so those calls pass ``False`` when that
            version was auto-detected — otherwise a package whose own metadata version is not PEP 440
            would be reported as a malformed ``--version`` flag the user never typed.

    Returns:
        ``2`` when *version* is given, was typed by the user, and fails PEP 440 parsing; ``None`` when it
        is valid, omitted, auto-detected, or when the ``packaging`` library is unavailable (each
        subcommand's own advisory ImportError fallback already handles that case).

    """
    if version is None or not explicit:
        return None
    try:
        _parse_version(version)
    except ImportError:
        return None
    except ValueError as err:
        _print(f"Invalid `--version` {version!r}: {err}", stderr=True)
        return 2
    return None


class _FromPyproject:
    """Sentinel default for CLI flags that ``pyproject.toml`` may set: read the file, else use the built-in.

    A typed value — even ``None``, which switches a rule off — must be told apart from an omitted one, so the signature
    default is this marker instead; the parser passes only the options that were typed.

    """

    def __repr__(self) -> str:
        return "pyproject.toml"


_FROM_PYPROJECT = _FromPyproject()
_ConfigFlag = Union[str, int, float, bool, None, Sequence[str], _FromPyproject]
#: A boolean flag that ``pyproject.toml`` may set: a ``bool`` when typed, :data:`_FROM_PYPROJECT` when omitted. Unlike
#: the wider :data:`_ConfigFlag` it marks the parameter as a boolean flag for the parser
#: (:func:`deprecate._cli.argv._cli_command`).
_ConfigBool = Union[bool, _FromPyproject]
#: A loaded ``[tool.pydeprecate]`` table (known keys only) and the ``pyproject.toml`` it came from.
_Config = tuple[dict[str, Any], Optional[str]]
#: Built-in value per policy rule, used when neither a flag nor ``[tool.pydeprecate.policy]`` sets it — the same
#: objects :func:`~deprecate.audit.validate_deprecation_policy` declares as its signature defaults.
_POLICY_DEFAULTS: dict[str, Any] = {
    PolicyRule.MIN_GRACE.value: _DEFAULT_MIN_GRACE,
    PolicyRule.MESSAGE_REQUIRED.value: _DEFAULT_MESSAGE_REQUIRED,
}
_CONFIG_TABLE_NAME = "[tool.pydeprecate]"
_POLICY_TABLE_NAME = "[tool.pydeprecate.policy]"
_EXCLUDE_KEY = "exclude"
_POLICY_KEY = "policy"


def _warn_unknown_keys(table: dict[str, Any], known: Sequence[str], table_name: str, toml_path: Optional[str]) -> None:
    """Report keys of *table* that the CLI does not recognise — a typo would otherwise configure nothing, silently."""
    unknown = sorted(set(table) - set(known))
    if unknown:
        _print(
            f"Ignoring unknown key(s) {', '.join(f'`{k}`' for k in unknown)} in `{table_name}` of {toml_path}; "
            f"the recognised keys are {', '.join(f'`{k}`' for k in known)}.",
            stderr=True,
        )


def _load_pydeprecate_config(path: str) -> Optional[_Config]:
    """Read ``[tool.pydeprecate]`` for the scanned *path*, warning about anything silently ignorable.

    Only an existing file-system path is searched — a bare module name would otherwise walk up from the
    current directory and adopt whatever unrelated project the caller is standing in. Two silent no-ops are
    turned into stderr advisories: a ``pyproject.toml`` within reach when no TOML parser is installed (Python
    3.9-3.10 without the ``audit`` extra), and unknown keys in the table or its ``policy`` sub-table. Two more
    are usage errors, because either would leave the author believing a configuration is in force that the run
    never applied: a ``pyproject.toml`` within reach that cannot be read or parsed, and a ``policy`` key that is
    not a table.

    Args:
        path: The ``path`` argument of the subcommand.

    Returns:
        The known keys of the table (``exclude`` and the known keys of ``policy``) and the ``pyproject.toml``
        they came from, or ``({}, None)``; ``None`` after printing a usage error (the caller exits 2).

    """
    if not Path(path).exists():
        return {}, None
    if not (_is_package_available("tomllib") or _is_package_available("tomli")):
        if next(_iter_pyproject_paths(path), None) is not None:
            _print(
                f"A `pyproject.toml` is within reach of `{path}` but no TOML parser is installed, so any "
                f"`{_CONFIG_TABLE_NAME}` section is ignored.\nInstall one with: `pip install 'pyDeprecate[audit]'`",
                stderr=True,
            )
        return {}, None
    try:
        table, toml_path = _read_pydeprecate_config(path)
    except _ConfigReadError as err:
        _print(str(err), stderr=True)
        return None
    _warn_unknown_keys(table, (_EXCLUDE_KEY, _POLICY_KEY), _CONFIG_TABLE_NAME, toml_path)
    config: dict[str, Any] = {}
    if _EXCLUDE_KEY in table:
        config[_EXCLUDE_KEY] = table[_EXCLUDE_KEY]
    policy = table.get(_POLICY_KEY)
    if isinstance(policy, dict):
        _warn_unknown_keys(policy, list(_POLICY_DEFAULTS), _POLICY_TABLE_NAME, toml_path)
        config[_POLICY_KEY] = {slug: policy[slug] for slug in _POLICY_DEFAULTS if slug in policy}
    elif policy is not None:
        _print(
            f"Invalid `{_POLICY_KEY}` value `{policy}` in `{_CONFIG_TABLE_NAME}` of {toml_path}; expected a table "
            f"such as `{_POLICY_TABLE_NAME}` holding the rule keys.",
            stderr=True,
        )
        return None
    return config, toml_path


def _resolve_setting(
    flag: _ConfigFlag,
    table: dict[str, Any],
    key: str,
    default: Any,  # noqa: ANN401
    config: _Config,
) -> tuple[Any, str]:
    """Resolve one setting as flag > ``pyproject.toml`` > built-in default, returning the value and its source.

    The source is ``"flag"``, the path of the ``pyproject.toml``, or ``"built-in"``.

    """
    if flag is not _FROM_PYPROJECT:
        return flag, "flag"
    if key in table:
        return table[key], str(config[1])
    return default, "built-in"


def _describe_source(flag: str, table_name: str, source: str) -> str:
    """Name where a value came from, for usage errors."""
    if source == "flag":
        return f"`--{flag}`"
    if source == "built-in":
        return "the built-in default"
    return f"`{table_name}` in {source}"


def _source_label(source: str) -> str:
    """Short provenance tag for header lines — ``flag``, ``built-in`` or ``pyproject.toml``."""
    return source if source in ("flag", "built-in") else "pyproject.toml"


def _resolve_exclude(
    path: str, exclude: _ConfigFlag, config: Optional[_Config] = None
) -> Optional[tuple[list[str], str]]:
    """Resolve the module-exclusion patterns as flag > ``pyproject.toml`` > nothing, printing any usage error.

    A flag value may be one pattern or a comma-separated string (a list in a direct call), never a list literal;
    the file value must be a list of strings.

    Args:
        path: The ``path`` argument of the subcommand.
        exclude: The value the subcommand received; :data:`_FROM_PYPROJECT` means "not typed".
        config: An already-loaded configuration, to avoid re-reading (and re-warning about) the file.

    Returns:
        The pattern list and its source, or ``None`` after printing a usage error (the caller exits 2).

    """
    if config is None:
        config = _load_pydeprecate_config(path) if exclude is _FROM_PYPROJECT else ({}, None)
        if config is None:
            return None
    raw, source = _resolve_setting(exclude, config[0], _EXCLUDE_KEY, [], config)
    if source == "flag" and isinstance(raw, str) and raw.lstrip().startswith("["):
        # a list literal would become one pattern that matches nothing, leaving the scan silently unexcluded
        _print(
            f"Invalid `--exclude` value `{raw}`; pass comma-separated patterns such as"
            " `--exclude='my_package.tests,*._legacy*'`, not a list.",
            stderr=True,
        )
        return None
    if isinstance(raw, str):
        patterns: Any = [item.strip() for item in raw.split(",") if item.strip()]
    elif raw is None:
        patterns = []
    else:
        patterns = list(raw) if isinstance(raw, (list, tuple)) else raw
    if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
        _print(
            f"Invalid `exclude` value `{raw}` from {_describe_source(_EXCLUDE_KEY, _CONFIG_TABLE_NAME, source)}; "
            'expected a list of module-name glob patterns such as `["my_package.tests"]`.',
            stderr=True,
        )
        return None
    return patterns, source


def _resolve_policy_spec(
    path: str, flags: dict[str, _ConfigFlag], config: _Config
) -> Optional[tuple["_PolicySpec", str]]:
    """Build the policy spec as flag > ``pyproject.toml`` > built-in default per rule, printing any usage error.

    A TOML ``false`` for ``min-grace`` is normalised to ``None`` (TOML has no null) so the value is what
    :func:`_build_policy_spec` expects.

    Args:
        path: The ``path`` argument of the subcommand.
        flags: Rule slug to the value the subcommand received; :data:`_FROM_PYPROJECT` means "not typed".
        config: The already-loaded ``[tool.pydeprecate]`` configuration; :func:`cmd_policy` loads it once so the
            file is never re-read (and re-warned about) here.

    Returns:
        The spec and the ``Policy:`` header line naming each value's source, or ``None`` after printing a usage
        error naming the offending value and its source (the caller exits 2).

    """
    table = config[0].get(_POLICY_KEY, {})
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for slug, flag in flags.items():
        values[slug], sources[slug] = _resolve_setting(flag, table, slug, _POLICY_DEFAULTS[slug], config)
    min_grace_slug, message_slug = PolicyRule.MIN_GRACE.value, PolicyRule.MESSAGE_REQUIRED.value
    if values[min_grace_slug] is False:
        values[min_grace_slug] = None
    if not isinstance(values[message_slug], bool):
        _print(
            f"Invalid `message_required` value `{values[message_slug]}` from "
            f"{_describe_source(message_slug, _POLICY_TABLE_NAME, sources[message_slug])}; expected `true` or `false`.",
            stderr=True,
        )
        return None
    try:
        spec = _build_policy_spec(values[min_grace_slug], values[message_slug])
    except ValueError as err:
        # the table spelling exists only in `pyproject.toml` and direct calls, so a flag's error never suggests it
        message = str(err).replace(_GRACE_WINDOW_TABLE_SPELLING, "") if sources[min_grace_slug] == "flag" else str(err)
        _print(
            f"{message} (from {_describe_source(min_grace_slug, _POLICY_TABLE_NAME, sources[min_grace_slug])})",
            stderr=True,
        )
        return None
    header = "Policy: " + "  ".join(f"{slug}={values[slug]} ({_source_label(sources[slug])})" for slug in values)
    return spec, header
