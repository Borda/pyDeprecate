# Transition Coverage

Tracks which wrappers in the test collection files exercise the **legacy bool/None sentinels** (`target=True`, `target=None`, `target=False`) versus the **modern `TargetMode` enum** (`TargetMode.NOTIFY`, `TargetMode.ARGS_REMAP`).

Legend:

- **Legacy** — wrapper uses the deprecated `target=True` / `target=None` / `target=False` literal; kept intentionally to verify backward-compatibility paths and that the right warnings are emitted.
- **Modern** — wrapper uses `TargetMode.*` enum or a callable target.

______________________________________________________________________

## `tests/collection_deprecate.py`

All modern — no legacy sentinels.

| Wrapper                                                            | `target=`                    | Notes                                                        |
| ------------------------------------------------------------------ | ---------------------------- | ------------------------------------------------------------ |
| `decorated_sum_warn_only`                                          | `TargetMode.NOTIFY`          | Decorator form; legacy parity in `collection_depr_legacy.py` |
| `wrapped_sum_warn_only`                                            | `TargetMode.NOTIFY`          | Wrapper assignment form                                      |
| `depr_target_mode_whole_warns_on_every_call`                       | `TargetMode.NOTIFY`          | `num_warns=-1`                                               |
| `depr_target_mode_whole_executes_original_body`                    | `TargetMode.NOTIFY`          | Body execution verified                                      |
| `depr_func_no_remove_in`                                           | `TargetMode.NOTIFY`          | No `remove_in` deadline                                      |
| `ServiceCls.old_warn_method`                                       | `TargetMode.NOTIFY`          | Class method; legacy parity in `collection_depr_legacy.py`   |
| `make_target_mode_whole_with_args_mapping_warns`                   | `TargetMode.NOTIFY`          | `UserWarning`: NOTIFY + `args_mapping` (misconfig)           |
| `make_target_mode_whole_with_args_extra_warns`                     | `TargetMode.NOTIFY`          | `UserWarning`: NOTIFY + `args_extra` (misconfig)             |
| `decorated_pow_self`                                               | `TargetMode.ARGS_REMAP`      | Decorator form; legacy parity in `collection_depr_legacy.py` |
| `wrapped_pow_self`                                                 | `TargetMode.ARGS_REMAP`      | Wrapper assignment form                                      |
| `depr_pow_self_double`                                             | `TargetMode.ARGS_REMAP`      | Two args remapped in one decorator                           |
| `depr_pow_self_twice`                                              | `TargetMode.ARGS_REMAP`      | Chained decorators, multi-step migration                     |
| `depr_pow_skip_if_true_false`                                      | `TargetMode.ARGS_REMAP`      | `skip_if=True` outer, `False` inner                          |
| `depr_target_mode_args_only_warns_when_old_arg_passed`             | `TargetMode.ARGS_REMAP`      | Warning fires on old arg                                     |
| `depr_target_mode_args_only_silent_when_new_arg_passed`            | `TargetMode.ARGS_REMAP`      | Silent when new arg used                                     |
| `depr_target_mode_args_only_remaps_kwargs`                         | `TargetMode.ARGS_REMAP`      | kwargs remapping                                             |
| `depr_target_mode_args_only_with_args_extra_injects_kwargs`        | `TargetMode.ARGS_REMAP`      | `args_extra` injection                                       |
| `fn_remap_with_extra`                                              | `TargetMode.ARGS_REMAP`      | ARGS_REMAP + `args_extra` (regression fix)                   |
| `ThisCls.__init__`                                                 | `TargetMode.ARGS_REMAP`      | Class `__init__` self-deprecation                            |
| `ServiceCls.self_renamed_method`                                   | `TargetMode.ARGS_REMAP`      | Class method; legacy parity in `collection_depr_legacy.py`   |
| `make_target_mode_args_only_without_args_mapping_warns`            | `TargetMode.ARGS_REMAP`      | `UserWarning`: ARGS_REMAP without `args_mapping`             |
| `{decorated/wrapped}_sum`                                          | `base_sum_kwargs`            | Basic forwarding                                             |
| `depr_make_new_cls` / `depr_make_new_cls_mapped`                   | `NewCls`                     | Function→class forwarding                                    |
| `{decorated/wrapped}_sum_no_stream`                                | `base_sum_kwargs`            | Silent (`stream=None`)                                       |
| `{decorated/wrapped}_sum_calls_2`                                  | `base_sum_kwargs`            | `num_warns=2`                                                |
| `{decorated/wrapped}_sum_calls_inf`                                | `base_sum_kwargs`            | `num_warns=-1`                                               |
| `{decorated/wrapped}_sum_msg`                                      | `base_sum_kwargs`            | Custom `template_mgs`                                        |
| `depr_pow_args` / `depr_pow_mix` / `depr_pow_wrong`                | `base_pow_args`              | Positional args; mismatch case                               |
| `depr_accuracy_skip` / `depr_accuracy_map` / `depr_accuracy_extra` | `accuracy_score`             | Drop, rename, inject args                                    |
| `PastCls.__init__` / `PastClsMapped.__init__`                      | `NewCls`                     | Class `__init__` forwarding                                  |
| `ServiceCls.old_redirect_method` / `.old_mapped_method`            | `compute` / `compute_scaled` | Method→method forwarding                                     |
| `CrossGuardSameClass.old_method`                                   | `new_method`                 | Same-class cross-guard                                       |
| `CrossGuardModuleLevel.old_method`                                 | module fn                    | Method→module-level fn                                       |
| `CrossGuardOldClass.__init__`                                      | `CrossGuardClassTargetNew`   | Constructor→constructor                                      |
| `depr_timing_wrapper`                                              | `timing_wrapper`             | Decorator wrapping a decorator                               |
| `DeprecatedTimerDecorator.__init__`                                | `TimerDecorator`             | Class-based decorator                                        |
| `depr_collision_old_new`                                           | `both_old_new_target`        | Old + new param both present                                 |
| `fn_old_default`                                                   | `fn_with_default`            | Stale-default regression                                     |
| `depr_func_targeting_proxy`                                        | `DeprecatedColorEnum`        | Function targeting a proxy                                   |

