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
    """Deprecate one major before removal and forward to the replacement — violates no rule.

    Examples:
        A maintainer deprecates a helper in ``1.0`` and schedules its removal for the next major,
        ``2.0`` — one major release of runway, a forwarding ``target``, and a ``deprecated_in`` that
        has already shipped. ``validate_deprecation_policy()`` reports no violation for this wrapper
        under the default policy (``min_grace="1 minor"``, ``remove_only_at="major"``,
        ``message_required=True``, ``deprecated_in_not_future=True``): the major bump clears the
        one-minor grace window, the removal lands on a major boundary, the ``target`` counts as
        migration guidance, and ``1.0`` is not ahead of the caller's current version.

    """
    return void(a, b)


@deprecated(target=double_value, deprecated_in="2.0", remove_in="2.0")
def no_grace_window(x: int) -> int:
    """Announce the deprecation and the removal in the same release — no grace window for callers at all.

    Examples:
        A team ships ``deprecated_in="2.0"`` and ``remove_in="2.0"`` in the same release cycle,
        giving downstream callers zero versions to react before the function disappears.
        ``validate_deprecation_policy()`` flags this wrapper under the ``min-grace`` rule because the
        distance between the two versions is smaller than the required ``"1 minor"`` window.

    """
    return void(x)


@deprecated(target=increment_value, deprecated_in="1.0", remove_in="2.0.1")
def removed_at_patch(x: int) -> int:
    """Schedule the removal for a patch release, which a major-only removal cadence forbids.

    Examples:
        A maintainer plans to drop this callable in a patch release, ``2.0.1``, instead of waiting
        for the next major boundary. ``validate_deprecation_policy()`` flags this wrapper under the
        ``remove-only-at`` rule because the default ``remove_only_at="major"`` only permits
        ``X.0.0``-shaped removal versions, and a patch release breaks that convention even though the
        grace window itself is otherwise fine.

    """
    return void(x)


@deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="3.0")
def warns_without_replacement(x: int) -> int:
    """Warn that the callable is going away without naming what replaces it.

    Examples:
        A library wants to warn callers that a function is being retired but has no direct
        replacement to forward to, so it uses ``TargetMode.NOTIFY`` — the warning fires and the
        original body still runs. ``validate_deprecation_policy()`` flags this wrapper under the
        ``message-required`` rule because there is no ``target``, ``args_mapping``, ``attrs_mapping``,
        or custom ``message_template`` for a caller to act on.

    """
    return x


@deprecated(target=identity_value, deprecated_in="9.0", remove_in="10.0")
def deprecated_in_the_future(x: int) -> int:
    """Record a ``deprecated_in`` version that has not been released yet, while already warning callers.

    Examples:
        A wrapper claims ``deprecated_in="9.0"`` while the package's actual current version is only
        ``"2.0"`` — the deprecation warning fires for a release that has not shipped yet, which
        misleads callers about when the migration window actually opened.
        ``validate_deprecation_policy()`` flags this wrapper under the ``deprecated-in-not-future``
        rule when scanned against a ``current_version`` of ``"2.0"``.

    """
    return void(x)


@deprecated_class(deprecated_in="1.0", remove_in="3.0")
class WarnOnlyLegacyClass(NewCls):
    """Warn-only class alias with no replacement configured — the proxy form of a message-less deprecation.

    Examples:
        A team wraps an old class with ``deprecated_class()`` to warn on instantiation, but never
        supplies a ``target``, mapping, or custom message pointing callers at a replacement.
        ``validate_deprecation_policy()`` flags this wrapper under the ``message-required`` rule for
        the same reason it flags a message-less function: nothing in the configuration tells a caller
        what to migrate to.

    """
