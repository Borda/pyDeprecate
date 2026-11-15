---
description: Known limitations and non-goals of pyDeprecate — async, proxies, scoped switches, static checkers, and the audit tooling — each with the workaround.
---

# Limitations and non-goals

pyDeprecate favours a small API over covering every corner of Python. This page lists the corners it deliberately does not cover, so you can recognise them before they surprise you.

Each entry states the behaviour in one or two sentences. It links to the section with the details and the workaround.

## Async and generators

!!! warning

    **Callable objects with `async def __call__` are not detected as coroutine functions.**

    `inspect.iscoroutinefunction` does not look at `__call__`. `@deprecated` rejects a plain callable instance with `TypeError`, so such an object cannot serve as an `async def` source.

    Define a thin `async def` that awaits the object and decorate that. See [functools.partial and callable wrappers](troubleshooting.md#why-cant-i-decorate-a-functoolspartial-or-callable-wrapper).

!!! warning

    **Async and sync targets are not interchangeable in both directions.**

    An `async def` source may forward to a sync target, which is called directly without being awaited. A sync source whose target is a coroutine function raises `TypeError` on the first call, because a sync wrapper cannot await it. Declare the source `async def`.

!!! warning

    **A `@types.coroutine` generator keeps the plain wrapper.**

    A generator-based coroutine is a generator function that `await` accepts only because its own code object carries the `CO_ITERABLE_COROUTINE` flag. A generator wrapper lacks that flag, so `@deprecated` gives it the plain function wrapper.

    The warning fires at the call rather than on the first step. `inspect.isgeneratorfunction` reports `False` for the wrapper.

!!! warning

    **A deprecated generator raises its argument errors on the first step, not at the call.**

    A native generator function called with the wrong number of arguments raises `TypeError` at once. Its deprecated wrapper accepts any arguments and only creates the generator.

    That arity `TypeError` uses pyDeprecate's own wording and raises on the first `next()` or `async for` step. A `Failed mapping` error from a forwarding target also raises at that step. See [Deprecating generator functions](guide/advanced.md#deprecating-generator-functions).

!!! warning

    **Cycle detection is lazy.**

    A `target=` chain that loops back on itself is not caught at decoration time. The `RuntimeError` fires when a wrapper in the cycle is first called.

    For generator wrappers it fires on the first `next()` or `async for` step, because calling a generator wrapper runs nothing. The same error fires when a replacement recurses through the deprecated name while the wrapper is still on the call path. For a generator, this means before its first `yield`.

    Recurse through the replacement's own name instead. See [the cycle error](troubleshooting.md#runtimeerror-circular-deprecation-cycle-detected).

## Proxies (`deprecated_instance`, `deprecated_class`)

!!! warning

    **Item writes never warn.**

    `proxy["key"] = value` and `del proxy["key"]` pass through silently, like attribute writes, and they honour `skip_if`. With `read_only=True` they raise `AttributeError` unless `skip_if` is active. See [deprecated_instance](guide/classes.md).

!!! warning

    **`read_only` covers only the standard mutators.**

    Besides writes through the proxy, it blocks the collection methods `append`, `clear`, `discard`, `extend`, `insert`, `pop`, `remove`, `setdefault`, `update` and `add`. A custom method such as `register()` or `reload()` still mutates the wrapped object. See [read_only troubleshooting](troubleshooting.md#my-object-mutated-despite-read_onlytrue).

!!! warning

    **A proxy over a `str` or `bytes` value claims `os.PathLike`.**

    A proxy carries only the protocols the wrapped object supports. `callable()`, `Iterator`, `Awaitable` and the other structural ABCs answer as for the object itself.

    The one exception is text: `int()`, `float()`, `complex()` and `os.fspath()` parse `str` and `bytes` without a dunder on the type. The proxy keeps those conversions so a deprecated `"8080"` or path string still converts. That makes such a proxy report `os.PathLike` and `typing.SupportsInt` too.

    Check the type of the unwrapped object when you use one of those two checks as a guard.

!!! warning

    **A proxy's protocols are fixed when it is created.**

    The proxy picks its protocols from the type of the wrapped object, and of its replacement, at construction. These protocols cover calling, iteration, `len()`, `in`, subscript, hashing and operators.

    A method the type gains afterwards does not reach that proxy. Examples include a `__call__` patched in later or a magic method set on a `Mock` after wrapping. As a result, `callable(obj)` can be `True` while `callable(proxy)` is `False`.

    Up to v0.14 every proxy forwarded every protocol. Finish setting up the object before you wrap it, or wrap it again after changing its type.

!!! warning

    **A class proxy cannot be pickled by reference when it replaces the class's own name.**

    When `deprecated_class` rebinds the decorated class's module-level name, `pickle` finds the proxy where it expects the class. Pickling the class alias itself therefore raises `PicklingError`.

    This covers the class object only. Instances of a class deprecated in place pickle and load as instances of the original class, and neither step warns.

    A payload pickled before the in-place deprecation loads only while its recorded `module.qualname` still names the real class. Once that name is rebound to the proxy, loading fails.

    Re-pickling before deprecating does not help. A payload written while the name is still bound to the real class names that class directly. Load such payloads through a `pickle.Unpickler.find_class` override that unwraps the proxy.

    This includes exception and `OrderedDict` subclasses, whose inherited `__reduce__` calls the class. A class that defines its own `__reduce_ex__` keeps its own pickling. Members of an Enum deprecated in place therefore still raise `PicklingError`.

    `deprecated_instance` proxies pickle fine. See [copy, deepcopy and pickle](troubleshooting.md#does-the-deprecation-survive-copy-deepcopy-and-pickle).

!!! warning

    **Pickled instances of a class deprecated in place load only while the deprecation is in place.**

    Their payload names one of pyDeprecate's rebuild helpers instead of the class, so the loading process needs pyDeprecate installed. Once the deprecation is removed, the helpers refuse the plain class with `AttributeError`.

    The helper names stay stable across releases, so upgrading pyDeprecate is safe. To migrate stored instances:

    1. Load them while the deprecation is in place.
    2. In the migration process, set the module attribute back to the real class.
    3. Re-pickle the instances.

    Re-pickling while the proxy still holds the name writes helper-dependent payloads again. See [loading pickled instances](troubleshooting.md#what-do-pickled-instances-of-a-class-deprecated-in-place-need-to-load).

!!! warning

    **The class of a proxy cannot be pickled.**

    `type(proxy)` is a generated subclass of `_DeprecatedProxy` that carries the proxy's protocols and borrows the base class's module and name. `pickle.dumps(type(proxy))` raises `PicklingError` because the borrowed name resolves to a different class.

    pyDeprecate v0.14 pickled the proxy class by reference. Pickling the proxy itself is unaffected. To detect a proxy, check `isinstance(obj, DeprecationProxy)` rather than storing or pickling its class.

!!! warning

    **A replacement object changes type checks at once.**

    With `deprecated_instance(old, target=new)`, `isinstance`, hashing and the served protocols follow `new` as soon as the deprecation ships, not at removal. A tuple constant replaced by a list is no longer hashable through the old name.

    Keep the replacement the same type as the old object, or a subclass. See [Serving a replacement object](guide/classes.md#serving-a-replacement-object).

!!! warning

    **`attrs_mapping` on an instance covers attributes, not keys.**

    `deprecated_instance(settings, attrs_mapping={"old": "new"})` redirects `settings.old`; a deprecated key of a dict (`settings["old"]`) cannot be renamed through the proxy.

!!! warning

    **A `bool`, `None` or a module cannot be a replacement object.**

    `target=None` means no replacement; `target=True`, `target=False` and a module raise `TypeError`. To replace a `bool` constant, wrap the new value itself (`deprecated_instance(NEW_FLAG, name="OLD_FLAG", ...)`); redirect a module with `deprecated_module`.

!!! warning

    **`args_mapping` passes a renamed value by keyword when the called object hides its signature.**

    To place a renamed value onto a positional-only parameter positionally, `deprecated_instance` and `deprecated_class` read the called object's signature once, when building the proxy.

    A builtin such as `iter` exposes none. An object whose `__signature__` raises is treated the same way. Consequently, `deprecated_instance(iter, args_mapping={"collection": "object"})` passes `object=` by keyword and the call raises the builtin's own `TypeError` (`iter() takes no keyword arguments`).

    Building the proxy does not warn about this. Builtins such as `max` and `min` hide their signature too, yet accept the renamed keyword (`key=`). Rename the arguments of such a builtin in a small Python function and wrap that function instead.

!!! warning

    **`cached_property` warns at most once per instance.**

    The cache is filled by the first read, so later reads of that instance never reach pyDeprecate and `num_warns` cannot raise the count. With `num_warns=-1` every instance warns on its first read; the default `num_warns=1` budget is shared by all instances, so only the first one warns. See [Properties](guide/properties.md).

## Configuration

!!! warning

    **`skip_if` must return a strict `bool`.**

    A truthy `int` or a string raises `TypeError`; wrap the result in `bool(...)`. See [the `skip_if` error](troubleshooting.md#typeerror-skip_if-function-must-return-bool).

!!! warning

    **A bound target does not work for a classmethod source.**

    A bound target such as `target=YourClass.method` works only for a source that is not a classmethod. For a genuine classmethod source it fails: the bound target has no receiver parameter, so the source's receiver argument is rejected.

    Inside the class body pass the raw descriptor (`target=method`) instead. See [Class methods and static methods](guide/advanced.md#class-methods-and-static-methods).

!!! warning

    **A classmethod target cannot tell a plain or static source from a classmethod.**

    A static source written `@deprecated(...)` above `@staticmethod` is rejected at decoration. A plain function, or a static source written `@staticmethod` above `@deprecated(...)`, looks like a classmethod source.

    Its first argument is forwarded as the replacement's class. When the call leaves that argument out, the parameter's default is forwarded instead. A value that is not a class raises `TypeError` before any warning.

    A class passed there or given as the default is forwarded as the receiver. Make the source a `@classmethod`, or give a static source a bound target such as `target=YourClass.method`. See [Class methods and static methods](guide/advanced.md#class-methods-and-static-methods).

!!! warning

    **An `as_exceptions()` scope reaches a new thread only if the interpreter copies the context.**

    The scope is local to the current thread and `asyncio` task, and tasks created inside it inherit it.

    Whether a thread started inside the block inherits it depends on the interpreter; [The global switch](guide/functions.md#the-global-switch) lists which threads and executors do. Run the thread's target through `contextvars.copy_context().run(...)` when it must inherit the scope. Inside a scope `deprecate.AS_EXCEPTIONS` still reports the global value, so do not read it to detect a scope.

!!! warning

    **An `as_exceptions()` scope held across a generator `yield` applies to the consumer.**

    A generator or async generator runs in its consumer's context. While it is suspended inside `with as_exceptions():`, the consumer's own code is fatal too.

    If the generator is closed from another task (`aclose()` elsewhere, garbage collection), the scope stays open in the consumer's context. Open and close the scope within one step of the generator. See [The global switch](guide/functions.md#the-global-switch).

## Static checkers

!!! warning

    **Only a literal `warnings.deprecated` is visible to type checkers.**

    PEP 702 tools recognise the decorator by name, so deprecations that pyDeprecate applies at runtime are invisible to mypy, pyright and IDEs.

    Stack a literal `warnings.deprecated` (or `typing_extensions.deprecated`) on top as shown in [Static type checkers (PEP 702)](guide/functions.md#static-type-checkers-pep-702). For classes see [PEP 702 static checkers](guide/classes.md#pep-702-static-checkers).

## Audit tooling

!!! warning

    **The Python static API shares the CLI `--static` blind spots.**

    `find_deprecation_wrappers_static()` (since v0.16) returns a `StaticScanResult`. Read `.gaps` for recognized unresolved settings and unaudited source, even when `.wrappers` is empty.

    Dynamic forms such as wrappers built in loops or through `getattr`, `importlib` or `exec` can remain invisible without a gap. Empty `.gaps` therefore does not prove complete coverage. Rows are approximations with stand-in targets that raise `TypeError` when called.

    See [scanning from Python without importing](guide/audit.md#scanning-from-python-without-importing) and [CLI static mode](guide/cli.md#static-mode).

!!! warning

    **The full audit imports the package it scans.**

    Its module-level code runs, and its runtime dependencies must be installed in the scanning environment. The import-mode remote pre-commit hooks therefore only suit packages without such dependencies; use a `repo: local` hook otherwise.

    See [Audit Tools](guide/audit.md) and [the CLI reference](guide/cli.md).

!!! warning

    **CLI `--static` covers `expiry` and `policy` only, and only what the source spells.**

    `pydeprecate expiry --static` and `policy --static` read the source with `ast`. `check`, `chains`, `all` and `status` need the imported wrappers, so they have no static mode.

    The following settings are reported as a `not statically resolvable` gap rather than checked:

    - A setting computed at runtime is a gap.
    - A setting held in a name bound more than once is a gap.
    - A setting splatted from anything but a module-level dict literal is a gap.

    A gap does not fail the run. Static mode cannot see:

    - It cannot see wrappers built in loops or through `getattr`, `importlib` or `exec`.
    - It cannot see a proxy built from the private `_DeprecatedProxy` directly.
    - It cannot see a deprecated object or configured decorator imported from outside the scanned package.

    It reads a `target` imported from outside the package as a replacement, even when it holds `None` or a `TargetMode` member. Members inherited from a base class outside the package are a gap. Its `--report-json` table names a replacement by its source text.

    See [Static mode](guide/cli.md#static-mode).

!!! warning

    **CLI scans are per package, never per file.**

    A single `.py` path is rejected with `File paths are not supported`. A directory holding two or more top-level packages is rejected as well.

    Therefore, "scan only the files changed in this commit" is not available. Pass the package directory or an importable name. See [Path formats](guide/cli.md#path-formats).

!!! warning

    **The GitHub Action's `install: true` re-pins pyDeprecate.**

    The default installs pyDeprecate from the action's own ref into the job's Python, replacing the version your job already had. Set `install: false` when your project pins its own version. See [GitHub Action](guide/ci-integration.md#github-action).
