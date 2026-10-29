---
description: Known limitations and non-goals of pyDeprecate — async, proxies, scoped switches, static checkers, and the audit tooling — each with the workaround.
---

# Limitations and non-goals

pyDeprecate favours a small API over covering every corner of Python. This page lists the corners it deliberately does not cover, so you can recognise them before they surprise you. Each entry states the behaviour in one or two sentences and links to the section with the details and the workaround.

## Async and generators

- **Async-generator wrappers are synchronous.** `@deprecated` on an `async def` + `yield` function returns a plain `def` wrapper that warns at call time and hands back the async generator, so `inspect.iscoroutinefunction(wrapper)` is `False` and the warning does not wait for the first `async for`. See [Async generators](guide/async.md#async-generators).
- **Callable objects with `async def __call__` are not detected as coroutine functions.** `inspect.iscoroutinefunction` does not look at `__call__`, and `@deprecated` rejects a plain callable instance with `TypeError`, so such an object cannot serve as an `async def` source. Define a thin `async def` that awaits the object and decorate that. See [functools.partial and callable wrappers](troubleshooting.md#why-cant-i-decorate-a-functoolspartial-or-callable-wrapper).
- **Async and sync targets are not interchangeable in both directions.** An `async def` source may forward to a sync target, which is called directly without being awaited. A sync source whose target is a coroutine function raises `TypeError` on the first call, because a sync wrapper cannot await it. Declare the source `async def`.
- **Cycle detection is lazy.** A `target=` chain that loops back on itself is not caught at decoration time; the `RuntimeError` fires when a wrapper in the cycle is first called. See [the cycle error](troubleshooting.md#runtimeerror-circular-deprecation-cycle-detected).

## Proxies (`deprecated_instance`, `deprecated_class`)

- **Item writes never warn.** `proxy["key"] = value` and `del proxy["key"]` pass through silently, like attribute writes, and they honour `skip_if`. With `read_only=True` they raise `AttributeError` unless `skip_if` is active. See [deprecated_instance](guide/classes.md).
- **`read_only` covers only the standard mutators.** Besides writes through the proxy, it blocks the collection methods `append`, `clear`, `discard`, `extend`, `insert`, `pop`, `remove`, `setdefault`, `update` and `add`. A custom method such as `register()` or `reload()` still mutates the wrapped object. See [read_only troubleshooting](troubleshooting.md#my-object-mutated-despite-read_onlytrue).
- **A proxy over a `str` or `bytes` value claims `os.PathLike`.** A proxy carries only the protocols the wrapped object supports, so `callable()`, `Iterator`, `Awaitable` and the other structural ABCs answer as for the object itself. The one exception is text: `int()`, `float()`, `complex()` and `os.fspath()` parse `str` and `bytes` without a dunder on the type, and the proxy keeps those conversions so a deprecated `"8080"` or path string still converts. That makes such a proxy report `os.PathLike` and `typing.SupportsInt` too. Check the type of the unwrapped object when you use one of those two checks as a guard.
- **A class proxy cannot be pickled by reference when it replaces the class's own name.** When `deprecated_class` rebinds the module-level name of the decorated class, `pickle` finds the proxy where it expects the class and raises `PicklingError`. Instance proxies pickle fine. See [copy, deepcopy and pickle](troubleshooting.md#does-the-deprecation-survive-copy-deepcopy-and-pickle).
- **`cached_property` warns at most once per instance.** The cache is filled by the first read, so later reads of that instance never reach pyDeprecate and `num_warns` cannot raise the count. With `num_warns=-1` every instance warns on its first read; the default `num_warns=1` budget is shared by all instances, so only the first one warns. See [Properties](guide/properties.md).

## Configuration

- **`skip_if` must return a strict `bool`.** A truthy `int` or a string raises `TypeError`; wrap the result in `bool(...)`. See [the `skip_if` error](troubleshooting.md#typeerror-skip_if-function-must-return-bool).
- **A bound target does not work for a classmethod source.** A bound target such as `target=YourClass.method` works only for a source that is not a classmethod; for a genuine classmethod source it fails, because the bound target has no receiver parameter and the source's receiver argument is rejected. Inside the class body pass the raw descriptor (`target=method`) instead. See [Class methods and static methods](guide/advanced.md#class-methods-and-static-methods).
- **An `as_exceptions()` scope reaches a new thread only if the interpreter copies the context.** The scope is local to the current thread and `asyncio` task, and tasks created inside it inherit it. Whether a thread started inside the block inherits it depends on the interpreter, so run the thread's target through `contextvars.copy_context().run(...)` when it must. Inside a scope `deprecate.AS_EXCEPTIONS` still reports the global value, so do not read it to detect a scope.
- **An `as_exceptions()` scope held across a generator `yield` applies to the consumer.** A generator or async generator runs in its consumer's context, so while it is suspended inside `with as_exceptions():` the consumer's own code is fatal too, and if the generator is closed from another task (`aclose()` elsewhere, garbage collection) the scope stays open in the consumer's context. Open and close the scope within one step of the generator. See [The global switch](guide/functions.md#the-global-switch).

## Static checkers

- **Only a literal `warnings.deprecated` is visible to type checkers.** PEP 702 tools recognise the decorator by name, so deprecations that pyDeprecate applies at runtime are invisible to mypy, pyright and IDEs. Stack a literal `warnings.deprecated` (or `typing_extensions.deprecated`) on top as shown in [Static type checkers (PEP 702)](guide/functions.md#static-type-checkers-pep-702); for classes see [PEP 702 static checkers](guide/classes.md#pep-702-static-checkers).

## Audit tooling

- **There is no import-free scan mode.** The audit imports the package it scans, so its module-level code runs and its runtime dependencies must be installed in the scanning environment. The remote pre-commit hook runs in an isolated environment and therefore only suits packages without such dependencies; use a `repo: local` hook otherwise. See [Audit Tools](guide/audit.md) and [the CLI reference](guide/cli.md).
- **Scans are per package, never per file.** A single `.py` path is rejected with `File paths are not supported`, and a directory holding two or more top-level packages is rejected as well, so "scan only the files changed in this commit" is not available. Pass the package directory or an importable name. See [Path formats](guide/cli.md#path-formats).
- **The GitHub Action's `install: true` re-pins pyDeprecate.** The default installs pyDeprecate from the action's own ref into the job's Python, replacing the version your job already had. Set `install: false` when your project pins its own version. See [GitHub Action](guide/audit.md#github-action).
