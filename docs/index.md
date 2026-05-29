---
id: index
description: >-
  pyDeprecate — zero-dependency Python library for deprecating functions and
  classes with call forwarding, argument mapping, and CI/CD audit tools.
  Python 3.9+.
---

# pyDeprecate

**Author:** [Jiri Borovec](https://github.com/Borda) · **License:** Apache 2.0 · **Python:** 3.9+ · **Install:** `pip install pyDeprecate` · **Import:** `from deprecate import deprecated` · **PyPI:** https://pypi.org/project/pyDeprecate/

Every time you rename a function or retire an argument, you end up writing the same boilerplate: a wrapper, a `warnings.warn` call with the right category and `stacklevel`, manual argument forwarding, and no way to enforce the removal deadline when it arrives. **pyDeprecate** replaces all of that with a single decorator and gives you CI tools to make sure deprecated code does not quietly outlive its deadline.

> **pyDeprecate is downloaded over 700,000 times per month** from PyPI (source: [pepy.tech](https://pepy.tech/project/pyDeprecate)) — used across production Python projects that need reliable API deprecation without adding runtime dependencies.
>
> **Read:** [Mastering API Deprecation in Python — the pain points and how pyDeprecate can help](https://medium.com/codex/mastering-api-deprecation-in-python-the-pain-points-and-how-pydeprecate-can-help-1dbfd90e2b62) — CodeX / Medium

```python
from deprecate import deprecated


# New function which replaces `addition`
def compute_sum(x: int, y: int) -> int:
    return x + y


@deprecated(target=compute_sum, deprecated_in="1.0", remove_in="2.0")
def addition(x: int, y: int) -> int: ...


print(addition(1, 2))
```

<details>
  <summary>Output: <code>addition(1, 2)</code></summary>

```
3
```

</details>

Calling `addition(1, 2)` now emits a `FutureWarning` and transparently forwards to `compute_sum`.

## Features

- **Automatic call forwarding** — the decorator routes every call to the replacement, including positional and keyword arguments. No manual `*args/**kwargs` plumbing.
- **Argument mapping** — `args_mapping={"old": "new"}` handles renames across the API boundary; map to `None` to drop an argument entirely.
- **Argument deprecation** — `TargetMode.ARGS_REMAP` warns only when the old argument name is actually passed; callers who have already migrated see no noise.
- **Class and Enum support** — `@deprecated_class` wraps entire classes, Enums, and dataclasses in a transparent proxy where `isinstance` and `issubclass` just work.
- **Instance / constant proxy** — `deprecated_instance(obj, ...)` wraps module-level objects (dicts, lists, custom objects) with optional `read_only` enforcement and transparent attribute/item access.
- **Configurable frequency** — `num_warns=1` (default) emits once per function, not on every call. Set `-1` for always or `N` for exactly N times.
- **Docstring injection** — `update_docstring=True` appends a Sphinx `.. deprecated::` or MkDocs admonition notice automatically, keeping rendered API docs accurate.
- **Sphinx Plugin** — ships `deprecate.docstring.sphinx_ext` so `_DeprecatedProxy` objects render with their injected deprecation notice in Sphinx autodoc.
- **MkDocs Plugin** — ships `deprecate.docstring.griffe_ext` for mkdocstrings so runtime-injected `!!! warning` admonitions are visible in MkDocs-generated API docs.
- **Decorator stacking** — stack `@deprecated` decorators for multi-version migrations: rename arguments across releases (`ARGS_REMAP + ARGS_REMAP`), then deprecate the whole function when a complete replacement arrives (`ARGS_REMAP + NOTIFY`). Unsupported combinations warn at decoration time, not at call time.
- **Custom streams** — route warnings to `logging`, standard `warnings`, or any callable via the `stream` parameter; `stream=None` silences output while forwarding still occurs.
- **Custom message templates** — `template_mgs` overrides the default message with `%`-style placeholders (`source_name`, `target_path`, `deprecated_in`, `remove_in`, `argument_map`).
- **Conditional skip** — `skip_if=callable` suppresses the deprecation notice when a runtime condition is met (e.g. caller has migrated to a newer dependency).
- **CI audit tools** — [`validate_deprecation_expiry()`](guide/audit.md#enforcing-removal-deadlines) catches zombie code past its deadline, [`validate_deprecation_chains()`](guide/audit.md#detecting-deprecation-chains) detects double-deprecation chains, and [`find_deprecation_wrappers()`](guide/audit.md#validating-wrapper-configuration) surfaces misconfigured `args_mapping` keys before they silently do nothing.
- **Static type-checker signals** — native PEP 702 static diagnostics come from `typing.deprecated`; with pyDeprecate, you can layer `typing.deprecated` when you need both static hints and runtime migration behavior.
- **CLI** — `pydeprecate check src/` / `pydeprecate all src/` runs all audit checks from the command line. See [CLI Reference](guide/cli.md).
- **Testing helpers** — `assert_no_warnings()` context manager asserts no warnings of a given type escape a block; `no_warning_call` retained as legacy alias.
- **Zero runtime dependencies** — nothing added to `install_requires`.
- **Python 3.9+** supported.

## Installation

```bash
pip install pyDeprecate
```

For CI audit features that compare version strings:

```bash
pip install "pyDeprecate[audit]"
```

!!! tip "The `[audit]` extra is only needed for `validate_deprecation_expiry`"

    The base install gives you `@deprecated`, `@deprecated_class`, `deprecated_instance`, and all other audit functions. Only `validate_deprecation_expiry()` needs the extra because it pulls in `packaging` for PEP 440 version comparison.

## Comparison with other tools

The alternatives emit a deprecation notice but leave forwarding, argument mapping, and deadline enforcement to you. Here is what each tool covers:

| Feature               | pyDeprecate | `warnings.warn` | `deprecation` | `Deprecated` (wrapt) | `typing.deprecated`† (py3.13+) |
| --------------------- | :---------: | :-------------: | :-----------: | :------------------: | :----------------------------: |
| Simple Warnings       |     ✅      |       ✅        |      ✅       |          ✅          |               ✅               |
| Auto call forwarding  |     ✅      |       ❌        |      ❌       |          ❌          |               ❌               |
| Argument mapping      |     ✅      |       ❌        |      ❌       |          ❌          |               ❌               |
| Argument Deprecation  |     ✅      |       ✍️        |      ❌       |          ❌          |               ❌               |
| Class / Enum proxy    |     ✅      |       ❌        |      ❌       |          ❌          |               ❌               |
| Docstring injection   |     ✅      |       ❌        |      ✅       |          ✅          |               ❌               |
| Version Tracking      |     ✅      |       ✍️        |      ✅       |          ✅          |               ❌               |
| Prevent Log Spam      |     ✅      |       ✍️        |      ❌       |          ❌          |               ❌               |
| Zero runtime deps     |     ✅      |       ✅        |      ❌       |          ❌          |               †                |
| Custom Streams        |     ✅      |       ✍️        |      ❌       |          ❌          |               ❌               |
| CI audit tools        |     ✅      |       ❌        |      ❌       |          ❌          |               ❌               |
| Testing helpers       |     ✅      |       ❌        |      ❌       |          ❌          |               ❌               |
| Static checker signal |     ✍️      |       ❌        |      ❌       |          ❌          |               ✅               |
| Decorator Stacking    |     ✅      |       ❌        |      ❌       |          ❌          |               ❌               |
| Sphinx Plugin         |     ✅      |       ❌        |      ❌       |          ❌          |               ❌               |
| MkDocs Plugin         |     ✅      |       ❌        |      ❌       |          ❌          |               ❌               |

✍️ = possible but requires manual implementation
</br>
† `typing.deprecated` in the stdlib on Python 3.13+; also available as the `typing_extensions.deprecated` backport for Python < 3.13

_Comparison as of v0.8, May 2026. [Open an issue](https://github.com/Borda/pyDeprecate/issues) if you spot an inaccuracy._

> **When to prefer `typing.deprecated` (PEP 702):** If your project targets Python 3.13+ and you only need simple call-site warnings visible to static type-checkers (mypy, pyright, IDEs), the stdlib decorator is the right choice — zero extra dependency. Choose `pyDeprecate` when you need call-forwarding, argument remapping, proxy wrapping of module-level constants, or CI audit tools — none of those exist in PEP 702.

**Fair strengths in alternative tools worth considering:**

- `typing.deprecated` is the strongest choice for static-only IDE/type-checker visibility.
- `deprecation` includes `@fail_if_not_removed`, a focused test helper integrated with its deprecation metadata.
- `Deprecated` ships `@versionadded` / `@versionchanged` helpers for broader API lifecycle annotations.
- `warnings.warn` remains the simplest solution for one-off internal warnings without compatibility requirements.

## Where to go next

- [Getting Started](getting-started.md) — install, write your first deprecation, see the full API at a glance.
- [Use Cases](guide/use-cases.md) — thirteen real-world deprecation patterns with worked examples.
- [Customization](guide/customization.md) — custom message templates and output redirection to loggers.
- [void() Helper](guide/void-helper.md) — sentinel function for self-documenting deprecated stubs.
- [Audit Tools](guide/audit.md) — enforce removal deadlines and catch deprecation chains in CI.
- [Troubleshooting](troubleshooting.md) — common errors and how to fix them.
- [Sphinx demo](demo-sphinx/index.html) · [MkDocs demo](demo-mkdocs/index.html) — live rendered output.

## Compatibility and fit

Recent PyPI download statistics show broad production use; see pepy.tech for current numbers.

| Topic                        | Status                                                                                                                                                            |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python versions              | Project metadata currently supports Python 3.9+; Borda-maintained development policy targets Python 3.10+ because Python 3.9 reached end of life in October 2025. |
| Runtime dependencies         | None.                                                                                                                                                             |
| Optional audit extra         | `packaging`.                                                                                                                                                      |
| Optional CLI extra           | `fire`, `rich`.                                                                                                                                                   |
| Docs engines                 | Sphinx and MkDocs compatible.                                                                                                                                     |
| Type checker static warnings | Prefer `typing.deprecated` for Python 3.13+ static-checker-only cases.                                                                                            |

### When not to use pyDeprecate

Use `warnings.warn` for one-off internal warnings that do not need compatibility behavior. Use `typing.deprecated` for static-checker-only visibility on Python 3.13+. Use pyDeprecate when callers need forwarding, argument mapping, class or object aliases, warning frequency control, or CI audit checks.
