"""Subcommand implementations for the CLI: ``check``, ``expiry``, ``policy``, ``chains``, ``all`` and ``status``."""

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from deprecate._cli.config import (
    _FROM_PYPROJECT,
    _Config,
    _ConfigBool,
    _ConfigFlag,
    _is_missing_packaging_import_error,
    _load_pydeprecate_config,
    _resolve_policy_spec,
    _validate_user_version,
)
from deprecate._cli.report import (
    _ensure_run_record,
    _GapKind,
    _print,
    _print_gap,
    _record_table,
    _Reporter,
)
from deprecate._cli.scan import _scan_for_command
from deprecate._pkg import _resolve_package_version, _safe_module_name
from deprecate.audit import (
    DeprecationWrapperInfo,
    PolicyRule,
    TableStyle,
    generate_deprecation_table,
)
from deprecate.audit._lifecycle import _check_expiry_for_callables, _parse_version
from deprecate.audit._policy import _build_policy_spec, _check_policy_for_callables
from deprecate.audit._wrappers import _format_subject

if TYPE_CHECKING:
    from deprecate.audit._policy import _PolicySpec


def _do_expiry_prescanned(wrappers: list[DeprecationWrapperInfo], version: Optional[str]) -> Optional[list[str]]:
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
        List of expired wrapper message strings (may be empty), or None when the check could not run
        (advisory — the reason is already printed to stderr).

    """
    if version is None:
        _print_gap(_GapKind.VERSION_UNRESOLVED, "Cannot check expiry: version not resolved. Pass --version explicitly.")
        return None
    try:
        expired = _check_expiry_for_callables(wrappers, version)
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
    _print_gap(
        _GapKind.PACKAGING_MISSING,
        f"The `packaging` library is required for the {skipped_rules} policy rule; skipping it.\n"
        "Install it with: `pip install 'pyDeprecate[audit]'`",
    )
    if not spec.message_required:
        return None
    # `message_required` needs no version parsing at all, so a spec with the grace-window rule switched off keeps
    # the version machinery — and with it the ImportError just handled — out of this second pass entirely.
    return _check_policy_for_callables(wrappers, _build_policy_spec(None, True))


# ---------------------------------------------------------------------------
# Subcommand functions
# ---------------------------------------------------------------------------


def cmd_check(
    path: str = ".",
    recursive: bool = True,
    exit_zero: bool = False,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    include_members: bool = True,
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
        include_members: Include deprecated class members such as methods and constructors (default True). Pass
            ``--noinclude-members`` to check module-level wrappers only.
        _wrappers: Pre-scanned wrapper list. When provided, skips the scan step. Underscore
            prefix hides this parameter from the Fire CLI (internal use by ``cmd_all`` only).

    Returns:
        0 on success or advisory-only issues; 1 when hard errors are found and ``exit_zero`` is False; 2 when
        ``--exclude`` is malformed or *path* is a file or a directory holding several packages.

    """
    if _wrappers is None:
        scan = _scan_for_command(path, recursive=recursive, exclude=exclude, include_members=include_members)
        if scan is None:
            return 2
        _wrappers = scan.wrappers

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
    include_members: bool = True,
    *,
    _wrappers: Optional[list[DeprecationWrapperInfo]] = None,
    _version_explicit: bool = True,
) -> int:
    """Check for deprecated wrappers that have passed their scheduled removal version.

    Requires the ``packaging`` library: ``pip install 'pyDeprecate[audit]'``.
    A missing ``packaging`` library is treated as advisory (returns 0 with a warning).

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for comparison (e.g. ``"2.0.0"``). Auto-detected from local
            project metadata, installed package metadata, or the top-level package's ``__version__`` if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        exit_zero: Always exit 0 even if expired wrappers are found.
            Useful for advisory CI steps that should report but never block.
        exclude: Module-name glob patterns to leave out of the scan — one pattern, a comma-separated string, or a
            list (``--exclude='my_package.tests,*._legacy*'``); the scan itself never imports a matching package
            nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the nearest
            ``pyproject.toml``, else nothing.
        include_members: Include deprecated class members such as methods and constructors (default True). Pass
            ``--noinclude-members`` to check module-level wrappers only.
        _wrappers: Pre-scanned wrapper list. When provided, skips the scan step and derives
            expired wrappers via ``_do_expiry_prescanned``. Underscore prefix hides this parameter from the
            Fire CLI (internal use by ``cmd_all`` only).
        _version_explicit: Whether *version* is a value the user typed rather than one the caller
            auto-detected. ``cmd_all`` forwards its already-resolved version and passes ``False``
            when that version came from auto-detection, so a malformed *installed* version is never
            reported as a malformed ``--version`` flag. Underscore prefix hides this parameter from
            the Fire CLI (internal use by ``cmd_all`` only).

    Returns:
        0 on success, when the ``packaging`` library is unavailable, when no version could be resolved (the gap
        is printed and the check is skipped), or when an auto-detected version turns out not to be valid
        PEP 440 (advisory — the check is skipped); 1 when expired wrappers are found and ``exit_zero`` is False;
        2 when a user-supplied ``--version`` is not a valid PEP 440 version string, ``--exclude`` is malformed, or
        *path* is a file or a directory holding several packages.

    """
    # Fire auto-converts numeric-looking strings (e.g. "1.0" → float); normalise to str.
    version_explicit = version is not None and _version_explicit
    if version is not None:
        version = str(version)
    err_code = _validate_user_version(version, explicit=_version_explicit)
    if err_code is not None:
        return err_code
    if _wrappers is None:
        # Standalone path: scan once, then the same derivation as the pre-scanned path, which reports an
        # unresolved version or missing `packaging` once.
        scan = _scan_for_command(
            path,
            recursive=recursive,
            exclude=exclude,
            include_members=include_members,
            version=version,
            version_explicit=version_explicit,
            resolve_version=True,
        )
        if scan is None:
            return 2
        _wrappers, version = scan.wrappers, scan.version
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
    message_required: _ConfigBool = _FROM_PYPROJECT,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    include_members: bool = True,
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
        include_members: Include deprecated class members such as methods and constructors (default True). Pass
            ``--noinclude-members`` to check module-level wrappers only.
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
        when a ``pyproject.toml`` within reach cannot be read or parsed, or when *path* is a file or a directory
        holding several packages.

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
        scan = _scan_for_command(
            path, recursive=recursive, exclude=exclude, config=config, include_members=include_members
        )
        if scan is None:
            return 2
        _wrappers = scan.wrappers
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
    include_members: bool = True,
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
        include_members: Include deprecated class members such as methods and constructors (default True). Pass
            ``--noinclude-members`` to check module-level wrappers only.
        _wrappers: Pre-scanned wrapper list. When provided, skips the scan step and filters
            for ``chain_type is not None`` internally. Underscore prefix hides this parameter
            from the Fire CLI (internal use by ``cmd_all`` only).

    Returns:
        0 when no chains are found or ``exit_zero`` is True; 1 when chains are found; 2 when ``--exclude`` is
        malformed or *path* is a file or a directory holding several packages.

    """
    if _wrappers is None:
        # every wrapper, not only chains: the run record needs them all for its table; the chain filter is below
        scan = _scan_for_command(path, recursive=recursive, exclude=exclude, include_members=include_members)
        if scan is None:
            return 2
        _wrappers = scan.wrappers
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
    output: Optional[str] = None,
    include_members: bool = True,
) -> int:
    """Run all four checks then append a deprecation table.

    Performs a single scan pass and distributes the wrappers to ``cmd_check``,
    ``cmd_expiry``, ``cmd_policy``, and ``cmd_chains`` so the filesystem is only traversed once.
    After all four checks complete, ``cmd_status`` is always called to append a
    compact markdown deprecation table to the output. The policy check runs in advisory mode here — its
    violations are printed but never change the aggregate exit code, because its defaults encode a project
    convention; run ``pydeprecate policy`` directly to gate on them.
    Version is auto-detected when not provided, exactly as for standalone ``expiry`` and ``status``.
    A missing ``packaging`` library skips the expiry check with a warning and does not
    count as a hard error.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for expiry comparison (e.g. ``"2.0.0"``). Auto-detected from local
            project metadata, installed package metadata, or the top-level package's ``__version__`` if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        exit_zero: Always exit 0 even if issues are found.
            Useful for advisory CI steps that should report but never block.
        exclude: Module-name glob patterns to leave out of the scan — one pattern, a comma-separated string, or a
            list (``--exclude='my_package.tests,*._legacy*'``); the scan itself never imports a matching package
            nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the nearest
            ``pyproject.toml``, else nothing.
        output: Optional file path the appended deprecation table is also written to, as with ``status --output``,
            so a CI job gets the table of the very scan it gated on. A failure to write it is reported on stderr as
            ``Could not write the deprecation table`` — apart from a failure to render the table — and never changes
            the exit code.
        include_members: Include deprecated class members such as methods and constructors (default True) in the one
            scan every check and the table share. Pass ``--noinclude-members`` to check module-level wrappers only.

    Returns:
        0 when the check, expiry, and chain gates pass or ``exit_zero`` is True; 1 when any of them finds a hard
        error; 2 for a usage error, which ``exit_zero`` never downgrades: a user-supplied ``--version`` that is
        not a valid PEP 440 version string, a malformed ``exclude`` (flag or file), a ``pyproject.toml`` within
        reach that cannot be read or parsed, a ``policy`` key in ``[tool.pydeprecate]`` that is not a table, a
        malformed rule value in ``[tool.pydeprecate.policy]``, or a *path* that is a file or a directory holding
        several packages.
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
    # ``output`` is not forwarded: ``cmd_status`` records the table it rendered and this function saves that, so a
    # failure to write the file is told apart from one to render the table.
    with _ensure_run_record() as run_record:
        try:
            cmd_status(
                path,
                version=resolved_version,
                recursive=recursive,
                output=None,
                _wrappers=wrappers,
                _version_explicit=version_explicit,
            )
        except Exception as exc:
            _print(f"Could not render the deprecation table: {exc}", stderr=True)
        else:
            if output is not None and run_record.table is not None:
                try:
                    _save_table(run_record.table, output)
                except OSError as exc:
                    _print(f"Could not write the deprecation table to {output}: {exc}", stderr=True)

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
    status as a Markdown table. Standalone — runs no checks, exits 0; 2 for a usage error (a malformed
    ``--exclude``, a file path, or a directory holding several packages).
    When ``--output`` is given, the table is also written to that file.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for lifecycle status (e.g. ``"2.0.0"``). Auto-detected from local
            project metadata, installed package metadata, or the top-level package's ``__version__`` if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        style: Table format — ``compact`` (default) or ``matrix``.
        include_members: Include deprecated class members such as methods and constructors (default True). Pass
            ``--noinclude-members`` to list module-level wrappers only.
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
        0 — status table generation is not a pass/fail gate; 2 only when ``--exclude`` is malformed or *path* is a
        file or a directory holding several packages.

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

    # Never resolve the importable module name eagerly: a plain directory without ``__init__.py`` has none, and
    # status still renders its table. ``_safe_module_name`` falls back to the raw path, used only as table metadata.
    module_name = _safe_module_name(path)
    if _wrappers is None:
        scan = _scan_for_command(
            path,
            recursive=recursive,
            exclude=exclude,
            include_members=include_members,
            version=version,
            version_explicit=version_explicit,
            resolve_version=True,
            record=False,
        )
        if scan is None:
            return 2
        _wrappers, resolved_version = scan.wrappers, scan.version
    else:
        resolved_version = version if version is not None else _resolve_package_version(module_name, path=path)
    markdown = generate_deprecation_table(
        module_name,
        current_version=resolved_version,
        style=table_style,
        _wrappers=_wrappers,
        _version_explicit=version_explicit,
    )
    _record_table(markdown)

    if _Reporter._HAS_RICH:
        from rich.markdown import Markdown

        _Reporter._console().print(Markdown(markdown))
    else:
        _print(markdown)

    if output is not None:
        _save_table(markdown, output)

    return 0
