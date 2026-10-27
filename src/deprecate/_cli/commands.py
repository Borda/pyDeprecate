"""Subcommand implementations for the CLI: ``check``, ``expiry``, ``policy``, ``chains``, ``all`` and ``status``."""

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from deprecate._cli._pkg import _safe_module_name
from deprecate._cli.config import (
    _FROM_PYPROJECT,
    _ConfigBool,
    _ConfigFlag,
    _is_missing_packaging_import_error,
    _load_pydeprecate_config,
    _resolve_policy_spec,
    _validate_user_version,
)
from deprecate._cli.findings import _exit_code
from deprecate._cli.report import (
    _ensure_run_record,
    _GapKind,
    _print,
    _print_gap,
    _record_findings,
    _record_table,
    _Reporter,
)
from deprecate._cli.scan import _scan_for_command
from deprecate.audit import (
    DeprecationWrapperInfo,
    PolicyRule,
    TableStyle,
    generate_deprecation_table,
)
from deprecate.audit._findings import _Finding, _Rule, _wrapper_findings
from deprecate.audit._lifecycle import _expiry_findings, _parse_version
from deprecate.audit._policy import _build_policy_spec, _policy_findings
from deprecate.audit._wrappers import _format_subject

if TYPE_CHECKING:
    from deprecate.audit._policy import _PolicySpec


def _do_expiry_prescanned(wrappers: list[DeprecationWrapperInfo], version: Optional[str]) -> Optional[list[_Finding]]:
    """Derive expired wrapper messages from an already-scanned wrapper list, without rescanning.

    Used by both ``cmd_expiry`` and ``cmd_all`` after their single scan. Every way this can fail is advisory: an
    explicit ``--version`` is validated up front (exit 2), so only an auto-detected version reaches here, and
    neither an unresolvable nor an unparsable one is a usage error the user can be blamed for, so the expiry gate
    is skipped with a message instead of failing the run.

    A wrapper whose own ``remove_in`` is not PEP 440 is skipped by the check, which only warns about it. This
    function also records each such wrapper as a :attr:`_GapKind.REMOVE_IN_INVALID` gap, since the warning can be
    silenced by the audited package; the expired list and the exit code are unchanged.

    Args:
        wrappers: Pre-scanned wrapper list to compare against *version*.
        version: Resolved package version, or None when it could not be resolved at all.

    Returns:
        One ``expired`` finding per overdue wrapper (may be empty), or None when the check could not run
        (advisory — the reason is already printed to stderr).

    """
    if version is None:
        _print_gap(_GapKind.VERSION_UNRESOLVED, "Cannot check expiry: version not resolved. Pass --version explicitly.")
        return None
    try:
        expired = _expiry_findings(wrappers, version)
        # Reached only when the check itself ran, so an unusable `version` is reported once (below) and not again per
        # wrapper. The audit layer only warns about a `remove_in` it skips; the gap is recorded here so it stays free
        # of CLI imports.
        for info in wrappers:
            remove_in = info.deprecated_info.remove_in
            if not remove_in:
                continue
            try:
                _parse_version(remove_in)
            except ValueError:
                _print_gap(
                    _GapKind.REMOVE_IN_INVALID,
                    f"{_format_subject(info)} has `remove_in` `{remove_in}`, which is not a valid PEP 440 version "
                    "string, so its removal deadline was not checked.",
                )
        return expired
    except ImportError:
        _print_gap(
            _GapKind.PACKAGING_MISSING,
            "The 'expiry' subcommand requires the 'packaging' library.\n"
            "Install it with: `pip install 'pyDeprecate[audit]'`",
        )
        return None
    except ValueError:
        # Only an auto-detected version reaches here — an explicit one is rejected up front with exit 2.
        # A package whose own metadata version is not PEP 440 is not a usage error, so the check degrades
        # to an advisory skip rather than aborting the run with an unhandled parse failure.
        _print_gap(
            _GapKind.VERSION_INVALID,
            f"Cannot check expiry: the auto-detected version `{version}` is not a valid PEP 440 version "
            "string. Pass `--version` explicitly for a definitive check.",
        )
        return None


def _save_table(markdown: str, output: str) -> None:
    """Write the rendered deprecation table to *output* (missing parent directories are created) and say so on stderr.

    Args:
        markdown: The rendered markdown table.
        output: File path to write.

    Raises:
        OSError: When the directory cannot be created or the file cannot be written.

    """
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    _print(f"→ Saved to: {out_path}", stderr=True)


