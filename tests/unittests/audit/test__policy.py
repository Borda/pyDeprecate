"""Unit tests for private helpers in :mod:`deprecate.audit._policy`."""

import importlib
import importlib.metadata
import importlib.util
import sys
import types
from typing import Any, NoReturn

import pytest

from deprecate import (
    PolicyRule,
    TargetMode,
    validate_deprecation_policy,
)
from deprecate._types import DeprecationConfig
from deprecate.audit import (
    DeprecationWrapperInfo,
    GraceWindow,
    GraceWindowSpec,
    VersionBump,
)
from deprecate.audit._lifecycle import (
    _parse_version,
)
from deprecate.audit._policy import (
    _build_policy_spec,
    _check_policy_for_callables,
    _has_migration_guidance,
    _satisfies_grace_window,
)

_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None
_requires_packaging = pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging library")

_MODULE_IDENTITY: dict[str, Any] = {"name": "pkg.old_mod", "deprecated_in": "1.0", "remove_in": "2.0"}
#: The notice ``deprecated_module()`` renders into ``message_template`` for that module when the author passes none.
_MODULE_BUILT_IN_NOTICE = "The `pkg.old_mod` module was deprecated since v1.0. It will be removed in v2.0."
#: Replacement module for the redirect case; ``deprecated_module()`` reads only its ``__name__``.
_MODULE_TARGET = types.ModuleType("pkg.new_mod")


class TestGraceWindow:
    """The strict ``min_grace`` form -- one unit, one count -- and parsing of every user-facing spelling into it."""

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            pytest.param("0.1", GraceWindow(1, VersionBump.MINOR), id="one-minor"),
            pytest.param("2.0", GraceWindow(2, VersionBump.MAJOR), id="two-majors"),
            pytest.param("0.0.3", GraceWindow(3, VersionBump.PATCH), id="three-patches"),
            pytest.param(" 0.1 ", GraceWindow(1, VersionBump.MINOR), id="padding"),
            pytest.param(1.0, GraceWindow(1, VersionBump.MAJOR), id="float-major"),
            pytest.param(0.3, GraceWindow(3, VersionBump.MINOR), id="float-minor"),
            pytest.param("0.0", GraceWindow(0, VersionBump.MINOR), id="zero-count-disables-distance"),
            pytest.param({"major": 1}, GraceWindow(1, VersionBump.MAJOR), id="table-major"),
            pytest.param({"minor": 3}, GraceWindow(3, VersionBump.MINOR), id="table-minor"),
            pytest.param({"patch": 2}, GraceWindow(2, VersionBump.PATCH), id="table-patch"),
            pytest.param({VersionBump.MINOR: 3}, GraceWindow(3, VersionBump.MINOR), id="table-enum-key"),
            pytest.param({"minor": 0}, GraceWindow(0, VersionBump.MINOR), id="table-zero-count"),
            pytest.param(GraceWindow(1, VersionBump.MAJOR), GraceWindow(1, VersionBump.MAJOR), id="passthrough"),
        ],
    )
    def test_parse_accepts_documented_spellings(self, spec: GraceWindowSpec, expected: GraceWindow) -> None:
        """Every documented spelling of a grace window converts to the same strict form.

        A policy is configured from a CLI flag, a ``pyproject.toml`` table, or a keyword argument typed by hand.
        The dotted delta reads like a version -- the same shape as ``deprecated_in`` and ``remove_in`` -- so
        ``"0.1"`` means one minor step and ``"0.0.3"`` three patch steps, and a float spells the same thing
        (``0.3``, which is also what Fire hands the CLI for an unquoted flag value); the table names the unit
        outright, which is what a TOML inline table (``{ minor = 3 }``) arrives as. Whatever the spelling, the
        scan only ever sees a ``GraceWindow``, and an instance built by hand passes through untouched.
        """
        assert GraceWindow.parse(spec) == expected

    @pytest.mark.parametrize(
        ("count", "unit"),
        [
            pytest.param(-1, VersionBump.MINOR, id="negative-count"),
            pytest.param(True, VersionBump.MINOR, id="bool-count"),
            pytest.param("3", VersionBump.MINOR, id="string-count"),
            pytest.param(1, "minor", id="string-unit"),
        ],
    )
    def test_construction_rejects_invalid_fields(self, count: object, unit: object) -> None:
        """Building a ``GraceWindow`` by hand is held to the same rules as parsing one.

        The strict form is the single place the scan trusts, so a window assembled in code (a test helper, a
        config loader that bypasses ``parse``) must not be able to carry a value no spelling could produce --
        otherwise the version arithmetic downstream would compare against a bool or a string.
        """
        with pytest.raises(ValueError, match="Invalid `min_grace` specification"):
            GraceWindow(count, unit)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "spec",
        [
            pytest.param("1", id="bare-major-ambiguous"),
            pytest.param(1, id="bare-int"),
            pytest.param(True, id="bool"),
            pytest.param("1 minor", id="legacy-count-unit-spelling"),
            pytest.param("1.2", id="two-non-zero-components"),
            pytest.param("0.0.0.1", id="four-components"),
            pytest.param("one", id="word-count"),
            pytest.param("", id="empty"),
            pytest.param({}, id="empty-table"),
            pytest.param({"major": 1, "minor": 2}, id="two-unit-table"),
            pytest.param({"release": 1}, id="unknown-unit"),
            pytest.param({"minor": "3"}, id="string-count"),
            pytest.param({"minor": -1}, id="negative-count"),
            pytest.param({"minor": True}, id="bool-count"),
        ],
    )
    def test_rejects_unparseable_specification(self, spec: object) -> None:
        """An unrecognised grace window fails loudly instead of silently disabling the rule.

        A typo that quietly turned the grace rule off would leave a CI gate reporting green while checking
        nothing at all, so the specification is validated before the scan starts. A bare ``"1"`` is rejected as
        ambiguous -- one *what*? -- and must be written ``"1.0"`` or ``{"major": 1}``; ``"1.2"`` has no meaning
        under the coarser-bump rule; a table can only ever name one unit with an integer count, which is the
        policy's own rule made structural.
        """
        with pytest.raises(ValueError, match="Invalid `min_grace` specification"):
            GraceWindow.parse(spec)  # type: ignore[arg-type]


