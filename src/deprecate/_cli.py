"""CLI entry point for pyDeprecate validation.

Provides two entry points for scanning Python code for misconfigured ``@deprecated`` wrappers:

- ``pydeprecate <subcommand> <path>`` — console script installed via ``pip install 'pyDeprecate[cli]'``
- ``python -m deprecate <subcommand> <path>`` — module invocation of the same CLI

Subcommands:
    check   — Validate wrapper configuration and flag misconfigured, chain-forming, or positional-only-arg wrappers.
    expiry  — Check for deprecated wrappers that have passed their scheduled ``remove_in`` deadline.
    policy  — Check wrappers against deprecation-governance rules (grace window, migration guidance).
    chains  — Detect deprecated wrappers whose ``target`` is itself a deprecated callable.
    all     — Run all four checks in a single scan pass.
    status  — Render a markdown deprecation table to stdout (and optionally save it to a file).

"""

import contextlib
import functools
import importlib.util
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from deprecate._pkg import (
    _auto_detect_version,
    _ConfigReadError,
    _find_child_packages,
    _is_package_dir,
    _iter_pyproject_paths,
    _managed_sys_path,
    _read_pydeprecate_config,
    _resolve_module_name,
    _safe_module_name,
)
from deprecate.audit import (
    DeprecationWrapperInfo,
    PolicyRule,
    TableStyle,
    find_deprecation_wrappers,
    generate_deprecation_table,
    validate_deprecation_chains,
    validate_deprecation_expiry,
)
from deprecate.audit._lifecycle import _check_expiry_for_callables, _parse_version
from deprecate.audit._policy import (
    _DEFAULT_MESSAGE_REQUIRED,
    _DEFAULT_MIN_GRACE,
    _build_policy_spec,
    _check_policy_for_callables,
)

if TYPE_CHECKING:
    from deprecate.audit._policy import _PolicySpec


def _is_package_available(name: str) -> bool:
    """Return True if *name* is importable without actually importing it."""
    return importlib.util.find_spec(name) is not None


def _print(msg: str, *, stderr: bool = False) -> None:
    """Print a message, using Rich console when available.

    Routes output through :class:`~rich.console.Console` when the ``rich``
    package is installed, falling back to built-in :func:`print` otherwise.

    Args:
        msg: The message to print.
        stderr: If ``True``, send the message to *stderr* instead of *stdout*.

    """
    if _Reporter._HAS_RICH:
        _Reporter._console(stderr).print(msg, markup=False, highlight=False)
    else:
        std_ = sys.stderr if stderr else sys.stdout
        print(msg, file=std_)


def _scan_directory(
    path: str, include_members: bool = True, exclude: Optional[Sequence[str]] = None
) -> list[DeprecationWrapperInfo]:
    """Scan a plain directory of top-level Python files.

    Nested Python files in subdirectories are skipped unless they are part of an importable package layout. Plain
    directories do not generally support dotted imports for nested modules, so this function only scans top-level
    modules and warns when deeper files are present.

    """
    abs_path: Path = Path(path).resolve()
    results: list[DeprecationWrapperInfo] = []

    original_argv = sys.argv[:]
    sys.argv = sys.argv[:1]  # hide CLI args from any module-level code (e.g. setup.py)
    try:
        for entry in sorted(abs_path.iterdir(), key=lambda ent: ent.name):
            if not (entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("__")):
                continue
            module_name: str = entry.stem
            try:
                results.extend(
                    find_deprecation_wrappers(
                        module_name, recursive=False, include_members=include_members, exclude=exclude
                    )
                )
            except SystemExit:
                _print(f"Skipping {module_name}: module-level code exited (not a library module)", stderr=True)
            except Exception as e:
                _print(f"Could not scan {module_name}: {e}", stderr=True)
    finally:
        sys.argv = original_argv

    nested_python_files_found: bool = any(
        not fpy.name.startswith("__") for fpy in abs_path.rglob("*.py") if fpy.parent != abs_path
    )

    if nested_python_files_found:
        _print(
            "Skipping nested Python files in plain directory scan. Use an importable package layout with '__init__.py'"
            " files, or scan an importable module/package path instead.",
            stderr=True,
        )
    return results


def _scan_path(
    path: str, recursive: bool = True, include_members: bool = True, exclude: Optional[Sequence[str]] = None
) -> list[DeprecationWrapperInfo]:
    """Scan a directory or importable module/package name for deprecated wrappers.

    File paths are not accepted because ``find_deprecation_wrappers()`` expects an importable module or package name,
    not a filesystem path.

    """
    pth: Path = Path(path)
    if pth.is_dir():
        if _is_package_dir(pth):
            # package dir: resolve importable name from directory stem
            return find_deprecation_wrappers(
                Path(path).resolve().name, recursive=recursive, include_members=include_members, exclude=exclude
            )
        # Flat src-layout or project root: find package in direct children or src/ subdir.
        child_pkgs = _find_child_packages(pth)
        if len(child_pkgs) == 1:
            return _scan_path(str(child_pkgs[0]), recursive=recursive, include_members=include_members, exclude=exclude)
        return _scan_directory(path, include_members=include_members, exclude=exclude)
    if pth.is_file():
        raise ValueError(
            f"File paths are not supported: {path!r}. Pass an importable module/package name or a directory instead."
        )
    return find_deprecation_wrappers(path, recursive=recursive, include_members=include_members, exclude=exclude)


# ---------------------------------------------------------------------------
# Reporter — all rich/plain dispatch in one namespace
# ---------------------------------------------------------------------------