def _skipped_policy_rules(spec: "_PolicySpec") -> list[str]:
    """Return the CLI-facing slugs of the version-dependent policy rules *spec* has enabled.

    These are exactly the rules that cannot run without the ``packaging`` library — used to name them in the advisory
    printed when ``packaging`` turns out to be unavailable. Today ``min-grace`` is the only such rule, so the list holds
    at most one entry.

    """
    return [PolicyRule.MIN_GRACE.value] if spec.grace is not None else []


def _policy_violations_without_packaging(
    wrappers: list[DeprecationWrapperInfo], spec: "_PolicySpec"
) -> Optional[list[_Finding]]:
    """Handle a missing-``packaging`` failure from :func:`_policy_findings`.

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
    _print_gap(
        _GapKind.PACKAGING_MISSING,
        f"The `packaging` library is required for the {skipped_rules} policy rule; skipping it.\n"
        "Install it with: `pip install 'pyDeprecate[audit]'`",
    )
    if not spec.message_required:
        return None
    # `message_required` needs no version parsing at all, so a spec with the grace-window rule switched off keeps
    # the version machinery — and with it the ImportError just handled — out of this second pass entirely.
    return _policy_findings(wrappers, _build_policy_spec(None, True))


# ---------------------------------------------------------------------------
# Subcommand functions
# ---------------------------------------------------------------------------


def cmd_check(
    path: str = ".",
    recursive: bool = True,
    exit_zero: bool = False,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    include_members: bool = True,
) -> int:
    """Scan Python code for misconfigured ``@deprecated`` wrappers and deprecation chains.

    Reports invalid argument mappings, misconfigured targets and inner-order ``@property @deprecated`` wrappers
    (errors, exit 1), and identity mappings, no-effect wrappers, deprecation chains and ``args_mapping`` entries
    targeting POSITIONAL_ONLY constructor parameters (warnings only, exit 0). Use the ``chains`` subcommand for a
    dedicated hard-error chain check.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        recursive: Scan submodules recursively (default True). Pass ``--no-recursive`` to scan top-level only.
        exit_zero: Always exit 0 even if hard errors (invalid argument mappings) are found.
            Useful for advisory CI steps that should report but never block.
        exclude: Module-name glob patterns to leave out of the scan — one pattern or a comma-separated string
            (``--exclude='my_package.tests,*._legacy*'``), or a list in a direct call; the scan itself never imports
            a matching package nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the
            nearest ``pyproject.toml``, else nothing.
        include_members: Include deprecated class members such as methods and constructors (default True). Pass
            ``--no-include-members`` to check module-level wrappers only.

    Returns:
        0 on success or warnings only; 1 when an error is found and ``exit_zero`` is False; 2 for a usage error: a
        malformed ``--exclude``, or a *path* that is a file, a directory holding several packages, or neither an
        existing path nor an importable module. Raises ``_ScanError`` (exit 3) when the scanned package itself cannot be
        imported.

    """
    scan = _scan_for_command(path, recursive=recursive, exclude=exclude, include_members=include_members)
    if scan is None:
        return 2
    return _check_wrappers(scan.wrappers, exit_zero=exit_zero)


def _check_wrappers(wrappers: list[DeprecationWrapperInfo], *, exit_zero: bool = False) -> int:
    """Report the configuration findings of already-scanned wrappers and return the ``check`` exit code.

    The evaluation half of :func:`cmd_check`, which ``cmd_all`` runs on the wrappers of its single scan.

    """
    if not wrappers:
        _print("No deprecated callables found.")
        return 0

    if _Reporter.issues(wrappers, error_on_chains=False):
        _print("\nIssues were found in deprecated wrappers.")
    else:
        _print("\nAll deprecated wrappers look correct!")

    findings = [finding for info in wrappers for finding in _wrapper_findings(info)]
    _record_findings(findings)
    return _exit_code(findings, "check", exit_zero=exit_zero)


def cmd_expiry(
    path: str = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    exit_zero: bool = False,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    include_members: bool = True,
) -> int:
    """Check for deprecated wrappers that have passed their scheduled removal version.

    Requires the ``packaging`` library: ``pip install 'pyDeprecate[audit]'``.
    A missing ``packaging`` library is treated as advisory (returns 0 with a warning).

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for comparison (e.g. ``"2.0.0"``). Auto-detected from local
            project metadata, installed package metadata, or the top-level package's ``__version__`` if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--no-recursive`` to scan top-level only.
        exit_zero: Always exit 0 even if expired wrappers are found.
            Useful for advisory CI steps that should report but never block.
        exclude: Module-name glob patterns to leave out of the scan — one pattern or a comma-separated string
            (``--exclude='my_package.tests,*._legacy*'``), or a list in a direct call; the scan itself never imports
            a matching package nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the
            nearest ``pyproject.toml``, else nothing.
        include_members: Include deprecated class members such as methods and constructors (default True). Pass
            ``--no-include-members`` to check module-level wrappers only.

    Returns:
        0 on success, when the ``packaging`` library is unavailable, when no version could be resolved (the gap is
        printed and the check is skipped), or when an auto-detected version turns out not to be valid PEP 440 (advisory
        — the check is skipped); 1 when expired wrappers are found and ``exit_zero`` is False; 2 for a usage error: a
        user-supplied ``--version`` that is not a valid PEP 440 version string, a malformed ``--exclude``, or a *path*
        that is a file, a directory holding several packages, or neither an existing path nor an importable module.
        Raises ``_ScanError`` (exit 3) when the scanned package itself cannot be imported.

    """
    err_code = _validate_user_version(version)
    if err_code is not None:
        return err_code
    scan = _scan_for_command(
        path,
        recursive=recursive,
        exclude=exclude,
        include_members=include_members,
        version=version,
        version_explicit=version is not None,
        resolve_version=True,
    )
    if scan is None:
        return 2
    return _expiry_wrappers(scan.wrappers, scan.version, exit_zero=exit_zero)


def _expiry_wrappers(wrappers: list[DeprecationWrapperInfo], version: Optional[str], *, exit_zero: bool = False) -> int:
    """Report the already-scanned wrappers past their removal deadline and return the ``expiry`` exit code.

    The evaluation half of :func:`cmd_expiry`, which ``cmd_all`` runs on the wrappers and the version of its single
    scan. *version* is never validated here: a typed ``--version`` was validated before the scan, and an auto-detected
    one that is not PEP 440 is an advisory skip, not a usage error.

    """
    expired = _do_expiry_prescanned(wrappers, version)
    if expired is None:  # check could not run — advisory already printed to stderr
        return 0
    _record_findings(expired)
    if not expired:
        _print("No expired deprecated wrappers found.")
        return 0
    _Reporter.expiry([finding.message for finding in expired])
    _print(f"\n{len(expired)} expired wrapper(s) found.")
    return _exit_code(expired, "expiry", exit_zero=exit_zero)


def cmd_policy(
    path: str = ".",
    recursive: bool = True,
    exit_zero: bool = False,
    min_grace: _ConfigFlag = _FROM_PYPROJECT,
    message_required: _ConfigBool = _FROM_PYPROJECT,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    include_members: bool = True,
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
        recursive: Scan submodules recursively (default True). Pass ``--no-recursive`` to scan top-level only.
        exit_zero: Always exit 0 even if violations are found.
            Useful for advisory CI steps that should report but never block.
        min_grace: Minimum distance between ``deprecated_in`` and ``remove_in`` as a version-shaped delta with
            two or three components — ``1.0`` one major, ``0.3`` three minors, ``0.0.2`` two patches (a bare
            ``1`` is rejected as ambiguous) — or, in ``pyproject.toml`` and a direct call only, a one-key unit table
            (``{"minor": 3}``); built-in default ``0.3``, ``None`` skips the rule. The removal must be one clean
            bump of a single component (``1.2`` → ``1.5`` or ``2.0``, never ``2.3``); a coarser bump always clears
            a finer window.
        message_required: Require every wrapper to name a replacement (built-in default True).
        exclude: Module-name glob patterns to leave out of the scan — one pattern or a comma-separated string
            (``--exclude='my_package.tests,*._legacy*'``), or a list in a direct call; the scan itself never imports
            a matching package nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the
            nearest ``pyproject.toml``, else nothing.
        include_members: Include deprecated class members such as methods and constructors (default True). Pass
            ``--no-include-members`` to check module-level wrappers only.

    Returns:
        0 on success, or when ``min-grace`` is skipped because ``packaging`` is unavailable and ``message_required``
        finds no violation; 1 when violations are found (including from a still-running ``message_required`` check) and
        ``exit_zero`` is False; 2 when a rule's value or ``exclude`` — from a flag or from ``pyproject.toml`` — is
        malformed, when ``policy`` in ``[tool.pydeprecate]`` is not a table, when a ``pyproject.toml`` within reach
        cannot be read or parsed, or a *path* that is a file, a directory holding several packages, or neither an
        existing path nor an importable module. Raises ``_ScanError`` (exit 3) when the scanned package itself cannot be
        imported.

    """
    config = _load_pydeprecate_config(path)
    if config is None:
        return 2
    resolved = _resolve_policy_spec(
        path, {PolicyRule.MIN_GRACE.value: min_grace, PolicyRule.MESSAGE_REQUIRED.value: message_required}, config
    )
    if resolved is None:
        return 2
    scan = _scan_for_command(path, recursive=recursive, exclude=exclude, config=config, include_members=include_members)
    if scan is None:
        return 2
    spec, policy_header = resolved
    return _policy_wrappers(scan.wrappers, spec, policy_header, exit_zero=exit_zero)