class TestBuildPolicySpec:
    """Validation of the raw policy arguments before any wrapper is scanned."""

    def test_disabled_rules_carry_none(self) -> None:
        """Passing ``None`` for the grace window disables it rather than falling back to the default.

        A project that only wants the guidance rule needs the grace window off while keeping the rest, so a
        disabled rule must survive as ``None`` all the way into the per-wrapper checks.
        """
        spec = _build_policy_spec(None, False)
        assert spec.grace is None
        assert spec.message_required is False


class TestSatisfiesGraceWindow:
    """Version-distance arithmetic behind the ``min-grace`` rule."""

    @pytest.mark.parametrize(
        ("deprecated_in", "remove_in", "count", "unit", "expected"),
        [
            pytest.param("1.0", "1.1", 1, VersionBump.MINOR, True, id="exactly-one-minor"),
            pytest.param("1.0", "1.0", 1, VersionBump.MINOR, False, id="same-release"),
            pytest.param("1.2", "1.5", 3, VersionBump.MINOR, True, id="three-minors"),
            pytest.param("1.2", "1.4", 3, VersionBump.MINOR, False, id="two-minors-short-of-three"),
            pytest.param("1.2", "2.0", 3, VersionBump.MINOR, True, id="major-bump-clears-minor-window"),
            pytest.param("1.2", "2.3", 3, VersionBump.MINOR, False, id="mixed-major-and-minor-bump"),
            pytest.param("1.0", "2.0", 1, VersionBump.MAJOR, True, id="one-major"),
            pytest.param("1.0", "1.9", 1, VersionBump.MAJOR, False, id="minors-do-not-clear-major-window"),
            pytest.param("1.2", "2.1", 1, VersionBump.MAJOR, False, id="major-bump-with-non-zero-minor"),
            pytest.param("1.0.0", "1.0.1", 1, VersionBump.PATCH, True, id="one-patch"),
            pytest.param("1.0.0", "1.1.0", 1, VersionBump.PATCH, True, id="minor-bump-clears-patch-window"),
            pytest.param("1.2.3", "1.3.0", 1, VersionBump.MINOR, True, id="lower-components-reset-on-bump"),
            pytest.param("1.2.3", "1.3.1", 1, VersionBump.MINOR, False, id="patch-after-minor-bump"),
            pytest.param("1.2.3", "1.2.5", 1, VersionBump.MINOR, False, id="patch-bump-finer-than-minor-unit"),
            pytest.param("1.0", "1.1", 0, VersionBump.MINOR, True, id="zero-count-accepts-any-minor"),
            pytest.param("1.0", "1.0.1", 0, VersionBump.MINOR, False, id="zero-count-still-rejects-finer-bump"),
            pytest.param("2", "3", 1, VersionBump.MAJOR, True, id="bare-major-versions"),
            pytest.param("1.0", "2.0.0.1", 1, VersionBump.MAJOR, False, id="fourth-component-is-not-a-clean-major"),
            pytest.param("1.0", "2.0rc1", 1, VersionBump.MAJOR, True, id="pre-release-of-next-major"),
            pytest.param("1.0", "2.0.post1", 1, VersionBump.MAJOR, True, id="post-release-of-next-major"),
        ],
    )
    @_requires_packaging
    def test_distance_between_versions(
        self, deprecated_in: str, remove_in: str, count: int, unit: VersionBump, expected: bool
    ) -> None:
        """The removal must be one clean bump of a single component, at least ``count`` steps in ``unit``.

        A wrapper deprecated in ``1.2`` and removed in ``2.0`` has a *smaller* minor number at removal even
        though callers got a whole major cycle, so a coarser bump clears the window; ``2.3`` mixes a major and
        a minor step and is never a release boundary a project promises removals on, so it fails whatever the
        window. Components below the bumped one restart at zero (``1.2.3`` → ``1.3.0``), and a pre- or
        post-release of a clean version is the same release line. PEP 440 allows more than three release
        components, so ``2.0.0.1`` is read as a follow-up release, not a clean major.
        """
        window = GraceWindow(count, unit)
        assert _satisfies_grace_window(_parse_version(deprecated_in), _parse_version(remove_in), window) is expected

    @pytest.mark.parametrize(
        ("deprecated_in", "remove_in"),
        [
            pytest.param("1.0", "1!1.0", id="forward-epoch-bump"),
            pytest.param("1!1.0", "2.0", id="backward-epoch-drop"),
        ],
    )
    @_requires_packaging
    def test_refuses_versions_from_different_epochs(self, deprecated_in: str, remove_in: str) -> None:
        """A pair of versions from different PEP 440 epochs is refused instead of being measured.

        Release numbers are only comparable inside one epoch -- ``2.0`` is *older* than ``1!1.0`` -- so no count
        of majors, minors, or patches describes the distance across an epoch change. A verdict either way would
        silently misjudge the window (a forward bump used to clear every window, in whichever direction), so the
        predicate raises and leaves the case to ``_grace_window_violation``, which intercepts it first and warns.
        """
        window = GraceWindow(1, VersionBump.MAJOR)
        with pytest.raises(ValueError, match="epoch"):
            _satisfies_grace_window(_parse_version(deprecated_in), _parse_version(remove_in), window)