### Class / instance deprecation (`deprecated_class`, `deprecated_instance`)

| Wrapper                                                                                  | API                                              | Notes                            |
| ---------------------------------------------------------------------------------------- | ------------------------------------------------ | -------------------------------- |
| `{Decorated/Wrapped}Enum`                                                                | `deprecated_class(target=NewEnum)`               | Enum form-equivalence pair       |
| `DeprecatedEnum` / `DeprecatedIntEnum`                                                   | `deprecated_class` (no target)                   | Warn-only enum                   |
| `RedirectedEnum` / `MappedEnum` / `MappedIntEnum` / `MappedValueEnum` / `SelfMappedEnum` | `deprecated_class(target=...)`                   | Enum variants                    |
| `{Decorated/Wrapped}DataClass`                                                           | `deprecated_class(target=NewDataClass)`          | Dataclass form-equivalence pair  |
| `DeprecatedDataClass` / `RedirectedDataClass`                                            | `deprecated_class`                               | Dataclass variants               |
| `DeprecatedColorEnum` / `WarnOnlyColorEnum` / `MappedColorEnum`                          | `deprecated_class`                               | Color enum variants              |
| `MappedDataClass` / `MappedDropArgDataClass` / `DeprecatedColorDataClass`                | `deprecated_class(target=...)`                   | Dataclass with mapping           |
| `ProxyArgsRemapAuto`                                                                     | `deprecated_class`                               | Auto-promotes via `args_mapping` |
| `ProxyCallableWithArgsMapping` / `ProxyClassWithArgsExtra` / `ChainedProxyColorEnum`     | `deprecated_class(target=...)`                   | Proxy edge cases                 |
| `ProxyArgsRemapForArgWarnMessage`                                                        | `deprecated_class(target=TargetMode.ARGS_REMAP)` | Per-arg warning via proxy        |
| `depr_config_dict` / `depr_config_dict_read_only`                                        | `deprecated_instance(...)`                       | Instance deprecation             |

______________________________________________________________________

## `tests/collection_depr_legacy.py`

All legacy — every wrapper uses the deprecated `target=True` / `target=None` sentinel directly. Decoration-time `FutureWarning` suppressed via `filterwarnings` in `pyproject.toml`. Names mirror their modern counterparts in `collection_deprecate.py`.

