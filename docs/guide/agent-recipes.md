---
description: Coding-agent guide for pyDeprecate imports, migration recipes, anti-patterns, and links to llms.txt and llms-full.txt.
---

# Coding Agent Recipes

This page is for coding assistants that need to add a correct deprecation cycle to Python code.

## Install the coding-agent plugin

The repository contains an unreleased `pydeprecate` plugin with the same skills for Codex and Claude Code. It is separate from the `pyDeprecate` library, and `pip` does not install agent skills. From the repository root, install the local checkout with the host that you use:

```bash
# Codex
codex plugin marketplace add .
codex plugin add pydeprecate@pydeprecate

# Claude Code
claude plugin marketplace add .
claude plugin install pydeprecate@pydeprecate
```

The plugin provides `$pydeprecate:deprecate` and `$pydeprecate:remove` in Codex, and `/pydeprecate:deprecate` and `/pydeprecate:remove` in Claude Code. Implementing a deprecation requires package or module scope plus `deprecated_in` and `remove_in`; a `remove` request requires scope plus the target release only. Example requests: “Deprecate `parse_config` in `acme.parsers`, deprecated_in=1.4, remove_in=2.0.” and “Remove compatibility due by target release 2.0 from `acme.parsers`.”

Preview remains read-only. A removal plan requires an explicit release deadline with `remove_in <= target release`, keeps surviving functions or classes available for argument/attribute compatibility, and leaves incomplete scans unresolved. Use the consumer environment's existing `pyDeprecate`; Python audits need `[audit]`, while CLI audits need `[audit,cli]`.

The local source remains under `plugins/pydeprecate/` until publication: `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`, `skills/deprecate/SKILL.md`, and `skills/remove/SKILL.md`.

For adoption scans, ask: “Scan `src/acme` for deprecation decorators, warnings, aliases, and shims; suggest supported pyDeprecate conversions without editing.” The scan verifies installed or explicitly identified release support and returns `convert`, `keep`, or `needs decision`. It does not force dependencies or convert operational warnings, and PEP 702 static-checker behavior is not equivalent merely because runtime metadata looks similar.

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
