"""Collection of deprecated wrappers exercising the deprecation-governance policy rules.

Every wrapper here is *correctly configured* — none of them is a misconfiguration in the
``collection_misconfigured.py`` sense — but each one is scheduled in a way that a project policy may forbid.
They are the fixtures for :func:`~deprecate.audit.validate_deprecation_policy` and the ``pydeprecate policy``
CLI subcommand.

Rule coverage, evaluated against a current version of ``2.0``:

| Wrapper                     | ``deprecated_in`` | ``remove_in`` | Violated rule              |
| --------------------------- | ----------------- | ------------- | -------------------------- |
| ``compliant_forward``       | ``1.0``           | ``2.0``       | — (policy-clean baseline)  |
| ``no_grace_window``         | ``2.0``           | ``2.0``       | ``min-grace``              |
| ``removed_at_patch``        | ``1.0``           | ``2.0.1``     | ``remove-only-at``         |
| ``warns_without_replacement`` | ``1.0``         | ``3.0``       | ``message-required``       |
| ``WarnOnlyLegacyClass``     | ``1.0``           | ``3.0``       | ``message-required``       |
| ``deprecated_in_the_future`` | ``9.0``          | ``10.0``      | ``deprecated-in-not-future`` |

Each wrapper violates exactly one rule so a test can assert on a rule in isolation; a real-world wrapper
usually trips several at once (a removal scheduled one patch after deprecation breaks both the grace window
and a major-only removal cadence).

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

from deprecate import TargetMode, deprecated, deprecated_class, void
from tests.collection_targets import NewCls, base_sum_kwargs, double_value, identity_value, increment_value


@deprecated(target=base_sum_kwargs, deprecated_in="1.0", remove_in="2.0")
def compliant_forward(a: int = 0, b: int = 3) -> int:
    """Deprecate one minor before a major removal and forward to the replacement — violates no rule."""
    return void(a, b)


@deprecated(target=double_value, deprecated_in="2.0", remove_in="2.0")
def no_grace_window(x: int) -> int:
    """Announce the deprecation and the removal in the same release — no grace window for callers at all."""
    return void(x)


@deprecated(target=increment_value, deprecated_in="1.0", remove_in="2.0.1")
def removed_at_patch(x: int) -> int:
    """Schedule the removal for a patch release, which a major-only removal cadence forbids."""
    return void(x)


@deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="3.0")
def warns_without_replacement(x: int) -> int:
    """Warn that the callable is going away without naming what replaces it."""
    return x


@deprecated(target=identity_value, deprecated_in="9.0", remove_in="10.0")
def deprecated_in_the_future(x: int) -> int:
    """Record a ``deprecated_in`` version that has not been released yet, while already warning callers."""
    return void(x)


@deprecated_class(deprecated_in="1.0", remove_in="3.0")
class WarnOnlyLegacyClass(NewCls):
    """Warn-only class alias with no replacement configured — the proxy form of a message-less deprecation."""