class _Reporter:
    _HAS_RICH: bool = _is_package_available("rich")
    _out: Any = None
    _err: Any = None
    _rich_box: Any = None
    _RichTable: Any = None
    _RichText: Any = None

    try:
        from rich import box as _rich_box_import
        from rich.console import Console as _RichConsole
        from rich.table import Table as _RichTable_import
        from rich.text import Text as _RichText_import

        _rich_box = _rich_box_import
        _RichTable = _RichTable_import
        # Rich parses square brackets in a cell as style markup, which would swallow the ``[rule-slug]``
        # prefix of a policy violation; wrapping the message in ``Text`` renders it literally.
        _RichText = _RichText_import
        _out = _RichConsole()
        _err = _RichConsole(stderr=True)
    except ImportError:  # pragma: no cover
        pass

    @staticmethod
    def _console(stderr: bool = False) -> Any:  # noqa: ANN401
        """Return the Rich Console for stdout (default) or stderr."""
        return _Reporter._err if stderr else _Reporter._out

    @staticmethod
    def _make_table(title: str, col: str, *, title_style: str, col_style: str) -> Any:  # noqa: ANN401
        """Build a three-column Module/Function/<col> Rich table."""
        table = _Reporter._RichTable(title=title, box=_Reporter._rich_box.ROUNDED, title_style=title_style)
        table.add_column("Module", style="cyan")
        table.add_column("Function", style="magenta")
        table.add_column(col, style=col_style)
        return table

    @staticmethod
    def _render_table(
        title: str,
        col: str,
        *,
        title_style: str,
        col_style: str,
        rows: list[tuple[str, ...]],
        plain_prefix: str,
    ) -> None:
        """Render a three-column Module/Function/Detail table (rich or plain fallback).

        Every cell is wrapped in :class:`rich.text.Text`, as in :meth:`_render_message_table`, so a square-bracketed
        detail is rendered literally instead of being parsed as Rich style markup.

        """
        if _Reporter._HAS_RICH:
            table = _Reporter._make_table(title, col, title_style=title_style, col_style=col_style)
            for row in rows:
                table.add_row(*(_Reporter._RichText(cell) for cell in row))
            _Reporter._console().print(table)
        else:
            _print(f"\n{plain_prefix}")
            for mod, fn, detail in rows:
                _print(f"\t- {mod}.{fn}: {detail}")

    @staticmethod
    def invalid_args(items: list[DeprecationWrapperInfo]) -> None:
        """Report wrappers with invalid ``args_mapping`` keys."""
        _Reporter._render_table(
            "Invalid Argument Mappings",
            "Invalid Args",
            title_style="bold red",
            col_style="red",
            rows=[(r.module, r.function, ", ".join(r.invalid_args)) for r in items],
            plain_prefix="[ERROR] Found functions with invalid argument mappings:",
        )

    @staticmethod
    def identity_args_mappings(items: list[DeprecationWrapperInfo]) -> None:
        """Report wrappers whose ``args_mapping`` maps an argument to itself."""
        _Reporter._render_table(
            "Identity Argument Mappings (arg -> arg)",
            "Identity Args",
            title_style="bold yellow",
            col_style="yellow",
            rows=[(r.module, r.function, ", ".join(r.identity_args_mapping)) for r in items],
            plain_prefix="[WARNING] Found functions with identity argument mappings (arg -> arg):",
        )

    @staticmethod
    def no_effect(items: list[DeprecationWrapperInfo]) -> None:
        """Report wrappers that have no observable effect on callers."""

        def _reasons(r: DeprecationWrapperInfo) -> str:
            """Format the no-effect reasons for a single wrapper as a comma-separated string."""
            parts = []
            if r.empty_args_mapping:
                parts.append("Empty mapping")
            if r.self_reference:
                parts.append("Self reference")
            if r.all_identity:
                parts.append("All identity mappings")
            return ", ".join(parts)

        _Reporter._render_table(
            "No-Effect Wrappers (zero impact)",
            "Reason",
            title_style="bold yellow",
            col_style="yellow",
            rows=[(r.module, r.function, _reasons(r)) for r in items],
            plain_prefix="[WARNING] Found deprecated wrappers with NO EFFECT (zero impact):",
        )

    @staticmethod
    def chains(items: list[DeprecationWrapperInfo], *, error: bool = False) -> None:
        """Report deprecated-to-deprecated forwarding chains; ``error=True`` renders in red."""
        style = "bold red" if error else "bold yellow"
        col_style = "red" if error else "yellow"
        prefix = "[ERROR]" if error else "[WARNING]"
        _Reporter._render_table(
            "Deprecation Chains",
            "Chain Type",
            title_style=style,
            col_style=col_style,
            rows=[(r.module, r.function, r.chain_type.value if r.chain_type is not None else "") for r in items],
            plain_prefix=f"{prefix} Found deprecated wrappers forming deprecation chains:",
        )

    @staticmethod
    def _render_message_table(title: str, plain_prefix: str, messages: list[str], *, error: bool = True) -> None:
        """Render a single-column Message table shared by the expiry and policy reports.

        Every message is wrapped in :class:`rich.text.Text` so a ``[rule-slug]``-style prefix (used by
        policy violation messages) is rendered literally instead of being parsed as Rich markup — this
        also closes that gap for plain expiry messages, which previously passed raw strings.

        Args:
            title: Rich table title.
            plain_prefix: Header line printed above the list in the no-Rich fallback.
            messages: Message strings to render, one per row/line.
            error: Render in red (a finding that fails the run); ``False`` renders in yellow, the colour the
                other reporters use for advisory findings.

        """
        if _Reporter._HAS_RICH:
            title_style, col_style = ("bold red", "red") if error else ("bold yellow", "yellow")
            table = _Reporter._RichTable(title=title, box=_Reporter._rich_box.ROUNDED, title_style=title_style)
            table.add_column("Message", style=col_style)
            for msg in messages:
                table.add_row(_Reporter._RichText(msg))
            _Reporter._console().print(table)
        else:
            _print(f"\n{plain_prefix}")
            for msg in messages:
                _print(f"\t- {msg}")

    @staticmethod
    def expiry(expired: list[str]) -> None:
        """Report deprecated wrappers that have passed their ``remove_in`` deadline."""
        _Reporter._render_message_table(
            "Expired Deprecated Wrappers", "[ERROR] Found expired deprecated wrappers:", expired
        )

    @staticmethod
    def policy(violations: list[str], *, advisory: bool = False) -> None:
        """Report wrappers that break one of the deprecation-governance policy rules.

        ``advisory=True`` renders the same table as a ``[WARNING]`` in yellow — how ``all`` reports violations it does
        not fail on — so a reader can tell it from the red ``[ERROR]`` of the gating ``policy`` subcommand.

        """
        prefix = "[WARNING]" if advisory else "[ERROR]"
        _Reporter._render_message_table(
            "Deprecation Policy Violations",
            f"{prefix} Found deprecation policy violations:",
            violations,
            error=not advisory,
        )

    @staticmethod
    def positional_only_args(items: list[DeprecationWrapperInfo]) -> None:
        """Report wrappers whose ``args_mapping`` remaps a kwarg to a POSITIONAL_ONLY constructor parameter."""
        _Reporter._render_table(
            "Positional-Only Constructor Args in args_mapping",
            "Positional-Only Args",
            title_style="bold yellow",
            col_style="yellow",
            rows=[(r.module, r.function, ", ".join(r.args_mapping_positional_only)) for r in items],
            plain_prefix=(
                "[WARNING] Found args_mapping entries targeting POSITIONAL_ONLY constructor parameters"
                " (proxy falls back to setattr for these entries):"
            ),
        )

    @staticmethod
    def issues(results: list[DeprecationWrapperInfo], *, error_on_chains: bool = False) -> bool:
        """Print categorised diagnostics and return whether any issues were found."""
        invalid_args = [r for r in results if r.invalid_args]
        identity_args_mappings = [r for r in results if r.identity_args_mapping]
        no_effect = [r for r in results if r.no_effect]
        chains = [r for r in results if r.chain_type is not None]
        positional_only = [r for r in results if r.args_mapping_positional_only]

        if not (invalid_args or identity_args_mappings or no_effect or chains or positional_only):
            return False

        if invalid_args:
            _Reporter.invalid_args(invalid_args)
        if identity_args_mappings:
            _Reporter.identity_args_mappings(identity_args_mappings)
        if no_effect:
            _Reporter.no_effect(no_effect)
        if chains:
            _Reporter.chains(chains, error=error_on_chains)
        if positional_only:
            _Reporter.positional_only_args(positional_only)

        return True