def _policy_wrappers(
    wrappers: list[DeprecationWrapperInfo],
    spec: "_PolicySpec",
    policy_header: str,
    *,
    exit_zero: bool = False,
    advisory: bool = False,
) -> int:
    """Report the policy violations of already-scanned wrappers and return the ``policy`` exit code.

    The evaluation half of :func:`cmd_policy`, which ``cmd_all`` runs on the wrappers of its single scan.

    Args:
        wrappers: The scanned wrappers.
        spec: The resolved policy rules.
        policy_header: The ``Policy:`` line naming each rule value and its source.
        exit_zero: Exit 0 even when violations are found.
        advisory: Render violations as a yellow ``[WARNING]`` with a trailer pointing at the ``policy``
            subcommand, instead of the red ``[ERROR]`` of a gating run. ``cmd_all`` reports policy violations
            without failing on them, and the output has to say so. Never inferred from *exit_zero*, which
            downgrades the exit code without changing what the violations mean.

    """
    _print(policy_header)
    try:
        violations = _policy_findings(wrappers, spec)
    except ImportError as exc:
        if not _is_missing_packaging_import_error(exc):
            raise
        fallback = _policy_violations_without_packaging(wrappers, spec)
        if fallback is None:
            return 0
        violations = fallback
    _record_findings(violations)
    if not violations:
        _print("No deprecation policy violations found.")
        return 0
    _Reporter.policy([finding.message for finding in violations], advisory=advisory)
    _print(f"\n{len(violations)} policy violation(s) found.")
    if advisory:
        _print("(advisory here — run `pydeprecate policy` to gate on it)")
    return _exit_code(violations, "policy", exit_zero=exit_zero)


