# Changelog

## [Unreleased]

### Changed

- **Docs site URL layout is now versioned.** Content is published under `https://borda.github.io/pyDeprecate/latest/` (for `main`) and `https://borda.github.io/pyDeprecate/<tag>/` (for release tags). The root URL (`https://borda.github.io/pyDeprecate/`) redirects to `latest/`. External bookmarks to flat paths like `.../pyDeprecate/troubleshooting.html` will break on first deploy — update them to `.../pyDeprecate/latest/troubleshooting.html`. ([#148](https://github.com/Borda/pyDeprecate/pull/148))

### Added

- **Multi-page topic documentation site.** Replaced the monolithic README-copy home page with a curated 7-page MkDocs Material site: Home, Getting Started, User Guide (Use Cases / void() Helper / Audit Tools), Troubleshooting, and demo links. Switched theme to Material, added Open Graph tags, JSON-LD structured data (SoftwareApplication / FAQPage / TechArticle per page), spec-compliant `llms.txt`, and `git-revision-date-localized` plugin. README is unchanged (still the PyPI cover page).

- **`pydeprecate` CLI command.** Run `pydeprecate <subcommand> path/to/your/package` to scan any package or module for misconfigured `@deprecated` wrappers — reports invalid argument mappings, identity mappings, and no-effect wrappers with rich-formatted output when `rich` is available. Also available as `python -m deprecate`. ([#76](https://github.com/Borda/pyDeprecate/pull/76))

- **Four CLI subcommands: `check`, `expiry`, `chains`, `all`.** `check` validates wrapper configuration; `expiry` reports wrappers past their `remove_in` deadline (requires `pip install 'pyDeprecate[audit]'`); `chains` detects deprecated-to-deprecated forwarding chains; `all` runs all three in a single scan pass. Flags: `--norecursive`, `--skip_errors`. ([#149](https://github.com/Borda/pyDeprecate/pull/149))

## [0.9.0] — 2026-04-29 — TargetMode transition

### Added

- **`TargetMode` enum exported from `deprecate`.** `TargetMode.TRANSPARENT` replaces `target=None` and `TargetMode.ARGS_ONLY` replaces `target=True`. Both are public API. ([#150](https://github.com/Borda/pyDeprecate/pull/150))

### Changed

- **Legacy `target=None` and `target=True` sentinels now warn at decoration time.** Both forms remain accepted in v0.9, emit `FutureWarning`, and map to `TargetMode.TRANSPARENT` / `TargetMode.ARGS_ONLY` respectively. ([#150](https://github.com/Borda/pyDeprecate/pull/150))
- **`target=False` now emits a `UserWarning`.** The sentinel was never valid; it remains tolerated in v0.9 but is scheduled to become a `TypeError` in v1.0. ([#150](https://github.com/Borda/pyDeprecate/pull/150))
- **Misconfigured `TargetMode` combinations now warn at construction time.** `TargetMode.ARGS_ONLY` without `args_mapping`, `TargetMode.TRANSPARENT` with `args_mapping`, and `TargetMode.TRANSPARENT` with `args_extra` all surface a `UserWarning` immediately. ([#150](https://github.com/Borda/pyDeprecate/pull/150))

______________________________________________________________________

## [0.7.0] — 2026-03-31 — Docstring Tooling

### Added

- **MkDocs admonition output.** `@deprecated` now accepts `docstring_style="mkdocs"` (alias: `"markdown"`). When `update_docstring=True`, the deprecation notice is injected as a `!!! warning "Deprecated in X"` admonition instead of a Sphinx `.. deprecated::` directive. Use `docstring_style="auto"` to detect style automatically from existing docstring content. ([#134](https://github.com/Borda/pyDeprecate/pull/134))
- **Google / NumPy section-aware docstring injection.** `update_docstring=True` now inserts the deprecation notice *before* the first section (`Args:`, `Returns:`, `Parameters`, …) rather than appending it at the end. ([#134](https://github.com/Borda/pyDeprecate/pull/134))
- **Inline arg deprecation in docstrings.** When `args_mapping` is set and `update_docstring=True`, each renamed or removed argument is annotated directly in the `Args:` / `:param` section of the docstring. ([#136](https://github.com/Borda/pyDeprecate/pull/136))
- **Griffe extension for mkdocstrings** (`deprecate.docstring.griffe_ext`, beta) and **Sphinx autodoc extension for deprecated classes** (`deprecate.docstring.sphinx_ext`, beta). ([#134](https://github.com/Borda/pyDeprecate/pull/134))
- **Live demo documentation** published to GitHub Pages — MkDocs demo, Sphinx demo, and portal landing page. ([#134](https://github.com/Borda/pyDeprecate/pull/134), [#137](https://github.com/Borda/pyDeprecate/pull/137))

### Fixed

- Fixed `getattr`/`setattr` string-literal calls (B009/B010) replaced with direct attribute access. ([#139](https://github.com/Borda/pyDeprecate/pull/139))
- Fixed proxy swap skipped correctly when `super().import_object()` returns `False` in the Griffe extension; empty `_proxy_doc` now delegates to `super().get_doc()` in the Sphinx extension. ([#139](https://github.com/Borda/pyDeprecate/pull/139))

______________________________________________________________________

## [0.6.0.post0] — 2026-03-14 — Deprecation Proxy for class/instances

### Changed

- **Softer class-deprecation fallback.** `@deprecated` applied directly to a class (Enum, dataclass, or plain class) now emits a `UserWarning` at decoration time and delegates to `deprecated_class()` internally, instead of raising `TypeError`. Code using the old pattern continues to work; the warning points to the recommended API. ([#132](https://github.com/Borda/pyDeprecate/pull/132))

## [0.6.0] — 2026-03-13

### Added

- **`deprecated_class()` and `deprecated_instance()` — full proxy support.** Enum, dataclass, and built-in types can now be wrapped in a transparent proxy. Attribute access, item access, method calls, and class behaviour all forward to the underlying type with a `FutureWarning` emitted on first access. ([#114](https://github.com/Borda/pyDeprecate/pull/114))
- **Correct `isinstance()` / `issubclass()` semantics on proxy classes.** `isinstance(x, proxy)` and `issubclass(Sub, proxy)` now work as expected — previously raised `TypeError`. Type checks do not consume the warning budget. ([#126](https://github.com/Borda/pyDeprecate/pull/126))

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

______________________________________________________________________

## [0.5.0] — 2026-02-23 — Deprecation Lifecycle Management

### Added

- **`deprecate.audit` module — deprecation lifecycle management.** A dedicated module grouping all inspection and enforcement utilities, designed to be called from pytest or CI scripts. Requires the optional `[audit]` extra: `pip install pyDeprecate[audit]`. ([#111](https://github.com/Borda/pyDeprecate/pull/111))
- **`find_deprecated_callables()` / `validate_deprecated_callable()` — zero-impact wrapper detection.** Scans a module or package for `@deprecated` wrappers that have no real effect: invalid `args_mapping` keys, identity mappings, self-referencing targets, or missing version fields. Returns `DeprecatedCallableInfo` dataclasses. ([#72](https://github.com/Borda/pyDeprecate/pull/72))
- **`validate_deprecation_expiry()` — enforce removal deadlines in CI.** Scans a module or package and returns all wrappers whose `remove_in` version has been reached or passed. Auto-detects the installed package version. Integrate as a pytest fixture or CI step to prevent zombie code from shipping past its scheduled removal. ([#89](https://github.com/Borda/pyDeprecate/pull/89))
- **`validate_deprecation_chains()` — detect deprecated-to-deprecated forwarding.** Identifies wrappers whose `target` is itself a deprecated callable, forming chains that users traverse unnecessarily. Reports two chain kinds via the `ChainType` enum: `TARGET` (forwarding chain) and `STACKED` (composed argument mappings). ([#90](https://github.com/Borda/pyDeprecate/pull/90))

### Fixed

- **`@deprecated` wrappers now correctly handle var-positional Enum signatures.** A subtle edge case where callables with var-positional parameters in their Enum signature caused incorrect argument forwarding is now resolved. ([#104](https://github.com/Borda/pyDeprecate/pull/104))

______________________________________________________________________

## [0.4.0] — 2025-12-03 — Enhanced Documentation & Modernization

### Added

- **`update_docstring` parameter — automatic Sphinx deprecation notices.** Set `update_docstring=True` on `@deprecated` to automatically append a `.. deprecated::` reStructuredText block to the function's docstring. IDE tooltips and Sphinx-generated API docs show the notice without any manual edits. ([#31](https://github.com/Borda/pyDeprecate/pull/31))

### Changed

- **Deprecation warnings now use `FutureWarning` instead of `DeprecationWarning`.** `DeprecationWarning` is silenced by Python's default warning filters outside of test contexts, making it invisible to most end-users. `FutureWarning` is shown by default, ensuring callers actually see the migration message. ([#16](https://github.com/Borda/pyDeprecate/pull/16))
- **Minimum Python version raised to 3.9.** Python 3.8 reached end-of-life in October 2024. ([#73](https://github.com/Borda/pyDeprecate/pull/73))
- **License changed from MIT to Apache-2.0.**
- **Error messages now include the originating class or function name** for easier debugging when a mapping fails. ([#11](https://github.com/Borda/pyDeprecate/pull/11))

______________________________________________________________________

## [0.3.2] — 2021-06-11 — Support containing `kwargs` in target function

### Added

- **`target` functions using `**kwargs` are now supported.** Previously, forwarding to a target that accepted `**kwargs` and accessed them via `kwargs.get(...)` raised `TypeError` for unrecognised argument names. Extra arguments from the deprecated call are now forwarded correctly. ([#6](https://github.com/Borda/pyDeprecate/pull/6))

## [0.3.1] — 2021-05-31 — Fixed `void` typing

### Fixed

- **`void()` type annotation corrected to satisfy mypy.** The return type of `void()` is now properly annotated — IDE and type checker warnings about unused parameters in deprecated function bodies are suppressed correctly.

## [0.3.0] — 2021-04-21 — Conditional skip

### Added

- **`skip_if` parameter — conditional deprecation.** Pass a `bool` or a zero-argument callable returning `bool` to skip the warning and forwarding when a runtime condition is true. Useful for gating deprecation behaviour on package version checks or feature flags. ([#4](https://github.com/Borda/pyDeprecate/pull/4))

______________________________________________________________________

## [0.2.0] — 2021-03-29 — Improved self arg deprecations

### Added

- **`target=True` — self-deprecation mode.** Deprecate and remap arguments within the same function without forwarding to a separate callable. Use with `args_mapping` to rename a parameter while keeping the function body intact. ([#3](https://github.com/Borda/pyDeprecate/pull/3))
- **`void()` helper.** Accepts any arguments and returns `None`. Silences IDE "unused parameter" warnings in deprecated function bodies where the body is never reached.
- **`no_warning_call()` context manager.** Assert that a block of code raises no deprecation warning — useful for verifying that new API paths are clean in tests. Renamed to `assert_no_warnings()` in v0.6.0. ([#2](https://github.com/Borda/pyDeprecate/pull/2))
- **Stacked `@deprecated` decorators.** Multiple `@deprecated(True, ...)` decorators can be stacked on the same function for multi-hop argument migrations across versions, each with independent warning counts and version metadata.

______________________________________________________________________

## [0.1.1] — 2021-03-21 — Allow infinite warning

### Added

- **`num_warns=-1` — always-on warnings.** Setting `num_warns` to `-1` causes the deprecation warning to fire on every call rather than stopping after N times.
- **`target=None` — warn-only mode.** The original function body still executes; `@deprecated` adds only a warning with no call forwarding. Useful when you want to signal deprecation without changing any call behaviour.

## [0.1.0] — 2021-03-20 — Initial release

### Added

- **`@deprecated(target=callable)` decorator.** Marks a function as deprecated and automatically forwards all calls — including argument mapping — to a replacement function. The deprecated function body is never executed when `target` is a callable.
- **Automatic argument mapping.** Positional arguments are resolved to keyword arguments and forwarded to the target's signature. `args_mapping` renames (`{"old": "new"}`) or drops (`{"old": None}`) individual arguments during forwarding.
- **`args_extra` — inject additional kwargs into the target call.** Pass a `dict` of extra keyword arguments to merge into every forwarded call. Useful for providing default values or adapter arguments that the deprecated API never accepted.
- **Configurable warning count (`num_warns`).** Warnings fire once per function by default; set to any positive integer to limit the total count per function lifetime.
- **Custom warning message template (`template_mgs`).** Format string with `%(source_name)s`, `%(target_path)s`, `%(deprecated_in)s`, `%(remove_in)s`, and `%(argument_map)s` placeholders.
- **Custom warning stream (`stream`).** Route warnings to `logging.warning`, `warnings.warn`, or any callable.