def _do_expiry(
    path: str, version: Optional[str], recursive: bool, exclude: Optional[Sequence[str]] = None
) -> Optional[list[str]]:
    """Run the expiry scan and return expired wrapper messages, or None when packaging is unavailable.

    Caller is responsible for setting up ``sys.path`` via :func:`_managed_sys_path` before calling.

    Args:
        path: Package directory path or importable module name string.
        version: Current package version for comparison, or None to auto-detect.
        recursive: Scan submodules recursively.
        exclude: Module-name glob patterns to leave out of the scan.

    Returns:
        List of expired wrapper message strings (may be empty), or None when the
        ``packaging`` library is unavailable (advisory — warning already printed to stderr).

    """
    module_name = _resolve_module_name(path)
    try:
        return validate_deprecation_expiry(module_name, version, recursive=recursive, exclude=exclude)
    except ImportError as exc:
        if _is_missing_packaging_import_error(exc):
            _print(
                "The 'expiry' subcommand requires the 'packaging' library.\n"
                "Install it with: `pip install 'pyDeprecate[audit]'`",
                stderr=True,
            )
            return None
        if getattr(exc, "name", None) is not None:
            # Python's import system sets ``exc.name`` when a module-level ``import`` statement
            # fails — this is a broken import inside the user's package, not a version-detection
            # failure.  Propagate so the caller gets the real error instead of the misleading
            # "Could not determine version" message.
            raise
        _print(
            "Could not determine the current package version automatically.\n"
            "Pass --version explicitly, or ensure the package is installed and importable.\n\n"
            f"Original error: {exc}",
            stderr=True,
        )
        return None
    # other exceptions propagate to cli()'s top-level handler around fire.Fire