def cmd_chains(
    path: str = ".",
    recursive: bool = True,
    exit_zero: bool = False,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    include_members: bool = True,
) -> int:
    """Detect deprecated wrappers whose ``target`` is itself a deprecated callable (chains).

    Two chain kinds are detected: ``target`` (forwarding chain to another deprecated
    callable) and ``stacked`` (composed argument mappings that should be collapsed).

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        recursive: Scan submodules recursively (default True). Pass ``--no-recursive`` to scan top-level only.
        exit_zero: Always exit 0 even if chains are found.
            Useful for advisory CI steps that should report but never block.
        exclude: Module-name glob patterns to leave out of the scan — one pattern or a comma-separated string
            (``--exclude='my_package.tests,*._legacy*'``), or a list in a direct call; the scan itself never imports
            a matching package nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the
            nearest ``pyproject.toml``, else nothing.
        include_members: Include deprecated class members such as methods and constructors (default True). Pass
            ``--no-include-members`` to check module-level wrappers only.

    Returns:
        0 when no chains are found or ``exit_zero`` is True; 1 when chains are found; 2 for a usage error: a malformed
        ``--exclude``, or a *path* that is a file, a directory holding several packages, or neither an existing path nor
        an importable module. Raises ``_ScanError`` (exit 3) when the scanned package itself cannot be imported.

    """
    # every wrapper, not only chains: the run record needs them all for its table; the chain filter is below
    scan = _scan_for_command(path, recursive=recursive, exclude=exclude, include_members=include_members)
    if scan is None:
        return 2
    return _chain_wrappers(scan.wrappers, exit_zero=exit_zero)


