# Changelog

## [UnReleased] - 2026-MM-DD

### Added

- **`deprecated_module()` — PEP-562-inspired module-level deprecation.** Call `deprecated_module(__name__, deprecated_in=..., remove_in=...)` once at the bottom of a module to install a `__class__` reassignment to a wrapper type that emits `FutureWarning` on every public attribute access (including real attributes already in `__dict__`). Three modes: in-place warn (Mode 1), redirect to replacement module with optional `attrs_mapping` (Mode 2), and parent alias via `deprecated_instance()` (Mode 3). `find_deprecation_wrappers()` discovers deprecated modules via the `__deprecated__` attribute; `validate_deprecation_wrapper()` accepts module objects directly. Double-call is idempotent (returns early); pre-existing `__getattr__` is chained with a `UserWarning`. ([#203](https://github.com/Borda/pyDeprecate/pull/203))
- **`deprecated_callable()` — strict callable-only form of `@deprecated`.** Same parameters and call-forwarding as `@deprecated`, but raises `TypeError` at decoration time when applied to a class. Accepts functions, methods, lambdas, and descriptors (`classmethod` / `staticmethod` / `property`); parallels the `deprecated_class` / `deprecated_instance` / `deprecated_module` family and is exported from the package. ([#221](https://github.com/Borda/pyDeprecate/pull/221))
- **`attrs_mapping` on the `deprecated()` front door.** `@deprecated` now accepts `attrs_mapping` for class and attribute-name remapping (constructor kwargs are affected only indirectly via dataclass auto-expansion); passing it on a callable source raises `TypeError` naming `deprecated_class`. ([#222](https://github.com/Borda/pyDeprecate/pull/222))

### Changed

- **`@deprecated` on a class is now first-class.** Applying `@deprecated` directly to a class dispatches to `deprecated_class` (full `_DeprecatedProxy` — `isinstance`/`__class__` transparency, forwarding, budget semantics) and emits a one-time (per class, removed in `0.13`) informational `UserWarning` — `` `@deprecated` on class `<Name>` now dispatches to `@deprecated_class`. `` — suppressed with `stream=None`. This replaces the v0.6 "will become a `TypeError`" warning; the threatened `TypeError` never ships. `@deprecated_class` stays the explicit, preferred form for classes. ([#222](https://github.com/Borda/pyDeprecate/pull/222))
- **`deprecated_class` default `target` is now `TargetMode.NOTIFY`** (was `None`). Observable only in audit metadata: a warn-only class's frozen `DeprecationConfig.target` now records `TargetMode.NOTIFY` instead of `None`. ([#222](https://github.com/Borda/pyDeprecate/pull/222))
- **`TargetMode.NOTIFY` + a mapping now auto-resolves instead of warning.** `deprecated_class(args_mapping=...)` / `(attrs_mapping=...)` and `@deprecated(args_mapping=...|attrs_mapping=...)` on a class resolve to `TargetMode.ARGS_REMAP` / `TargetMode.ATTRS_REMAP` and apply the mapping; the prior "`NOTIFY` + mapping is a misconfiguration" warning (which ignored the mapping) is retired, including the legacy `target=True` + mapping combination. A mapping present is always applied. ([#222](https://github.com/Borda/pyDeprecate/pull/222))
- **Non-callable sources raise a clear `TypeError`.** Applying `@deprecated` to a plain object, a `__call__` instance without `__name__`, or `functools.partial` of a class now raises `TypeError` naming `deprecated_instance`, instead of crashing later on `__name__` access. ([#222](https://github.com/Borda/pyDeprecate/pull/222))

### Deprecated

### Removed

### Fixed

______________________________________________________________________

## [0.11.0] — 2026-07-15 — Operator forwarding, PEP 560 subclassing, & proxy identity fixes

### Added

- **Deprecated proxies now forward operator and protocol dunders to the wrapped object.** Arithmetic (`proxy + 1`), comparison/ordering, context managers (`with proxy:`), iteration (`next`, `reversed`), numeric conversion (`int`/`float`/`round`/`abs`), `os.fspath`, `format`, and the async protocols (`async with`, `async for`, `await`) now delegate to the active object instead of raising `TypeError`. Binary operators preserve `NotImplemented` semantics, so unsupported-operand errors surface normally. The warn-policy contract is documented in the `_DeprecatedProxy` docstring: data use warns (within the `num_warns` budget), cheap probes stay silent. **Caveat**: in-place operators (`+=`, `-=`, `*=`, …) return the active object's result rather than a re-wrapped proxy — after `x += 1`, the name `x` is rebound to a plain `int` and all subsequent uses are silent even if the deprecation window has not closed. ([#214](https://github.com/Borda/pyDeprecate/pull/214))
- **Subclassing a deprecated class alias now works (PEP 560).** `class Child(OldName)` on a `deprecated_class` alias previously raised a confusing metaclass arity `TypeError`; `__mro_entries__` now resolves the alias to the active class and emits the deprecation warning (subclassing is a use of the deprecated name), respecting the warn budget and staying silent for `attrs_mapping`-only and `args_mapping`-only proxies. ([#214](https://github.com/Borda/pyDeprecate/pull/214))

### Changed

**Performance**

- **Call forwarding is ~2.4× faster.** Decoration-time-stable signature facts are now precomputed onto `DeprecationConfig` instead of re-derived on every call (uncached `inspect.getfullargspec` removed from the hot path); forwarded-call overhead drops from ~10.4 µs to ~4.3 µs with no behavior change. ([#214](https://github.com/Borda/pyDeprecate/pull/214))

**Proxy identity**

- **`proxy.__class__` now reports the wrapped object's type for `isinstance` transparency.** `_DeprecatedProxy` exposes a `__class__` property returning the active object's type, so type checks in downstream code (JSON encoders, validators, `functools.singledispatch`) keep working when an object is wrapped; `type(proxy)` still reveals the proxy. Code that previously detected the proxy via `obj.__class__ is _DeprecatedProxy` should use `type(obj) is _DeprecatedProxy` instead. ([#210](https://github.com/Borda/pyDeprecate/pull/210))
- **Proxy identity operations now reflect the served (active) object.** `repr()`, `str()`, `==`, and `hash()` on a target-forwarding proxy previously used the deprecated source while attribute/item/call access used the active target, so a proxy could compare equal to an object it never served; all four now route through the active object for consistency. ([#216](https://github.com/Borda/pyDeprecate/pull/216))
- **`isinstance` / `issubclass` with an *instance* proxy as the second argument now raise `TypeError`.** Using a `deprecated_instance` proxy (one wrapping a value rather than a class) as the second argument to `isinstance`/`issubclass` previously returned `False` silently, hiding the misuse; it now raises the same `TypeError` the builtins raise. Class-alias proxies are unaffected. ([#216](https://github.com/Borda/pyDeprecate/pull/216))

**Audit & expiry**

- **`validate_deprecation_expiry()` now scans class members by default (`include_members=True`).** Previously the expiry gate defaulted to `include_members=False` while `find_deprecation_wrappers()` defaulted to `True`, so CI gates silently skipped expired deprecated methods, constructors, classmethods, staticmethods, and properties. The flip can only surface additional expired wrappers — pass `include_members=False` explicitly to restore the old scope. ([#210](https://github.com/Borda/pyDeprecate/pull/210))
- **Batch expiry now warns on an unparsable `remove_in` instead of skipping it silently.** `validate_deprecation_expiry()` (and the CLI expiry gate) previously dropped wrappers whose `remove_in` version could not be parsed, leaving them permanently un-expirable with no signal; such wrappers now emit a `UserWarning` naming the callable while the rest of the scan continues. ([#216](https://github.com/Borda/pyDeprecate/pull/216))

### Fixed

**Audit**

- **Audit scans no longer double-count re-exported wrappers.** Recursive `find_deprecation_wrappers` previously reported a wrapper once per importing module (e.g. once under the package root re-export and once under its defining submodule), inflating expiry counts and table rows; wrappers are now attributed to their defining module and deduplicated by identity across the scan. ([#215](https://github.com/Borda/pyDeprecate/pull/215))
- **Audit report formatting no longer triggers chained proxies.** Formatting a report for a wrapper whose `target` is itself a deprecated proxy previously emitted a spurious `FutureWarning` from inside the audit tooling, consumed the proxy's warn budget, and printed a fabricated module path; proxy targets are now read via static metadata access. ([#215](https://github.com/Borda/pyDeprecate/pull/215))
- **Audit scans survive submodules that fail to import.** `find_deprecation_wrappers(recursive=True)` previously aborted the whole scan when any submodule raised a non-`ImportError` at import time (e.g. `RuntimeError` from an optional dependency); such submodules are now skipped with a `warnings.warn` naming the module and error. ([#210](https://github.com/Borda/pyDeprecate/pull/210))
- **Audit tools now surface deprecated private and dunder members.** `_scan_class` skipped every `_`-prefixed member except `__init__`, so a deprecated private method or dunder could never be flagged as expired; private/dunder members that carry deprecation metadata are now included. ([#216](https://github.com/Borda/pyDeprecate/pull/216))
- **Audit now detects proxy self-reference.** A `deprecated_instance`/`deprecated_class` proxy whose target is its own wrapped object was reported as effective because the self-reference check compared against the proxy rather than the wrapped object; it is now flagged as a no-op self-reference. ([#216](https://github.com/Borda/pyDeprecate/pull/216))
- **Recursive audit scans tolerate foreign objects that raise on attribute access.** Probing a scanned object for deprecation metadata used `getattr(..., default)`, which only suppresses `AttributeError`; a third-party object whose `__getattr__` raised something else (e.g. a lazy proxy raising `RuntimeError`) aborted the whole scan. Such failures are now treated as "no metadata". ([#216](https://github.com/Borda/pyDeprecate/pull/216))

**CLI**

- **`all` and `status` no longer fail on plain directories.** Scanning a directory without `__init__.py` exited 1 from the status-table step even when every check passed; module-name resolution is now lazy and a status-rendering failure cannot change the aggregate exit code. ([#215](https://github.com/Borda/pyDeprecate/pull/215))
- **Version auto-detection resolves distributions whose name differs from the import name.** `importlib.metadata` lookup previously failed for packages like pyDeprecate itself (import `deprecate`, distribution `pyDeprecate`); the import name is now mapped via `packages_distributions()`, and expiry prints an explicit note when it runs without a resolved version. ([#215](https://github.com/Borda/pyDeprecate/pull/215))
- **Version auto-detection no longer picks up an unrelated project's `pyproject.toml`.** When the scan path is an importable module name rather than a filesystem path, `expiry`/`status` previously walked up from the current directory and could gate against whatever project the shell happened to be in; filesystem-based detection now runs only for real paths. ([#210](https://github.com/Borda/pyDeprecate/pull/210))
- **Unknown or misspelled flags now exit with an error instead of being silently ignored.** `sys.exit` inside the Fire invocation previously preempted Fire's unconsumed-argument check, so a typo such as `--verison` was dropped and the command ran with defaults; the CLI now lets Fire report the unconsumed flag and exits non-zero. ([#210](https://github.com/Borda/pyDeprecate/pull/210))
- **A stream whose `encoding` is `None` no longer crashes UTF-8 setup.** `_ensure_utf8_streams` called `.lower()` on a possibly-`None` `encoding` attribute, raising `AttributeError` for some redirected streams; a `None` encoding is now tolerated. ([#216](https://github.com/Borda/pyDeprecate/pull/216))
- **An exception carrying no message now exits with a non-blank stderr line.** The top-level handler called `sys.exit(str(exc))`, which printed nothing (exit 1) for a message-less exception; the exception type now prefixes the exit message so CI shows what failed. ([#216](https://github.com/Borda/pyDeprecate/pull/216))

**Call forwarding & signatures**

- **Sources with positional-only parameters no longer raise `TypeError` on every call.** `@deprecated` on `def f(a, /, b=2)` previously failed at call time in the default notify mode and `TargetMode.ARGS_REMAP` because positional-only arguments were re-passed as keywords; they are now split back out positionally for both sync and async sources. ([#210](https://github.com/Borda/pyDeprecate/pull/210))
- **Surplus `*args` are now forwarded to callable targets instead of being silently dropped.** A `*args`-declaring source forwarding to a target previously discarded everything past the named positionals (`old_sum(1, 2, 3)` returned `1`); the positional tail is now forwarded, and incompatible targets raise the curated mapping `TypeError`. ([#210](https://github.com/Borda/pyDeprecate/pull/210))
- **Positional-only forwarding no longer misbinds values when an earlier parameter is absent.** The positional split previously appended present values in declaration order, sliding a later parameter's value into an earlier defaulted slot; gaps now stop the split and conflicting later values raise `TypeError`. The same signature-order dispatch replaces the `setattr` fallback in `deprecated_class(args_mapping=...)`, which also failed for required positional-only parameters and frozen dataclasses. ([#210](https://github.com/Borda/pyDeprecate/pull/210))
- **Cross-class forwarding between staticmethods no longer raises a spurious `TypeError`.** The cross-class guard exists to prevent `self` carrying the wrong type, but staticmethods have no `self`; `@deprecated(target=NewCls.compute)` on a staticmethod forwarding to another class's staticmethod is now allowed. ([#214](https://github.com/Borda/pyDeprecate/pull/214))
- **The cross-class forwarding guard now fires for descriptor-decorated methods.** The guard read the enclosing class name from a fixed stack depth, which the extra frames of `@property`/`@classmethod`/`@staticmethod` wrapping pushed out of reach — silently disabling the check; a bounded frame walk now locates the class body regardless of descriptor frames. ([#216](https://github.com/Borda/pyDeprecate/pull/216))
- **Argument validation against a `**kwargs`/`*args` target yields the curated message again.** The internal signature helper leaked `*args`/`**kwargs` names into caller-argument validation, producing a raw `TypeError` instead of the "argument not accepted by target" message; the variadic names are now excluded as documented. ([#216](https://github.com/Borda/pyDeprecate/pull/216))

**Proxy, decoration & config**

- **`num_warns` quota is now thread-safe.** Concurrent first calls to a shared wrapper could each pass the quota check before any counter increment, emitting up to one warning per thread instead of the configured budget; the warn path now synchronizes on a per-wrapper lock, so exactly `num_warns` warnings are emitted under concurrency. ([#214](https://github.com/Borda/pyDeprecate/pull/214))
- **Instantiating an `attrs_mapping`-only deprecated class no longer emits a class-level warning.** `TargetMode.ATTRS_REMAP` scopes the deprecation to the listed attributes, yet plain instantiation fired the blanket `FutureWarning` and consumed the warn budget; construction is now silent and only deprecated-attribute access warns. ([#214](https://github.com/Borda/pyDeprecate/pull/214))
- **Deprecated proxies now support `copy.copy`, `copy.deepcopy`, and `pickle`.** Copying or pickling any `deprecated_class`/`deprecated_instance` proxy previously crashed with `RecursionError` — the `_cfg` property and `__getattr__` fell into infinite mutual recursion on half-initialized instances. Proxies now implement the copy/pickle protocol and reconstruct a functional proxy. Note: `deprecated_instance` proxies wrapping plain objects (dicts, lists) are fully picklable; `deprecated_class` proxies may raise `PicklingError` when the decorated class name is replaced by the proxy (the common alias pattern), because pickle cannot find the original class by reference. ([#212](https://github.com/Borda/pyDeprecate/pull/212))
- **Proxy introspection no longer consumes the warning budget.** `hasattr()` probes on missing attributes, `copy.deepcopy` protocol lookups, and dunder access (e.g. `__mro__` reads by doc tools) previously emitted the deprecation warning and exhausted the default `num_warns=1` budget before any real usage. Warnings now fire only on successful non-dunder attribute access. ([#210](https://github.com/Borda/pyDeprecate/pull/210))
- **Bare `@deprecated` (no parentheses) now raises a clear `TypeError` when the first argument is not callable.** Forgetting the call parentheses previously surfaced as a cryptic `AttributeError: 'int' object has no attribute '__name__'` on the first call; the decorator now explains that it must be called with arguments. ([#216](https://github.com/Borda/pyDeprecate/pull/216))
- **Custom `template_mgs` with a bare `%`-conversion is now rejected at decoration time.** A template containing `%s`/`%d` (rather than a `%(name)s` mapping key) silently rendered the whole substitution dict into the warning; such templates now raise `ValueError` when the decorator is applied. ([#216](https://github.com/Borda/pyDeprecate/pull/216))
- **`args_mapping`, `args_extra`, and `attrs_mapping` are defensively copied at decoration time.** The frozen configuration previously aliased the caller's dict, so mutating it after decoration could silently change forwarding behavior (or introduce a redirect cycle that validation had already rejected); the mappings are now copied. ([#216](https://github.com/Borda/pyDeprecate/pull/216))
- **A warning stream that raises `TypeError` internally is no longer invoked twice.** Both the decorator and proxy warning paths called the stream with `stacklevel` and caught `TypeError` to retry without it — which also swallowed a `TypeError` raised *inside* a stacklevel-accepting stream and re-ran it (duplicate log/print). The paths now decide once, via a cached signature probe, whether the stream accepts `stacklevel`. ([#216](https://github.com/Borda/pyDeprecate/pull/216))
- **Version normalization preserves PEP 440 local segments.** `_normalize_version_string` ran its pre/post/dev label rule over the whole string, mangling a legitimate local version such as `1.2.3+cuda` into `1.2.3+cuda0`; the local segment (after `+`) is now split off and re-attached verbatim, and only a single leading `v`/`V` is stripped. ([#216](https://github.com/Borda/pyDeprecate/pull/216))

______________________________________________________________________

## [0.10.1] — 2026-07-03 — Cycle detection, strict property, & inner-order audit flag

### Added

- **`DeprecationWrapperInfo.inner_order_property` flag.** `find_deprecation_wrappers()` now sets `inner_order_property=True` when a plain `property` (not `_DeprecatedProperty`) has a `@deprecated`-wrapped `fget` — the inner-order `@property @deprecated` shape where only the getter warns; setters and deleters added via `@value.setter` / `@value.deleter` remain silently unprotected. CI pipelines can filter on this field to reject the pattern. ([#201](https://github.com/Borda/pyDeprecate/pull/201))
- **Opt-in strict `property` replacement.** `from deprecate import property` now exports `_StrictProperty`, a `property` subclass that raises `TypeError` at class-definition time when handed an already-`@deprecated` getter (inner-order detection). Import it in modules that want compile-time enforcement; star imports (`from deprecate import *`) are unaffected. ([#201](https://github.com/Borda/pyDeprecate/pull/201))

### Fixed

- **Circular deprecation chains now raise `RuntimeError` at call time.** Callable `target` chains that form a cycle (A → B → A) previously caused unbounded recursion and a `RecursionError`. The decorator now detects the cycle via a `ContextVar` re-entrancy guard and raises a clear `RuntimeError` naming the circular path. Async deprecation cycle detection was also improved to avoid false positives from concurrent tasks sharing a threading-local guard. ([#200](https://github.com/Borda/pyDeprecate/pull/200))
- **`_StrictProperty` `TypeError` message now references the correct module path.** The error previously pointed to `deprecate._StrictProperty`; corrected to `deprecate.deprecation._StrictProperty`, which is the actual import path. ([#201](https://github.com/Borda/pyDeprecate/pull/201))

## [0.10.0] — 2026-06-21 — Property accessors, class attribute mapping & descriptor targets

### Added

**Class attribute & dataclass mapping**

- **`deprecated_class(attrs_mapping={...})` for selective attribute deprecation.** Deprecated attribute names emit `FutureWarning` on read, write, and delete with per-attribute warning budgets. `None` as the redirect value means warn-only (no rename). `TargetMode.ATTRS_REMAP` is the corresponding mode — can be combined with a callable `target` to redirect attribute access across class boundaries. Multi-hop chains and fan-in renames allowed; cycles raise `ValueError` at decoration time. ([#191](https://github.com/Borda/pyDeprecate/pull/191))
- **Dataclass `attrs_mapping` auto-expand.** When the wrapped class is a `@dataclass`, a single `deprecated_class(attrs_mapping={"old_field": "new_field"})` call automatically generates the corresponding `args_mapping` entry so both attribute access (`obj.old_field`) and constructor kwargs (`DC(old_field=5)`) emit `FutureWarning` from one decorator. Explicitly-provided `args_mapping` keys always win over auto-expanded entries. For non-dataclass targets, `attrs_mapping` covers attribute access only. ([#193](https://github.com/Borda/pyDeprecate/pull/193))

**Descriptors & property accessors**

- **`target=` now accepts raw `staticmethod` / `classmethod` descriptors directly.** Inside a class body the new method is still a raw descriptor (not yet bound); passing it as `target=new_method` no longer requires the explicit `.__func__` suffix. `_normalize_target` unwraps the descriptor automatically. For `classmethod` descriptors the symmetric same-class pattern is supported (both deprecated and replacement are classmethods); asymmetric usage raises `TypeError` at decoration time. ([#192](https://github.com/Borda/pyDeprecate/pull/192))
- **`@deprecated @property` now wraps `fset` and `fdel` with `FutureWarning`.** Applying `@deprecated` on the outside of `@property` (outer order, or explicit `deprecated(...)(property(fget, fset, fdel))`) now wraps all three accessors. Previously, only `fget` emitted a warning; `fset` and `fdel` were silently passed through. Consumers running `filterwarnings=error::FutureWarning` that wrote to or deleted a deprecated property will now see `FutureWarning` errors — use inner-order (`@property @deprecated`) or decorate only `fget` directly if you want a silent setter/deleter. Chain-style rebinding via `@value.setter` / `@value.deleter` is fully supported through the new `_DeprecatedProperty` subclass. ([#190](https://github.com/Borda/pyDeprecate/pull/190))

**Stacking & audit**

- **`deprecated_class` stacking is now supported.** Two `@deprecated_class` decorators applied to the same class (each with its own `attrs_mapping` and version pair) now work correctly: `isinstance()` resolves through the proxy chain, instantiation emits at most one warning instead of two, and the type annotation accepts `_DeprecatedProxy` without a `cast`. Stacking ATTRS_REMAP outer + ARGS_REMAP inner is also supported: the inner proxy no longer emits a spurious global warning on attribute access — `TargetMode.ARGS_REMAP` now correctly restricts its warnings to call-time argument remapping only. No-target two-layer stacking (both layers deprecating the class in-place without forwarding to a different type) is also supported: the outer `ATTRS_REMAP` proxy delegates `__call__` to the inner `ARGS_REMAP` proxy without firing a second global warning. ([#193](https://github.com/Borda/pyDeprecate/pull/193))
- **`validate_mapping_compatibility()` audit function.** Returns `list[DeprecationWrapperInfo]` for all `deprecated_class` proxies whose `args_mapping` remaps deprecated names to `POSITIONAL_ONLY` constructor parameters — those proxies fall back to `setattr` at call time instead of forwarding via kwargs. Use in CI to detect configurations that silently degrade to attribute assignment. ([#193](https://github.com/Borda/pyDeprecate/pull/193))

### Fixed

- **`@deprecated` now correctly forwards calls to targets with POSITIONAL_ONLY parameters.** When a callable `target` declares any parameter as positional-only (`def new_fn(x, /): ...`), the decorator previously raised `TypeError` at call time because all arguments were forwarded as kwargs. The decorator now detects POSITIONAL_ONLY params at decoration time, emits a `UserWarning` naming the affected parameters, and splits the call-time dispatch so those values are forwarded positionally. `args_mapping` remaps applied before the split — remapped names that land on a POSITIONAL_ONLY target param are handled correctly. The thin-adapter pattern (`def new_fn_compat(x): return new_fn(x)` as `target`) remains valid and suppresses the `UserWarning`. ([#194](https://github.com/Borda/pyDeprecate/pull/194))
- **`args_mapping` precedence: explicit new-name always wins when both old and new kwargs passed.** When a caller passed both the deprecated old argument name and the new name simultaneously (e.g. `fn(val=5, new_val=6)`), the remapped old-name value previously overwrote the explicit new-name value due to dict-comprehension last-write-wins ordering. The explicit new-name value now always wins, regardless of argument order at the call site. Affects `@deprecated` with `target=TargetMode.ARGS_REMAP` or a callable target, and `deprecated_class()` with `args_mapping`. ([#198](https://github.com/Borda/pyDeprecate/pull/198))

______________________________________________________________________

## [0.9.0] — 2026-06-05 — Generators, async, & markdown audit tables

### Added

**Async & callable shapes**

- **Generator function support for `@deprecated`.** Decorating a generator function now emits the deprecation warning eagerly at call time — before the first `next()` — consistent with regular function behavior. The generator body executes lazily as normal when iterated. All three `TargetMode` variants (`NOTIFY`, `ARGS_REMAP`, callable target) work transparently; no `isgeneratorfunction` check is required. ([#176](https://github.com/Borda/pyDeprecate/pull/176))
- **`async def` coroutine wrapper support for `@deprecated`.** Decorating an `async def` function now produces an `async def` wrapper — `inspect.iscoroutinefunction(wrapper)` returns `True`. All three `TargetMode` variants (`NOTIFY`, `ARGS_REMAP`, callable target) work with async sources and async targets. The deprecation warning fires when the coroutine is awaited, not when the wrapper is called. `pytest-asyncio` is required in the test suite to run the async integration tests. ([#180](https://github.com/Borda/pyDeprecate/pull/180))
- **Async generator function support for `@deprecated`.** Decorating an `async def` + `yield` function no longer emits a `UserWarning` at decoration time. The wrapper is a sync callable that fires the deprecation warning eagerly at call time and returns the async generator object; callers iterate with `async for`. All three `TargetMode` variants work. Because the wrapper is sync, `inspect.isasyncgenfunction(wrapper)` returns `False` — frameworks that branch on that flag may need a thin async generator passthrough. ([#181](https://github.com/Borda/pyDeprecate/pull/181))
- **Order-agnostic `@classmethod` / `@staticmethod`.** Both `@classmethod @deprecated` and `@deprecated @classmethod` (and the equivalent for `@staticmethod`) now produce `classmethod(deprecated_wrapper)` — the descriptor is unwrapped at decoration time, the inner function is deprecated, and the result is re-wrapped. `FutureWarning` fires at call time in either order; no `UserWarning` is emitted. ([#178](https://github.com/Borda/pyDeprecate/pull/178))

**Stacking**

- **Stacked `@deprecated` — `ARGS_REMAP + NOTIFY` combination.** Lifecycle pattern: rename arguments first, deprecate the whole function later. The outer `ARGS_REMAP` remaps kwargs, then the inner `NOTIFY` warns and runs the source body. Six other stacking shapes (e.g. callable-over-callable, callable-over-`ARGS_REMAP`) now emit `UserWarning` at decoration time naming the specific shape and will become `TypeError` in v1.0. ([#172](https://github.com/Borda/pyDeprecate/pull/172))

**Audit & CLI tables**

- **Markdown deprecation tables — `generate_deprecation_table()` + `pydeprecate status` CLI subcommand.** Renders compact or matrix-style Markdown reports grouped by module and API-type (function, method, classmethod, staticmethod, property, class, instance). Two new public enums: `DeprecationStatus` (lifecycle classification — active, expired, plus dev/alpha/beta/rc removal windows) and `TableStyle` (`compact` / `matrix`). New `--style` and `--output` CLI flags. Integrated into `pydeprecate all`. Auto-detects package version from `pyproject.toml` or installed metadata. ([#133](https://github.com/Borda/pyDeprecate/pull/133))
- **`ChainType` enum now exported as public API.** Previously documented and returned by `validate_deprecation_chains()`; now listed in `deprecate.__all__`.
- **Audit discovery extended to class descriptors.** `find_deprecation_wrappers()` now inspects `classmethod` and `staticmethod` descriptors on class members so `@deprecated`-wrapped descriptors are found during scans. ([#178](https://github.com/Borda/pyDeprecate/pull/178))

### Changed

**CLI**

- **Renamed `--skip_errors` to `--exit-zero`** across all four subcommands (`check`, `expiry`, `chains`, `all`). ([#187](https://github.com/Borda/pyDeprecate/pull/187)) **Breaking change for existing scripts** — `--skip_errors` no longer accepted; update calls to `--exit-zero`. The new name matches the established linter convention (ruff, pylint, shellcheck) and accurately describes the behaviour: exit-code override only, no exception suppression. The canonical spelling is `--exit-zero` (dash); the CLI framework also accepts `--exit_zero` (underscore) as an alias.
- **CLI warning suppression narrowed to `deprecate.*` warnings only.** Third-party warnings emitted during a scan are no longer silenced. ([#133](https://github.com/Borda/pyDeprecate/pull/133))

**Stacking**

- **Misconfigured stacking combinations warn at decoration time.** Six previously-undefined `@deprecated` stacking shapes (e.g. callable-over-callable, callable-over-`ARGS_REMAP`) now emit `UserWarning` at decoration time naming the specific shape. Scheduled to become `TypeError` in v1.0. The new module-level `_V1_BREAK_VERSION = "v1.0"` constant centralises the "Will be TypeError in v1.0" wording across these warnings. ([#172](https://github.com/Borda/pyDeprecate/pull/172))

**Audit tables**

- **Audit chain classification — `ARGS_REMAP + NOTIFY` is now classified as `STACKED`** rather than `TARGET`. Fixes audit reports for the supported new stacking shape. ([#172](https://github.com/Borda/pyDeprecate/pull/172))
- **`generate_deprecation_table()` gains `include_members` parameter** for scanning descriptor members; `validate_deprecation_expiry()` default unchanged at `include_members=False` to preserve existing scan scope. ([#133](https://github.com/Borda/pyDeprecate/pull/133))

### Fixed

- **`stacklevel` attribution on Python 3.12+.** `inspect.signature(warnings.warn)` raises `ValueError` on Python 3.12+ because the C builtin lacks an introspectable signature. This caused every `@deprecated` warning to point to `deprecation.py` instead of the caller's file. Fixed by replacing the `inspect.signature` probe with a try/except at call site: `stream(msg, stacklevel=N)` is tried first; if `TypeError` is raised (stream does not accept `stacklevel`), retried as `stream(msg)`. ([#176](https://github.com/Borda/pyDeprecate/pull/176))
- **`find_deprecation_wrappers()` no longer aborts on PEP 702 `typing_extensions.deprecated` objects.** The scanner previously checked `callable(obj) and hasattr(obj, "__deprecated__")`, which matched PEP 702 wrappers (whose `__deprecated__` is a string), causing `validate_deprecation_wrapper` to raise `ValueError` and abort the scan. Replaced with `_has_deprecation_meta(obj)`, which checks `isinstance(..., DeprecationConfig)`. ([#178](https://github.com/Borda/pyDeprecate/pull/178))
- **Short-circuit forwarding for `*args` sources now preserves extra positional arguments.** Previously, when forwarding from a `*args` source, extra positional arguments past the named parameters were silently dropped. ([#180](https://github.com/Borda/pyDeprecate/pull/180))

______________________________________________________________________

## [0.8.0] — 2026-05-21 — Default `TargetMode` enum & CLI audit tools

### Added

**Core API & config**

- **`TargetMode` enum exported from `deprecate`.** `TargetMode.NOTIFY` replaces `target=None` and `TargetMode.ARGS_REMAP` replaces `target=True`. Both are public API. ([#150](https://github.com/Borda/pyDeprecate/pull/150))
- **`args_extra` parameter for `deprecated_class()` and `deprecated_instance()`.** Injects fixed keyword arguments into forwarded calls after `args_mapping` has been applied, matching the same semantics as `@deprecated(args_extra=...)`. Ignored (with a construction-time `UserWarning`) when `target` is `TargetMode.NOTIFY`. ([#150](https://github.com/Borda/pyDeprecate/pull/150))
- **`template_mgs` parameter for `deprecated_class()` and `deprecated_instance()`.** Overrides the built-in warning message template with a `%`-style format string, matching the same semantics as `@deprecated(template_mgs=...)`. Available placeholders: `%(source_name)s`, `%(deprecated_in)s`, `%(remove_in)s`, `%(target_name)s` (callable target only), `%(target_path)s` (callable target only), `%(argument_map)s` (`args_mapping` warnings only). ([#150](https://github.com/Borda/pyDeprecate/pull/150))
- **`DeprecationConfig.misconfigured` field.** Boolean field on the shared metadata dataclass; `True` when an invalid raw target sentinel (`False`) was passed at decoration time. Audit tools surface this via `DeprecationWrapperInfo.misconfigured_target`. ([#150](https://github.com/Borda/pyDeprecate/pull/150))
- **`template_mgs` validated at decoration time.** Malformed `%`-style format strings now raise `ValueError` immediately at decoration time — not silently at call time. Applies to `@deprecated` and the `deprecated_class()`/`deprecated_instance()` proxy factories. ([#169](https://github.com/Borda/pyDeprecate/pull/169))
- **Stacked-callable-target guard.** Applying `@deprecated(target=fn_a)` on a callable whose target is itself a callable-target `@deprecated` wrapper now emits `UserWarning` at decoration time instead of crashing with `TypeError` at call time. ([#169](https://github.com/Borda/pyDeprecate/pull/169))

**CLI & audit**

- **`pydeprecate` CLI command.** Run `pydeprecate <subcommand> path/to/your/package` to scan any package or module for misconfigured `@deprecated` wrappers — reports invalid argument mappings, identity mappings, and no-effect wrappers with rich-formatted output when `rich` is available. Also available as `python -m deprecate`. ([#76](https://github.com/Borda/pyDeprecate/pull/76))
- **Four CLI subcommands: `check`, `expiry`, `chains`, `all`.** `check` validates wrapper configuration; `expiry` reports wrappers past their `remove_in` deadline (requires `pip install 'pyDeprecate[audit]'`); `chains` detects deprecated-to-deprecated forwarding chains; `all` runs all three in a single scan pass. Flags: `--norecursive`, `--skip_errors`. ([#149](https://github.com/Borda/pyDeprecate/pull/149))
- **New `DeprecationWrapperInfo.empty_deprecated_in` field.** `True` when `deprecated_in` is absent on a wrapper; intended for CI pipeline introspection. `dataclasses.asdict()` output and `repr()` now include this field. ([#166](https://github.com/Borda/pyDeprecate/pull/166))

**Docs**

- **Multi-page topic documentation site.** Replaced the monolithic README-copy home page with a curated 7-page MkDocs Material site: Home, Getting Started, User Guide (Use Cases / void() Helper / Audit Tools), Troubleshooting, and demo links. Switched theme to Material, added Open Graph tags, JSON-LD structured data (SoftwareApplication / FAQPage / TechArticle per page), spec-compliant `llms.txt`, and `git-revision-date-localized` plugin. README is unchanged (still the PyPI cover page). ([#146](https://github.com/Borda/pyDeprecate/pull/146))

### Deprecated

- **`target=None` sentinel — use `TargetMode.NOTIFY`.** Passing `target=None` now emits a `FutureWarning` at decoration time. The sentinel remains accepted but will be removed in v1.0. Migrate to `target=TargetMode.NOTIFY`. ([#150](https://github.com/Borda/pyDeprecate/pull/150))
- **`target=True` sentinel — use `TargetMode.ARGS_REMAP`.** Passing `target=True` now emits a `FutureWarning` at decoration time. The sentinel remains accepted but will be removed in v1.0. Migrate to `target=TargetMode.ARGS_REMAP`. ([#150](https://github.com/Borda/pyDeprecate/pull/150))
- **`DeprecationWrapperInfo` attributes `empty_mapping` → `empty_args_mapping` and `identity_mapping` → `identity_args_mapping`.** Old names are kept as deprecated `@property` aliases that emit `DeprecationWarning` on access and will be removed in v1.0. ([#166](https://github.com/Borda/pyDeprecate/pull/166))

### Changed

**Config & API**

- **Misconfigured `TargetMode` combinations now warn at construction time.** `TargetMode.ARGS_REMAP` without `args_mapping`, `TargetMode.NOTIFY` with `args_mapping`, and `TargetMode.NOTIFY` with `args_extra` all surface a `UserWarning` immediately. ([#150](https://github.com/Borda/pyDeprecate/pull/150))
- **`DeprecationConfig.target` always stores a normalised `TargetMode` or callable.** Legacy boolean sentinels (`True` / `False`) are now normalised at decoration time and are never stored verbatim in `DeprecationConfig.target`. Code that inspects `__deprecated__.target` must compare against `TargetMode.NOTIFY`, `TargetMode.ARGS_REMAP`, a callable, or `None` — never against `True` or `False`. ([#150](https://github.com/Borda/pyDeprecate/pull/150))
- **`deprecated_class()` with `target=TargetMode.NOTIFY` now emits `UserWarning` at decoration time when `args_mapping` or `args_extra` is supplied.** These parameters are ignored in `NOTIFY` mode; passing them has always been a misconfiguration. The warning will become `TypeError` in v1.0. ([#150](https://github.com/Borda/pyDeprecate/pull/150))
- **`target` parameter of `@deprecated` now defaults to `TargetMode.NOTIFY`.** Callers can omit `target` entirely for warn-only deprecation: `@deprecated(deprecated_in="1.0", remove_in="2.0")` is now the canonical form. Passing `target=TargetMode.NOTIFY` explicitly remains valid. This default is permanent and will not change in future releases. ([#162](https://github.com/Borda/pyDeprecate/pull/162))
- **Decoration-time `UserWarning` when `@deprecated` omits `deprecated_in`.** When `deprecated_in` is absent a `UserWarning` is emitted immediately at decoration time (not at call time) regardless of `target` shape (`TargetMode.NOTIFY`, callable, or `ARGS_REMAP`), even if `remove_in` is set. Applies to functions and methods (not classes). Suppressed when `stream=None` or when a custom `template_mgs` is provided. ([#162](https://github.com/Borda/pyDeprecate/pull/162))

**CLI**

- **CLI chains reporting split.** `check` subcommand reports deprecated-to-deprecated chains as warnings (exit 0); `chains` and `all` subcommands report chains as errors (exit 1). ([#149](https://github.com/Borda/pyDeprecate/pull/149))

**Docs**

- **Docs site URL layout is now versioned.** Content is published under `https://borda.github.io/pyDeprecate/stable/` (for the stable alias) and `https://borda.github.io/pyDeprecate/<tag>/` (for release tags). The root URL (`https://borda.github.io/pyDeprecate/`) redirects to `stable/`. External bookmarks to flat paths like `.../pyDeprecate/troubleshooting.html` will break on first deploy — update them to `.../pyDeprecate/stable/troubleshooting.html`. ([#148](https://github.com/Borda/pyDeprecate/pull/148))

### Fixed

**Guards & compatibility**

- **PEP 702 compatibility crash fixed.** When `@deprecated` was stacked under a PEP 702 `@typing.deprecated` decorator, `wrapped_fn` attempted to look up `__deprecated__` on the outer wrapper and raised `AttributeError`. Fixed by capturing `dep_meta` as a closure variable at decoration time instead of re-reading it from the wrapper. ([#169](https://github.com/Borda/pyDeprecate/pull/169))
- **Cross-class guard false positives resolved; `TypeError` semantics preserved.** Two previously documented "irresolvable" false-positive scenarios are now handled: (1) targets with metaclass/dynamic-class qualnames (e.g. `type("Name", bases, ns)` or manual `fn.__qualname__ = "FakeOwner.method"`) — guard now skips silently when the named class is absent from the target's module globals; (2) pre-applied decorators that rewrite the source's `__qualname__` — guard reads the true enclosing class from the Python class-body frame, which cannot be mutated by user decorators. The guard continues to raise `TypeError` at decoration time for genuine cross-class forwarding. ([#169](https://github.com/Borda/pyDeprecate/pull/169))
- **`target=False` sentinel now emits `UserWarning` at decoration time.** `target=False` was never a valid configuration; previously the behavior was undefined. The sentinel now surfaces a `UserWarning` immediately and will raise `TypeError` in v1.0. ([#150](https://github.com/Borda/pyDeprecate/pull/150))

**Warnings & forwarding**

- **Double `FutureWarning` emission on `deprecated_class()` in NOTIFY mode fixed.** Using `deprecated_class()` with `target=TargetMode.NOTIFY` triggered two `FutureWarning` emissions per construction call. ([#162](https://github.com/Borda/pyDeprecate/pull/162))
- **`args_mapping` rename no longer clobbers source default when both old and new parameter names are present.** Previously, calling a deprecated wrapper with the old argument name while the source also accepted the new name could silently overwrite the new-name value. The remapping now correctly renames `old=X` to `new=X` without discarding a separately supplied `new` value. ([#150](https://github.com/Borda/pyDeprecate/pull/150))

______________________________________________________________________

## [0.7.0] — 2026-03-31 — Docstring Tooling

### Added

**Docstring injection**

- **MkDocs admonition output.** `@deprecated` now accepts `docstring_style="mkdocs"` (alias: `"markdown"`). When `update_docstring=True`, the deprecation notice is injected as a `!!! warning "Deprecated in X"` admonition instead of a Sphinx `.. deprecated::` directive. Use `docstring_style="auto"` to detect style automatically from existing docstring content. ([#134](https://github.com/Borda/pyDeprecate/pull/134))
- **Google / NumPy section-aware docstring injection.** `update_docstring=True` now inserts the deprecation notice *before* the first section (`Args:`, `Returns:`, `Parameters`, …) rather than appending it at the end. ([#134](https://github.com/Borda/pyDeprecate/pull/134))
- **Inline arg deprecation in docstrings.** When `args_mapping` is set and `update_docstring=True`, each renamed or removed argument is annotated directly in the `Args:` / `:param` section of the docstring. ([#136](https://github.com/Borda/pyDeprecate/pull/136))

**Extensions & demos**

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

**Core decorator & forwarding**

- **`@deprecated(target=callable)` decorator.** Marks a function as deprecated and automatically forwards all calls — including argument mapping — to a replacement function. The deprecated function body is never executed when `target` is a callable.
- **Automatic argument mapping.** Positional arguments are resolved to keyword arguments and forwarded to the target's signature. `args_mapping` renames (`{"old": "new"}`) or drops (`{"old": None}`) individual arguments during forwarding.
- **`args_extra` — inject additional kwargs into the target call.** Pass a `dict` of extra keyword arguments to merge into every forwarded call. Useful for providing default values or adapter arguments that the deprecated API never accepted.

**Warning controls**

- **Configurable warning count (`num_warns`).** Warnings fire once per function by default; set to any positive integer to limit the total count per function lifetime.
- **Custom warning message template (`template_mgs`).** Format string with `%(source_name)s`, `%(target_path)s`, `%(deprecated_in)s`, `%(remove_in)s`, and `%(argument_map)s` placeholders.
- **Custom warning stream (`stream`).** Route warnings to `logging.warning`, `warnings.warn`, or any callable.
