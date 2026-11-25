---
description: Coding-agent guide for pyDeprecate imports, migration recipes, anti-patterns, and links to llms.txt and llms-full.txt.
---

# Coding Agent Recipes

This page is for coding assistants that need to add a correct deprecation cycle to Python code.

## Install the coding-agent plugin

The repository contains a `pydeprecate` plugin with the same skills for Codex and Claude Code. It is available from the repository's default branch (early, pre-1.0). It is separate from the `pyDeprecate` library, and `pip` does not install agent skills.

Install from GitHub:

```bash
# Codex
codex plugin marketplace add Borda/pyDeprecate
codex plugin add pydeprecate@pydeprecate

# Claude Code
claude plugin marketplace add Borda/pyDeprecate
claude plugin install pydeprecate@pydeprecate
```

For local development or unpublished changes, run these commands from the repository root with `.` instead of `Borda/pyDeprecate` in the marketplace-add command. The plugin-install command stays the same.

The plugin provides `$pydeprecate:sunset`, `$pydeprecate:audit` and `$pydeprecate:prune` in Codex, and `/pydeprecate:sunset`, `/pydeprecate:audit` and `/pydeprecate:prune` in Claude Code.

| Request                            | Required inputs                                                           | Example                                                                      |
| ---------------------------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `sunset` implements a deprecation. | It requires package or module scope plus `deprecated_in` and `remove_in`. | “Sunset `parse_config` in `acme.parsers`, deprecated_in=1.4, remove_in=2.0.” |
| `audit` checks existing wrappers.  | It requires package or module scope only.                                 | “Audit the deprecations in `acme.parsers` and list what is misconfigured.”   |
| `prune` removes compatibility.     | It requires scope plus the target release only.                           | “Prune compatibility due by target release 2.0 from `acme.parsers`.”         |

Preview remains read-only. A removal plan requires an explicit release deadline: `remove_in <= target release` under PEP 440.

!!! note "Release-candidate preparation"

    A plan also includes a `remove_in` whose base release equals an explicitly requested RC target (`2.0rc1` for `remove_in="2.0"`). This applies only when the user is preparing that final release.

The plan keeps surviving functions or classes available for argument/attribute compatibility, and leaves incomplete scans unresolved. Use the consumer environment's existing `pyDeprecate`; expiry, policy and `all` audits need `[audit]`; the other audits do not.

The plugin source lives under `plugins/pydeprecate/`: `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`, `skills/sunset/SKILL.md`, `skills/audit/SKILL.md`, and `skills/prune/SKILL.md`.

For adoption scans, ask:

> “Scan `src/acme` for deprecation decorators, warnings, aliases, and shims; suggest supported pyDeprecate conversions without editing.”

The scan verifies installed or explicitly identified release support and returns `convert`, `keep`, or `needs decision`. It does not force dependencies or convert operational warnings. PEP 702 static-checker behavior is not equivalent merely because runtime metadata looks similar.

For audits of deprecations a project already declares, ask:

> “Audit `src/acme` with pyDeprecate and list what is misconfigured, chained, expired or badly scheduled; do not edit.”

The audit skill runs the CLI audits, reads every gap, and returns findings by rule with their source location. It edits only after you confirm a fix list, and it adds pre-commit, GitHub Action or pytest gates only when asked. A typical run:

```bash
pydeprecate all src/acme --version 2.0 --gate=policy --strict --report-json audit.json
```

Exit `0` with open gaps is incomplete coverage, not a clean audit. Without the package's runtime dependencies, `pydeprecate expiry --static` and `pydeprecate policy --static` read the source without importing it; see the [CLI static mode](../audit/cli.md#static-mode).

## Rules for agents

- Install package: `pyDeprecate`.
- Import package: `deprecate`.
- Prefer `@deprecated(target=new_callable)` for callable renames.
- Prefer `TargetMode.ARGS_REMAP` for argument renames or removals.
- Prefer `deprecated_class` for class renames.
- Prefer `deprecated_instance` for object aliases.
- Always include `deprecated_in`, `remove_in`, and a migration message when available.

## Function rename

```python
from deprecate import deprecated


def detect_objects(value: int) -> int:
    return value + 1


@deprecated(target=detect_objects, deprecated_in="1.2", remove_in="2.0")
def detect(value: int) -> int:
    pass  # body never runs — pyDeprecate intercepts all calls before reaching here
```

## Argument rename

```python
from deprecate import TargetMode, deprecated


@deprecated(
    target=TargetMode.ARGS_REMAP,
    args_mapping={"old": "new"},
    deprecated_in="1.2",
    remove_in="2.0",
)
def api(*, new: str) -> str:
    return new


print(api(old="demo"))
```

<details>
  <summary>Output: <code>api(old="demo")</code></summary>

```
demo
```

</details>

## Anti-patterns

- Do not write `import pydeprecate` in Python code.
- Do not use `target=True` for argument remapping.
- Do not use `target=None` for warning-only behavior.
- Do not call the replacement function manually inside the deprecated function body when pyDeprecate is already forwarding.

## Agent context files

- [llms.txt](https://borda.github.io/pyDeprecate/llms.txt)
- [llms-full.txt](https://borda.github.io/pyDeprecate/llms-full.txt)