| Wrapper                                                     | `target=` | Notes                                                    |
| ----------------------------------------------------------- | --------- | -------------------------------------------------------- |
| `decorated_sum_warn_only`                                   | `None`    | Warn-only; call-time parity with modern fixture          |
| `decorated_pow_self`                                        | `True`    | Self-deprecation; call-time parity with modern fixture   |
| `ServiceCls.old_warn_method`                                | `None`    | Method warn-only; parity with modern `ServiceCls`        |
| `ServiceCls.self_renamed_method`                            | `True`    | Method self-deprecation; parity with modern `ServiceCls` |
| `depr_target_mode_args_only_with_args_extra_injects_kwargs` | `True`    | `target=True` + `args_extra` parity                      |
| `depr_target_mode_whole_warns_on_every_call`                | `None`    | `num_warns=-1`; parity with modern fixture               |
| `depr_target_mode_whole_executes_original_body`             | `None`    | Body execution parity with modern fixture                |
| `depr_target_mode_args_only_warns_when_old_arg_passed`      | `True`    | ARGS_REMAP warns on old arg parity                       |
| `depr_target_mode_args_only_silent_when_new_arg_passed`     | `True`    | ARGS_REMAP silent on new arg parity                      |
| `depr_target_mode_args_only_remaps_kwargs`                  | `True`    | ARGS_REMAP kwarg remapping parity                        |
| `depr_pow_self_double`                                      | `True`    | Two-arg remap parity with modern fixture                 |
| `fn_remap_with_extra`                                       | `True`    | `args_extra` + remap parity with modern fixture          |
| `ThisCls.__init__`                                          | `True`    | Class `__init__` self-deprecation parity                 |

Decoration-time `FutureWarning` / `UserWarning` assertions for `target=None` / `target=True` / `target=False` are exercised inline in `tests/integration/test_target_mode.py::TestLegacySentinels`.

______________________________________________________________________

## `tests/collection_misconfigured.py`

| Wrapper                                     | `target=`               | Notes                                                       |
| ------------------------------------------- | ----------------------- | ----------------------------------------------------------- |
| `invalid_args_deprecation`                  | `TargetMode.ARGS_REMAP` | Nonexistent key in `args_mapping`                           |
| `empty_mapping_deprecation`                 | `TargetMode.ARGS_REMAP` | `args_mapping={}` — no-op                                   |
| `none_mapping_deprecation`                  | `TargetMode.ARGS_REMAP` | `args_mapping=None` — no-op                                 |
| `identity_mapping_deprecation`              | `TargetMode.ARGS_REMAP` | Single identity mapping                                     |
| `all_identity_mapping_deprecation`          | `TargetMode.ARGS_REMAP` | All-identity mapping                                        |
| `partial_identity_mapping_deprecation`      | `TargetMode.ARGS_REMAP` | Mixed identity + valid mapping                              |
| `self_referencing_deprecation`              | callable (patched)      | Wrapper targeting itself                                    |
| `target_false_deprecation`                  | `target=False`          | Invalid sentinel; audit flags as misconfigured              |
| `whole_with_mapping_deprecation`            | `TargetMode.NOTIFY`     | NOTIFY + `args_mapping` ignored                             |
| `args_only_no_mapping_deprecation`          | `TargetMode.ARGS_REMAP` | ARGS_REMAP without `args_mapping`                           |
| `whole_clean_deprecation`                   | `TargetMode.NOTIFY`     | Correctly configured NOTIFY                                 |
| `args_only_clean_deprecation`               | `TargetMode.ARGS_REMAP` | Correctly configured ARGS_REMAP                             |
| `make_class_target_none_with_args_mapping`  | `target=None`           | `target=None` normalised to NOTIFY; `args_mapping` stripped |
| `make_class_target_false`                   | `target=False`          | `target=False` invalid; surfaces as misconfig               |
| `make_class_target_false_with_args_mapping` | `target=False`          | `target=False` + `args_mapping`; both flagged as misconfig  |

______________________________________________________________________

## `tests/collection_docstrings.py`

All modern. Tests `update_docstring=True` behavior across docstring styles.

