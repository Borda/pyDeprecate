# 🎉 pyDeprecate 0.5.0

We're excited to announce pyDeprecate 0.5.0 — the **deprecation lifecycle release**!
This version ships a brand-new `audit` module that brings CI-grade tooling for keeping your deprecated APIs healthy, discoverable, and eventually removed on schedule.

---

## ✨ What's New

### 🔍 `audit` Module — Deprecation Lifecycle Management

> [!NOTE]
> These utilities are designed for use with **pyDeprecate's `@deprecated` decorator** and inspect its `__deprecated__` metadata. They are not a general-purpose deprecation auditing framework.
>
> The `audit` module requires the optional `[audit]` extra: `pip install pyDeprecate[audit]`

The headline feature is a dedicated `deprecate.audit` module that groups all inspection and enforcement utilities into one coherent API designed to be called from pytest or your CI scripts.

```python
import mypackage
from deprecate.audit import find_deprecated_callables

# Scan an entire package for deprecated wrappers
infos = find_deprecated_callables(mypackage)
for info in infos:
    print(info.module, info.function, info.no_effect)
```

Three complementary audit capabilities are included:

#### 🛡️ Zero-Impact Wrapper Validation

`validate_deprecated_callable` and `find_deprecated_callables` detect wrappers that have no real effect — invalid `args_mapping` keys, identity mappings, missing version fields, or a `target` that points back to the same function.

```python
from deprecate.audit import validate_deprecated_callable
from mypackage import old_func

info = validate_deprecated_callable(old_func)
assert not info.no_effect, f"Misconfigured wrapper: {info.invalid_args}"
```

#### ⏰ Expiry Enforcement

`validate_deprecation_expiry` scans a module or package and raises when any wrapper's `remove_in` version has been reached or passed — so zombie code never ships past its scheduled removal deadline.

Auto-detects the installed package version via `importlib.metadata` (falls back to `__version__`):

```python
from deprecate.audit import validate_deprecation_expiry
import mypackage

# Raises if any deprecated wrapper is past its remove_in deadline
validate_deprecation_expiry(mypackage)

# Or pin the version explicitly
validate_deprecation_expiry(mypackage, current_version="0.5.0")
```

#### 🔗 Deprecation Chain Detection

`validate_deprecation_chains` detects wrappers whose `target` is itself a deprecated callable, forming chains that users traverse unnecessarily.
Two chain kinds are reported via the new `ChainType` enum: `TARGET` (forwarding chain) and `STACKED` (composed argument mappings).

```python
from deprecate.audit import validate_deprecation_chains
import mypackage

chains = validate_deprecation_chains(mypackage)
assert not chains, f"Chained deprecations found: {chains}"
```

---

### 🐛 Bug Fixes

- **Class deprecation guard** (#120): Applying `@deprecated` to a class now raises `TypeError` immediately at decoration time, preventing silent misuse.
- **Cross-class method guard** (#121): Forwarding a deprecated method to a method on a different class is now detected and rejected at decoration time, preventing incorrect call routing.

---

## 🔧 Under the Hood

- **Comparison table** added to README showing how `pyDeprecate` stacks up against alternatives (#97)
- **Test suite reorganized** into `tests/unittests/` and `tests/integration/` for clearer separation of concerns (#112, #95)
- mypy moved from CI step to pre-commit hook for faster local feedback (#87)
- Link checker added to CI to catch broken documentation references (#91)
- Linting, type hint, and code quality improvements throughout (#85, #86, #98)

---

## 📦 Installation

```bash
pip install --upgrade pyDeprecate

# For audit features:
pip install --upgrade "pyDeprecate[audit]"
```

---

## 🙏 Thank You

A big thank you to everyone who contributed to this release!

---

**Full Changelog**: [`v0.4.0...v0.5.0`](https://github.com/Borda/pyDeprecate/compare/v0.4.0...v0.5.0)