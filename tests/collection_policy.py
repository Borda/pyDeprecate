"""Collection of deprecated wrappers exercising the deprecation-governance policy rules.

Every wrapper here is *correctly configured* — none of them is a misconfiguration in the
``collection_misconfigured.py`` sense — but each one is scheduled in a way that a project policy may forbid.
They are the fixtures for :func:`~deprecate.audit.validate_deprecation_policy` and the ``pydeprecate policy``
CLI subcommand.

Rule coverage under the default policy (``min_grace="0.3"``, ``message_required=True``):

| Wrapper                       | ``deprecated_in`` | ``remove_in`` | Violated rule                     |
| ----------------------------- | ----------------- | ------------- | --------------------------------- |
| ``compliant_forward``         | ``1.0``           | ``2.0``       | — (policy-clean baseline)         |
| ``no_grace_window``           | ``2.0``           | ``2.0``       | ``min-grace`` (no distance)       |
| ``removed_at_patch``          | ``1.0``           | ``2.0.1``     | ``min-grace`` (off a boundary)    |
| ``warns_without_replacement`` | ``1.0``           | ``3.0``       | ``message-required``              |
| ``WarnOnlyLegacyClass``       | ``1.0``           | ``3.0``       | ``message-required``              |

Each wrapper violates exactly one rule so a test can assert on a rule in isolation; a real-world wrapper
may trip both at once (a warn-only wrapper removed one patch after deprecation).

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

from deprecate import TargetMode, deprecated, deprecated_class, void
from tests.collection_targets import NewCls, base_sum_kwargs, double_value, increment_value


@deprecated(target=base_sum_kwargs, deprecated_in="1.0", remove_in="2.0")
def compliant_forward(a: int = 0, b: int = 3) -> int:
    """Deprecate one major before removal and forward to the replacement — violates no rule.

    Examples:
        A maintainer deprecates a helper in ``1.0`` and schedules its removal for the next major,
        ``2.0`` — one major release of runway and a forwarding ``target``.
        ``validate_deprecation_policy()`` reports no violation for this wrapper under the default
        policy (``min_grace="0.3"``, ``message_required=True``): the clean major bump clears the
        three-minor grace window, and the ``target`` counts as migration guidance.

    """
    return void(a, b)


@deprecated(target=double_value, deprecated_in="2.0", remove_in="2.0")
def no_grace_window(x: int) -> int:
    """Announce the deprecation and the removal in the same release — no grace window for callers at all.

    Examples:
        A team ships ``deprecated_in="2.0"`` and ``remove_in="2.0"`` in the same release cycle,
        giving downstream callers zero versions to react before the function disappears.
        ``validate_deprecation_policy()`` flags this wrapper under the ``min-grace`` rule because the
        distance between the two versions is smaller than the required ``"0.3"`` (three-minor) window.

    """
    return void(x)


@deprecated(target=increment_value, deprecated_in="1.0", remove_in="2.0.1")
def removed_at_patch(x: int) -> int:
    """Schedule the removal for a patch release, which lands off every clean release boundary.

    Examples:
        A maintainer plans to drop this callable in a patch release, ``2.0.1``, instead of on the
        major boundary ``2.0``. ``validate_deprecation_policy()`` flags this wrapper under the
        ``min-grace`` rule: the removal is not a clean single-component bump from ``1.0`` (it moves
        the major *and* the patch), so it is rejected even though callers got more than the
        three-minor window.

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
