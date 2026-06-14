---
id: customization
description: Customize pyDeprecate deprecation messages with built-in templates or your own, and redirect deprecation output to a logger, print, or any callable via the stream parameter.
---

# Customization

## Deprecation Messages and Templates

pyDeprecate picks a deprecation message template automatically based on how you configured the decorator. Override it with `template_mgs` when the defaults do not fit.

### Default templates

Three built-in templates cover the common scenarios:

| Template                     | When it fires                                                                           | Example output                                                                                                                   |
| ---------------------------- | --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `TEMPLATE_WARNING_CALLABLE`  | `target` is a callable (function forwarding)                                            | `The 'score' was deprecated since v1.0 in favor of 'pkg.score_predictions'. It will be removed in v2.0.`                         |
| `TEMPLATE_WARNING_ARGUMENTS` | `TargetMode.ARGS_REMAP` with `args_mapping` and the caller passes a deprecated argument | `The 'my_func' uses deprecated arguments: 'lr' -> 'learning_rate'. They were deprecated since v1.0 and will be removed in v2.0.` |
| `TEMPLATE_WARNING_NO_TARGET` | `TargetMode.NOTIFY` (notice-only, no forwarding)                                        | `The 'legacy_func' was deprecated since v1.0. It will be removed in v2.0.`                                                       |

The selection logic is:

1. If `target` is a callable (function, method, or class) → `TEMPLATE_WARNING_CALLABLE`
2. If `TargetMode.ARGS_REMAP` and the caller passes a deprecated argument from `args_mapping` → `TEMPLATE_WARNING_ARGUMENTS`
3. If `TargetMode.NOTIFY` → `TEMPLATE_WARNING_NO_TARGET`

When you provide `template_mgs`, your custom template replaces whichever default would have been chosen.

### Placeholder variables

Custom templates use Python `%`-style formatting (`%(key)s`). Available placeholders depend on the deprecation type:

| Placeholder     | Available when                              | Value                                            |
| --------------- | ------------------------------------------- | ------------------------------------------------ |
| `source_name`   | Always                                      | Name of the deprecated function (e.g. `"score"`) |
| `source_path`   | Always                                      | Fully qualified path (e.g. `"mypackage.score"`)  |
| `target_name`   | `target` is callable                        | Name of the replacement function                 |
| `target_path`   | `target` is callable                        | Fully qualified path of the replacement          |
| `deprecated_in` | Always                                      | Value of `deprecated_in` parameter               |
| `remove_in`     | Always                                      | Value of `remove_in` parameter                   |
| `argument_map`  | `TargetMode.ARGS_REMAP` with `args_mapping` | Formatted string like `` `old` -> `new` ``       |

### Custom template example

```python
from deprecate import TargetMode, deprecated


# NEW API — detects objects and returns a scaled count
def detect_objects(x: int) -> int:
    return x * 10


# DEPRECATED API — `detect` replaced by `detect_objects`
@deprecated(
    target=detect_objects,
    deprecated_in="2.0",
    remove_in="3.0",
    template_mgs=("[MIGRATION] `%(source_name)s` is removed in v%(remove_in)s. Switch to `%(target_path)s`."),
)
def detect(x: int) -> int:
    pass


# Emits: [MIGRATION] `detect` is removed in v3.0. Switch to `your_module.detect_objects`.
result = detect(5)
print(result)
```

<details>
  <summary>Output: <code>detect(5)</code></summary>

```
50
```

</details>

For argument deprecation, a custom template that references the mapping:

```python
from deprecate import TargetMode, deprecated


@deprecated(
    target=TargetMode.ARGS_REMAP,
    deprecated_in="1.5",
    remove_in="2.0",
    args_mapping={"lr": "learning_rate"},
    template_mgs="%(source_name)s: renamed args %(argument_map)s (since v%(deprecated_in)s, removal v%(remove_in)s)",
)
def train(lr: float = 0.01, learning_rate: float = 0.01) -> float:
    return learning_rate


# Emits: train: renamed args `lr` -> `learning_rate` (since v1.5, removal v2.0)
print(train(lr=0.001))
```

<details>
  <summary>Output: <code>train(lr=0.001)</code></summary>

```
0.001
```

</details>

## Deprecation Output Sink (`stream`)

`stream` controls where deprecation messages go. It accepts any callable with signature `(msg: str) -> None`, or `None` to silence output entirely.

### Default: FutureWarning via `warnings.warn`

By default, `stream` is `functools.partial(warnings.warn, category=FutureWarning)`. In practice this means:

- Deprecation notices appear as `FutureWarning`, visible by default in scripts and interactive sessions.
- Standard warning filters apply — suppress with `warnings.filterwarnings("ignore", category=FutureWarning)` when needed.
- The traceback points to internal pyDeprecate wrapper code. For caller-level tracebacks, use a custom stream that calls `warnings.warn` with an appropriate `stacklevel`.