| Wrapper                              | `target=`               | Notes                                          |
| ------------------------------------ | ----------------------- | ---------------------------------------------- |
| `old_no_target_function`             | `TargetMode.NOTIFY`     | Docstring: no `:func:` ref when NOTIFY         |
| `no_target_with_args_mapping`        | `TargetMode.NOTIFY`     | Docstring: NOTIFY + `args_mapping` (misconfig) |
| `mkdocs_no_target_with_args_mapping` | `TargetMode.NOTIFY`     | Same, MkDocs docstring style                   |
| `google_args_removed`                | `TargetMode.ARGS_REMAP` | Arg dropped, Google style                      |
| `google_args_renamed`                | `TargetMode.ARGS_REMAP` | Arg renamed, Google style                      |
| `sphinx_args_removed`                | `TargetMode.ARGS_REMAP` | Arg dropped, Sphinx style                      |
| `args_not_in_docstring`              | `TargetMode.ARGS_REMAP` | Mapped arg absent from docstring               |
| `google_multi_args_all_found`        | `TargetMode.ARGS_REMAP` | Multiple args, all documented                  |
| `google_partial_annotation`          | `TargetMode.ARGS_REMAP` | Multiple args, partial docstring               |
| `google_arguments_header`            | `TargetMode.ARGS_REMAP` | `Arguments:` header variant                    |
| `sphinx_arg_not_in_docstring`        | `TargetMode.ARGS_REMAP` | Sphinx arg absent from docstring               |
| `google_args_multiline`              | `TargetMode.ARGS_REMAP` | Multiline Google-style description             |
| `sphinx_args_multiline`              | `TargetMode.ARGS_REMAP` | Multiline Sphinx-style description             |
| `old_function`                       | `new_function`          | RST style, `update_docstring=True`             |
| `old_function_plain`                 | `new_function`          | No existing docstring                          |
| `old_google_no_sections_function`    | `new_function`          | Google style, no sections                      |
| `old_numpy_no_sections_function`     | `new_function`          | NumPy style, no sections                       |
| `old_google_style_function`          | `new_function`          | Google style with sections                     |
| `old_numpy_style_function`           | `new_function`          | NumPy style with sections                      |
| `old_no_remove_version_function`     | `new_function`          | No `remove_in` deadline                        |
| `old_mkdocs_style_function`          | `new_function`          | MkDocs style                                   |
| `old_markdown_alias_function`        | `new_function`          | `markdown` style alias                         |
| `OldClass.__init__`                  | `NewClass`              | Class `__init__` update                        |
| `OldClassPlain.__init__`             | `NewClass`              | Class `__init__`, no docstring                 |
| `callable_target_with_args_mapping`  | `new_function`          | Callable + `args_mapping`                      |

______________________________________________________________________

## Summary

| File                          | NOTIFY | ARGS_REMAP | callable | Legacy sentinels |
| ----------------------------- | :----: | :--------: | :------: | :--------------: |
| `collection_deprecate.py`     |   9    |     21     |    26    |        0         |
| `collection_depr_legacy.py`   |   4    |     9      |    0     |        13        |
| `collection_misconfigured.py` |   3†   |     8      |    1     |        4         |
| `collection_docstrings.py`    |   3    |     10     |    12    |        0         |
| **Total**                     | **19** |   **48**   |  **39**  |      **17**      |

† `collection_misconfigured.py` also contains 3 `target=False` (invalid sentinel) calls not counted in any mode column.

______________________________________________________________________

## Test coverage

Every test that exercises a NOTIFY / ARGS_REMAP mode wrapper. Modern ✓ = test runs against the modern `TargetMode.*` fixture. Legacy ✓ = also runs against the `target=None` / `target=True` legacy fixture from `collection_depr_legacy.py`. Blank Legacy cell = not yet paired; `—` = intentionally unpaired (reason in Notes).

