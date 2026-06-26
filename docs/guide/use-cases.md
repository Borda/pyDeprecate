---
id: use-cases
description: Overview of all deprecation patterns in pyDeprecate — functions, classes, properties, async, and advanced topics. Start here to find the right guide for your use case.
---

# Use Cases

The most common reasons to deprecate something are renaming a function, renaming an argument, or replacing a class. This overview maps each scenario to the right topic page. If you are new to the library, start with [Getting Started](../getting-started.md).

## Topics

### [Functions](functions.md)

Deprecating Python functions and methods: simple call forwarding, argument renaming with `args_mapping`, notice-only deprecation, self argument remapping with `TargetMode.ARGS_REMAP`, stacking multiple decorators for multi-release migrations, and conditional suppression with `skip_if`.

### [Classes](classes.md)

Deprecating classes, Enums, dataclasses, and module-level constants: forwarding an old class name to a replacement with `deprecated_class()`, wrapping module-level objects with `deprecated_instance()`, selective attribute deprecation with `attrs_mapping`, and stacking multiple proxy layers for multi-version attribute migrations.

### [Modules](modules.md)

Deprecating an entire module via PEP 562 `__getattr__`: in-place warn on missing-attribute access (Mode 1), redirect all attribute access to a replacement module with optional per-attribute `attrs_mapping` (Mode 2), and parent alias via `deprecated_instance()` in `__init__.py` (Mode 3). Includes audit integration and PEP 562 real-attribute gap guidance.

### [Properties](properties.md)

Deprecating `@property` and `@cached_property` descriptors: decorator order rules, wrapping all three accessors (`fget`, `fset`, `fdel`) at once, chaining `.setter` / `.deleter`, and the dataclass field alias pattern.

### [Async](async.md)

Deprecating `async def` functions and async generator functions: all three TargetModes, warning timing differences (await-time vs call-time), coroutine introspection behaviour, and concurrency caveats.

### [Advanced](advanced.md)

Advanced patterns: injecting a deprecation notice into the docstring at import time (`update_docstring=True`), supplying a fixed default for a new required argument (`args_extra`), testing helpers (`assert_no_warnings`), deprecating `@classmethod` and `@staticmethod` descriptors, and generator functions.

## Quick decision table

| Scenario                                      | Where to look                                                                                                |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Rename a function or method                   | [Functions → Simple forwarding](functions.md#simple-function-forwarding)                                     |
| Rename an argument                            | [Functions → Argument renaming](functions.md#argument-renaming-and-mapping)                                  |
| Notice only — no replacement yet              | [Functions → Notice-only](functions.md#notice-only-deprecation)                                              |
| Rename an argument within the same function   | [Functions → Self argument mapping](functions.md#self-argument-mapping)                                      |
| Stack decorators across releases              | [Functions → Stacked decorators](functions.md#stacked-deprecation-decorators)                                |
| Suppress notice conditionally                 | [Functions → Conditional skip](functions.md#conditional-skip)                                                |
| Rename a class, Enum, or dataclass            | [Classes → Class deprecation](classes.md#class-deprecation)                                                  |
| Deprecate a module-level constant or object   | [Classes → Constants and instances](classes.md#constants-and-instances)                                      |
| Deprecate selected class attributes           | [Classes → Selective attributes](classes.md#selective-attribute-deprecation)                                 |
| Deprecate a `@property`                       | [Properties](properties.md)                                                                                  |
| Deprecate an `async def` function             | [Async → Async functions](async.md#async-functions)                                                          |
| Deprecate an async generator                  | [Async → Async generators](async.md#async-generators)                                                        |
| Inject deprecation notice into docstring      | [Advanced → Docstring updates](advanced.md#automatic-docstring-updates)                                      |
| Inject a fixed default for a new required arg | [Advanced → Injecting new args](advanced.md#injecting-new-required-arguments)                                |
| Silence warnings in test fixtures             | [Advanced → Testing helpers](advanced.md#suppressing-futurewarning-in-test-fixtures-with-assert_no_warnings) |
| Deprecate a `@classmethod` or `@staticmethod` | [Advanced → Class/static methods](advanced.md#class-methods-and-static-methods)                              |
| Deprecate a generator function                | [Advanced → Generators](advanced.md#deprecating-generator-functions)                                         |
| Deprecate an entire module                    | [Modules](modules.md)                                                                                        |

## See also

- [Customization](customization.md) — redirect deprecation output to a logger or use a custom message template
- [void() Helper](void-helper.md) — when and why the deprecated function body should call `void()`
- [Audit Tools](audit.md) — enforce removal deadlines and detect deprecation chains in CI
- [Troubleshooting](../troubleshooting.md) — common errors and fixes for `@deprecated` configuration
- [Compare Python Deprecation Tools](compare-python-deprecation-tools.md) — how pyDeprecate compares to `warnings.warn`, `deprecation`, `wrapt`, and `warnings.deprecated`
- [Agent Recipes](agent-recipes.md) — copy-paste patterns for AI coding assistants generating deprecation code