class TestHasMigrationGuidance:
    """Detection of whether a wrapper tells callers what to migrate to."""

    @pytest.mark.parametrize(
        ("config", "expected"),
        [
            pytest.param(DeprecationConfig(target=str), True, id="callable-target"),
            pytest.param(DeprecationConfig(target=TargetMode.NOTIFY), False, id="warn-only"),
            pytest.param(DeprecationConfig(target=None), False, id="unset-target"),
            pytest.param(
                DeprecationConfig(target=TargetMode.ARGS_REMAP, args_mapping={"old": "new"}),
                True,
                id="args-mapping-names-replacement",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.ATTRS_REMAP, attrs_mapping={"old": "new"}),
                True,
                id="attrs-mapping-names-replacement",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.NOTIFY, message_template="use `new_api` instead"),
                True,
                id="custom-message-spells-it-out",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.NOTIFY, message_template=""),
                False,
                id="empty-message-template-selects-the-built-in-text",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.ARGS_REMAP, args_mapping={}),
                False,
                id="remap-mode-with-empty-mapping-renames-nothing",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.ATTRS_REMAP),
                False,
                id="attrs-remap-mode-without-mapping",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.NOTIFY, args_mapping={"old": "new"}),
                False,
                id="notify-ignores-its-args-mapping",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.NOTIFY, attrs_mapping={"old": "new"}),
                False,
                id="notify-ignores-its-attrs-mapping",
            ),
            pytest.param(
                DeprecationConfig(
                    target=TargetMode.NOTIFY,
                    args_mapping={"old": "new"},
                    message_template="use `new_api` instead",
                ),
                True,
                id="notify-with-a-message-guides-despite-the-ignored-mapping",
            ),
            pytest.param(
                DeprecationConfig(target=None, args_mapping={"old": "new"}),
                True,
                id="unset-target-still-applies-its-mapping",
            ),
        ],
    )
    def test_guidance_sources(self, config: DeprecationConfig, expected: bool) -> None:
        """Any of a target, a live mapping, or a custom message counts as telling callers where to go.

        The rule exists to catch the dead-end warning ("this is deprecated", full stop); a wrapper that renames
        arguments or carries a hand-written migration sentence is not a dead end even without a target. A mapping
        counts only where it survives to call time: an empty one renames nothing, and one paired with an explicit
        ``TargetMode.NOTIFY`` is discarded at decoration time, so both leave callers the same dead end.
        """
        info = DeprecationWrapperInfo(module="pkg", function="old_api", deprecated_info=config)
        assert _has_migration_guidance(info) is expected

    @pytest.mark.parametrize(
        ("config", "expected"),
        [
            pytest.param(
                DeprecationConfig(
                    target=TargetMode.NOTIFY, message_template=_MODULE_BUILT_IN_NOTICE, **_MODULE_IDENTITY
                ),
                False,
                id="built-in-notice-only",
            ),
            pytest.param(
                DeprecationConfig(
                    target=TargetMode.NOTIFY, message_template="use `pkg.new_mod` instead", **_MODULE_IDENTITY
                ),
                True,
                id="custom-template-spells-it-out",
            ),
            pytest.param(
                DeprecationConfig(
                    target=TargetMode.NOTIFY,
                    message_template=_MODULE_BUILT_IN_NOTICE,
                    attrs_mapping={"old_name": "new_name"},
                    **_MODULE_IDENTITY,
                ),
                True,
                id="attrs-mapping-without-target-is-applied",
            ),
            pytest.param(
                DeprecationConfig(
                    target=_MODULE_TARGET,
                    message_template=(
                        "The `pkg.old_mod` module was deprecated since v1.0 in favor of `pkg.new_mod`."
                        " It will be removed in v2.0."
                    ),
                    **_MODULE_IDENTITY,
                ),
                True,
                id="redirect-target",
            ),
        ],
    )
    def test_module_guidance_sources(self, config: DeprecationConfig, expected: bool) -> None:
        """A deprecated module is judged on its target, its attribute mapping, and a template of its own.

        ``deprecated_module()`` differs from the other factories in two ways the rule has to see through: it
        renders the warning up front and stores the result in ``message_template`` -- the built-in notice when
        the author passed none, their own text otherwise -- and it stores ``TargetMode.NOTIFY`` for "no
        replacement module" rather than leaving the target unset, while still applying ``attrs_mapping`` on
        every access. Reading the stored text as guidance would make the rule inert for every module; reading
        the sentinel as an explicit opt-out would discard a live mapping and flag a module whose author wrote a
        migration sentence by hand. Only the built-in notice with nothing else is the dead end.
        """
        info = DeprecationWrapperInfo(module="pkg.old_mod", deprecated_info=config, api_type="module")
        assert _has_migration_guidance(info) is expected