def _do_expiry_prescanned(wrappers: list[DeprecationWrapperInfo], version: Optional[str]) -> Optional[list[str]]:
    """Derive expired wrapper messages from an already-scanned wrapper list, without rescanning.

    The single-scan path used by ``cmd_all``. Every way this can fail is advisory: ``cmd_all`` resolves one
    version for all of its subcommands, and neither an unresolvable nor an unparsable one is a usage error
    the user can be blamed for, so the expiry gate is skipped with a message instead of failing the run.

    Args:
        wrappers: Pre-scanned wrapper list to compare against *version*.
        version: Resolved package version, or None when it could not be resolved at all.

    Returns:
        List of expired wrapper message strings (may be empty), or None when the check could not run
        (advisory — the reason is already printed to stderr).

    """
    if version is None:
        _print("Cannot check expiry: version not resolved. Pass --version explicitly.", stderr=True)
        return None
    try:
        return _check_expiry_for_callables(wrappers, version)
    except ImportError:
        _print(
            "The 'expiry' subcommand requires the 'packaging' library.\n"
            "Install it with: `pip install 'pyDeprecate[audit]'`",
            stderr=True,
        )
        return None
    except ValueError:
        # Only an auto-detected version reaches here — an explicit one is rejected up front with exit 2.
        # A package whose own metadata version is not PEP 440 is not a usage error, so the check degrades
        # to an advisory skip rather than aborting the run with an unhandled parse failure.
        _print(
            f"Cannot check expiry: the auto-detected version `{version}` is not a valid PEP 440 version "
            "string. Pass `--version` explicitly for a definitive check.",
            stderr=True,
        )
        return None


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

    Fire cannot tell a typed ``--min-grace=0.3`` from the signature default, so the signature default is this marker
    instead — the ``repr`` is what ``--help`` shows as ``Default:``.

    """

    def __repr__(self) -> str:
        return "pyproject.toml"


_FROM_PYPROJECT = _FromPyproject()
_ConfigFlag = Union[str, int, float, bool, None, Sequence[str], _FromPyproject]
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

    A flag value may be one pattern, a comma-separated string, or a list; the file value must be a list of
    strings.

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
        _print(
            f"{err} (from {_describe_source(min_grace_slug, _POLICY_TABLE_NAME, sources[min_grace_slug])})", stderr=True
        )
        return None
    header = "Policy: " + "  ".join(f"{slug}={values[slug]} ({_source_label(sources[slug])})" for slug in values)
    return spec, header


def _skipped_policy_rules(spec: "_PolicySpec") -> list[str]:
    """Return the CLI-facing slugs of the version-dependent policy rules *spec* has enabled.

    These are exactly the rules that cannot run without the ``packaging`` library — used to name them in the advisory
    printed when ``packaging`` turns out to be unavailable. Today ``min-grace`` is the only such rule, so the list holds
    at most one entry.

    """
    return [PolicyRule.MIN_GRACE.value] if spec.grace is not None else []


def _policy_violations_without_packaging(
    wrappers: list[DeprecationWrapperInfo], spec: "_PolicySpec"
) -> Optional[list[str]]:
    """Handle a missing-``packaging`` failure from :func:`_check_policy_for_callables`.

    Prints an advisory naming the version-dependent rule *spec* had enabled (``min_grace``) — it is skipped.
    ``message_required`` needs no version parsing, so when it is enabled it is re-run standalone and its
    violations still gate the exit code; only the fully-disabled case falls through to an advisory no-op.

    Args:
        wrappers: Pre-scanned wrapper list to re-check for ``message_required`` alone.
        spec: The originally requested (unsatisfiable) policy configuration.

    Returns:
        The ``message_required``-only violations list, or ``None`` when ``message_required`` is disabled and there
        is nothing left to check.

    """
    skipped_rules = ", ".join(f"`{rule}`" for rule in _skipped_policy_rules(spec))
    _print(
        f"The `packaging` library is required for the {skipped_rules} policy rule; skipping it.\n"
        "Install it with: `pip install 'pyDeprecate[audit]'`",
        stderr=True,
    )
    if not spec.message_required:
        return None
    # `message_required` needs no version parsing at all, so a spec with the grace-window rule switched off keeps
    # the version machinery — and with it the ImportError just handled — out of this second pass entirely.
    return _check_policy_for_callables(wrappers, _build_policy_spec(None, True))


# ---------------------------------------------------------------------------
# Subcommand functions
# ---------------------------------------------------------------------------


def _print_scan_header(
    path: str,
    version: Optional[str] = None,
    *,
    user_provided: bool = False,
    exclude: Optional[tuple[list[str], str]] = None,
) -> None:
    """Print a consistent scan header: scanning location, package name, version, and any exclusion patterns."""
    module = _safe_module_name(path)
    _print(f"Scanning: {path}")
    if version is not None:
        source = "user-provided" if user_provided else "auto-detected"
        _print(f"Package: {module}  Version: {version} ({source})")
    else:
        _print(f"Package: {module}")
    if exclude is not None and exclude[0]:
        _print(f"Exclude: {', '.join(exclude[0])} ({_source_label(exclude[1])})")


def cmd_check(
    path: str = ".",
    recursive: bool = True,
    exit_zero: bool = False,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    *,
    _wrappers: Optional[list[DeprecationWrapperInfo]] = None,
) -> int:
    """Scan Python code for misconfigured ``@deprecated`` wrappers and deprecation chains.

    Reports invalid argument mappings (exit 1), identity mappings, no-effect wrappers,
    deprecation chains, and ``args_mapping`` entries targeting POSITIONAL_ONLY constructor
    parameters (all advisory warnings only, exit 0). Use the ``chains`` subcommand for a
    dedicated hard-error chain check.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        exit_zero: Always exit 0 even if hard errors (invalid argument mappings) are found.
            Useful for advisory CI steps that should report but never block.
        exclude: Module-name glob patterns to leave out of the scan — one pattern, a comma-separated string, or a
            list (``--exclude='my_package.tests,*._legacy*'``); the scan itself never imports a matching package
            nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the nearest
            ``pyproject.toml``, else nothing.
        _wrappers: Pre-scanned wrapper list. When provided, skips the scan step. Underscore
            prefix hides this parameter from the Fire CLI (internal use by ``cmd_all`` only).

    Returns:
        0 on success or advisory-only issues; 1 when hard errors are found and ``exit_zero`` is False; 2 when
        ``--exclude`` is malformed.

    """
    if _wrappers is None:
        resolved_exclude = _resolve_exclude(path, exclude)
        if resolved_exclude is None:
            return 2
        _print_scan_header(path, exclude=resolved_exclude)
        with _managed_sys_path(path):
            _wrappers = _scan_path(path, recursive=recursive, exclude=resolved_exclude[0])

    if not _wrappers:
        _print("No deprecated callables found.")
        return 0

    if _Reporter.issues(_wrappers, error_on_chains=False):
        _print("\nIssues were found in deprecated wrappers.")
    else:
        _print("\nAll deprecated wrappers look correct!")

    has_invalid = any(r.invalid_args for r in _wrappers)
    return 1 if not exit_zero and has_invalid else 0


def cmd_expiry(
    path: str = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    exit_zero: bool = False,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    *,
    _wrappers: Optional[list[DeprecationWrapperInfo]] = None,
    _version_explicit: bool = True,
) -> int:
    """Check for deprecated wrappers that have passed their scheduled removal version.

    Requires the ``packaging`` library: ``pip install 'pyDeprecate[audit]'``.
    A missing ``packaging`` library is treated as advisory (returns 0 with a warning).

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for comparison (e.g. ``"2.0.0"``). Auto-detected
            from installed package metadata if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        exit_zero: Always exit 0 even if expired wrappers are found.
            Useful for advisory CI steps that should report but never block.
        exclude: Module-name glob patterns to leave out of the scan — one pattern, a comma-separated string, or a
            list (``--exclude='my_package.tests,*._legacy*'``); the scan itself never imports a matching package
            nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the nearest
            ``pyproject.toml``, else nothing.
        _wrappers: Pre-scanned wrapper list. When provided, skips the scan step and derives
            expired wrappers via ``_do_expiry_prescanned``. Requires *version* to be
            non-``None`` when set. Underscore prefix hides this parameter from the Fire CLI
            (internal use by ``cmd_all`` only).
        _version_explicit: Whether *version* is a value the user typed rather than one the caller
            auto-detected. ``cmd_all`` forwards its already-resolved version and passes ``False``
            when that version came from auto-detection, so a malformed *installed* version is never
            reported as a malformed ``--version`` flag. Underscore prefix hides this parameter from
            the Fire CLI (internal use by ``cmd_all`` only).

    Returns:
        0 on success, when the ``packaging`` library is unavailable, or when an auto-detected version
        turns out not to be valid PEP 440 (advisory — the check is skipped); 1 when expired wrappers
        are found and ``exit_zero`` is False; 2 when a user-supplied ``--version`` is not a valid
        PEP 440 version string or ``--exclude`` is malformed.

    """
    # Fire auto-converts numeric-looking strings (e.g. "1.0" → float); normalise to str.
    version_explicit = version is not None and _version_explicit
    if version is not None:
        version = str(version)
    err_code = _validate_user_version(version, explicit=_version_explicit)
    if err_code is not None:
        return err_code
    if _wrappers is None:
        resolved_exclude = _resolve_exclude(path, exclude)
        if resolved_exclude is None:
            return 2
        # Standalone path: full scan + version auto-detect inside _do_expiry.
        resolved_version = version if version is not None else _auto_detect_version(_safe_module_name(path), path=path)
        if resolved_version is None and version is None:
            _print(
                "Could not resolve the current package version automatically and no --version was given; "
                "the expiry check runs without a resolved version and its result may be unreliable. "
                "Pass --version explicitly for a definitive check.",
                stderr=True,
            )
        _print_scan_header(path, resolved_version, user_provided=version_explicit, exclude=resolved_exclude)
        with _managed_sys_path(path):
            raw = _do_expiry(path, resolved_version, recursive, exclude=resolved_exclude[0])
        if raw is None:  # packaging unavailable — warning already printed to stderr
            return 0
        expired = raw
    else:
        # Pre-scanned path: derive expired list from wrappers directly.
        raw = _do_expiry_prescanned(_wrappers, version)
        if raw is None:  # check could not run — advisory already printed to stderr
            return 0
        expired = raw
    if not expired:
        _print("No expired deprecated wrappers found.")
        return 0
    _Reporter.expiry(expired)
    _print(f"\n{len(expired)} expired wrapper(s) found.")
    return 0 if exit_zero else 1


def cmd_policy(
    path: str = ".",
    recursive: bool = True,
    exit_zero: bool = False,
    min_grace: _ConfigFlag = _FROM_PYPROJECT,
    message_required: _ConfigFlag = _FROM_PYPROJECT,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    *,
    _wrappers: Optional[list[DeprecationWrapperInfo]] = None,
    _config: Optional[_Config] = None,
    _advisory: bool = False,
) -> int:
    """Check deprecated wrappers against deprecation-governance policy rules.

    Where ``expiry`` asks whether a wrapper was removed on time, ``policy`` asks whether it was scheduled
    responsibly: a removal deadline that leaves callers too short a grace window or lands off a release
    boundary, or a warning that never names a replacement.

    The ``min-grace`` rule needs the ``packaging`` library (``pip install 'pyDeprecate[audit]'``) for version
    comparison; ``message-required`` does not. When ``packaging`` is unavailable, ``min-grace`` is skipped with
    an advisory warning; ``message_required`` still runs and gates the exit code normally. The gate only fully
    no-ops (return 0 with a warning) when ``min-grace`` was requested and ``message_required`` is also disabled.

    Each rule is resolved as flag > ``[tool.pydeprecate.policy]`` in the nearest ``pyproject.toml`` (the scanned
    directory or up to two levels above it) > built-in default, and the header names the source of each. The
    table uses the rule slugs as keys — ``min-grace = "0.3"``, ``message-required = true`` — and ``false`` for
    ``min-grace`` where the flag would take ``None`` (TOML has no null). It is only consulted for an existing
    path, never for a bare module name.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        exit_zero: Always exit 0 even if violations are found.
            Useful for advisory CI steps that should report but never block.
        min_grace: Minimum distance between ``deprecated_in`` and ``remove_in`` as a version-shaped delta with
            two or three components — ``1.0`` one major, ``0.3`` three minors, ``0.0.2`` two patches (a bare
            ``1`` is rejected as ambiguous) — or a one-key unit table (``{"minor": 3}``, the natural spelling in
            ``pyproject.toml``); built-in default ``0.3``, ``None`` skips the rule. The removal must be one clean
            bump of a single component (``1.2`` → ``1.5`` or ``2.0``, never ``2.3``); a coarser bump always clears
            a finer window. Ten or more steps: quote as a string (``--min-grace='"0.10"'``), else Fire parses
            ``0.10`` as ``0.1``.
        message_required: Require every wrapper to name a replacement (built-in default True).
        exclude: Module-name glob patterns to leave out of the scan — one pattern, a comma-separated string, or a
            list (``--exclude='my_package.tests,*._legacy*'``); the scan itself never imports a matching package
            nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the nearest
            ``pyproject.toml``, else nothing.
        _wrappers: Pre-scanned wrapper list. When provided, skips the scan step. Underscore prefix hides this
            parameter from the Fire CLI (internal use by ``cmd_all`` only).
        _config: Already-loaded ``[tool.pydeprecate]`` configuration, so ``cmd_all`` does not re-read (and
            re-warn about) the file. Underscore prefix hides this parameter from the Fire CLI.
        _advisory: Render violations as a yellow ``[WARNING]`` with a trailer pointing at this subcommand, instead
            of the red ``[ERROR]`` of a gating run. Only ``cmd_all`` passes ``True`` — it reports policy
            violations without failing on them, and the output has to say so. Never inferred from ``exit_zero``,
            which downgrades the exit code without changing what the violations mean. Underscore prefix hides
            this parameter from the Fire CLI.

    Returns:
        0 on success, or when ``min-grace`` is skipped because ``packaging`` is unavailable and
        ``message_required`` finds no violation; 1 when violations are found (including from a still-running
        ``message_required`` check) and ``exit_zero`` is False; 2 when a rule's value or ``exclude`` — from a
        flag or from ``pyproject.toml`` — is malformed, when ``policy`` in ``[tool.pydeprecate]`` is not a table,
        or when a ``pyproject.toml`` within reach cannot be read or parsed.

    """
    config = _config if _config is not None else _load_pydeprecate_config(path)
    if config is None:
        return 2
    resolved = _resolve_policy_spec(
        path, {PolicyRule.MIN_GRACE.value: min_grace, PolicyRule.MESSAGE_REQUIRED.value: message_required}, config
    )
    if resolved is None:
        return 2
    spec, policy_header = resolved

    if _wrappers is None:
        resolved_exclude = _resolve_exclude(path, exclude, config)
        if resolved_exclude is None:
            return 2
        _print_scan_header(path, exclude=resolved_exclude)
        with _managed_sys_path(path):
            _wrappers = _scan_path(path, recursive=recursive, exclude=resolved_exclude[0])
    _print(policy_header)
    try:
        violations = _check_policy_for_callables(_wrappers, spec)
    except ImportError as exc:
        if not _is_missing_packaging_import_error(exc):
            raise
        fallback = _policy_violations_without_packaging(_wrappers, spec)
        if fallback is None:
            return 0
        violations = fallback
    if not violations:
        _print("No deprecation policy violations found.")
        return 0
    _Reporter.policy(violations, advisory=_advisory)
    _print(f"\n{len(violations)} policy violation(s) found.")
    if _advisory:
        _print("(advisory here — run `pydeprecate policy` to gate on it)")
    return 0 if exit_zero else 1


def cmd_chains(
    path: str = ".",
    recursive: bool = True,
    exit_zero: bool = False,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    *,
    _wrappers: Optional[list[DeprecationWrapperInfo]] = None,
) -> int:
    """Detect deprecated wrappers whose ``target`` is itself a deprecated callable (chains).

    Two chain kinds are detected: ``target`` (forwarding chain to another deprecated
    callable) and ``stacked`` (composed argument mappings that should be collapsed).

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        exit_zero: Always exit 0 even if chains are found.
            Useful for advisory CI steps that should report but never block.
        exclude: Module-name glob patterns to leave out of the scan — one pattern, a comma-separated string, or a
            list (``--exclude='my_package.tests,*._legacy*'``); the scan itself never imports a matching package
            nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the nearest
            ``pyproject.toml``, else nothing.
        _wrappers: Pre-scanned wrapper list. When provided, skips the scan step and filters
            for ``chain_type is not None`` internally. Underscore prefix hides this parameter
            from the Fire CLI (internal use by ``cmd_all`` only).

    Returns:
        0 when no chains are found or ``exit_zero`` is True; 1 when chains are found; 2 when ``--exclude`` is
        malformed.

    """
    if _wrappers is None:
        resolved_exclude = _resolve_exclude(path, exclude)
        if resolved_exclude is None:
            return 2
        _print_scan_header(path, exclude=resolved_exclude)
        with _managed_sys_path(path):
            _wrappers = validate_deprecation_chains(
                _resolve_module_name(path), recursive=recursive, exclude=resolved_exclude[0]
            )
    chains = [r for r in _wrappers if r.chain_type is not None]
    if not chains:
        _print("No deprecation chains found.")
        return 0
    _Reporter.chains(chains, error=True)
    _print(f"\n{len(chains)} deprecation chain(s) found.")
    return 0 if exit_zero else 1


def cmd_all(
    path: str = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    exit_zero: bool = False,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
) -> int:
    """Run all four checks then append a deprecation table.

    Performs a single scan pass and distributes the wrappers to ``cmd_check``,
    ``cmd_expiry``, ``cmd_policy``, and ``cmd_chains`` so the filesystem is only traversed once.
    After all four checks complete, ``cmd_status`` is always called to append a
    compact markdown deprecation table to the output. The policy check runs in advisory mode here — its
    violations are printed but never change the aggregate exit code, because its defaults encode a project
    convention; run ``pydeprecate policy`` directly to gate on them.
    Version is auto-detected from installed package metadata when not provided.
    A missing ``packaging`` library skips the expiry check with a warning and does not
    count as a hard error.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for expiry comparison (e.g. ``"2.0.0"``).
            Auto-detected from installed package metadata if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        exit_zero: Always exit 0 even if issues are found.
            Useful for advisory CI steps that should report but never block.
        exclude: Module-name glob patterns to leave out of the scan — one pattern, a comma-separated string, or a
            list (``--exclude='my_package.tests,*._legacy*'``); the scan itself never imports a matching package
            nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the nearest
            ``pyproject.toml``, else nothing.

    Returns:
        0 when the check, expiry, and chain gates pass or ``exit_zero`` is True; 1 when any of them finds a hard
        error; 2 for a usage error, which ``exit_zero`` never downgrades: a user-supplied ``--version`` that is
        not a valid PEP 440 version string, a malformed ``exclude`` (flag or file), a ``pyproject.toml`` within
        reach that cannot be read or parsed, a ``policy`` key in ``[tool.pydeprecate]`` that is not a table, or a
        malformed rule value in ``[tool.pydeprecate.policy]``.
        Policy violations are advisory here and never contribute to this code.
        The deprecation table is appended after the gates, so a usage error raised by the policy pass skips it.

    """
    if version is not None:
        version = str(version)
        err_code = _validate_user_version(version)
        if err_code is not None:
            return err_code
    config = _load_pydeprecate_config(path)
    if config is None:
        return 2
    resolved_exclude = _resolve_exclude(path, exclude, config)
    if resolved_exclude is None:
        return 2
    version_path = path if Path(path).exists() else None
    version_explicit = version is not None
    resolved_version = version if version_explicit else _auto_detect_version(_safe_module_name(path), path=version_path)
    _print_scan_header(path, resolved_version, user_provided=version_explicit, exclude=resolved_exclude)
    with _managed_sys_path(path):
        wrappers = _scan_path(path, recursive=recursive, exclude=resolved_exclude[0])

    # Sub-commands run with exit_zero=False so cmd_all sees their truthful exit codes;
    # the user-facing --exit-zero is applied to the aggregate below.
    # ``resolved_version`` may be auto-detected; ``_version_explicit`` carries that provenance so the
    # subcommands do not re-validate it as if the user had typed it — a package stamped with a non-PEP 440
    # version would otherwise be reported as an invalid ``--version`` flag and skip the checks entirely.
    check_code = cmd_check(path, recursive=recursive, exit_zero=False, _wrappers=wrappers)
    expiry_code = cmd_expiry(
        path,
        version=resolved_version,
        recursive=recursive,
        exit_zero=False,
        _wrappers=wrappers,
        _version_explicit=version_explicit,
    )
    # Advisory inside ``all``: the policy defaults encode a project convention (a three-minor grace window on a
    # clean release boundary) that not every repo shares, so ``all`` reports violations (exit 1 below) but never
    # fails on them — gate on them with the dedicated ``policy`` subcommand, whose exit code is truthful. A usage
    # error (exit 2: a malformed rule value in ``pyproject.toml``) is not a violation and stops the run, exactly
    # as a malformed ``exclude`` does above.
    policy_code = cmd_policy(
        path, recursive=recursive, exit_zero=False, _wrappers=wrappers, _config=config, _advisory=True
    )
    if policy_code == 2:
        return 2
    chains_code = cmd_chains(path, recursive=recursive, exit_zero=False, _wrappers=wrappers)

    # The status table is a display artifact appended after the three gates. Render it defensively:
    # a table-rendering failure must never change the aggregate exit code the three checks produced.
    try:
        cmd_status(
            path,
            version=resolved_version,
            recursive=recursive,
            _wrappers=wrappers,
            _version_explicit=version_explicit,
        )
    except Exception as exc:
        _print(f"Could not render the deprecation table: {exc}", stderr=True)

    has_errors = bool(check_code or expiry_code or chains_code)
    return 0 if not has_errors or exit_zero else 1


def cmd_status(
    path: str = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    style: str = "compact",
    include_members: bool = True,
    output: Optional[str] = None,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    *,
    _wrappers: Optional[list[DeprecationWrapperInfo]] = None,
    _version_explicit: bool = True,
) -> int:
    """Print a markdown deprecation status table to stdout.

    Scans the target package for deprecated wrappers and renders their lifecycle
    status as a Markdown table. Standalone — runs no checks, exits 0 (2 only for a malformed ``--exclude``).
    When ``--output`` is given, the table is also written to that file.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for lifecycle status (e.g. ``"2.0.0"``).
            Auto-detected from installed package metadata if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        style: Table format — ``compact`` (default) or ``matrix``.
        include_members: Include deprecated class members such as methods and constructors (default True).
        output: Optional file path to write the markdown table. The table is always
            printed to stdout regardless of this flag.
        exclude: Module-name glob patterns to leave out of the scan — one pattern, a comma-separated string, or a
            list (``--exclude='my_package.tests,*._legacy*'``); the scan itself never imports a matching package
            nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the nearest
            ``pyproject.toml``, else nothing.
        _wrappers: Pre-scanned wrapper list. When provided, skips the scan step. Underscore prefix hides
            this parameter from the Fire CLI (internal use by ``cmd_all`` only).
        _version_explicit: Whether *version* is a value the user typed rather than one the caller
            auto-detected. ``cmd_all`` forwards its already-resolved version and passes ``False`` when that
            version came from auto-detection, so a package stamped with a non-PEP 440 version still renders
            its table (unparsed) instead of aborting it. Underscore prefix hides this parameter from the
            Fire CLI (internal use by ``cmd_all`` only).

    Returns:
        0 — status table generation is not a pass/fail gate; 2 only when ``--exclude`` is malformed.

    """
    version_explicit = version is not None and _version_explicit
    if version is not None:
        version = str(version)
    try:
        table_style = TableStyle(style)
    except ValueError:
        valid = ", ".join(s.value for s in TableStyle)
        _print(f"Invalid style {style!r}; falling back to 'compact'. Expected one of: {valid}.", stderr=True)
        table_style = TableStyle.COMPACT

    # Never resolve the importable module name eagerly: status "always exits 0" (it is not a
    # pass/fail gate), so a plain directory without ``__init__.py`` — which ``check`` scans fine and
    # for which ``_wrappers`` is already supplied by ``cmd_all`` — must not raise here. ``_safe_module_name``
    # falls back to the raw path, used only as table metadata for version resolution.
    module_name = _safe_module_name(path)
    resolved_version = version if version is not None else _auto_detect_version(module_name, path=path)
    if _wrappers is None:
        resolved_exclude = _resolve_exclude(path, exclude)
        if resolved_exclude is None:
            return 2
        _print_scan_header(path, resolved_version, user_provided=version_explicit, exclude=resolved_exclude)
        with _managed_sys_path(path):
            _wrappers = _scan_path(
                path, recursive=recursive, include_members=include_members, exclude=resolved_exclude[0]
            )
    markdown = generate_deprecation_table(
        module_name,
        current_version=resolved_version,
        style=table_style,
        _wrappers=_wrappers,
        _version_explicit=version_explicit,
    )

    if _Reporter._HAS_RICH:
        from rich.markdown import Markdown

        _Reporter._console().print(Markdown(markdown))
    else:
        _print(markdown)

    if output is not None:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
        _print(f"→ Saved to: {out_path}", stderr=True)

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _ensure_utf8_streams() -> None:
    """Reconfigure stdout/stderr to UTF-8 on platforms where the default encoding may reject non-ASCII characters.

    On Windows the default console codec (``charmap``) cannot encode Unicode emoji used by
    :class:`~deprecate.audit.DeprecationStatus`.  Calling ``reconfigure`` before any output is written ensures emoji
    reach the terminal (or CI log) without a :exc:`UnicodeEncodeError`.

    The call is a no-op when the streams are already UTF-8 or when they lack a ``reconfigure`` method (binary streams,
    pytest capture wrappers).

    """
    for stream in (sys.stdout, sys.stderr):
        # ``encoding`` may be *present but None* (e.g. some redirected/binary-ish wrappers); ``getattr`` with a
        # default only covers a missing attribute, so guard the ``None`` case with ``or "utf-8"`` before ``.lower()``.
        encoding = (getattr(stream, "encoding", None) or "utf-8").lower()
        if hasattr(stream, "reconfigure") and encoding != "utf-8":
            with contextlib.suppress(Exception):
                stream.reconfigure(encoding="utf-8")


def cli() -> None:
    """CLI entry point for pydeprecate.

    The subcommand's integer return value becomes the process exit code, applied *after* ``fire.Fire``
    returns. Raising ``SystemExit`` from inside the Fire trace would abort Fire's own unconsumed-argument
    check, silently ignoring unknown or misspelled flags (exit 0 on typos); letting the trace complete
    makes Fire report ``Could not consume arg`` and exit 2 for such flags. Acts as the single top-level
    exception handler: unhandled exceptions from subcommands are converted to a non-zero exit with the
    exception type and message (the type prefix guarantees a non-blank stderr line even when the exception
    carries no message). SystemExit (Fire's ``--help`` and error exits) passes through unchanged.

    A path whose name collides with a subcommand (``check``, ``expiry``, ``policy``, ``chains``, ``all``, ``status``) is
    treated as that subcommand by the implicit-``check`` shim. Scan such a path explicitly, e.g.
    ``pydeprecate check ./check``, so the leading token is the subcommand and the path is its argument.

    """
    _ensure_utf8_streams()
    try:
        import fire
    except ImportError:
        sys.exit("The 'pydeprecate' CLI requires the 'fire' package.\nInstall it with: pip install 'pyDeprecate[cli]'")

    subcommands = {"check", "expiry", "policy", "chains", "all", "status"}
    argv = sys.argv[1:]
    if argv and argv[0] not in subcommands and argv[0] not in {"-h", "--help"}:
        argv = ["check", *argv]

    exit_code: Optional[int] = None

    def _capture(fn: Callable[..., int]) -> Callable[..., None]:
        """Record the cmd_* return code without returning it into the Fire trace.

        Returning the int would make Fire print it and treat it as a further component to consume arguments against;
        returning ``None`` keeps the output clean and lets Fire finish its trace.

        """

        @functools.wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> None:
            nonlocal exit_code
            exit_code = fn(*args, **kwargs)

        return wrapper

    try:
        fire.Fire(
            {
                "check": _capture(cmd_check),
                "expiry": _capture(cmd_expiry),
                "policy": _capture(cmd_policy),
                "chains": _capture(cmd_chains),
                "all": _capture(cmd_all),
                "status": _capture(cmd_status),
            },
            command=argv,
        )
    except Exception as exc:  # SystemExit is BaseException — Fire's own exits pass through untouched
        # Prefix the exception type so an exception with an empty message still produces a non-blank stderr
        # line (bare ``str(exc)`` on such exceptions exited 1 with nothing printed).
        sys.exit(f"{type(exc).__name__}: {exc}")
    # exit_code stays None when no subcommand ran (bare `pydeprecate` help) → return normally (exit 0).
    if exit_code is not None:
        sys.exit(exit_code)
