# 🎉 pyDeprecate 0.9.0 ([#150](https://github.com/Borda/pyDeprecate/pull/150))

pyDeprecate 0.9.0 introduces `TargetMode` as the public replacement for the legacy `target=None` and `target=True` sentinels, while keeping those forms working for one release with explicit warnings. It also tightens misconfiguration reporting so invalid `TargetMode` combinations surface immediately during decoration.

______________________________________________________________________

## 🚀 Added ([#150](https://github.com/Borda/pyDeprecate/pull/150))

### `TargetMode` enum exported from `deprecate`. ([#150](https://github.com/Borda/pyDeprecate/pull/150))

`TargetMode.WHOLE` replaces `target=None` and `TargetMode.ARGS_ONLY` replaces `target=True`. Both members are importable from `deprecate` and are the preferred public API for deprecation mode selection.

### Legacy sentinel forms now emit migration warnings. ([#150](https://github.com/Borda/pyDeprecate/pull/150))

`target=None` and `target=True` continue to work in v0.9, but both emit a `FutureWarning` at decoration time. `target=None` maps to `TargetMode.WHOLE`, and `target=True` maps to `TargetMode.ARGS_ONLY`.

### Invalid legacy sentinel `target=False` now warns. ([#150](https://github.com/Borda/pyDeprecate/pull/150))

`target=False` was never a supported deprecation mode. It now emits a `UserWarning` at decoration time and is scheduled to become a `TypeError` in v1.0.

### Misconfigured `TargetMode` combinations now warn at construction time. ([#150](https://github.com/Borda/pyDeprecate/pull/150))

`TargetMode.ARGS_ONLY` without `args_mapping`, `TargetMode.WHOLE` with `args_mapping`, and `TargetMode.WHOLE` with `args_extra` all emit construction-time `UserWarning`s so the misconfiguration is visible immediately.
