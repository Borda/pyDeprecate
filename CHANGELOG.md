# Changelog

## [0.6.0.post0] — 2026-03-15 — Deprecation Proxy for class/instances

### Changed

- **Softer class-deprecation fallback.** `@deprecated` applied directly to a class (Enum, dataclass, or plain class) now emits a `UserWarning` at decoration time and delegates to `deprecated_class()` internally, instead of raising `TypeError`. Code using the old pattern continues to work; the warning points to the recommended API. ([#132](https://github.com/Borda/pyDeprecate/pull/132))

## [0.6.0] — 2026-03-12

### Added

- **`deprecated_class()` and `deprecated_instance()` — full proxy support.** Enum, dataclass, and built-in types can now be wrapped in a transparent `_DeprecatedProxy`. Attribute access, item access, method calls, and class behaviour all forward to the underlying type with a `FutureWarning` emitted on first access. ([#114](https://github.com/Borda/pyDeprecate/pull/114))
- **Correct `isinstance()` / `issubclass()` semantics on proxy classes.** `_DeprecatedProxy` now implements `__instancecheck__` and `__subclasscheck__` so `isinstance(x, proxy)` and `issubclass(Sub, proxy)` work as expected when a proxy is the second argument — previously raised `TypeError`. Type checks do not consume the warning budget. ([#126](https://github.com/Borda/pyDeprecate/pull/126))

### Changed

- **`@deprecated` on a class raises `TypeError`.** Applying `@deprecated` directly to a class now raises `TypeError` at decoration time instead of silently misbehaving. Superseded in `v0.6.0.post0` by a `UserWarning` + delegation to `deprecated_class()`. Use `@deprecated_class()` for class-level deprecation. ([#120](https://github.com/Borda/pyDeprecate/pull/120))

### Deprecated

- **Audit API renamed for consistency.** Old names remain as `@deprecated` shims until v1.0. ([#125](https://github.com/Borda/pyDeprecate/pull/125))

  | Old name                       | New name                       |
  | ------------------------------ | ------------------------------ |
  | `find_deprecated_callables`    | `find_deprecation_wrappers`    |
  | `validate_deprecated_callable` | `validate_deprecation_wrapper` |
  | `DeprecatedCallableInfo`       | `DeprecationWrapperInfo`       |

- **`no_warning_call` renamed to `assert_no_warnings`.** The new name mirrors `assertWarns` / `assertRaises` from the standard library, making test intent immediately obvious. Old name kept as a deprecated alias until v1.0. ([#131](https://github.com/Borda/pyDeprecate/pull/131))

### Fixed

- **Cross-class method forwarding now fails at decoration time.** Passing a class as `target` on a non-`__init__` method previously silently forwarded `self` of the wrong type — always a runtime bug, never a valid pattern. The guard now raises `TypeError` at decoration time so the misconfiguration is caught immediately. ([#121](https://github.com/Borda/pyDeprecate/pull/121))
- **`find_deprecation_wrappers()` no longer reports false `invalid_args` for proxy objects.** The proxy `__call__` catch-all signature previously caused all `args_mapping` keys to be flagged as invalid; signature validation is now skipped for proxy objects. ([#124](https://github.com/Borda/pyDeprecate/pull/124))

## [0.5.0] — 2026-02-23 — Deprecation Lifecycle Management

### Added

- **`deprecate.audit` module — deprecation lifecycle management.** A dedicated module grouping all inspection and enforcement utilities, designed to be called from pytest or CI scripts. Requires the optional `[audit]` extra: `pip install pyDeprecate[audit]`. ([#111](https://github.com/Borda/pyDeprecate/pull/111))
- **`find_deprecated_callables()` / `validate_deprecated_callable()` — zero-impact wrapper detection.** Scans a module or package for `@deprecated` wrappers that have no real effect: invalid `args_mapping` keys, identity mappings, self-referencing targets, or missing version fields. Returns `DeprecatedCallableInfo` dataclasses. ([#72](https://github.com/Borda/pyDeprecate/pull/72))
- **`validate_deprecation_expiry()` — enforce removal deadlines in CI.** Scans a module or package and returns all wrappers whose `remove_in` version has been reached or passed. Auto-detects the installed package version via `importlib.metadata`. Integrate as a pytest fixture or CI step to prevent zombie code from shipping past its scheduled removal. ([#89](https://github.com/Borda/pyDeprecate/pull/89))
- **`validate_deprecation_chains()` — detect deprecated-to-deprecated forwarding.** Identifies wrappers whose `target` is itself a deprecated callable, forming chains that users traverse unnecessarily. Reports two chain kinds via the `ChainType` enum: `TARGET` (forwarding chain) and `STACKED` (composed argument mappings). ([#90](https://github.com/Borda/pyDeprecate/pull/90))

### Fixed

- **`@deprecated` wrappers now correctly handle var-positional Enum signatures.** A subtle edge case where callables with var-positional parameters in their Enum signature caused incorrect argument forwarding is now resolved. ([#104](https://github.com/Borda/pyDeprecate/pull/104))

## [0.4.0] — 2025-12-03 — Enhanced Documentation & Modernization

### Added

- **`update_docstring` parameter — automatic Sphinx deprecation notices.** Set `update_docstring=True` on `@deprecated` to automatically append a `.. deprecated::` reStructuredText block to the function's docstring. IDE tooltips and Sphinx-generated API docs show the notice without any manual edits.
- **Python 3.12, 3.13, and 3.14 support.** All new interpreter versions are tested in CI. ([#66](https://github.com/Borda/pyDeprecate/pull/66))

### Changed

- **Deprecation warnings now use `FutureWarning` instead of `DeprecationWarning`.** `DeprecationWarning` is silenced by Python's default warning filters outside of test contexts, making it invisible to most end-users. `FutureWarning` is shown by default, ensuring callers actually see the migration message. ([#16](https://github.com/Borda/pyDeprecate/pull/16))
- **Minimum Python version raised to 3.9.** Python 3.8 reached end-of-life in October 2024. ([#73](https://github.com/Borda/pyDeprecate/pull/73))
- **License changed from MIT to Apache-2.0.**
- **Error messages now include the originating class or function name** for easier debugging when a mapping fails. ([#11](https://github.com/Borda/pyDeprecate/pull/11))
- **Project source layout moved to `src/`.** No API changes — import paths are unchanged. ([#29](https://github.com/Borda/pyDeprecate/pull/29))

## [0.3.2] — 2021-06-11 — Support containing `kwargs` in target function

### Added

- **`target` functions using `**kwargs` are now supported.** Previously, forwarding to a target that accepted `**kwargs` and accessed them via `kwargs.get(...)` raised `TypeError` for unrecognised argument names. Extra arguments from the deprecated call are now forwarded correctly. ([#6](https://github.com/Borda/pyDeprecate/pull/6))

## [0.3.1] — 2021-05-31 — Fixed `void` typing

### Fixed

- **`void()` type annotation corrected to satisfy mypy.** The return type of `void()` is now properly annotated — IDE and type checker warnings about unused parameters in deprecated function bodies are suppressed correctly.

## [0.3.0] — 2021-04-21 — Conditional skip

### Added

- **`skip_if` parameter — conditional deprecation.** Pass a `bool` or a zero-argument callable returning `bool` to skip the warning and forwarding when a runtime condition is true. Useful for gating deprecation behaviour on package version checks or feature flags. ([#4](https://github.com/Borda/pyDeprecate/pull/4))

## [0.2.0] — 2021-03-29 — Improved self arg deprecations

### Added

- **`target=True` — self-deprecation mode.** Deprecate and remap arguments within the same function without forwarding to a separate callable. Use with `args_mapping` to rename a parameter while keeping the function body intact. ([#3](https://github.com/Borda/pyDeprecate/pull/3))
- **`void()` helper.** Accepts any arguments and returns `None`. Silences IDE "unused parameter" warnings in deprecated function bodies where the body is never reached.
- **`no_warning_call()` context manager.** Assert that a block of code raises no deprecation warning — useful for verifying that new API paths are clean in tests. Renamed to `assert_no_warnings()` in v0.6.0. ([#2](https://github.com/Borda/pyDeprecate/pull/2))
- **Stacked `@deprecated` decorators.** Multiple `@deprecated(True, ...)` decorators can be stacked on the same function for multi-hop argument migrations across versions, each with independent warning counts and version metadata.

## [0.1.1] — 2021-03-21 — Allow infinite warning

### Added

- **`num_warns=-1` — always-on warnings.** Setting `num_warns` to `-1` causes the deprecation warning to fire on every call rather than stopping after N times.
- **`target=None` — warn-only mode.** The original function body still executes; `@deprecated` adds only a warning with no call forwarding. Useful when you want to signal deprecation without changing any call behaviour.

## [0.1.0] — 2021-03-20 — Initial release

### Added

- **`@deprecated(target=callable)` decorator.** Marks a function as deprecated and automatically forwards all calls — including argument mapping — to a replacement function. The deprecated function body is never executed when `target` is a callable.
- **Automatic argument mapping.** Positional arguments are resolved to keyword arguments and forwarded to the target's signature. `args_mapping` renames (`{"old": "new"}`) or drops (`{"old": None}`) individual arguments during forwarding.
- **Configurable warning count (`num_warns`).** Warnings fire once per function by default; set to any positive integer to limit the total count per function lifetime.
- **Custom warning message template (`template_mgs`).** Format string with `%(source_name)s`, `%(target_path)s`, `%(deprecated_in)s`, `%(remove_in)s`, and `%(argument_map)s` placeholders.
- **Custom warning stream (`stream`).** Route warnings to `logging.warning`, `warnings.warn`, or any callable.
