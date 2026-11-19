---
id: testing
description: "Test deprecation warnings, warning budgets, and warning suppression in fixtures."
---

# Testing Deprecated Code

`assert_no_warnings` **fails if a specified warning category is raised** inside its context block. Pair it with `pytest.warns` to test both the *presence* and *absence* of deprecation notices.

`num_warns` defaults to `1`, limiting the deprecation message to **one emission** per function lifetime.

The first test checks that the warning appears. The third checks the default silence on subsequent calls.

```python
from deprecate import deprecated, assert_no_warnings, void
import pytest


# NEW API — doubles the input value
def score_predictions(x: int) -> int:
    return x * 2


# DEPRECATED API — `score` replaced by `score_predictions`
@deprecated(target=score_predictions, deprecated_in="1.0", remove_in="2.0")
def score(x: int) -> int:
    pass


# DEPRECATED API — `score_v2` replaced by `score_predictions`
@deprecated(target=score_predictions, deprecated_in="1.0", remove_in="2.0")
def score_v2(x: int) -> int:
    return void(x)


def test_deprecated_function_shows_warning():
    """Verify the deprecation warning is shown."""
    with pytest.warns(FutureWarning, match="score.*deprecated"):
        result = score(42)
    assert result == 84


def test_new_function_no_warning():
    """Verify new function doesn't trigger warnings."""
    with assert_no_warnings(FutureWarning):
        result = score_predictions(42)
    assert result == 84


def test_no_warning_after_first_call():
    """By default, warnings are shown only once per function."""
    # First call shows warning
    with pytest.warns(FutureWarning):
        score_v2(1)

    # Subsequent calls don't show warning (by default num_warns=1)
    with assert_no_warnings(FutureWarning):
        score_v2(2)


# call the tests for CI demonstration/validation
test_deprecated_function_shows_warning()
test_new_function_no_warning()
test_no_warning_after_first_call()
print("Warning tests passed")
```

<details>
  <summary>Output: <code>"Warning tests passed"</code></summary>

```text
Warning tests passed
```

</details>

Use these *warning budgets* to test repeated calls:

| Setting        | Behavior              | Use                                                    |
| -------------- | --------------------- | ------------------------------------------------------ |
| `num_warns=-1` | Warn on every call.   | Make critical deprecations impossible to miss.         |
| `num_warns=N`  | Warn exactly N times. | Verify emission counts precisely in integration tests. |

```python
# NEW API — doubles the input value
def score_predictions(x: int) -> int:
    return x * 2


# ---------------------------

from deprecate import deprecated


# DEPRECATED API — `predict` replaced by `score_predictions`; warn on every call
@deprecated(target=score_predictions, deprecated_in="1.0", remove_in="2.0", num_warns=-1)
def predict(x: int) -> int:
    pass


# DEPRECATED API — `predict_batch` replaced by `score_predictions`; warn five times
@deprecated(target=score_predictions, deprecated_in="1.0", remove_in="2.0", num_warns=5)
def predict_batch(x: int) -> int:
    pass


print(predict_batch(1))
```

<details>
  <summary>Output: <code>predict_batch(1)</code></summary>

```
2
```

</details>

### Suppressing warnings in test fixtures

A deprecated call in test setup **still emits** its first `FutureWarning`. This includes fixtures, factory helpers, and shared utilities. With default `num_warns=1`, subsequent calls are silent.

To exercise forwarding with clean pytest output, suppress the *specific message* at test-session level in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
filterwarnings = [
    'ignore:The `create_session` was deprecated:FutureWarning',
]
```

Alternatively, configure the equivalent runtime filter in `conftest.py`:

```python
# phmdoctest:skip — pytest conftest.py configuration snippet, not a runnable example
import warnings

warnings.filterwarnings("ignore", message="The `create_session` was deprecated", category=FutureWarning)
```

!!! warning "Keep filters specific"

    Filter the deprecation message rather than the entire `FutureWarning` category. Unrelated deprecations from dependencies <u>must remain visible</u>.

```python
from deprecate import deprecated, assert_no_warnings, void


# NEW API — creates a session dict with host and timeout
def new_create_session(host: str, timeout: int = 30) -> dict:
    return {"host": host, "timeout": timeout}


# DEPRECATED API — `create_session` replaced by `new_create_session`
@deprecated(target=new_create_session, deprecated_in="1.0", remove_in="2.0")
def create_session(host: str, timeout: int = 30) -> dict:
    return void(host, timeout)


def make_test_session(host: str = "localhost") -> dict:
    """Test fixture helper — calls deprecated API."""
    return create_session(host, timeout=5)  # warns: FutureWarning


# The first call emits one FutureWarning (suppressed in pytest by the filter above):
session = make_test_session()
print(session)

# Meanwhile, assert_no_warnings verifies NEW code is clean:
with assert_no_warnings(FutureWarning):
    clean_session = new_create_session("prod.example.com")
print(clean_session)
```

<details>
  <summary>Output: <code>new_create_session("prod.example.com")</code></summary>

```
{'host': 'localhost', 'timeout': 5}
{'host': 'prod.example.com', 'timeout': 30}
```

</details>

### Choosing the right testing tool

| Tool                                                       | Use when...                                                 | Behaviour                                              |
| ---------------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------ |
| `pytest.warns(FutureWarning)`                              | Testing that a deprecated function DOES warn on first call  | Fails if no matching warning is raised                 |
| `assert_no_warnings(FutureWarning)`                        | Testing that new code or subsequent calls do NOT warn       | Fails if a matching warning IS raised                  |
| `assert_no_warnings(FutureWarning, match="pattern")`       | Testing that a specific warning message is absent           | Only fails if a warning matching the pattern is raised |
| `filterwarnings` config (`pyproject.toml` / `conftest.py`) | Calling deprecated code in fixtures/setup without assertion | Silently suppresses matching warnings; never fails     |

!!! note "Substring matching"

    `assert_no_warnings` accepts a substring through `match`. It checks the captured warnings' message content. You can forbid **one specific deprecation** while allowing unrelated warnings.

## See also

- [Python audit API](../audit/index.md)
- [Deprecation policy](../audit/policy.md)
- [CI integration](../audit/ci-integration.md)
- [Classes](../usage/classes.md)
- [Selective attribute deprecation](../usage/attributes.md)
- [Use Cases](../usage/index.md) — Deprecation patterns and scenarios.
- [Getting Started](../getting-started.md) — The decorator parameters.
- [Troubleshooting](../troubleshooting.md) — Common configuration errors and fixes.
