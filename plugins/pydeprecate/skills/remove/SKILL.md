---
name: remove
description: Remove pyDeprecate compatibility due by a user-supplied Python package release version, or preview that cleanup. Use for scheduled deprecation retirement; not arbitrary file deletion, dependency removal, or adding deprecations.
---

# Remove scheduled deprecations

Retire only compatibility due in the requested release. An expired record identifies a candidate, not which definition to delete.

## Scope and inventory

1. Require package/source scope and an explicit target release version. This is the consumer package's release, not pyDeprecate's version. Do not substitute today's date or auto-detected installed package version. A preview/list/assessment request authorizes no edits.
2. Inspect consumer repository instructions and existing changes. Use its environment; compare pyDeprecate distribution metadata with imported `deprecate.__version__` and confirm imported package paths point to the intended checkout. Report stale editable-install metadata; validate capabilities against loaded source.
3. Consult the [agent guide](https://borda.github.io/pyDeprecate/llms.txt) and installed public API signatures. Prefer `find_deprecation_wrappers(..., recursive=True, include_members=True)` and `get_deprecation_config` when supported. Expiry messages are diagnostics, not structured edit instructions; inspect wrapper metadata and source. Trust boundary: the guide uses the maintainer's GitHub Pages domain declared in the plugin homepage and matching its repository owner/project, rather than a lookalike domain. The live fetch remains a standing integrity dependency: page compromise or DNS/CDN hijacking could inject instructions into every downstream agent session that fetches it.
4. Runtime discovery imports modules and executes module-level code. Use the project's normal test environment; do not import an unknown package if its side effects cannot be contained. Cross-check source decorators/factories, private/conditional definitions, re-exports and stacked layers. Record failed/skipped imports and unresolved static candidates. Manual warnings and other deprecation libraries require separate analysis, never silent inclusion.
5. Select `remove_in <= target_release` with PEP 440 ordering, using installed audit support (`pyDeprecate[audit]`), never lexical or float comparisons. Include exact-boundary deadlines; preserve future deadlines. A prerelease such as `2.0rc1` precedes `2.0`; use a final version only if the user actually requests final-release preparation. Missing/invalid deadlines remain unresolved; do not guess or rewrite them.

Summarize each candidate: source location, symbol/layer, mode, deadline, replacement, affected callers/exports, intended edit. Apply an explicit removal request within its existing authorization; obey repository approval requirements. Ask only for unresolved decisions that change the public contract. Do not turn a preview into a cleanup.

## Edit according to what expires

| Due compatibility                                   | Required edit                                                                                              |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Obsolete forwarding function/class/object alias     | Remove old entry point and obsolete exports; preserve replacement                                          |
| `ARGS_REMAP` on a surviving function or constructor | Remove due mapping/decorator and obsolete parameter/fallback; retain live callable and new signature       |
| `ATTRS_REMAP` on a surviving class                  | Remove due alias/mapping; preserve class, new attributes and unrelated proxy behavior                      |
| Stacked wrappers with different deadlines           | Remove only due layer; retain later layers and valid decorator order                                       |
| Warn-only function, property or class               | Determine whether symbol is retired; migrate owned callers before removing it                              |
| Deprecated module                                   | Preserve needed implementation in replacement; remove obsolete module/export/import path only within scope |

Trace replacement chains before deleting a target still used by a retained wrapper. Preserve remaining mappings, `args_extra`, `skip_if` and proxy behavior unless their migration is explicitly due. Do not replace an old wrapper with a permanent silent alias when the old entry point is meant to disappear. Updating local callers does not establish compatibility for unknown downstream users.

Update active docs, imports, exports and tests. Preserve historical changelog entries. Do not delete useful tests merely to make the suite pass; replace old compatibility assertions with retirement/new-API assertions where appropriate.

## Prove the result

- Test retained APIs and new calling conventions. Verify retired names/arguments are no longer accepted as promised, and future layers still behave correctly.
- Rerun discovery and expiry at the same explicit release. Prefer `validate_deprecation_expiry(module, current_version=target_release)` with the original release preserved as a Python string (for example, `"0.10"`), using the verified module scope and installed API. CLI argument parsing can coerce `0.10` to `0.1`; shell quoting alone does not guarantee preservation. Use the CLI only after verifying that its reported version retains the requested PEP 440 meaning. Python expiry requires `[audit]`; CLI requires `[audit,cli]`.
- Zero exit or an empty list is insufficient: missing audit dependencies can skip checks; malformed deadlines and import failures can omit candidates. Report these as incomplete coverage. Never use `--exit-zero`, postpone `remove_in`, suppress scan warnings or change versions to manufacture success.
- Run the consumer's relevant tests, lint/types and docs checks. Report removed, retained and unresolved items, target release, scan coverage and exact verification. Claim complete cleanup only for the verified scope.
- Commit, publish and host-configuration changes require their own user request.