def _chain_wrappers(wrappers: list[DeprecationWrapperInfo], *, exit_zero: bool = False) -> int:
    """Report the deprecation chains among already-scanned wrappers and return the ``chains`` exit code.

    The evaluation half of :func:`cmd_chains`, which ``cmd_all`` runs on the wrappers of its single scan.

    """
    chains = [r for r in wrappers if r.chain_type is not None]
    findings = [finding for info in chains for finding in _wrapper_findings(info) if finding.rule is _Rule.CHAIN]
    _record_findings(findings)
    if not chains:
        _print("No deprecation chains found.")
        return 0
    _Reporter.chains(chains, error=True)
    _print(f"\n{len(chains)} deprecation chain(s) found.")
    return _exit_code(findings, "chains", exit_zero=exit_zero)


def cmd_all(
    path: str = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    exit_zero: bool = False,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    output: Optional[str] = None,
    include_members: bool = True,
) -> int:
    """Run all four checks then append a deprecation table.

    Performs a single scan pass and hands the wrappers to the checks of ``check``, ``expiry``, ``policy`` and
    ``chains`` so the filesystem is only traversed once. After all four checks complete, the compact markdown
    deprecation table of ``status`` is always appended to the output. The policy check runs in advisory mode here — its
    violations are printed but never change the aggregate exit code, because its defaults encode a project
    convention; run ``pydeprecate policy`` directly to gate on them.
    Version is auto-detected when not provided, exactly as for standalone ``expiry`` and ``status``.
    A missing ``packaging`` library skips the expiry check with a warning and does not
    count as a hard error.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for expiry comparison (e.g. ``"2.0.0"``). Auto-detected from local
            project metadata, installed package metadata, or the top-level package's ``__version__`` if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--no-recursive`` to scan top-level only.
        exit_zero: Always exit 0 even if issues are found.
            Useful for advisory CI steps that should report but never block.
        exclude: Module-name glob patterns to leave out of the scan — one pattern or a comma-separated string
            (``--exclude='my_package.tests,*._legacy*'``), or a list in a direct call; the scan itself never imports
            a matching package nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the
            nearest ``pyproject.toml``, else nothing.
        output: Optional file path the appended deprecation table is also written to, as with ``status --output``,
            so a CI job gets the table of the very scan it gated on. A failure to write it is reported on stderr as
            ``Could not write the deprecation table`` — apart from a failure to render the table — and never changes
            the exit code.
        include_members: Include deprecated class members such as methods and constructors (default True) in the one
            scan every check and the table share. Pass ``--no-include-members`` to check module-level wrappers only.

    Returns:
        0 when the check, expiry, and chain gates pass or ``exit_zero`` is True; 1 when any of them finds an error (one
        exit code computed from all their findings); 2 for a usage error, which ``exit_zero`` never downgrades: a
        user-supplied ``--version`` that is not a valid PEP 440 version string, a malformed ``exclude`` (flag or file),
        a ``pyproject.toml`` within reach that cannot be read or parsed, a ``policy`` key in ``[tool.pydeprecate]`` that
        is not a table, a malformed rule value in ``[tool.pydeprecate.policy]``, or a *path* that is a file, a directory
        holding several packages, or neither an existing path nor an importable module. Raises ``_ScanError`` (exit 3)
        when the scanned package itself cannot be imported. Policy violations are advisory here and never contribute to
        this code.

    """
    err_code = _validate_user_version(version)
    if err_code is not None:
        return err_code
    config = _load_pydeprecate_config(path)
    if config is None:
        return 2
    # a malformed `[tool.pydeprecate.policy]` value is a usage error, reported before the package is imported
    policy_flags: dict[str, _ConfigFlag] = {
        rule.value: _FROM_PYPROJECT for rule in (PolicyRule.MIN_GRACE, PolicyRule.MESSAGE_REQUIRED)
    }
    policy = _resolve_policy_spec(path, policy_flags, config)
    if policy is None:
        return 2
    version_explicit = version is not None
    scan = _scan_for_command(
        path,
        recursive=recursive,
        exclude=exclude,
        config=config,
        include_members=include_members,
        version=version,
        version_explicit=version_explicit,
        resolve_version=True,
        record=False,
    )
    if scan is None:
        return 2
    wrappers, resolved_version = scan.wrappers, scan.version

    # The checks record their findings and their exit codes are not used: the exit code of ``all`` is computed
    # once below from those findings, at the levels ``all`` gives them (policy violations are warnings here).
    # ``resolved_version`` may be auto-detected; only a typed one was validated, so a package stamped with a
    # non-PEP 440 version skips the expiry check with an advisory instead of failing as an invalid ``--version``.
    with _ensure_run_record() as run_record:
        first_finding = len(run_record.findings)
        _check_wrappers(wrappers)
        _expiry_wrappers(wrappers, resolved_version)
        # Advisory inside ``all``: the policy defaults encode a project convention (a three-minor grace window on a
        # clean release boundary) that not every repo shares, so ``all`` reports violations but never fails on
        # them — gate on them with the dedicated ``policy`` subcommand.
        _policy_wrappers(wrappers, *policy, advisory=True)
        _chain_wrappers(wrappers)
        findings = run_record.findings[first_finding:]

        # The status table is a display artifact appended after the gates. Render it defensively: a
        # table-rendering failure must never change the exit code the gates produced. The table is recorded as it
        # is rendered and saved from that record, so a failure to write the file is told apart from one to render.
        try:
            _status_table(
                wrappers, path, resolved_version, style=TableStyle("compact"), version_explicit=version_explicit
            )
        except Exception as exc:
            _print(f"Could not render the deprecation table: {exc}", stderr=True)
        else:
            if output is not None and run_record.table is not None:
                try:
                    _save_table(run_record.table, output)
                except OSError as exc:
                    _print(f"Could not write the deprecation table to {output}: {exc}", stderr=True)

    return _exit_code(findings, "all", exit_zero=exit_zero)


