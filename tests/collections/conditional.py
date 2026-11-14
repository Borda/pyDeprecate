"""Conditional deprecations whose ``skip_if`` raises or counts its calls, for audits that must never run it.

An audit scan inspects wrappers without using them, so it must not evaluate a ``skip_if`` condition: the condition can
depend on runtime state, such as a live application context, that a CI scan does not have.
"""

from deprecate import deprecated_instance

#: One entry per evaluation of :func:`counting_skip`; a test clears it and reads it back.
SKIP_CALLS: list[str] = []

#: Whether the application context :func:`raising_skip` needs is available. A test switches it off to stand for a CI
#: scan; it stays on otherwise, so importing or collecting this module never trips the condition.
APP_CONTEXT: dict[str, bool] = {"available": True}


def raising_skip() -> bool:
    """Decide the skip from a live application context, raising when none is available, as in a CI audit scan."""
    if not APP_CONTEXT["available"]:
        raise RuntimeError("skip_if needs a live application context")
    return False


def counting_skip() -> bool:
    """Record each evaluation and keep the deprecation live."""
    SKIP_CALLS.append("evaluated")
    return False


class AppSettings:
    """Application settings whose legacy module-level instance is replaced by a new one."""

    level = 1


#: The replacement settings object both legacy aliases forward to.
NEW_SETTINGS = AppSettings()

#: Legacy settings served silently inside the running app; deciding that needs the app context.
app_settings = deprecated_instance(
    AppSettings(),
    target=NEW_SETTINGS,
    deprecated_in="0.1",
    remove_in="0.9",
    skip_if=raising_skip,
    name="app_settings",
)

#: Legacy settings whose condition counts how often anything asks whether the deprecation is live.
counted_settings = deprecated_instance(
    AppSettings(),
    target=NEW_SETTINGS,
    deprecated_in="0.1",
    remove_in="0.9",
    skip_if=counting_skip,
    name="counted_settings",
)