| Test                                                                                         | Modern | Legacy | Notes                                                                  |
| -------------------------------------------------------------------------------------------- | :----: | :----: | ---------------------------------------------------------------------- |
| `test_functions.py::TestDeprecationWarnings::test_warn_only`                                 |   ✓    |   ✓    | `decorated_sum_warn_only` warn-only message parity                     |
| `test_functions.py::TestArgumentMapping::test_arguments_new_only`                            |   ✓    |   ✓    | `decorated_pow_self` no-warning when new arg used                      |
| `test_functions.py::TestArgumentMapping::test_arguments_deprecated`                          |   ✓    |   ✓    | `decorated_pow_self` warning content + remap on old arg                |
| `test_functions.py::TestArgumentMapping::test_arguments_double_deprecated`                   |   ✓    |   ✓    | `depr_pow_self_double` two-arg remap parity                            |
| `test_classes.py::TestDeprecatedClass::test_class_self_new_args`                             |   ✓    |   ✓    | `ThisCls.__init__` silent when new arg used                            |
| `test_classes.py::TestDeprecatedClass::test_class_self_deprecated_args`                      |   ✓    |   ✓    | `ThisCls.__init__` warns + remaps `c` -> `nc`                          |
| `test_classes.py::TestDeprecatedClassMethod::test_warn_only_method_emits_warning`            |   ✓    |   ✓    | `ServiceCls.old_warn_method` emits FutureWarning                       |
| `test_classes.py::TestDeprecatedClassMethod::test_warn_only_method_body_executes`            |   ✓    |   ✓    | `ServiceCls.old_warn_method` body still runs                           |
| `test_classes.py::TestDeprecatedClassMethod::test_warn_only_warning_content`                 |   ✓    |   ✓    | `ServiceCls.old_warn_method` warning message has versions              |
| `test_classes.py::TestDeprecatedClassMethod::test_self_rename_with_deprecated_arg_warns`     |   ✓    |   ✓    | `ServiceCls.self_renamed_method` warns + remaps `old_x` -> `x`         |
| `test_classes.py::TestDeprecatedClassMethod::test_self_rename_with_new_arg_no_warning`       |   ✓    |   ✓    | `ServiceCls.self_renamed_method` silent when new arg used              |
| `test_regressions.py::TestFix2ArgsExtraOnArgsRemap::test_old_name_merges_args_extra`         |   ✓    |   ✓    | `fn_remap_with_extra` args_extra injected on old-arg call              |
| `test_regressions.py::TestFix2ArgsExtraOnArgsRemap::test_new_name_merges_args_extra`         |   ✓    |   ✓    | `fn_remap_with_extra` args_extra injected on new-arg call              |
| `test_target_mode.py::TestArgsRemapMode::test_args_extra_equivalence_with_legacy`            |   ✓    |   ✓    | `depr_target_mode_args_only_with_args_extra_injects_kwargs` parity     |
| `test_target_mode.py::TestNotifyMode::test_warns_on_every_call`                              |   ✓    |   ✓    | `num_warns=-1` NOTIFY parity                                           |
| `test_target_mode.py::TestNotifyMode::test_executes_original_body`                           |   ✓    |   ✓    | NOTIFY body execution parity                                           |
| `test_target_mode.py::TestNotifyMode::test_construction_warns_with_args_mapping`             |   ✓    |   —    | Construction-time UserWarning; no call-time legacy to compare          |
| `test_target_mode.py::TestNotifyMode::test_construction_warns_with_args_extra`               |   ✓    |   —    | Construction-time UserWarning; no call-time legacy to compare          |
| `test_target_mode.py::TestNotifyMode::test_args_mapping_is_runtime_noop`                     |   ✓    |   —    | Misconfig runtime noop; not a legacy-sentinel behavior dimension       |
| `test_target_mode.py::TestNotifyMode::test_args_extra_is_runtime_noop`                       |   ✓    |   —    | Misconfig runtime noop; not a legacy-sentinel behavior dimension       |
| `test_target_mode.py::TestArgsRemapMode::test_warns_when_old_arg_passed`                     |   ✓    |   ✓    | ARGS_REMAP warns on old arg parity                                     |
| `test_target_mode.py::TestArgsRemapMode::test_silent_when_new_arg_passed`                    |   ✓    |   ✓    | ARGS_REMAP silent on new arg parity                                    |
| `test_target_mode.py::TestArgsRemapMode::test_remaps_kwargs`                                 |   ✓    |   ✓    | ARGS_REMAP kwarg remapping parity                                      |
| `test_target_mode.py::TestArgsRemapMode::test_with_args_extra_injects_kwargs`                |   ✓    |   —    | Parity covered by `test_args_extra_equivalence_with_legacy`            |
| `test_target_mode.py::TestArgsRemapMode::test_construction_warns_without_args_mapping`       |   ✓    |   —    | Construction-time UserWarning; not a call-time legacy dimension        |
| `test_target_mode.py::TestArgsRemapMode::test_positional_passthrough`                        |   ✓    |   ✓    | ARGS_REMAP positional-arg passthrough parity                           |
| `test_audit.py::TestValidateDeprecatedWrapper::test_valid_deprecation`                       |   ✓    |   —    | Audit: `from_legacy()` normalises to identical `__deprecated__` config |
| `test_audit.py::TestValidateDeprecatedWrapper::test_valid_wrapper_also_not_misconfigured`    |   ✓    |   —    | Audit: same normalisation — re-asserting legacy adds no signal         |
| `test_audit.py::TestCheckDeprecationExpiry::test_not_expired_before_deadline`                |   ✓    |   —    | Audit: expiry logic is config-level; unaffected by sentinel vs enum    |
| `test_audit.py::TestCheckDeprecationExpiry::test_raises_at_or_after_deadline`                |   ✓    |   —    | Audit: same                                                            |
| `test_audit.py::TestCheckDeprecationExpiry::test_error_message_content`                      |   ✓    |   —    | Audit: same                                                            |
| `test_audit.py::TestCheckDeprecationExpiry::test_invalid_current_version_raises_value_error` |   ✓    |   —    | Audit: same                                                            |

All gaps closed. Intentionally unpaired (`—`) rows are construction-time warning tests and audit tests where legacy sentinel vs enum makes no behavioral difference at call time.

`TestLegacySentinels` in `test_target_mode.py` is not listed above — it tests decoration-time FutureWarning emission for `target=None` / `target=True` / `target=False` inline (no shared fixture) and does not test call-time behavior parity.
