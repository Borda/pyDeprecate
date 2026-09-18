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
| ``args_mapping_only_guidance``     | ``1.0``      | ``2.0``       | — (``args_mapping``-only guidance)|
| ``AttrsMappingOnlyGuidance``       | ``1.0``      | ``2.0``       | — (``attrs_mapping``-only guidance)|
| ``message_template_only_guidance`` | ``1.0``      | ``2.0``       | — (``message_template``-only)     |
| ``warns_with_template_instance``   | ``1.0``      | ``2.0``       | — (instance with custom template) |
| ``warns_without_template_instance``| ``1.0``      | ``2.0``       | ``message-required`` (instance)   |
| ``short_minor_runway``             | ``1.0``      | ``1.1``       | ``min-grace`` (insufficient distance)|

Each wrapper violates exactly one rule so a test can assert on a rule in isolation; a real-world wrapper
may trip both at once (a warn-only wrapper removed one patch after deprecation).

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

from typing import Any

from deprecate import TargetMode, deprecated, deprecated_class, deprecated_instance, void
from tests.collection_targets import NewCls, Palette, base_sum_kwargs, double_value, increment_value

#: Shared ``(deprecated_in, remove_in)`` for every fixture that is policy-clean under the default window:
#: one major release of runway, which clears the ``"0.3"`` (three-minor) grace window outright.
_DEPRS_CASE_COMPLIANT_ARGS: dict[str, Any] = {"deprecated_in": "1.0", "remove_in": "2.0"}


@deprecated(target=base_sum_kwargs, **_DEPRS_CASE_COMPLIANT_ARGS)
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


@deprecated(target=increment_value, deprecated_in="1.0", remove_in="1.1")
def short_minor_runway(x: int) -> int:
    """Schedule the removal one minor after the deprecation — a real bump, but short of the window.

    Examples:
        A maintainer bumps ``remove_in`` to the very next minor, ``1.1``, right after deprecating in
        ``1.0`` — a clean single-component bump, unlike ``removed_at_patch``, but only one minor step.
        ``validate_deprecation_policy()`` flags this wrapper under the ``min-grace`` rule because the
        default window (``"0.3"``, three minors) demands at least three, and this wrapper offers only
        one — the insufficient-distance case, as distinct from ``no_grace_window``'s zero distance.

    """
    return void(x)


@deprecated(target=TargetMode.ARGS_REMAP, **_DEPRS_CASE_COMPLIANT_ARGS, args_mapping={"old_x": "x"})
def args_mapping_only_guidance(x: int = 0) -> int:
    """Rename an argument via ``args_mapping`` with no ``target`` and no custom message — violates no rule.

    Examples:
        A maintainer renames a keyword argument in place using ``TargetMode.ARGS_REMAP`` — old callers
        passing ``old_x`` are transparently remapped to ``x`` — without pointing at any separate
        replacement callable. ``validate_deprecation_policy()`` reports no ``message-required``
        violation for this wrapper: the non-empty ``args_mapping`` itself names what changed for a
        caller, even with no ``target`` and no custom ``message_template``.

    """
    return void(x)


@deprecated_class(**_DEPRS_CASE_COMPLIANT_ARGS, attrs_mapping={"color": "colour"}, stream=None)
class AttrsMappingOnlyGuidance(Palette):
    """Rename an attribute via ``attrs_mapping`` with no ``target`` and no custom message — violates no rule.

    Examples:
        A team renames an attribute on a legacy class alias using ``attrs_mapping`` alone, with no
        ``target`` class to redirect to and no hand-written migration sentence: old callers reading
        ``.color`` are redirected to ``Palette``'s real ``colour`` class attribute.
        ``validate_deprecation_policy()`` reports no ``message-required`` violation: the non-empty
        ``attrs_mapping`` already tells a caller which attribute replaced the old one.

    """


@deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_COMPLIANT_ARGS, message_template="use `new_thing` instead")
def message_template_only_guidance(x: int) -> int:
    """Warn with a hand-written migration sentence and no ``target`` or mapping — violates no rule.

    Examples:
        A maintainer uses ``TargetMode.NOTIFY`` to warn callers a function is going away, but — unlike
        ``warns_without_replacement`` — spells out the replacement by hand in a custom
        ``message_template``. ``validate_deprecation_policy()`` reports no ``message-required``
        violation: the template alone tells a caller what to migrate to.

    """
    return x


#: Wrap an instance with no ``message_template`` at all — the ``deprecated_instance()`` dead end. A team wraps a
#: soon-to-be-removed configuration object with ``deprecated_instance()`` but supplies no ``message_template`` —
#: the only migration-guidance knob ``deprecated_instance()`` accepts, since it has no ``target`` or mapping
#: argument. ``validate_deprecation_policy()`` flags this wrapper under the ``message-required`` rule, and the
#: remedy text names only a custom ``message_template``.
warns_without_template_instance = deprecated_instance(NewCls(1.0), **_DEPRS_CASE_COMPLIANT_ARGS)

#: Wrap an instance with a custom ``message_template`` — violates no rule. A team wraps a soon-to-be-removed
#: configuration object with ``deprecated_instance()`` and supplies a custom ``message_template`` naming the
#: replacement. ``validate_deprecation_policy()`` reports no ``message-required`` violation for this wrapper:
#: the template is the only guidance knob ``deprecated_instance()`` has, and it is set.
warns_with_template_instance = deprecated_instance(
    NewCls(1.0), **_DEPRS_CASE_COMPLIANT_ARGS, message_template="use `NewCls` directly instead"
)
