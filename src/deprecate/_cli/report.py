"""Console output for the CLI: plain-text printing and the rich/plain ``_Reporter`` dispatch."""

import importlib.util
import sys
from typing import Any

from deprecate.audit import DeprecationWrapperInfo


def _is_package_available(name: str) -> bool:
    """Return True if *name* is importable without actually importing it."""
    return importlib.util.find_spec(name) is not None


# Starts of the "gap lines": stderr lines ``<kind>: <detail>`` the CLI prints when a run passes without auditing
# everything. CI wrappers (the GitHub Action) count the lines starting with each kind, so a gap line is written with
# plain ``print`` — never through Rich, which wraps long lines at the terminal width — and it does not depend on the
# warnings filters the audited package may install. Each kind stays one full literal: the Action's tests look every
# kind up verbatim in this source.

#: A module the scan could not import (or whose module-level code exited), so none of its wrappers were audited.
_GAP_MODULE_NOT_AUDITED = "pydeprecate gap: module not audited"
#: The package version could not be resolved, so removal deadlines were not checked.
_GAP_VERSION_UNRESOLVED = "pydeprecate gap: version not resolved"
#: The auto-detected package version is not a valid PEP 440 version, so removal deadlines were not checked.
_GAP_VERSION_INVALID = "pydeprecate gap: version not PEP 440"
#: The ``packaging`` library is missing, so version-based checks were skipped.
_GAP_PACKAGING_MISSING = "pydeprecate gap: packaging missing"
#: A plain-directory scan left the Python files in its subdirectories out.
_GAP_NESTED_FILES = "pydeprecate gap: nested files not audited"


def _print_gap(kind: str, detail: str) -> None:
    """Print one gap line ``<kind>: <detail>`` to stderr, unwrapped; ``detail`` may continue on further lines.

    Args:
        kind: One of the ``_GAP_*`` constants, which starts the line.
        detail: The human-readable message following the kind.

    """
    print(f"{kind}: {detail}", file=sys.stderr, flush=True)


def _print_skipped_modules(skipped: dict[str, str]) -> None:
    """Print one :data:`_GAP_MODULE_NOT_AUDITED` line per skipped module (``name: error repr``) to stderr."""
    for module_name, reason in skipped.items():
        _print_gap(_GAP_MODULE_NOT_AUDITED, f"{module_name}: {reason}")


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
