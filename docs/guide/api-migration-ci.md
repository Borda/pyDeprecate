---
description: Add CI checks for Python deprecation removal deadlines, wrapper discovery, and deprecation-chain validation with pyDeprecate audit tools.
---

# API Migration CI Audit

Deprecation warnings are useful only if removal deadlines are enforced. pyDeprecate audit helpers let maintainers fail CI when wrappers are expired or inconsistent.

## Python audit checks

```python phmdoctest:skip
from deprecate import (
    find_deprecation_wrappers,
    validate_deprecation_chains,
    validate_deprecation_expiry,
)


wrappers = find_deprecation_wrappers("src")
validate_deprecation_chains("src")
validate_deprecation_expiry("src", current_version="2.0")
assert wrappers
```

## CLI audit check

```bash
pydeprecate all src/
```

## GitHub Actions example

```yaml
name: Deprecation audit

on:
  pull_request:
  push:
    branches: [main]

jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python -m pip install 'pyDeprecate[audit,cli]'
      - run: pydeprecate all src/
```

## Related pages

- [Deprecate arguments](deprecate-arguments.md)
- [Use Cases](use-cases.md)
- [Troubleshooting](../troubleshooting.md)
