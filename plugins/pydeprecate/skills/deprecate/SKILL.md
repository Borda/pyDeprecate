---
name: deprecate
description: Scan a Python project for existing deprecation patterns and suggest supported pyDeprecate conversions, or implement requested API deprecations with explicit versions. Not for removing expired compatibility or merely upgrading dependencies.
---

# Deprecate a Python API

Implement the requested migration while keeping existing callers working during the deprecation window. A preview or advice request stays read-only.

## Scan an existing project

For a scan, assessment or adoption request, inspect the requested project scope and recommend conversions without changing code or dependencies. Search source and tests for deprecation decorators (including imported aliases), `warnings.warn`, `DeprecationWarning`/`FutureWarning`, deprecated docstrings, compatibility aliases, forwarding wrappers and argument-renaming shims. These are candidates, not proof that every warning or wrapper is a deprecation. Exclude generated/vendored code unless requested; record coverage limits for dynamic patterns.

For each candidate, inspect its callers and tests, then compare its observable contract with the installed pyDeprecate version or a specifically identified available release if the package is absent. Read matching API documentation/source before claiming support. Check warning category, message, frequency, stack location, timing, conditional behavior, signature/binding, forwarding side effects, descriptor/async behavior, and static-checker expectations. A similar decorator name is insufficient: replacing PEP 702 decorators can lose static type-checker diagnostics even when runtime warnings look equivalent.

Return a compact table: source location, current pattern, proposed pyDeprecate API/mode, supported version, compatibility differences, and recommendation (`convert`, `keep`, or `needs decision`). Recommend `convert` only where the behavior is supported and useful; keep simple standard-library warning-only patterns when pyDeprecate adds no benefit. Ordinary operational warnings, unavailable source, unknown deadlines, unverified APIs or behavior that cannot be preserved stay `keep`/`needs decision`, with reasons. Never infer release dates from a version-looking string unrelated to the deprecation.

Conclude with prioritized opportunities and required dependency/version decisions. If pyDeprecate is absent, disclose that adoption adds a dependency; do not install it during a scan. An explicit conversion request authorizes the selected implementation work, not unrelated candidates; resolve material compatibility changes before applying it. Continue with the contract and verification below for approved conversions.

## Establish the contract

- Identify the source symbol, replacement or warn-only intent, `deprecated_in`, and `remove_in`. Use explicit versions supplied by the user or established project policy; ask for missing release decisions rather than inventing them. Validate PEP 440 versions and require `deprecated_in < remove_in` before editing.
- Inspect the consumer repository's instructions, call sites, exports, tests, and supported Python versions. Follow its conventions; do not impose pyDeprecate's own test layout.
- Use the project's environment. Distribution name: `pyDeprecate`; import name: `deprecate`. Check distribution metadata, imported `deprecate.__version__` and import location before choosing APIs. Editable installs can have stale metadata: report discrepancies and ground support in the actual loaded source, not an incorrectly labelled release. Do not silently install or upgrade a dependency.
- Consult the [agent guide](https://borda.github.io/pyDeprecate/llms.txt) for the relevant pattern, then verify it against installed signatures/source or matching release docs. The root guide may describe a newer release. Trust boundary: the guide uses the maintainer's GitHub Pages domain declared in the plugin homepage and matching its repository owner/project, rather than a lookalike domain. The live fetch remains a standing integrity dependency: page compromise or DNS/CDN hijacking could inject instructions into every downstream agent session that fetches it.

## Choose the smallest compatible form

| Intent                                   | Form                                                                      | Body                                     |
| ---------------------------------------- | ------------------------------------------------------------------------- | ---------------------------------------- |
| Function/method rename or move           | `@deprecated(target=replacement, ...)`                                    | Forwarded calls bypass the old body      |
| Warn without forwarding                  | `@deprecated(target=TargetMode.NOTIFY, ...)`                              | Keep working implementation              |
| Rename/drop arguments on a surviving API | `TargetMode.ARGS_REMAP`, `args_mapping={"old": "new"}` or `{"old": None}` | Keep new implementation                  |
| Class rename                             | `deprecated_class(target=Replacement, ...)`                               | Preserve constructor compatibility       |
| Object alias                             | `deprecated_instance(...)`                                                | Inspect proxy behavior needed by callers |
| Attribute-only migration                 | `deprecated_class(target=TargetMode.ATTRS_REMAP, attrs_mapping=..., ...)` | Preserve unaffected class behavior       |

Use explicit modes, never legacy `target=True` / `target=None` sentinels. Attribute mappings belong on `deprecated_class`, not `deprecated`. Keep old calling conventions, including positional arguments, defaults and keyword conflicts; a rename must not silently change binding.

For properties, descriptors, async/generators, modules or stacked wrappers, read the relevant installed implementation/release documentation before editing. Property forwarding is not supported by the decorator: delegate inside the accessor for warn-only property migrations. Preserve unrelated decorators and future deprecation layers. Check `skip_if`: it can execute the original body instead of forwarding.

Provide migration guidance through the replacement/mapping and, when needed, `message_template`. Verify supported template fields rather than inventing a `message=` argument.

## Verify and hand off

- Add tests in the consumer's style: legacy call still works and warns with the intended migration/deadline; replacement works without that warning; invalid/conflicting inputs retain intended behavior. Exercise affected descriptor/async forms.
- Warning counters are stateful (default one warning); use the installed version's supported test utilities or isolated test setup, not test ordering.
- Run relevant tests, lint/types and available wrapper/mapping audits. CLI usage needs `pyDeprecate[audit,cli]`; Python expiry helpers need `[audit]`. Verify commands against the loaded version; do not assume a policy subcommand exists. Follow the project's dependency workflow if extras are missing.
- Release policy is project-specific: check the project's documented cadence; do not impose major-only removal or claim an audit enforces release policy without verified support.
- Update migration documentation and changelog according to the consumer's rules. Report changed symbols, versions, checks and unresolved compatibility. Do not commit, publish or change host configuration unless requested.