def cmd_status(
    path: str = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    style: str = "compact",
    include_members: bool = True,
    output: Optional[str] = None,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
) -> int:
    """Print a markdown deprecation status table to stdout.

    Scans the target package for deprecated wrappers and renders their lifecycle status as a Markdown table.
    Standalone — runs no checks and never fails on findings. When ``--output`` is given, the table is also written
    to that file.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for lifecycle status (e.g. ``"2.0.0"``). Auto-detected from local
            project metadata, installed package metadata, or the top-level package's ``__version__`` if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--no-recursive`` to scan top-level only.
        style: Table format — ``compact`` (default) or ``matrix``.
        include_members: Include deprecated class members such as methods and constructors (default True). Pass
            ``--no-include-members`` to list module-level wrappers only.
        output: Optional file path to write the markdown table. The table is always
            printed to stdout regardless of this flag.
        exclude: Module-name glob patterns to leave out of the scan — one pattern or a comma-separated string
            (``--exclude='my_package.tests,*._legacy*'``), or a list in a direct call; the scan itself never imports
            a matching package nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the
            nearest ``pyproject.toml``, else nothing.

    Returns:
        0 once the table is rendered — status is not a pass/fail gate; 2 for a usage error: a user-supplied
        ``--version`` that is not a valid PEP 440 version string, a ``--style`` other than ``compact`` or ``matrix``, a
        malformed ``--exclude``, or a *path* that is a file, a directory holding several packages, or neither an
        existing path nor an importable module. Raises ``_ScanError`` (exit 3) when the scanned package itself cannot be
        imported.

    """
    err_code = _validate_user_version(version)
    if err_code is not None:
        return err_code
    try:
        table_style = TableStyle(style)
    except ValueError:
        valid = ", ".join(s.value for s in TableStyle)
        _print(f"Invalid `--style` {style!r}; expected one of: {valid}.", stderr=True)
        return 2

    scan = _scan_for_command(
        path,
        recursive=recursive,
        exclude=exclude,
        include_members=include_members,
        version=version,
        version_explicit=version is not None,
        resolve_version=True,
        record=False,
    )
    if scan is None:
        return 2
    markdown = _status_table(scan.wrappers, path, scan.version, style=table_style, version_explicit=version is not None)
    if output is not None:
        _save_table(markdown, output)
    return 0


def _status_table(
    wrappers: list[DeprecationWrapperInfo],
    path: str,
    version: Optional[str],
    *,
    style: TableStyle,
    version_explicit: bool,
) -> str:
    """Render, record and print the deprecation table of already-scanned wrappers, and return it.

    The rendering half of :func:`cmd_status`, which ``cmd_all`` runs on the wrappers and the version of its single scan.
    *version_explicit* tells a typed version from an auto-detected one, so a package stamped with a non-PEP 440 version
    still renders its table (unparsed) instead of aborting it.

    """
    # Never resolve the importable module name eagerly: a plain directory without ``__init__.py`` has none, and
    # status still renders its table. ``_safe_module_name`` falls back to the raw path, used only as table metadata.
    markdown = generate_deprecation_table(
        _safe_module_name(path),
        current_version=version,
        style=style,
        _wrappers=wrappers,
        _version_explicit=version_explicit,
    )
    _record_table(markdown)
    _print(markdown)
    return markdown