### Silencing deprecation output entirely

Pass `stream=None` to disable all deprecation output for a specific function. Call forwarding still works — only the message is suppressed. This is useful for internal wrappers that exist solely for backwards compatibility without user-facing noise.

!!! warning "Testing gotcha: `stream=None` suppresses `FutureWarning`"

    When `stream=None` is set, no `FutureWarning` is emitted, so `pytest.warns(FutureWarning)` will fail. Test the call-forwarding result directly instead of asserting the warning. See [Testing Deprecated Code](audit.md#testing-deprecated-code) for patterns.

```python
from deprecate import deprecated


def _compute(x: int) -> int:
    return x + 1


@deprecated(target=_compute, deprecated_in="1.0", remove_in="2.0", stream=None)
def _compute_raw(x: int) -> int:
    pass


# No warning emitted, but call is still forwarded to _compute
print(_compute_raw(5))
```

<details>
  <summary>Output: <code>_compute_raw(5)</code></summary>

```
6
```

</details>

### Redirecting to a logger

Pass `logging.warning` (or any logging level method) to route deprecation messages through Python's logging system. This plugs straight into your existing log aggregation, filtering, and formatting.

```python
import logging
from deprecate import deprecated

logging.basicConfig(level=logging.WARNING)


# NEW API — sorts a list and returns the sorted copy
def sort_items(data: list) -> list:
    return sorted(data)


# DEPRECATED API — `process_batch` replaced by `sort_items`
@deprecated(
    target=sort_items,
    deprecated_in="1.0",
    remove_in="2.0",
    stream=logging.warning,
)
def process_batch(data: list) -> list:
    pass


# Instead of a FutureWarning, this emits a WARNING-level log line:
#   WARNING:root:The `process_batch` was deprecated since v1.0 in favor of `your_module.sort_items`.
#   It will be removed in v2.0.
print(process_batch([3, 1, 2]))
```

<details>
  <summary>Output: <code>process_batch([3, 1, 2])</code></summary>

```
[1, 2, 3]
```

</details>

Pick the log level that matches the urgency:

- `logging.warning` — standard choice; visible in default configs
- `logging.error` — critical deprecations nearing removal deadline
- `logging.info` — low-priority deprecations during early migration

!!! tip "Combine `num_warns=-1` with `stream=logging.warning` for migration tracking"

    With unlimited notices routed to your logger, every deprecated call site appears in your log aggregation system (ELK, Datadog, CloudWatch). Query the logs to measure migration progress and find remaining callers before the removal deadline.

### Using `print` for simple console output

For quick debugging or scripts where you want immediate stdout output without the warnings module:

```python
from deprecate import deprecated


# NEW API — formats a greeting string for the given name
def say_hello(name: str) -> str:
    return f"Hello, {name}!"


say_hello.__module__ = "your_module"


# DEPRECATED API — `greet` replaced by `say_hello`
@deprecated(target=say_hello, deprecated_in="1.0", remove_in="2.0", stream=print)
def greet(name: str) -> str:
    pass


# Prints directly to stdout:
#   The `greet` was deprecated since v1.0 in favor of `your_module.say_hello`.
#   It will be removed in v2.0.
print(greet("World"))
```

<details>
  <summary>Output: <code>greet("World")</code></summary>

```
The `greet` was deprecated since v1.0 in favor of `your_module.say_hello`. It will be removed in v2.0.
Hello, World!
```

</details>

### Custom stream callable

Any callable accepting a single string argument works. Here is an example that collects deprecation messages into a list for later processing:

```python
from deprecate import deprecated

collected_warnings: list = []


def collector(msg: str) -> None:
    collected_warnings.append(msg)


# NEW API — doubles the input value
def target_fn(x: int) -> int:
    return x * 2


target_fn.__module__ = "your_module"


# DEPRECATED API — `source_fn` replaced by `target_fn`
@deprecated(target=target_fn, deprecated_in="1.0", remove_in="2.0", stream=collector)
def source_fn(x: int) -> int:
    pass


source_fn(10)
print(collected_warnings)
```

<details>
  <summary>Output: <code>print(collected_warnings)</code></summary>

```
['The `source_fn` was deprecated since v1.0 in favor of `your_module.target_fn`. It will be removed in v2.0.']
```

</details>

## See also

- [Use Cases](use-cases.md) — worked examples of all deprecation patterns including stream and template usage
- [Audit Tools](audit.md) — complement custom streams with CI enforcement of removal deadlines
- [Troubleshooting](../troubleshooting.md) — how to redirect deprecation output to a Python logger instead of `warnings.warn`

______________________________________________________________________

Next: [Audit Tools](audit.md) — validate decorator configuration, enforce removal deadlines, and detect deprecation chains in CI.
