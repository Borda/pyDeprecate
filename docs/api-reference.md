# API Reference

This reference is auto-generated from source docstrings. For usage examples, see the [Use Cases](guide/use-cases.md) guide. For installation and a first deprecation, see [Getting Started](getting-started.md).

The `pydeprecate` command-line tool is documented in the [CLI Reference](guide/cli.md).

## Decorator

::: deprecate.deprecation.deprecated

::: deprecate.routine.deprecated_callable

## Proxy

::: deprecate.proxy.deprecated_class

::: deprecate.proxy.deprecated_instance

## Module

::: deprecate.module.deprecated_module

## Fatal deprecations

`deprecate.AS_EXCEPTIONS` is a plain `bool` module attribute: the process-wide switch that turns every deprecation into a `DeprecatedError`. It is seeded at import from the `DEPRECATE_AS_EXCEPTIONS` environment variable and re-read on every emission. See [Fatal deprecations](guide/functions.md#fatal-deprecations).

::: deprecate.as_exceptions

::: deprecate.DeprecatedError

## Audit

::: deprecate.audit.find_deprecation_wrappers

::: deprecate.audit.find_deprecation_wrappers_static

::: deprecate.audit.StaticScanResult

::: deprecate.audit.validate_deprecation_wrapper

::: deprecate.audit.validate_deprecation_expiry

::: deprecate.audit.validate_deprecation_policy

::: deprecate.audit.validate_deprecation_chains

::: deprecate.audit.validate_mapping_compatibility

::: deprecate.audit.generate_deprecation_table

::: deprecate.audit.DeprecationWrapperInfo

::: deprecate.audit.ChainType

::: deprecate.audit.PolicyRule

::: deprecate.audit.GraceWindow

::: deprecate.audit.GraceWindowSpec

::: deprecate.audit.VersionBump

::: deprecate.audit.DeprecationStatus

::: deprecate.audit.TableStyle

### Legacy audit aliases

These aliases are deprecated since v0.6 and removed in v1.0. Use the replacement named in each entry.

::: deprecate.audit.DeprecatedCallableInfo

::: deprecate.audit.find_deprecated_callables

::: deprecate.audit.validate_deprecated_callable

## Types

::: deprecate.TargetMode

::: deprecate.DeprecationProxy

::: deprecate.get_deprecation_config

## Utilities

::: deprecate.utils.void

::: deprecate.utils.assert_no_warnings

## Command line

The `pydeprecate` command wraps the audit functions above for CI and pre-commit use. Its subcommands, flags and exit codes are listed in the [CLI Reference](guide/cli.md).
