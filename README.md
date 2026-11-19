<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/banner-dark.svg">
  <img alt="pyDeprecate — seamless API migration" src="docs/assets/brand/banner-light.svg">
</picture>

# pyDeprecate

**Deprecate Python APIs without breaking existing callers.**

Use pyDeprecate when you rename a function, move a class, or retire an argument while existing user code must keep working. A single decorator **warns callers, forwards calls, and remaps arguments** during the migration window. CI audit tools expose deprecations that have reached their *removal deadline*.

**Author:** [Jiri Borovec](https://github.com/Borda) · **License:** Apache 2.0 · **Python:** 3.9+ · **Import:** `from deprecate import deprecated, deprecated_class`.

The [documentation site](https://borda.github.io/pyDeprecate/stable/) owns the full guides and API reference. This README covers installation, a quick start, the everyday patterns, warning controls, audits, testing and docstring notices; each section links to the guide with the details.

[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/pyDeprecate)](https://pypi.org/project/pyDeprecate/) [![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/Borda/pyDeprecate/blob/main/LICENSE) [![FOSSA Status](https://app.fossa.com/api/projects/git%2Bgithub.com%2FBorda%2FpyDeprecate.svg?type=shield&issueType=license)](https://app.fossa.com/projects/git%2Bgithub.com%2FBorda%2FpyDeprecate?ref=badge_shield&issueType=license)

[![PyPI Status](https://badge.fury.io/py/pyDeprecate.svg)](https://badge.fury.io/py/pyDeprecate) [![PyPI Status](https://pepy.tech/badge/pyDeprecate)](https://pepy.tech/project/pyDeprecate) [![Conda](https://img.shields.io/conda/v/conda-forge/pyDeprecate?label=conda&color=success)](https://anaconda.org/conda-forge/pyDeprecate) ![Conda](https://img.shields.io/conda/dn/conda-forge/pyDeprecate?color=blue) [![CodeFactor](https://www.codefactor.io/repository/github/borda/pydeprecate/badge)](https://www.codefactor.io/repository/github/borda/pydeprecate) [![Documentation](https://img.shields.io/badge/docs-borda.github.io-blue)](https://borda.github.io/pyDeprecate/)

[![CI testing](https://github.com/Borda/pyDeprecate/actions/workflows/ci_testing.yml/badge.svg?branch=main&event=push)](https://github.com/Borda/pyDeprecate/actions/workflows/ci_testing.yml) [![Install pkg](https://github.com/Borda/pyDeprecate/actions/workflows/ci_install-pkg.yml/badge.svg?branch=main&event=push)](https://github.com/Borda/pyDeprecate/actions/workflows/ci_install-pkg.yml) [![codecov](https://codecov.io/gh/Borda/pyDeprecate/branch/main/graph/badge.svg?token=BG7RQ86UJA)](https://codecov.io/gh/Borda/pyDeprecate) [![pre-commit.ci status](https://results.pre-commit.ci/badge/github/Borda/pyDeprecate/main.svg)](https://results.pre-commit.ci/latest/github/Borda/pyDeprecate/main)

## 📖 Overview

Keep the old entry point *temporarily*, tell users what changed, and forward calls to its replacement. Remove the deprecated entry point *after* callers have migrated.

![Deprecation lifecycle: warn, migrate callers, audit deadlines, then remove the old API](docs/assets/images/pydeprecate-lifecycle.svg)

The usual migration has four steps:

1. **Publish** the replacement API.
2. **Deprecate** the old entry point with introduction and removal versions.
3. **Audit** deprecated wrappers in tests, pre-commit, or CI.
4. **Remove** the old entry point when its migration window closes.

Read the [getting-started guide](https://borda.github.io/pyDeprecate/stable/getting-started.html) for the full walkthrough.

Read [Mastering API Deprecation in Python](https://medium.com/codex/mastering-api-deprecation-in-python-the-pain-points-and-how-pydeprecate-can-help-1dbfd90e2b62) for the motivation and migration pain points.

## ✨ Features

### 🔁 Runtime compatibility

- **Forward** function and method calls while preserving signatures, annotations, and metadata.
- Rename, drop, or inject arguments during forwarding.
- Keep old class, Enum, dataclass, and object names available through proxies.
- Deprecate an entire module or selected attributes.

See the [use-case decision table](https://borda.github.io/pyDeprecate/stable/guide/use-cases.html#quick-decision-table) to choose a pattern.

### 🧩 Callable support

- Support functions, methods, constructors, generators, and async functions.
- Warn on generator *iteration* and coroutine *awaiting*.
- Deprecate property getters, setters, and deleters, plus `cached_property` getters.
- Keep the base library free of runtime dependencies.

See [async functions](https://borda.github.io/pyDeprecate/stable/guide/async.html) and [properties](https://borda.github.io/pyDeprecate/stable/guide/properties.html) for timing and accessor rules.

### 🎛 Migration controls

- Warn **once per callable** by default, with configurable warning budgets.
- Send notices through Python warnings, logging, or a custom callable.
- Configure message templates and conditional `skip_if` behavior.
- Raise `DeprecatedError` when a deprecation must become fatal.

See [customization](https://borda.github.io/pyDeprecate/stable/guide/customization.html) and [fatal deprecations](https://borda.github.io/pyDeprecate/stable/guide/functions.html#fatal-deprecations) for controls and limits.

### 🛠 Maintenance tools

- Update docstrings in RST/Sphinx or MkDocs/Markdown format.
- **Validate** wrapper configuration, removal deadlines, governance policies, and deprecation chains.
- Generate deprecation status tables for release planning.
- Test warning behavior with `assert_no_warnings`.

See the [audit guide](https://borda.github.io/pyDeprecate/stable/guide/audit/index.html) for Python and CI workflows.

## 💾 Installation

Requires **Python 3.9 or later**.

```bash
pip install pyDeprecate
```

Choose the installation for your workflow:

| Workflow                            | Command                                                             | Includes                                                                 |
| ----------------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| Runtime wrappers                    | `pip install pyDeprecate`                                           | Decorators, proxies, docstring helpers, and most audit metadata helpers. |
| Runtime wrappers with conda         | `conda install -c conda-forge pydeprecate`                          | The same runtime package from conda-forge.                               |
| Removal deadlines and policy checks | `pip install 'pyDeprecate[audit]'`                                  | PEP 440 version comparison through `packaging`.                          |
| Command-line audits                 | `pip install 'pyDeprecate[audit]'`                                  | Version-aware audit commands and the CLI.                                |
| Latest development version          | `pip install https://github.com/Borda/pyDeprecate/archive/main.zip` | Unreleased changes from the default branch.                              |

The CLI itself uses only the standard library; `[audit]` adds `packaging` for the version-aware commands. See [installation details](https://borda.github.io/pyDeprecate/stable/getting-started.html#installation) for the full package and extras reference.

## 🚀 Quick Start

Forward an existing function to its replacement with `@deprecated(target=...)`:

```python
from deprecate import deprecated, void


# NEW API — the replacement computes the sum
def compute_sum(a: int, b: int) -> int:
    return a + b


# DEPRECATED API — `addition` now forwards to `compute_sum`; its own body never runs
@deprecated(target=compute_sum, deprecated_in="1.0", remove_in="2.0")
def addition(a: int, b: int) -> int:
    return void(a, b)


print(addition(1, 2))  # warns: FutureWarning
```

<details>
  <summary>Output: <code>addition(1, 2)</code></summary>

```
3
```

</details>

The result comes from `compute_sum`, because the deprecated body only returns `None`. Existing callers see this warning *once*, and new callers should call `compute_sum` **directly**:

```text
FutureWarning: The `addition` was deprecated since v1.0 in favor of `__main__.compute_sum`. It will be removed in v2.0.
```

> **Warning — forwarding skips the old body.** With a callable `target`, the deprecated function body does not run. If `skip_if` evaluates to `True`, the source body runs instead, so keep a working fallback when combining these options.

See [simple function forwarding](https://borda.github.io/pyDeprecate/stable/guide/functions.html#simple-function-forwarding) for argument handling and callable-target limits.

## 📊 Comparison with Other Tools

A hand-written `warnings.warn` shim covers the **first row only**. pyDeprecate covers the rest of the migration from the *same decorator*:

| Migration need                              | Hand-written `warnings.warn`               | pyDeprecate                          |
| ------------------------------------------- | ------------------------------------------ | ------------------------------------ |
| Warn old callers                            | Write the message and `stacklevel` by hand | Generated message with both versions |
| Forward calls to the replacement            | Write a shim per function                  | `target=new_function`                |
| Rename or drop arguments                    | Translate keyword arguments by hand        | `args_mapping`                       |
| Keep old class, constant, and module names  | Subclass or write a module `__getattr__`   | Transparent proxies                  |
| Warn once instead of on every call          | Track call state yourself                  | `num_warns` budget per wrapper       |
| Fail CI once the removal version is reached | Not available                              | `pydeprecate expiry`                 |

Use `warnings.deprecated` (PEP 702, Python 3.13+) or `typing_extensions.deprecated` when you *only* need a type-checker signal; stack it above `@deprecated` with `category=None`, so callers get the type-checker signal and only one runtime warning. See the [Python deprecation tools comparison](https://borda.github.io/pyDeprecate/stable/guide/compare-python-deprecation-tools.html) for `deprecation`, `Deprecated` and other libraries.

## 📚 Use-cases and Applications

Each example below is a complete basic setup you can paste and run. The [use-case guide](https://borda.github.io/pyDeprecate/stable/guide/use-cases.html#quick-decision-table) helps you choose a pattern for other cases.

### 🔄 Rename an argument

Use `TargetMode.ARGS_REMAP` when the function **stays in place** but one of its arguments has a new name:

```python
from deprecate import TargetMode, deprecated


# NEW API — `factor` is the current argument name
# DEPRECATED API — callers passing `scale` are remapped to `factor`
@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"scale": "factor"}, deprecated_in="0.2", remove_in="0.4")
def resize_width(width: int, scale: float = 1.0, factor: float = 1.0) -> int:
    return int(width * factor)


print(resize_width(100, scale=2))  # warns: FutureWarning
```

<details>
  <summary>Output: <code>resize_width(100, scale=2)</code></summary>

```
200
```

</details>

```text
FutureWarning: The `resize_width` uses deprecated arguments: `scale` -> `factor`. They were deprecated since v0.2 and will be removed in v0.4.
```

### 🔀 Move a function and rename its arguments

Combine a callable `target` with `args_mapping` when the replacement also renamed or dropped arguments. Map an old name to `None` to drop it before forwarding:

```python
from deprecate import deprecated, void


# NEW API — `max_rows` replaces `limit`, and the `verbose` flag is gone
def fetch_rows(table: str, max_rows: int = 10) -> list[str]:
    return [f"{table}-{i}" for i in range(max_rows)]


# DEPRECATED API — `load_rows` forwards to `fetch_rows`, renaming `limit` and dropping `verbose`
@deprecated(
    target=fetch_rows,
    args_mapping={"limit": "max_rows", "verbose": None},
    deprecated_in="1.3",
    remove_in="2.0",
)
def load_rows(table: str, limit: int = 10, verbose: bool = False) -> list[str]:
    return void(table, limit, verbose)


print(load_rows("users", limit=2, verbose=True))  # warns: FutureWarning
```

<details>
  <summary>Output: <code>load_rows("users", limit=2, verbose=True)</code></summary>

```
['users-0', 'users-1']
```

</details>

```text
FutureWarning: The `load_rows` was deprecated since v1.3 in favor of `__main__.fetch_rows`. It will be removed in v2.0.
```

### ⚠ Warn without changing behavior

Use `TargetMode.NOTIFY` when the function **keeps its body** and only needs a removal notice:

```python
from deprecate import TargetMode, deprecated


# DEPRECATED API — `order_total` still runs its own body until v0.5
@deprecated(target=TargetMode.NOTIFY, deprecated_in="0.1", remove_in="0.5")
def order_total(prices: list[float]) -> float:
    return sum(prices)


print(order_total([1.5, 2.5]))  # warns: FutureWarning
```

<details>
  <summary>Output: <code>order_total([1.5, 2.5])</code></summary>

```
4.0
```

</details>

```text
FutureWarning: The `order_total` was deprecated since v0.1. It will be removed in v0.5.
```

### 🧰 Deprecate a method or a property

The same decorator works on methods, class methods, static methods and properties. Put `@deprecated` below `@property`; the property warns on read:

```python
from deprecate import TargetMode, deprecated


class Invoice:
    def __init__(self, amount: float) -> None:
        self.amount = amount

    # NEW API — the total including tax
    def total(self) -> float:
        return self.amount * 1.2

    # DEPRECATED API — `gross` still runs its body, which calls `total`
    @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.1", remove_in="2.0")
    def gross(self) -> float:
        return self.total()

    # DEPRECATED API — read `amount` instead
    @property
    @deprecated(deprecated_in="1.1", remove_in="2.0")
    def value(self) -> float:
        return self.amount


invoice = Invoice(10.0)
print(invoice.gross())  # warns: FutureWarning
print(invoice.value)  # warns: FutureWarning
```

<details>
  <summary>Output: <code>invoice.gross(); invoice.value</code></summary>

```
12.0
10.0
```

</details>

```text
FutureWarning: The `gross` was deprecated since v1.1. It will be removed in v2.0.
FutureWarning: The `value` was deprecated since v1.1. It will be removed in v2.0.
```

A property with a setter or deleter needs `@deprecated` **above** `@property` so that every accessor warns. See [properties](https://borda.github.io/pyDeprecate/stable/guide/properties.html) and [class and static methods](https://borda.github.io/pyDeprecate/stable/guide/advanced.html#class-methods-and-static-methods).

### ⏳ Deprecate an async function

`async def` functions take the same decorator. The warning fires *when the coroutine runs*, and the call forwards to an async replacement:

```python
import asyncio

from deprecate import deprecated, void


# NEW API — the renamed coroutine
async def fetch_invoice(invoice_id: int) -> str:
    return f"invoice-{invoice_id}"


# DEPRECATED API — `get_invoice` forwards to `fetch_invoice`
@deprecated(target=fetch_invoice, deprecated_in="1.4", remove_in="2.0")
async def get_invoice(invoice_id: int) -> str:
    return void(invoice_id)


print(asyncio.run(get_invoice(7)))  # warns: FutureWarning
```

<details>
  <summary>Output: <code>asyncio.run(get_invoice(7))</code></summary>

```
invoice-7
```

</details>

Generators and async generators work the same way: keep `yield` in the deprecated stub, and the warning fires on the first iteration. See [async functions](https://borda.github.io/pyDeprecate/stable/guide/async.html#async-functions) and [generators](https://borda.github.io/pyDeprecate/stable/guide/advanced.html#deprecating-generator-functions).

### 🏗 Rename a class

Use `deprecated_class` to keep an old class name working while construction goes to the new class:

```python
from deprecate import deprecated_class


# NEW API — the renamed writer
class ReportWriter:
    def __init__(self, path: str = "") -> None:
        self.path = path


# DEPRECATED API — `CsvReportWriter` forwards to `ReportWriter`
@deprecated_class(target=ReportWriter, deprecated_in="1.2", remove_in="2.0")
class CsvReportWriter:
    def __init__(self, path: str = "") -> None:
        self.path = path


writer = CsvReportWriter("out.csv")  # warns: FutureWarning
print(type(writer).__name__, writer.path)
```

<details>
  <summary>Output: <code>type(writer).__name__, writer.path</code></summary>

```
ReportWriter out.csv
```

</details>

```text
FutureWarning: The `CsvReportWriter` was deprecated since v1.2 in favor of `__main__.ReportWriter`. It will be removed in v2.0.
```

### 🏷 Rename a class attribute

Use `attrs_mapping` when the class keeps its name but an attribute is renamed. **Only the listed names** warn; the others pass through silently:

```python
from deprecate import deprecated_class


# NEW API — `colour` is the canonical spelling
# DEPRECATED API — `color` redirects to `colour`
@deprecated_class(attrs_mapping={"color": "colour"}, deprecated_in="2.0", remove_in="3.0")
class Palette:
    colour: str = "red"
    size: int = 10


print(Palette.color)  # warns: FutureWarning
print(Palette.size)  # no warning
```

<details>
  <summary>Output: <code>Palette.color; Palette.size</code></summary>

```
red
10
```

</details>

```text
FutureWarning: The `color` was deprecated since v2.0 in favor of `Palette.colour`. It will be removed in v3.0.
```

Reads, writes and deletes all redirect. Map a name to `None` to warn without renaming. See [selective attributes](https://borda.github.io/pyDeprecate/stable/guide/attributes.html#selective-attribute-deprecation) for Enum members and dataclass fields.

### 📦 Rename a constant

Use `deprecated_instance` to keep an old module-level name for a dict, list, constant, or object:

```python
from deprecate import deprecated_instance

# NEW API — renamed to say what it configures
TRAINING_CONFIG = {"lr": 0.001, "batch_size": 32}

# DEPRECATED API — `DEFAULTS` was the original name; `read_only=True` refuses writes through the old name
DEFAULTS = deprecated_instance(TRAINING_CONFIG, name="DEFAULTS", deprecated_in="1.2", remove_in="2.0", read_only=True)

print(DEFAULTS["lr"])  # warns: FutureWarning
```

<details>
  <summary>Output: <code>DEFAULTS["lr"]</code></summary>

```
0.001
```

</details>

```text
FutureWarning: The `DEFAULTS` was deprecated since v1.2. It will be removed in v2.0.
```

### 📁 Deprecate a module

Call `deprecated_module` **once at the bottom** of the old module file. Every public attribute access on the old module then warns, on each access by default:

```python
# phmdoctest:skip — template: the last lines of old_calculator.py; new_calculator is your replacement module
# DEPRECATED API — old_calculator.py; its functions now live in new_calculator.py
import new_calculator

from deprecate import deprecated_module

deprecated_module(__name__, target=new_calculator, deprecated_in="2.0", remove_in="3.0")
```

Names missing from `old_calculator` are forwarded to `new_calculator`, so `old_calculator.add(1, 2)` keeps working with a `FutureWarning`. Leave out `target` to keep the module in place and only warn. See [module deprecation](https://borda.github.io/pyDeprecate/stable/guide/modules.html) for attribute renames during the redirect.

### ➕ More patterns

- **Stacked decorators** — rename arguments across several releases ([guide](https://borda.github.io/pyDeprecate/stable/guide/functions.html#stacked-deprecation-decorators)).
- **Fatal deprecations** — raise `DeprecatedError` instead of warning, with `as_exception=True` on one wrapper, `with deprecate.as_exceptions():` for one block, or `DEPRECATE_AS_EXCEPTIONS=1` for the whole process ([guide](https://borda.github.io/pyDeprecate/stable/guide/functions.html#fatal-deprecations)).
- **Strict callable-only deprecation** — reject class sources with `deprecated_callable` ([guide](https://borda.github.io/pyDeprecate/stable/guide/functions.html#strict-callable-only-deprecation)).
- **Enums and dataclasses** — forward class access and construction ([guide](https://borda.github.io/pyDeprecate/stable/guide/classes.html#enums-and-dataclasses)).
- **New required arguments** — inject fixed forwarded kwargs ([guide](https://borda.github.io/pyDeprecate/stable/guide/advanced.html#injecting-new-required-arguments)).
- **The `void()` helper** — write deprecated stubs ([guide](https://borda.github.io/pyDeprecate/stable/guide/void-helper.html#basic-usage)).

For existing installations, read the [migration guide](https://borda.github.io/pyDeprecate/stable/guide/migration.html) before upgrading.

## 🎛 Control the Warnings

Every decorator and proxy takes the same options for where its notice goes, how often it fires, what it says, and when it applies.

### 📣 Output, budget and message

Send the notice to a logger with `stream`, repeat it on every call with `num_warns=-1`, and replace the default text with `message_template`:

```python
import logging

from deprecate import deprecated, void


# NEW API — the renamed helper
def normalize(text: str) -> str:
    return text.strip().lower()


# DEPRECATED API — `clean` forwards to `normalize` and logs each call
@deprecated(
    target=normalize,
    deprecated_in="1.0",
    remove_in="2.0",
    stream=logging.warning,
    num_warns=-1,
    message_template="`%(source_name)s` is deprecated, call `%(target_name)s` instead.",
)
def clean(text: str) -> str:
    return void(text)


print(clean("  Hello "))  # logs: WARNING
print(clean(" World"))  # logs: WARNING again
```

<details>
  <summary>Output: <code>clean("  Hello "); clean(" World")</code></summary>

```
hello
world
```

</details>

```text
WARNING:root:`clean` is deprecated, call `normalize` instead.
WARNING:root:`clean` is deprecated, call `normalize` instead.
```

- `stream` accepts any callable that takes the message, such as `print` or your own function. `stream=None` silences the notice; `deprecated_module` still warns with it until v1.0.
- `num_warns` defaults to `1`, so each wrapper warns once per process. A positive number sets the budget and `-1` warns on every call.
- `message_template` always accepts `%(source_name)s`, `%(deprecated_in)s` and `%(remove_in)s`. With a callable `target` it also accepts `%(target_name)s` and `%(target_path)s`.

See [customization](https://borda.github.io/pyDeprecate/stable/guide/customization.html) for every placeholder and stream recipe.

### 🔀 Apply a deprecation conditionally

`skip_if` takes a boolean or a zero-argument callable. While it returns `True`, the wrapper **neither warns nor forwards**, and the source body runs:

```python
from deprecate import TargetMode, deprecated

LEGACY_EXPORT = True


# DEPRECATED API — `export_csv` warns only once the legacy export flag is off
@deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0", skip_if=lambda: LEGACY_EXPORT)
def export_csv(rows: list[int]) -> int:
    return len(rows)


print(export_csv([1, 2]))  # no warning while LEGACY_EXPORT is True
LEGACY_EXPORT = False
print(export_csv([1, 2]))  # warns: FutureWarning
```

<details>
  <summary>Output: <code>export_csv([1, 2])</code> before and after the switch</summary>

```
2
2
```

</details>

See [conditional skip](https://borda.github.io/pyDeprecate/stable/guide/functions.html#conditional-skip) for the callback rules.

## 🗺 API at a Glance

Choose an entry point for the object you are deprecating:

| Entry point           | Purpose                                       | Reference                                                                                               |
| --------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `deprecated`          | Deprecate functions, methods, and properties. | [Decorator API](https://borda.github.io/pyDeprecate/stable/api-reference.html#decorator)                |
| `deprecated_callable` | Deprecate callables and reject class sources. | [Decorator API](https://borda.github.io/pyDeprecate/stable/api-reference.html#decorator)                |
| `deprecated_class`    | Deprecate classes, Enums, and dataclasses.    | [Class guide](https://borda.github.io/pyDeprecate/stable/guide/classes.html)                            |
| `deprecated_instance` | Deprecate objects and constants.              | [Instance guide](https://borda.github.io/pyDeprecate/stable/guide/classes.html#constants-and-instances) |
| `deprecated_module`   | Deprecate a module in place or redirect it.   | [Module guide](https://borda.github.io/pyDeprecate/stable/guide/modules.html)                           |

![An old call reaches the deprecated wrapper, which warns and forwards to the new API](docs/assets/images/pydeprecate-flow.svg)

Use `deprecated_in` and `remove_in` to define the migration window. A callable `target` identifies the replacement in the default warning message. See the [parameter matrix](https://borda.github.io/pyDeprecate/stable/getting-started.html#parameter-matrix-across-the-api) and the [complete API reference](https://borda.github.io/pyDeprecate/stable/api-reference.html) for every option.

### 🎯 Target modes

Choose the behavior explicitly when configuring a wrapper:

| Target                   | Behavior                                                                                    |
| ------------------------ | ------------------------------------------------------------------------------------------- |
| A replacement callable   | Warn and forward calls to the replacement.                                                  |
| `TargetMode.NOTIFY`      | Warn and keep running the source body.                                                      |
| `TargetMode.ARGS_REMAP`  | Rename arguments within the same function.                                                  |
| `TargetMode.ATTRS_REMAP` | Warn on selected attribute aliases (`deprecated_class`; `deprecated_instance` since v0.16). |

The default `TargetMode.AUTO` infers the mode at decoration time. See [mode differences](https://borda.github.io/pyDeprecate/stable/guide/functions.html#behaviour-comparison) for warning triggers and mapping rules.

## 🔍 Audit

Audit wrappers before release to catch invalid configuration and wrappers that outlived their removal version.

### ⏰ Check from the command line

```bash
pip install 'pyDeprecate[audit]'
pydeprecate check path/to/your/package                    # invalid wrapper configuration
pydeprecate expiry path/to/your/package --version 2.0.0   # wrappers at or past their remove_in version
pydeprecate status path/to/your/package                   # deprecation status table
pydeprecate all path/to/your/package --version 2.0.0      # every check in one scan, plus the table
```

`check` exits `1` on configuration errors and *only reports* advisory findings such as identity mappings. `expiry` exits `1` on any expired wrapper, and `status` *only reports*. See [CLI exit codes](https://borda.github.io/pyDeprecate/stable/guide/audit/cli.html#exit-codes) before making one a required CI gate.

### 🪝 Run on every commit

Add the hook to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/Borda/pyDeprecate
    rev: v0.15.0
    hooks:
      - id: pydeprecate-check
```

The hook **imports the scanned package**, so its imports must resolve without your project's runtime dependencies. The `pydeprecate-expiry`, `pydeprecate-policy`, `pydeprecate-chains` and `pydeprecate-all` hooks run the other checks. See [pre-commit integration](https://borda.github.io/pyDeprecate/stable/guide/audit/ci-integration.html#pre-commit-integration) for options.

### ⚙ Run in GitHub Actions

Add a step after checking out the repository and installing your package:

```yaml
- uses: Borda/pyDeprecate@v0.15.0
  with:
    subcommand: expiry
    path: src/mypackage
```

The step returns the CLI exit code. See the [GitHub Action recipe](https://borda.github.io/pyDeprecate/stable/guide/audit/ci-integration.html#github-action) for every input.

### 🐍 Check from Python

The same checks run from a test, which needs no extra CI setup. Removal-deadline checks need the `[audit]` extra:

```python
# phmdoctest:skip — CI template: replace my_package with your actual package
import my_package

from deprecate import validate_deprecation_expiry


def test_no_expired_deprecations():
    expired = validate_deprecation_expiry(my_package, current_version=my_package.__version__)
    assert not expired, "\n".join(expired)
```

`find_deprecation_wrappers(my_package)` lists every wrapper and its configuration, and `generate_deprecation_table(my_package)` renders the release-planning table.

The [audit guide](https://borda.github.io/pyDeprecate/stable/guide/audit/index.html) covers the Python API, deprecation tables, [policy checks](https://borda.github.io/pyDeprecate/stable/guide/audit/policy.html#enforcing-a-deprecation-policy) and [chain detection](https://borda.github.io/pyDeprecate/stable/guide/audit/index.html#detecting-deprecation-chains).

## 🧪 Testing

Pair `pytest.warns` with `assert_no_warnings` to check both that old callers warn and that new callers stay silent:

```python
import pytest

from deprecate import assert_no_warnings, deprecated, void


# NEW API — doubles the input value
def score_predictions(x: int) -> int:
    return x * 2


# DEPRECATED API — `score` replaced by `score_predictions`
@deprecated(target=score_predictions, deprecated_in="1.0", remove_in="2.0")
def score(x: int) -> int:
    return void(x)


def test_deprecated_function_shows_warning():
    with pytest.warns(FutureWarning, match="score.*deprecated"):
        assert score(42) == 84


def test_new_function_no_warning():
    with assert_no_warnings(FutureWarning):
        assert score_predictions(42) == 84


# README demo only: pytest collects the test_* functions itself, so leave these two calls out of a test module
test_deprecated_function_shows_warning()
test_new_function_no_warning()
```

A wrapper warns once by default (`num_warns=1`). A `pytest.warns` check must therefore be the **first call** of that deprecated function in the test process, or the wrapper needs `num_warns=-1`. See [testing deprecated code](https://borda.github.io/pyDeprecate/stable/guide/testing.html) for more patterns.

## 📝 Documentation tools

Set `update_docstring=True` to write the deprecation notice into the wrapper's docstring, so your rendered API reference shows it next to the signature:

```python
from deprecate import TargetMode, deprecated


# DEPRECATED API — `tidy` keeps working until v2.0 and says so in its docstring
@deprecated(
    target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0", update_docstring=True, docstring_style="rst"
)
def tidy(text: str) -> str:
    """Strip and lowercase the text."""
    return text.strip().lower()


print(tidy.__doc__)
```

<details>
  <summary>Output: <code>tidy.__doc__</code></summary>

```
Strip and lowercase the text.

.. deprecated:: 1.0
   Will be removed in 2.0.
```

</details>

With a callable `target`, the notice adds a line naming the replacement, such as `` Use :func:`mypackage.new_name` instead. ``

The notice is RST/Sphinx or MkDocs/Markdown. Set `docstring_style` to pick one; otherwise the style is detected from the environment. Bundled extensions keep the notice visible on deprecated classes in documentation builds (beta):

- **Sphinx** — add `"deprecate.docstring.sphinx_ext"` to `extensions` in `conf.py`.
- **MkDocs** — add `deprecate.docstring.griffe_ext:RuntimeDocstrings` to the mkdocstrings Python handler's `options.extensions`.

See [automatic docstring updates](https://borda.github.io/pyDeprecate/stable/guide/advanced.html#automatic-docstring-updates) for configuration and extension setup.

### 📘 MkDocs preview

![MkDocs demo — deprecation notice rendered as a warning admonition](docs/assets/images/demo-docs-mkdocs.png)

View the [MkDocs demo](https://borda.github.io/pyDeprecate/stable/demo-mkdocs/api.html) for rendered notices.

### 📗 Sphinx preview

![Sphinx demo — deprecation notice rendered as a styled deprecated directive](docs/assets/images/demo-docs-sphinx.png)

View the [Sphinx demo](https://borda.github.io/pyDeprecate/stable/demo-sphinx/api.html) for rendered notices.

## 🔧 Troubleshooting

The [troubleshooting guide](https://borda.github.io/pyDeprecate/stable/troubleshooting.html) explains common errors and integration limits.

| Symptom                               | Guide                                                                                                                                        |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `UserWarning` when decorating a class | [Choose the class entry point](https://borda.github.io/pyDeprecate/stable/troubleshooting.html#userwarning-when-decorating-a-class).         |
| `TypeError: Failed mapping`           | [Check argument names](https://borda.github.io/pyDeprecate/stable/troubleshooting.html#typeerror-failed-mapping).                            |
| `skip_if` does not return `bool`      | [Fix the callback result](https://borda.github.io/pyDeprecate/stable/troubleshooting.html#typeerror-skip_if-function-must-return-bool).      |
| Warning not showing                   | [Check warning budgets and filters](https://borda.github.io/pyDeprecate/stable/troubleshooting.html#deprecation-notice-not-appearing).       |
| Unexpected target path across modules | [Check the target import](https://borda.github.io/pyDeprecate/stable/troubleshooting.html#deprecation-target-path-incorrect-across-modules). |

See [limitations](https://borda.github.io/pyDeprecate/stable/limitations.html) for the few corners that behave differently than you might expect, each with a workaround. If one blocks a real use case, [open an issue](https://github.com/Borda/pyDeprecate/issues): we are happy to discuss it and integrate a fix when it fits.

![pyDeprecate — navigate API transitions smoothly without the chaos or crashes](docs/assets/images/path-illustration.webp)

## 🏷️ Show that you use pyDeprecate

Add this badge to show that your project provides an API migration path:

[![API migration by pyDeprecate](https://img.shields.io/badge/API%20migration%20by-pyDeprecate-EAA238?labelColor=%232E6893)](https://github.com/Borda/pyDeprecate)

```markdown
[![API migration by pyDeprecate](https://img.shields.io/badge/API%20migration%20by-pyDeprecate-EAA238?labelColor=%232E6893)](https://github.com/Borda/pyDeprecate)
```

<!-- following section will be skipped from PyPI description -->

## 🤖 Coding-agent plugin

This repository includes a `pydeprecate` plugin for Codex and Claude Code, available from the repository's default branch (early, pre-1.0). It is a separate artifact from the `pyDeprecate` library and is not installed by `pip`.

Install it from GitHub:

```bash
# Codex
codex plugin marketplace add Borda/pyDeprecate
codex plugin add pydeprecate@pydeprecate

# Claude Code
claude plugin marketplace add Borda/pyDeprecate
claude plugin install pydeprecate@pydeprecate
```

For local development or unpublished changes, run these commands from the repository root with `.` instead of `Borda/pyDeprecate` in the marketplace-add command. The plugin-install command stays the same.

Use `$pydeprecate:sunset` / `$pydeprecate:prune` in Codex, or `/pydeprecate:sunset` / `/pydeprecate:prune` in Claude Code, to implement or retire a deprecation — see the [coding-agent recipes](https://borda.github.io/pyDeprecate/stable/guide/agent-recipes.html) for invocation examples, host commands, and workflow boundaries.

<!-- end skipping PyPI description -->

## 🤝 Contributing

Contributions, bug reports, and migration stories are welcome.

Read the [Contributing Guide](.github/CONTRIBUTING.md) for development and testing instructions.

Follow the [Code of Conduct](.github/CODE_OF_CONDUCT.md) when participating in the community.