class TestMessageRequiredRemedy:
    """The ``message-required`` violation names only the knobs the wrapper's own factory accepts."""

    @pytest.mark.parametrize(
        ("api_type", "config", "named", "absent"),
        [
            pytest.param(
                "data",
                DeprecationConfig(target=None, name="old_cfg"),
                ["Instance `pkg.old_cfg`", "a custom `message_template`"],
                ["`target`", "`args_mapping`", "`attrs_mapping`"],
                id="instance-has-only-a-template",
            ),
            pytest.param(
                "module",
                DeprecationConfig(
                    target=TargetMode.NOTIFY, message_template=_MODULE_BUILT_IN_NOTICE, **_MODULE_IDENTITY
                ),
                ["Module `pkg.old_cfg`", "a `target`", "an `attrs_mapping`", "a custom `message_template`"],
                ["`args_mapping`"],
                id="module-has-no-args-mapping",
            ),
            pytest.param(
                "callable",
                DeprecationConfig(target=TargetMode.NOTIFY),
                [
                    "Callable `pkg.old_cfg`",
                    "a `target`",
                    "an `args_mapping`/`attrs_mapping`",
                    "a custom `message_template`",
                ],
                [],
                id="callable-lists-every-knob",
            ),
        ],
    )
    def test_remedy_matches_the_factory(
        self, api_type: str, config: DeprecationConfig, named: list[str], absent: list[str]
    ) -> None:
        """The remedy text lists the arguments the wrapper's factory actually takes, under the matching subject noun.

        A maintainer reads the violation and reaches for the first option it names. ``deprecated_instance()`` has
        no ``target`` or mapping argument at all, and ``deprecated_module()`` has no ``args_mapping``, so a remedy
        copied from the callable case sends them to a keyword that raises ``TypeError`` -- and calling an instance
        proxy a *Callable* points them at the wrong factory to begin with.
        """
        info = DeprecationWrapperInfo(module="pkg", function="old_cfg", deprecated_info=config, api_type=api_type)
        spec = _build_policy_spec(None, True)

        (violation,) = _check_policy_for_callables([info], spec)

        assert violation.startswith(f"[{PolicyRule.MESSAGE_REQUIRED.value}] ")
        assert all(fragment in violation for fragment in named)
        assert not any(fragment in violation for fragment in absent)


