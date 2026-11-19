---
id: use-cases
description: Overview of all deprecation patterns in pyDeprecate — functions, classes, properties, async, and advanced topics. Start here to find the right guide for your use case.
---

# Use Cases

The most common reasons to deprecate something are renaming a function, renaming an argument, or replacing a class. This overview maps each scenario to the right topic page. If you are new to the library, start with [Getting Started](../getting-started.md).

The pages here cover *writing* a deprecation. The tools pages cover everything *around* it: testing the migration, enforcing deadlines and policy in CI, and showing notices in your API docs. [Lifecycle](lifecycle.md) walks through all of it in order. If your case is not listed, [open an issue](https://github.com/Borda/pyDeprecate/issues) and we will happily look at it with you.

## Topics

### [Functions](functions.md)

The function guide covers these patterns for Python functions and methods:

- **Forward calls** to a replacement.
- Rename arguments with `args_mapping`.
- Emit a **notice-only** deprecation.
- Rename arguments within the same function with `TargetMode.ARGS_REMAP`.
- Stack multiple decorators for multi-release migrations.
- Suppress warnings conditionally with `skip_if`.
- Promote a deprecation to a raised error with `as_exception`.

### [Classes](classes.md)

The class guide covers classes, Enums, dataclasses, and module-level constants:

- Forward an old class name to a replacement with `deprecated_class()`.
- Wrap module-level objects with `deprecated_instance()`.
- Deprecate selected attributes with `attrs_mapping`; see [Attributes](attributes.md).
- Stack multiple proxy layers for multi-version attribute migrations.

### [Modules](modules.md)

The module guide covers **whole-module** deprecation via `__class__` reassignment and audit integration:

| Mode   | Behavior                                                                                                                                      |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Mode 1 | It warns in place on every public attribute access, with normal `AttributeError` for missing names.                                           |
| Mode 2 | It warns on every public access and redirects missing-attribute lookups to a replacement module, with optional per-attribute `attrs_mapping`. |
| Mode 3 | It provides a parent alias via `deprecated_instance()` in `__init__.py`.                                                                      |

### [Properties](properties.md)

The property guide covers deprecation of `@property` and `@cached_property` descriptors:

- Follow **decorator order** rules.
- Wrap all three accessors (`fget`, `fset`, `fdel`) at once.
- Chain `.setter` / `.deleter`.
- Use the dataclass field alias pattern.

### [Async](async.md)

The async guide covers deprecation of `async def` functions and async generator functions:

- Use all three TargetModes.
- Understand warning timing: coroutines warn *when awaited*, and async generators warn on the *first iteration*.
- Preserve coroutine introspection behaviour.
- Account for concurrency caveats.

### [Advanced](advanced.md)

The advanced guide covers these patterns:

- Inject a deprecation notice into the docstring at import time (`update_docstring=True`).
- Supply a fixed default for a new required argument (`args_extra`).
- Use testing helpers (`assert_no_warnings`).
- Deprecate `@classmethod` and `@staticmethod` descriptors.
- Deprecate generator functions.

### Around the decorator

These pages cover what happens before and after you write the wrapper:

- [Lifecycle](lifecycle.md) follows one deprecation from the first wrapper through tests, CI audits, removal and release notes.
- [Testing](../integrations/testing.md) tests warnings, warning budgets and fixtures.
- [Audit Tools](../audit/index.md), [Audit Policy](../audit/policy.md) and [CLI](../audit/cli.md) list deprecations, enforce removal deadlines and lint them against a policy.
- [CI Integration](../audit/ci-integration.md) wires those checks into pytest, pre-commit and a GitHub Action.
- [Docs Integration](../integrations/docs-integration.md) shows runtime notices in Sphinx and MkDocs API pages.

## Quick decision table

To include symbols deprecated *only* with `warnings.deprecated` or `typing_extensions.deprecated` in an audit, pass `include_pep702=True` to `find_deprecation_wrappers()`; the **default scan stays unchanged**. For static-checker warnings alongside runtime forwarding, see [Functions → Static type checkers (PEP 702)](functions.md#static-type-checkers-pep-702).

| Scenario                                       | Where to look                                                                                         |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Rename a function or method                    | [Functions → Simple forwarding](functions.md#simple-function-forwarding)                              |
| Rename an argument                             | [Functions → Argument renaming](functions.md#argument-renaming-and-mapping)                           |
| Notice only — no replacement yet               | [Functions → Notice-only](functions.md#notice-only-deprecation)                                       |
| Rename an argument within the same function    | [Functions → Rename arguments within one function](functions.md#rename-arguments-within-one-function) |
| Stack decorators across releases               | [Functions → Stacked decorators](functions.md#stacked-deprecation-decorators)                         |
| Suppress notice conditionally                  | [Functions → Conditional skip](functions.md#conditional-skip)                                         |
| Raise instead of warn (fatal deprecation)      | [Functions → Fatal deprecations](functions.md#fatal-deprecations)                                     |
| Rename a class, Enum, or dataclass             | [Classes → Class deprecation](classes.md#class-deprecation)                                           |
| Deprecate a module-level constant or object    | [Classes → Constants and instances](classes.md#constants-and-instances)                               |
| Deprecate selected class attributes            | [Classes → Selective attributes](attributes.md#selective-attribute-deprecation)                       |
| Deprecate a `@property`                        | [Properties](properties.md)                                                                           |
| Deprecate an `async def` function              | [Async → Async functions](async.md#async-functions)                                                   |
| Deprecate an async generator                   | [Async → Async generators](async.md#async-generators)                                                 |
| Inject deprecation notice into docstring       | [Advanced → Docstring updates](advanced.md#automatic-docstring-updates)                               |
| Inject a fixed default for a new required arg  | [Advanced → Injecting new args](advanced.md#injecting-new-required-arguments)                         |
| Silence warnings in test fixtures              | [Advanced → Testing helpers](advanced.md#suppressing-futurewarning-in-test-fixtures)                  |
| Deprecate a `@classmethod` or `@staticmethod`  | [Advanced → Class/static methods](advanced.md#class-methods-and-static-methods)                       |
| Deprecate a generator function                 | [Advanced → Generators](advanced.md#deprecating-generator-functions)                                  |
| Deprecate an entire module                     | [Modules](modules.md)                                                                                 |
| Static-checker warnings alongside runtime      | [Functions → Static type checkers (PEP 702)](functions.md#static-type-checkers-pep-702)               |
| Audit PEP 702-only symbols                     | [Audit → PEP 702-only deprecations](../audit/index.md#pep-702-only-deprecations)                      |
| Follow one deprecation from start to removal   | [Lifecycle](lifecycle.md)                                                                             |
| Fail CI when a removal deadline passes         | [Audit Policy → Removal deadlines](../audit/policy.md#enforcing-removal-deadlines)                    |
| Lint deprecations against a team policy        | [Audit Policy → Deprecation policy](../audit/policy.md#enforcing-a-deprecation-policy)                |
| Detect deprecation chains                      | [Audit → Chains](../audit/index.md#detecting-deprecation-chains)                                      |
| Run the checks in pre-commit or GitHub Actions | [CI Integration](../audit/ci-integration.md)                                                          |
| Test that a deprecation warns and forwards     | [Testing](../integrations/testing.md)                                                                 |
| Show notices in Sphinx or MkDocs API pages     | [Docs Integration](../integrations/docs-integration.md)                                               |
| Generate a release-planning table              | [Audit → Deprecation tables](../audit/index.md#generating-deprecation-tables)                         |
| Upgrade pyDeprecate itself                     | [Migration Guide](../migration/index.md#pick-your-upgrade-path)                                       |

## See also

- Use [Customization](customization.md) to redirect deprecation output to a logger or use a custom message template.
- [void() Helper](void-helper.md) explains when and why the deprecated function body should call `void()`.
- Use [Audit Tools](../audit/index.md) to enforce removal deadlines and detect deprecation chains in CI.
- [Troubleshooting](../troubleshooting.md) covers common errors and fixes for `@deprecated` configuration.
- [Compare Python Deprecation Tools](../adoption/compare-python-deprecation-tools.md) explains how pyDeprecate compares to `warnings.warn`, `deprecation`, `wrapt`, and `warnings.deprecated`.
- [Agent Recipes](../adoption/agent-recipes.md) covers copy-paste patterns for AI coding assistants generating deprecation code.
