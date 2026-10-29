# Changelog

All notable changes to the `pydeprecate` coding-agent plugin (the `sunset` and `prune` skills) are listed here. The plugin `version` follows its own cadence, independent of the `pyDeprecate` package version; `compatible_package_version` is the package support window the skills are verified against.

## [0.5.1] - Unreleased

### Fixed

- `sunset`: block-scoped fatal handling no longer describes `as_exceptions()` as enabling the process-wide switch; it now says the call makes deprecations fatal for the block and restores the previous setting on exit, matching the per-thread and per-task scope.

## [0.5.0] - 2026-10-16

### Added

- `sunset`: guidance for the scoped `deprecate.as_exceptions()` context manager, with a `hasattr` fallback to `AS_EXCEPTIONS` for installed releases that lack it.

## [0.4.0] - 2026-10-02

### Added

- `sunset`: note that type checkers and IDEs flag a deprecation only through `warnings.deprecated` or `typing_extensions.deprecated`, and how to add editor support alongside pyDeprecate's decorators.

## [0.3.0] - 2026-09-30

### Added

- `sunset`: opt-in fatal deprecations with `as_exception=True` (since pyDeprecate 0.14) for the final release before removal.

### Changed

- `compatible_package_version` raised from `>=0.10` to `>=0.11`; the `message_template` guidance gained a since-note (`template_mgs` on 0.11).

## [0.2.0] - 2026-09-18

### Added

- `sunset`: read the project's `[tool.pydeprecate.policy]` table before choosing `deprecated_in` and `remove_in`, present the derived versions for confirmation, and verify with `pydeprecate policy`.

## [0.1.0] - 2026-09-17

### Added

- Initial release for Claude Code and Codex: the `sunset` skill (add a deprecation) and the `prune` skill (retire compatibility due by a given release).