class TestValidateDeprecationPolicy:
    """End-to-end policy scan over the ``tests.collection_policy`` fixtures."""

    @pytest.mark.parametrize(
        ("wrapper_name", "rule"),
        [
            pytest.param("no_grace_window", PolicyRule.MIN_GRACE, id="min-grace-no-distance"),
            pytest.param("removed_at_patch", PolicyRule.MIN_GRACE, id="min-grace-off-boundary"),
            pytest.param("short_minor_runway", PolicyRule.MIN_GRACE, id="min-grace-insufficient-distance"),
            pytest.param("warns_without_replacement", PolicyRule.MESSAGE_REQUIRED, id="message-required"),
            pytest.param("WarnOnlyLegacyClass", PolicyRule.MESSAGE_REQUIRED, id="message-required-proxy"),
            pytest.param(
                "warns_without_template_instance", PolicyRule.MESSAGE_REQUIRED, id="message-required-instance"
            ),
        ],
    )
    @_requires_packaging
    def test_each_fixture_trips_its_rule(self, wrapper_name: str, rule: PolicyRule) -> None:
        """Every governance rule fires on the wrapper that breaks it, and reports it under its own slug.

        This is the reviewer-facing contract: a PR that schedules a removal in the same release, off a clean
        release boundary, or warns without naming a replacement, has to come back with a message naming *which*
        policy it broke. The sweep runs under the default policy, so each fixture is judged by the rule it was
        written for.
        """
        violations = validate_deprecation_policy("tests.collection_policy", recursive=False)
        matching = [v for v in violations if wrapper_name in v]
        assert len(matching) == 1
        assert matching[0].startswith(f"[{rule.value}]")

    @pytest.mark.parametrize(
        ("min_grace", "expected_window"),
        [
            pytest.param("0.1", "at least 1 minor release, landing on a clean major or minor boundary.", id="singular"),
            pytest.param("2.0", "at least 2 major releases, landing on a clean major boundary.", id="plural"),
        ],
    )
    @_requires_packaging
    def test_violation_message_echoes_configured_window(self, min_grace: str, expected_window: str) -> None:
        """The reported grace window reads back as prose naming the count, the unit, and the allowed boundaries.

        A maintainer who configured ``min_grace="2.0"`` reads the CI log to learn what the gate expected; a
        message echoing the bare ``2`` would leave them guessing which component it counts, so the message
        spells out ``2 major releases`` -- a one-unit window still reads as the singular -- and names the
        release levels a removal may land on, so a mixed-bump violation is explained by the same sentence.
        """
        violations = validate_deprecation_policy("tests.collection_policy", recursive=False, min_grace=min_grace)
        matching = [v for v in violations if "no_grace_window" in v]
        assert len(matching) == 1
        assert matching[0].endswith(expected_window)

    @pytest.mark.parametrize(
        "wrapper_name",
        [
            pytest.param("compliant_forward", id="target-forward"),
            pytest.param("args_mapping_only_guidance", id="args-mapping-only"),
            pytest.param("AttrsMappingOnlyGuidance", id="attrs-mapping-only"),
            pytest.param("message_template_only_guidance", id="message-template-only"),
            pytest.param("warns_with_template_instance", id="instance-with-template"),
        ],
    )
    @_requires_packaging
    def test_compliant_wrapper_is_not_reported(self, wrapper_name: str) -> None:
        """A wrapper that offers guidance through any single accepted channel trips no rule.

        A wrapper's guidance may come from a forwarding ``target`` (``compliant_forward``), a live
        ``args_mapping`` or ``attrs_mapping`` with no ``target`` at all (``args_mapping_only_guidance``,
        ``AttrsMappingOnlyGuidance``), a hand-written ``message_template`` with no ``target`` or mapping
        (``message_template_only_guidance``), or a ``deprecated_instance()`` template
        (``warns_with_template_instance``). The gate is only useful if every one of these disciplined
        cases passes silently — a policy that flags every wrapper is one a team turns off in its first
        week.
        """
        violations = validate_deprecation_policy("tests.collection_policy", recursive=False)
        assert not [v for v in violations if wrapper_name in v]

    @_requires_packaging
    def test_instance_remedy_names_only_the_message_template(self) -> None:
        """A bare ``deprecated_instance`` violation tells the reader to set ``message_template`` and nothing else.

        ``deprecated_instance()`` accepts no ``target`` or mapping, so a remedy listing those would send a
        maintainer after knobs that do not exist; the message must name the one knob the API actually has.
        """
        violations = validate_deprecation_policy("tests.collection_policy", recursive=False)

        (violation,) = [v for v in violations if "warns_without_template_instance" in v]

        assert "Instance" in violation
        assert "configure a custom `message_template`" in violation
        assert "args_mapping" not in violation
        assert "`target`" not in violation

    @_requires_packaging
    def test_module_with_custom_template_is_not_reported(self) -> None:
        """A ``deprecated_module()`` fixture with a custom ``message_template`` naming a replacement trips no rule.

        ``tests.collection_modules.old_math`` is deprecated in place with a ``message_template`` that names
        ``new_math`` as the replacement and no ``target``. Since ``_module_has_migration_guidance`` reads a
        template that differs from the built-in notice as guidance, ``validate_deprecation_policy()`` must
        report no ``message-required`` violation for the module itself.
        """
        violations = validate_deprecation_policy("tests.collection_modules.old_math", recursive=False)
        assert not [v for v in violations if PolicyRule.MESSAGE_REQUIRED.value in v]

    @_requires_packaging
    def test_module_with_only_the_built_in_notice_is_reported(self) -> None:
        """A ``deprecated_module()`` fixture with no ``target``, mapping, or custom template is flagged.

        ``tests.collection_modules.old_stats`` is deprecated in place with none of the three module-level
        guidance channels, so callers only ever see the built-in notice — the module-level dead end.
        ``validate_deprecation_policy()`` must flag it under ``message-required``, and the remedy must name
        only the arguments ``deprecated_module()`` accepts (a ``target``, an ``attrs_mapping``, or a custom
        ``message_template``) -- never ``args_mapping``, which that factory has no such keyword for.
        """
        violations = validate_deprecation_policy("tests.collection_modules.old_stats", recursive=False)
        (violation,) = [v for v in violations if PolicyRule.MESSAGE_REQUIRED.value in v]
        assert "a `target`" in violation
        assert "an `attrs_mapping`" in violation
        assert "a custom `message_template`" in violation
        assert "args_mapping" not in violation

    @_requires_packaging
    def test_all_rules_disabled_reports_nothing_for_a_maximally_violating_wrapper(self) -> None:
        """Disabling every governance rule silences a wrapper that would otherwise trip both at once.

        A wrapper deprecated at `9.0` and removed one patch later (`9.0.1`), warning without naming a
        replacement, breaks `min-grace` and `message-required` simultaneously — the worst case for the gate.
        If disabling both did not also disable the check for this wrapper, a project could never fully opt out
        of the policy gate on a single incorrigible case.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="everything_wrong",
            deprecated_info=DeprecationConfig(deprecated_in="9.0", remove_in="9.0.1", target=TargetMode.NOTIFY),
        )
        enabled_spec = _build_policy_spec("0.1", True)
        assert len(_check_policy_for_callables([info], enabled_spec)) == 2

        disabled_spec = _build_policy_spec(None, False)
        assert _check_policy_for_callables([info], disabled_spec) == []

    @_requires_packaging
    def test_disabled_rule_stops_reporting(self) -> None:
        """Setting a rule to ``None`` removes its violations without affecting the other rule.

        Projects with no fixed release cadence must be able to keep the guidance rule while dropping the
        grace-window rule, instead of abandoning the whole gate.
        """
        violations = validate_deprecation_policy("tests.collection_policy", recursive=False, min_grace=None)
        assert not [v for v in violations if PolicyRule.MIN_GRACE.value in v]
        assert [v for v in violations if PolicyRule.MESSAGE_REQUIRED.value in v]

    @_requires_packaging
    def test_missing_versions_are_not_violations(self) -> None:
        """A wrapper with no ``remove_in`` is skipped by the version-distance rules rather than flagged.

        Deprecating without scheduling a removal is a deliberate, common choice; treating it as a policy breach
        would flood the report with entries the team already decided about.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="warn_forever",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", target=str),
        )
        spec = _build_policy_spec("0.1", True)
        assert _check_policy_for_callables([info], spec) == []

    @_requires_packaging
    def test_unparsable_version_warns_and_skips_dependent_rules(self) -> None:
        """A typo'd ``remove_in`` warns once and skips only the rules that need it, instead of aborting the scan.

        One broken version string in a large package must not take the whole CI gate down, but it also must not
        vanish — the wrapper would otherwise stay permanently unlintable with no signal at all.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="broken_version",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="not.a.version!!", target=str),
        )
        spec = _build_policy_spec("0.1", True)
        with pytest.warns(UserWarning, match="unparsable `remove_in`"):
            assert _check_policy_for_callables([info], spec) == []

    @_requires_packaging
    def test_forward_epoch_change_warns_and_skips_the_grace_window(self) -> None:
        """A removal that crosses a PEP 440 epoch forward is reported as unmeasurable instead of quietly passing.

        An epoch bump is how a project restarts its numbering after changing versioning schemes, and release
        numbers either side of one are not comparable — ``2.0`` is *older* than ``1!1.0``. Letting the epoch
        clear the window silently would stamp a wrapper that gave callers no warning cycle at all as
        policy-clean, so the skip has to be visible to whoever reads the CI log.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="epoch_switch",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="1!1.0", target=str),
        )
        spec = _build_policy_spec("0.1", False)

        with pytest.warns(UserWarning, match="epoch"):
            violations = _check_policy_for_callables([info], spec)

        assert not [v for v in violations if PolicyRule.MIN_GRACE.value in v]

    @_requires_packaging
    def test_backward_epoch_change_reports_min_grace_violation(self) -> None:
        """A removal that sorts at or before its deprecation across an epoch drop is flagged, not skipped.

        ``remove_in="2.0"`` sorts *before* ``deprecated_in="1!1.0"`` once the epoch is taken into account, so
        no grace window at all was given. Treating this the same as an unmeasurable forward epoch bump would let
        a removal scheduled for the same release — or an earlier one — pass the ``min-grace`` gate silently.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="epoch_switch",
            deprecated_info=DeprecationConfig(deprecated_in="1!1.0", remove_in="2.0", target=str),
        )
        spec = _build_policy_spec("0.1", False)

        violations = _check_policy_for_callables([info], spec)

        assert [v for v in violations if PolicyRule.MIN_GRACE.value in v]

    @_requires_packaging
    def test_same_epoch_backward_removal_reports_min_grace_violation(self) -> None:
        """A backwards patch release fails even when its minor-component delta is zero.

        A repository can accidentally schedule removal in ``1.0.0`` after declaring the deprecation in
        ``1.0.1``. A zero-minor window must not mask that impossible schedule merely because both versions
        share the same minor component.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="backwards_patch",
            deprecated_info=DeprecationConfig(deprecated_in="1.0.1", remove_in="1.0.0", target=str),
        )
        spec = _build_policy_spec("0.0", False)

        violations = _check_policy_for_callables([info], spec)

        assert [v for v in violations if PolicyRule.MIN_GRACE.value in v]

    @_requires_packaging
    def test_same_version_removal_fails_a_zero_count_window(self) -> None:
        """Removing in the very release that deprecated is a violation even under a zero-count window.

        A zero-count window (``"0.0"``, ``{"minor": 0}``) switches the distance check off but still demands a
        clean bump of that unit or coarser, so ``deprecated_in="1.0", remove_in="1.0"`` gives callers no
        warning cycle at all and must keep failing ``min-grace`` — the contract reviewers repeatedly questioned.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="same_release",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="1.0", target=str),
        )
        spec = _build_policy_spec(GraceWindow(0, VersionBump.MINOR), False)

        violations = _check_policy_for_callables([info], spec)

        assert [v for v in violations if PolicyRule.MIN_GRACE.value in v]

    @_requires_packaging
    def test_mixed_batch_warns_once_and_still_evaluates_the_parsable_wrapper(self) -> None:
        """One unparsable version in a batch warns for that wrapper only; the parsable one is still linted.

        A large package with a single typo'd ``remove_in`` must not lose the verdicts of every other wrapper in
        the same scan, and the warning must name the broken wrapper so the typo can be found.
        """
        broken = DeprecationWrapperInfo(
            module="pkg",
            function="broken_version",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="not.a.version!!", target=str),
        )
        too_close = DeprecationWrapperInfo(
            module="pkg",
            function="too_close",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="1.1", target=str),
        )
        spec = _build_policy_spec("0.3", False)

        with pytest.warns(UserWarning, match="broken_version") as record:
            violations = _check_policy_for_callables([broken, too_close], spec)

        assert len(record) == 1
        assert [v for v in violations if PolicyRule.MIN_GRACE.value in v and "too_close" in v]
        assert not [v for v in violations if "broken_version" in v]


def _reject_version_parse(_version_string: str) -> NoReturn:
    """Stand in for ``_parse_version`` on an install that lacks the optional ``packaging`` library."""
    raise ImportError(
        "Version comparison requires the 'packaging' library. Install with: pip install pyDeprecate[audit]"
    )


class TestPolicyVersionParsingIsLazy:
    """A version string is only parsed when a switched-on rule actually reads it."""

    def test_guidance_only_policy_needs_no_version_machinery(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A ``message_required``-only policy reports its violations without the ``packaging`` library.

        A team installs the package without the ``[audit]`` extra and gates CI on one promise — every
        deprecation names a replacement. No version comparison is enabled, so demanding ``packaging`` there
        would turn a check that needs no version arithmetic into an install error.
        """
        monkeypatch.setattr("deprecate.audit._policy._parse_version", _reject_version_parse)
        info = DeprecationWrapperInfo(
            module="pkg",
            function="warns_without_replacement",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="2.0", target=TargetMode.NOTIFY),
        )
        spec = _build_policy_spec(None, True)

        violations = _check_policy_for_callables([info], spec)

        assert [v for v in violations if PolicyRule.MESSAGE_REQUIRED.value in v]

    def test_version_rule_still_reports_missing_packaging(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With the grace-window rule switched on, a missing ``packaging`` still surfaces the install hint.

        The same CI job turns the grace window back on. That rule cannot be evaluated without version
        arithmetic, so skipping it silently would report a green gate that checked nothing — the ImportError,
        which the CLI renders as an install hint, is the honest answer.
        """
        monkeypatch.setattr("deprecate.audit._policy._parse_version", _reject_version_parse)
        info = DeprecationWrapperInfo(
            module="pkg",
            function="no_grace_window",
            deprecated_info=DeprecationConfig(deprecated_in="2.0", remove_in="2.0", target=str),
        )
        spec = _build_policy_spec("0.1", False)

        with pytest.raises(ImportError, match="packaging"):
            _check_policy_for_callables([info], spec)

    def test_genuinely_missing_packaging_import_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A real install without ``packaging`` fails the same way the hand-constructed-``ImportError`` tests assume.

        The other tests in this class fake the failure with ``monkeypatch.setattr(_parse_version, ...)``, which
        proves the *caller* handles an ``ImportError`` correctly but never exercises ``_parse_version``'s own
        ``except ImportError`` branch. Blocking both ``packaging`` and the already-imported ``packaging.version``
        submodule in ``sys.modules`` (the parent alone is insufficient once the submodule is cached from an
        earlier test) forces `from packaging.version import ...` to genuinely fail, so this test exercises the
        real import-failure path instead of a stand-in for it.
        """
        monkeypatch.setitem(sys.modules, "packaging", None)
        monkeypatch.setitem(sys.modules, "packaging.version", None)
        info = DeprecationWrapperInfo(
            module="pkg",
            function="no_grace_window",
            deprecated_info=DeprecationConfig(deprecated_in="2.0", remove_in="2.0", target=str),
        )
        spec = _build_policy_spec("0.1", False)

        with pytest.raises(ImportError, match="packaging"):
            _check_policy_for_callables([info], spec)
