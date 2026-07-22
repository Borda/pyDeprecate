"""Deprecation warning templates and emission helpers.

Shared warning machinery used by both :mod:`deprecate.routine` (function/method deprecation) and :mod:`deprecate.proxy`
(class/instance proxies): the built-in ``%``-style message templates, the decoration-time ``message_template``
validator, and the call-time warning emitters that honour the per-wrapper warn budget.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import re
from collections.abc import Mapping
from functools import partial
from typing import Callable, Optional, Union
from warnings import warn

from deprecate._types import TargetMode, _WrapperState
from deprecate.utils import _unwrap_descriptor_target

# caller → wrapped_fn → _raise_warn_callable/_raise_warn_arguments → _raise_warn → warnings.warn
_DEFAULT_STACKLEVEL_TO_CALLER: int = 4


#: Default template warning message for redirecting callable
TEMPLATE_WARNING_CALLABLE = (
    "The `%(source_name)s` was deprecated since v%(deprecated_in)s in favor of `%(target_path)s`."
    " It will be removed in v%(remove_in)s."
)
#: Default template warning message for changing argument mapping
TEMPLATE_WARNING_ARGUMENTS = (
    "The `%(source_name)s` uses deprecated arguments: %(argument_map)s."
    " They were deprecated since v%(deprecated_in)s and will be removed in v%(remove_in)s."
)
#: Template for mapping from old to new examples
TEMPLATE_ARGUMENT_MAPPING = "`%(old_arg)s` -> `%(new_arg)s`"
#: Default template warning message for no target func/method
TEMPLATE_WARNING_NO_TARGET = (
    "The `%(source_name)s` was deprecated since v%(deprecated_in)s. It will be removed in v%(remove_in)s."
)


deprecation_warning = partial(warn, category=FutureWarning)


#: All ``%``-style placeholders accepted by the built-in warning templates.  Probing a user-supplied
#: ``message_template`` against this mapping at decoration time surfaces typos (``%(wrong_name_or_typo)s``) and
#: malformed conversion specifiers (``%(source_name)d``) before any call site ever triggers them.
_MESSAGE_TEMPLATE_PROBE_ARGS: dict[str, str] = {
    "source_name": "x",
    "source_path": "x.y",
    "deprecated_in": "0.0",
    "remove_in": "1.0",
    "target_name": "x",
    "target_path": "x.y",
    "argument_map": "x -> y",
}


def _resolve_message_template_alias(
    message_template: Optional[str],
    template_mgs: Optional[str],
    *,
    stacklevel: int = 3,
) -> Optional[str]:
    """Fold the deprecated ``template_mgs`` keyword into ``message_template``.

    ``template_mgs`` was a typo (``mgs`` for ``msg``); it was renamed to ``message_template`` in ``v0.12``.
    The old name is accepted as a deprecated alias until ``v1.0``: supplying it emits a
    :class:`FutureWarning` and its value is used as ``message_template``.  Supplying both raises
    :class:`TypeError` — there is no sensible merge of two message templates.

    Args:
        message_template: The value of the canonical ``message_template`` argument (may be ``None``).
        template_mgs: The value of the deprecated ``template_mgs`` alias (``None`` when not supplied).
        stacklevel: Stack level forwarded to :func:`warnings.warn` so the notice points at the caller.

    Returns:
        The resolved ``message_template`` value.

    Raises:
        TypeError: If both ``message_template`` and ``template_mgs`` are supplied.

    """
    if template_mgs is None:
        return message_template
    if message_template is not None:
        raise TypeError(
            "Both `message_template` and `template_mgs` were supplied; pass only one (`template_mgs` deprecated)."
        )
    warn(
        "`template_mgs` is deprecated since `v0.12` (renamed to `message_template`);"
        " use `message_template` instead. Will be removed in `v1.0`.",
        FutureWarning,
        stacklevel=stacklevel,
    )
    return template_mgs


def _validate_message_template(message_template: Optional[str]) -> None:
    """Probe ``message_template`` with every documented placeholder, raising at decoration time on failure.

    Args:
        message_template: User-supplied warning message template, or ``None``.  ``None`` and empty strings are
            no-ops because the call sites already fall back to the built-in templates.

    Raises:
        ValueError: When ``message_template`` references an unknown ``%(...)s`` key, uses a malformed conversion
            specifier, or otherwise fails ``%``-formatting against the full placeholder set.

    """
    if not message_template:
        return
    # Reject bare ``%``-conversions (``%s``, ``%d``, a trailing ``%``) that are not part of a
    # ``%(name)s`` mapping key.  ``"...%s..." % {mapping}`` does not raise — it renders the whole
    # mapping dict into the message — so the probe below cannot catch them.  ``%%`` (escaped percent)
    # is legitimate and stripped before the search.
    if re.search(r"%(?!\()", message_template.replace("%%", "")):
        raise ValueError(
            f"Invalid message_template: bare `%`-conversion found in {message_template!r}; only mapping keys of the "
            f"form `%(name)s` are supported. Available placeholders: {list(_MESSAGE_TEMPLATE_PROBE_ARGS)}"
        )
    try:
        message_template % _MESSAGE_TEMPLATE_PROBE_ARGS
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid message_template: {exc!r}. Available placeholders: {list(_MESSAGE_TEMPLATE_PROBE_ARGS)}"
        ) from exc


def _raise_warn(
    stream: Callable,
    source: Callable,
    message_template: str,
    stacklevel: int = _DEFAULT_STACKLEVEL_TO_CALLER,
    **extras: str,
) -> None:
    """Issue a deprecation warning using the specified stream and message template.

    This is the core warning issuer that formats and emits deprecation warnings.  It extracts source function metadata
    and combines it with provided template variables to generate the final warning message.

    Args:
        stream: Callable that outputs the warning (e.g., warnings.warn, logging.warning).
        source: The deprecated function/method being wrapped.
        message_template: Python format string with placeholders for message variables.
        stacklevel: Passed to ``warnings.warn`` so the warning points to the user's call site.  Default 4 accounts for
            the ``_raise_warn → _raise_warn_callable/_raise_warn_arguments → wrapped_fn → caller`` chain.
        **extras: Additional string values to substitute into the template (e.g., deprecated_in="1.0", remove_in="2.0").

    Note:
        Automatically extracts source_name and source_path from the source callable:
        - For regular functions: uses ``__name__``
        - For ``__init__`` methods: extracts class name from ``__qualname__``

    Example:
        >>> import warnings
        >>> def old_func(): pass
        >>> _raise_warn(
        ...     warnings.warn,
        ...     old_func,
        ...     "%(source_name)s deprecated in %(version)s",
        ...     version="1.0"
        ... )

    """
    source_name = _source_display_name(source)
    source_path = f"{source.__module__}.{source_name}"
    msg_args = dict(source_name=source_name, source_path=source_path, **extras)
    msg = message_template % msg_args
    try:
        stream(msg, stacklevel=stacklevel)
    except TypeError as _exc:
        if "stacklevel" in str(_exc) or "keyword" in str(_exc):
            stream(msg)
        else:
            raise


def _source_display_name(source: Callable) -> str:
    """Return display name: class name for ``__init__``, function name otherwise."""
    return source.__qualname__.split(".")[-2] if source.__name__ == "__init__" else source.__name__


def _raise_warn_callable(
    stream: Callable,
    source: Callable,
    target: Union[None, bool, Callable, TargetMode, staticmethod, classmethod],
    deprecated_in: str,
    remove_in: str,
    message_template: Optional[str] = None,
    stacklevel: int = _DEFAULT_STACKLEVEL_TO_CALLER,
) -> None:
    """Issue deprecation warning for callable (function/class) deprecation.

    This specialized warning issuer handles deprecation of entire functions or classes that are being replaced by new
    implementations.  It automatically determines the appropriate message template based on whether a target callable
    is specified.

    Args:
        stream: Callable that outputs the warning (e.g., warnings.warn, logging.warning).
        source: The deprecated function/method being wrapped.
        target: The replacement implementation:
            - Callable: Forward to this function/class
            - None: No forwarding (warning only mode)
            - bool: Not applicable for this function (use _raise_warn_arguments instead)
        deprecated_in: Version when the source was marked deprecated (e.g., "1.0.0").
        remove_in: Version when the source will be removed (e.g., "2.0.0").
        message_template: Custom message template. If None, uses :data:`TEMPLATE_WARNING_CALLABLE` when a target
            callable is provided, otherwise :data:`TEMPLATE_WARNING_NO_TARGET`.
        stacklevel: Passed through to :func:`_raise_warn`; default 4 points to the user's call site.

    Template Variables Available:
        - source_name: Function name (e.g., "old_func")
        - source_path: Full path (e.g., "mymodule.old_func")
        - target_name: Target function name (only if target is callable)
        - target_path: Full target path (only if target is callable)
        - deprecated_in: Version parameter value
        - remove_in: Version parameter value

    Example:
        >>> import warnings
        >>> def new_func(): pass
        >>> def old_func(): pass
        >>> _raise_warn_callable(
        ...     stream=warnings.warn,
        ...     source=old_func,
        ...     target=new_func,
        ...     deprecated_in="1.0",
        ...     remove_in="2.0"
        ... )
        >>> # Outputs: "The `old_func` was deprecated since v1.0 in favor of
        >>> #           `__main__.new_func`. It will be removed in v2.0."

    """
    # Unwrap descriptor: _build_call_plan passes the raw (pre-normalization) target so
    # the warning can name the class rather than __init__.  For descriptor targets,
    # callable(staticmethod(fn)) is False on Python 3.9 and callable(classmethod(fn))
    # is always False, so without this unwrap the no-target template fires incorrectly.
    target = _unwrap_descriptor_target(target)
    if callable(target):
        target_name = target.__name__
        target_path = f"{target.__module__}.{target_name}"
        template_warn = TEMPLATE_WARNING_CALLABLE
    else:
        target_name, target_path = "", ""
        template_warn = TEMPLATE_WARNING_NO_TARGET
    _raise_warn(
        stream=stream,
        source=source,
        message_template=message_template or template_warn,
        stacklevel=stacklevel,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        target_name=target_name,
        target_path=target_path,
    )


def _raise_warn_arguments(
    stream: Callable,
    source: Callable,
    arguments: Mapping[str, Optional[str]],
    deprecated_in: str,
    remove_in: str,
    message_template: Optional[str] = None,
    stacklevel: int = _DEFAULT_STACKLEVEL_TO_CALLER,
) -> None:
    """Issue deprecation warning for deprecated function arguments.

    This specialized warning issuer handles deprecation of specific function parameters that are being renamed or
    removed.  It generates a mapping string showing the old-to-new argument names.

    Args:
        stream: Callable that outputs the warning (e.g., warnings.warn, logging.warning).
        source: The function/method whose arguments are deprecated.
        arguments: Mapping from deprecated argument names to new names (e.g., ``{'old_arg': 'new_arg',
            'removed_arg': None}``).
        deprecated_in: Version when arguments were marked deprecated (e.g., "1.0.0").
        remove_in: Version when arguments will be removed (e.g., "2.0.0").
        message_template: Custom message template. If None, uses default template.
        stacklevel: Passed through to :func:`_raise_warn`; default 4 points to the user's call site.

    Template Variables Available:
        - source_name: Function name (e.g., "my_func")
        - source_path: Full path (e.g., "mymodule.my_func")
        - argument_map: Formatted string showing mappings (e.g., "`old` -> `new`")
        - deprecated_in: Version parameter value
        - remove_in: Version parameter value

    Example:
        >>> import warnings
        >>> def my_func(old_arg=1, new_arg=1): pass
        >>> _raise_warn_arguments(
        ...     warnings.warn,
        ...     my_func,
        ...     {'old_arg': 'new_arg'},
        ...     "1.0",
        ...     "2.0"
        ... )
        >>> # Outputs: "The `my_func` uses deprecated arguments: `old_arg` -> `new_arg`.
        >>> #           They were deprecated since v1.0 and will be removed in v2.0."

    """
    args_map = ", ".join(TEMPLATE_ARGUMENT_MAPPING % {"old_arg": a, "new_arg": str(b)} for a, b in arguments.items())
    _raise_warn(
        stream,
        source,
        message_template or TEMPLATE_WARNING_ARGUMENTS,
        stacklevel=stacklevel,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        argument_map=args_map,
    )


def _consume_warn_budget(
    state: _WrapperState,
    num_warns: int,
    reason_callable: bool,
    reason_argument: dict[str, Optional[str]],
) -> bool:
    """Check the warn budget and consume one unit from it when a warning may fire.

    Must be called while holding ``state.lock`` — the read-check-increment sequence is exactly
    what the lock protects (see the thread-safety note at the call site).

    Args:
        state: Mutable per-wrapper counters.
        num_warns: Configured budget; negative means unlimited.
        reason_callable: Warning is for the deprecated callable itself — consumes ``warned_calls``.
        reason_argument: Deprecated argument names present in this call — consumes per-argument
            budgets in ``warned_args``; takes precedence over the call counter for the check.

    Returns:
        True when the caller should emit the warning (budget available and now consumed).

    """
    # The budget must track the warning variant that will be emitted:
    # - callable-level warnings consume warned_calls
    # - argument-level warnings consume warned_args
    if reason_callable:
        nb_warned = state.warned_calls
    elif reason_argument:
        nb_warned = min((state.warned_args.get(arg, 0) for arg in reason_argument), default=0)
    else:
        nb_warned = state.warned_calls
    if num_warns >= 0 and nb_warned >= num_warns:
        return False
    if reason_callable:
        state.warned_calls += 1
    elif reason_argument:
        for arg in reason_argument:
            if num_warns < 0 or state.warned_args.get(arg, 0) < num_warns:
                state.warned_args[arg] = state.warned_args.get(arg, 0) + 1
    return True
