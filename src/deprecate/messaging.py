"""Deprecation warning templates and emission helpers.

Shared warning machinery used by both :mod:`deprecate.routine` (function/method deprecation) and :mod:`deprecate.proxy`
(class/instance proxies): the built-in ``%``-style message templates, the decoration-time ``message_template``
validator, and the call-time warning emitters that honour the per-wrapper warn budget.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import inspect
import re
import weakref
from collections.abc import Mapping
from functools import partial
from typing import Any, Callable, Optional, Union
from warnings import warn

from deprecate._fatal import DeprecatedError
from deprecate._frames import _caller_stacklevel
from deprecate._types import _MAJOR_BREAK_VERSION, DeprecationConfig, TargetMode, _WrapperState
from deprecate.utils import _partial_chain, _unwrap_descriptor_target

#: Memoised ``stacklevel`` decisions of :func:`_stream_accepts_stacklevel`: one entry per live ``stream`` object,
#: keyed by ``id(stream)``.  Each entry pairs the decision with a *weak* reference to its stream.  A hit counts only
#: while that reference still resolves to the very same object, so an id recycled after garbage collection, or a
#: second stream that merely compares ``==`` (and hashes) equal, can never borrow another stream's answer, and the
#: memo never keeps a stream alive.
_STREAM_DECISIONS: dict[int, tuple["weakref.ReferenceType[Any]", bool]] = {}


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
) -> Optional[str]:
    """Fold the deprecated ``template_mgs`` keyword into ``message_template``.

    ``template_mgs`` was a typo (``mgs`` for ``msg``); it was renamed to ``message_template`` in ``v0.12``.
    The old name is accepted as a deprecated alias until ``v1.0``: supplying it emits a
    :class:`FutureWarning` and its value is used as ``message_template``.  Supplying both raises
    :class:`TypeError` — there is no sensible merge of two message templates.

    Args:
        message_template: The value of the canonical ``message_template`` argument (may be ``None``).
        template_mgs: The value of the deprecated ``template_mgs`` alias (``None`` when not supplied).

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
        f" use `message_template` instead. Will be removed in `{_MAJOR_BREAK_VERSION}`.",
        FutureWarning,
        stacklevel=_caller_stacklevel(),
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
    stream: Optional[Callable],
    source: Callable,
    message_template: str,
    as_exception: bool = False,
    **extras: str,
) -> None:
    """Issue a deprecation warning using the specified stream and message template.

    This is the core warning issuer that formats and emits deprecation warnings.  It extracts source function metadata
    and combines it with provided template variables to generate the final warning message.

    Args:
        stream: Callable that outputs the warning (e.g., warnings.warn, logging.warning).  May be ``None`` only
            together with ``as_exception=True``, where the message is raised and never written to a stream.
        source: The deprecated function/method being wrapped.
        message_template: Python format string with placeholders for message variables.
        as_exception: When ``True``, raise :class:`~deprecate._fatal.DeprecatedError` carrying the rendered
            message instead of writing it to ``stream`` — the fatal-deprecation path (``as_exception=True`` or
            ``deprecate.AS_EXCEPTIONS``).  The text is identical either way, so an author maintains one message.
        **extras: Additional string values to substitute into the template (e.g., deprecated_in="1.0", remove_in="2.0").

    Raises:
        DeprecatedError: When ``as_exception`` is ``True``.

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
    msg = _format_deprecation_message(message_template, source_name, source_path, **extras)
    if as_exception:
        # Deliberately ahead of the stream call: a fatal deprecation is a closed door, so the message is
        # raised rather than written anywhere — which is also why ``stream=None`` cannot switch the gate off.
        raise DeprecatedError(msg)
    # ``stream`` is Optional only to let the fatal path above accept a silenced wrapper; every caller gates on
    # a truthy stream once ``as_exception`` is False.
    assert stream is not None  # noqa: S101
    _emit(stream, msg)


def _inspect_stream_accepts_stacklevel(stream: Callable[..., Any]) -> bool:
    """Decide from its signature whether ``stream`` can be called as ``stream(msg, stacklevel=...)``.

    Uncached core of :func:`_stream_accepts_stacklevel`; see there for the decision rules.

    """
    try:
        # Look through ``functools.wraps``-style pass-throughs: a layer whose own signature is a bare
        # ``(*args, **kwargs)`` says nothing about what it forwards, so the callable it wraps decides. Every layer that
        # declares parameters of its own stops the walk, because that signature is what actually gets called — a
        # ``functools.wraps(logging.warning)`` stream written as ``(msg)`` stays message-only.
        stream = inspect.unwrap(stream, stop=_declares_own_parameters)
        layers = _partial_chain(stream)
        if layers:
            # ``inspect.unwrap`` stops at a partial, whose signature is derived from what it binds. When that is a
            # bare pass-through, the partial's signature is the pass-through's ``(*args, **kwargs)`` and says nothing,
            # so judge the same partial layers rebuilt over the callable the pass-through forwards to.
            core = inspect.unwrap(layers[-1].func, stop=_declares_own_parameters)
            if core is not layers[-1].func:
                for layer in reversed(layers):
                    core = partial(core, *layer.args, **layer.keywords)
                return _inspect_stream_accepts_stacklevel(core)
        # Identity, not introspection: ``warnings.warn`` is a C builtin whose signature support varies across Python
        # versions, and it always accepts ``stacklevel``.
        if (layers[-1].func if layers else stream) is warn:
            return True
        signature = inspect.signature(stream, follow_wrapped=False)
    except Exception:
        # User-provided metadata descriptors can raise arbitrary exceptions. Only introspection is defensive:
        # call with the message alone rather than guess, and let any callback error propagate without retry.
        return False
    return _binds_message_and_stacklevel(signature)


def _declares_own_parameters(layer: Callable[..., Any]) -> bool:
    """Stop :func:`inspect.unwrap` at ``layer`` unless its own signature is just ``(*args, **kwargs)``.

    ``follow_wrapped=False`` keeps the lookup on the layer itself rather than on the callable it copied metadata from.

    """
    kinds = [param.kind for param in inspect.signature(layer, follow_wrapped=False).parameters.values()]
    return kinds != [inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD]


def _binds_message_and_stacklevel(signature: inspect.Signature) -> bool:
    """Tell whether ``signature`` accepts the exact call ``_emit`` makes: one positional message plus ``stacklevel=``.

    Binding mirrors the real call, so the positional message occupies the first slot before ``stacklevel`` is
    considered: ``def sink(stacklevel)`` is rejected (``stacklevel`` is already taken by the message), while
    ``def sink(*args, stacklevel=None)`` and ``def sink(msg, **kwargs)`` are accepted.

    """
    try:
        signature.bind("", stacklevel=0)
    except TypeError:
        return False
    return True


def _decision_evictor(key: int) -> Callable[["weakref.ReferenceType[Any]"], None]:
    """Build the weak-reference callback that drops the memo entry of ``key`` once its stream is garbage-collected."""
    # Bound now rather than looked up on ``_STREAM_DECISIONS`` later: the callback can run during interpreter shutdown,
    # when module globals may already be cleared.
    pop = _STREAM_DECISIONS.pop

    def evict(_ref: "weakref.ReferenceType[Any]") -> None:
        pop(key, None)

    return evict


def _stream_accepts_stacklevel(stream: Callable[..., Any]) -> bool:
    """Tell whether ``stream`` can receive ``stacklevel=`` as a keyword argument, deciding once per stream object.

    Decision rules, in order:

    1. A stream whose own signature is a bare ``(*args, **kwargs)`` and that carries ``__wrapped__`` (the shape a
       :func:`functools.wraps` pass-through decorator produces) is replaced by the callable it wraps, layer by layer,
       until a layer declares parameters of its own; the rules below then apply to that callable. A narrow stream that
       merely copied ``logging.warning``'s metadata with ``functools.wraps`` declares its own parameters, so it is
       judged as written.
    2. ``warnings.warn`` itself, or a (nested) :func:`functools.partial` of it, accepts ``stacklevel``.
    3. Otherwise, when :func:`inspect.signature` describes ``stream``, it accepts ``stacklevel`` if that signature
       binds the call ``stream(msg, stacklevel=...)``: the positional message fills the first slot, then ``stacklevel``
       must bind by keyword (a keyword-capable ``stacklevel`` parameter other than the one the message lands in, or
       ``**kwargs``). ``def sink(stacklevel)`` therefore receives the message alone. A stream that declares
       ``**kwargs`` is thereby promised ``stacklevel=`` and must accept or drop it, not hand it to a callable that
       rejects it.
    4. When :func:`inspect.signature` (or the ``__wrapped__`` lookup) raises an :class:`Exception` (missing or
       failing signature metadata), the answer is ``False``: the stream is called with the message alone, which costs
       caller attribution for a stream that would have accepted ``stacklevel``. Exceptions from calling the stream
       still propagate.

    The answer is memoised per live stream object, by identity and through a weak reference: ``==``-equal streams with
    different signatures each keep their own answer (an unhashable stream included), and the memo never keeps a stream
    alive. A stream that cannot be weakly referenced, such as an instance of a ``__slots__`` class without
    ``__weakref__``, is inspected afresh on every call.

    Args:
        stream: The ``stream=`` callable configured on a deprecation wrapper.

    Returns:
        ``True`` when ``stream(msg, stacklevel=...)`` is the right call, ``False`` for ``stream(msg)``.

    Example:
        >>> import logging, warnings
        >>> _stream_accepts_stacklevel(warnings.warn), _stream_accepts_stacklevel(logging.warning)
        (True, True)
        >>> def message_only(msg): pass
        >>> _stream_accepts_stacklevel(message_only)
        False
        >>> import functools
        >>> @functools.wraps(message_only)
        ... def pass_through(*args, **kwargs): return message_only(*args, **kwargs)
        >>> _stream_accepts_stacklevel(pass_through)
        False

    """
    key = id(stream)
    entry = _STREAM_DECISIONS.get(key)
    if entry is not None and entry[0]() is stream:
        return entry[1]
    decision = _inspect_stream_accepts_stacklevel(stream)
    try:
        ref = weakref.ref(stream, _decision_evictor(key))
    except TypeError:
        # No weak reference possible, so nothing safe to anchor a memo entry to: decide afresh on the next emission.
        return decision
    # Plain dict reads and writes are atomic under the GIL. Two threads racing past the lookup both compute the same
    # deterministic decision and the last write wins, so no lock is needed.
    _STREAM_DECISIONS[key] = (ref, decision)
    return decision


def _emit(stream: Callable[..., Any], msg: str) -> None:
    """Write ``msg`` to ``stream`` exactly once, passing ``stacklevel`` only when the stream accepts it.

    The single delivery point for every custom-stream deprecation message.  Whether ``stacklevel`` is passed is
    decided before the call (:func:`_stream_accepts_stacklevel`), never by retrying after a failure, so the stream
    runs once and any exception it raises — :class:`TypeError` included — propagates unchanged.

    The ``stacklevel`` value is measured here by :func:`~deprecate._frames._caller_stacklevel`, relative to this
    frame, so it lands on the first frame outside the ``deprecate`` package for :func:`warnings.warn` or a C-level
    :func:`functools.partial` of it.  A pure-Python stream that forwards ``stacklevel`` to ``warnings.warn`` adds
    its own frame and lands one frame short.

    Contract for a stream declaring ``**kwargs`` (``def sink(msg, **kwargs)``): it receives ``stacklevel=`` and must
    forward it to a callable that accepts it (``warnings.warn``, ``logging.warning``) or drop it. Forwarding all
    keywords to a callable that rejects ``stacklevel`` (``print(msg, **kwargs)``) raises :class:`TypeError`; declare
    the stream as ``def sink(msg)`` instead and it is called with the message alone.

    Args:
        stream: The ``stream=`` callable configured on a deprecation wrapper.
        msg: The fully rendered deprecation message.

    Example:
        >>> import warnings
        >>> with warnings.catch_warnings(record=True) as caught:
        ...     warnings.simplefilter("always")
        ...     _emit(warnings.warn, "old_func is deprecated")
        >>> str(caught[0].message)
        'old_func is deprecated'

    """
    if _stream_accepts_stacklevel(stream):
        stream(msg, stacklevel=_caller_stacklevel())
    else:
        stream(msg)


def _format_deprecation_message(
    message_template: str,
    source_name: str,
    source_path: str,
    **extras: str,
) -> str:
    """Render *message_template* against ``source_name``/``source_path`` plus scenario-specific ``extras``.

    Shared by the call-time emitters (:func:`_raise_warn`) and the decoration-time static renderer
    (:func:`_render_static_deprecation_message`) so both paths format messages identically.

    """
    # Templates are validated against the complete public placeholder vocabulary independent of target mode.
    # Inapplicable values therefore render as empty strings instead of failing after validation.
    msg_args = dict.fromkeys(_MESSAGE_TEMPLATE_PROBE_ARGS, "")
    msg_args.update(source_name=source_name, source_path=source_path, **extras)
    return message_template % msg_args


def _render_static_deprecation_message(dep: DeprecationConfig, source_name: str, source_path: str) -> str:
    """Render the PEP-702-conformant ``__deprecated__`` message string for *dep* at decoration time.

    Selects the same built-in template :func:`_raise_warn_callable` / :func:`_raise_warn_arguments` would use
    for the first call, from ``dep`` alone (no live call-site information is available at decoration time).  An
    ``ARGS_REMAP`` config renders the **full** ``args_mapping`` (every configured rename), not the subset a
    single call would trigger — this is a static snapshot, not a per-call message.  ``message_template`` was
    already validated against every placeholder at decoration time (:func:`_validate_message_template`), so
    formatting here never raises.

    Args:
        dep: The frozen deprecation metadata to render.
        source_name: Display name of the deprecated source (see :func:`_source_display_name`).
        source_path: Fully-qualified path of the deprecated source (``f"{module}.{source_name}"``).

    Returns:
        The rendered message string, suitable for the wrapper's ``__deprecated__`` attribute.

    """
    if dep.target is TargetMode.ARGS_REMAP:
        argument_map = ", ".join(
            TEMPLATE_ARGUMENT_MAPPING % {"old_arg": old, "new_arg": str(new)}
            for old, new in (dep.args_mapping or {}).items()
        )
        return _format_deprecation_message(
            dep.message_template or TEMPLATE_WARNING_ARGUMENTS,
            source_name,
            source_path,
            deprecated_in=dep.deprecated_in,
            remove_in=dep.remove_in,
            argument_map=argument_map,
        )
    if callable(dep.target):
        target_name, target_path = _callable_display_name(dep.target)
        return _format_deprecation_message(
            dep.message_template or TEMPLATE_WARNING_CALLABLE,
            source_name,
            source_path,
            deprecated_in=dep.deprecated_in,
            remove_in=dep.remove_in,
            target_name=target_name,
            target_path=target_path,
        )
    # NOTIFY, ATTRS_REMAP, or no target: warn-only shape, no redirect to name.
    return _format_deprecation_message(
        dep.message_template or TEMPLATE_WARNING_NO_TARGET,
        source_name,
        source_path,
        deprecated_in=dep.deprecated_in,
        remove_in=dep.remove_in,
    )


def _callable_display_name(obj: Any) -> tuple[str, str]:  # noqa: ANN401
    """Return the ``(name, dotted_path)`` pair a warning shows for *obj*, for any callable or object, never raising.

    A ``target`` can be any callable — a ``functools.partial`` or a callable instance has no ``__name__`` of its own,
    and an arbitrary object may expose a non-string ``__name__``. Resolution, in order:

    1. Unwrap nested ``functools.partial`` layers to the innermost ``.func`` (see :func:`_partial_chain`).
    2. A base with a string ``__name__`` is named by it, with its own ``__module__``.
    3. Anything else (callable instance, plain object) is named by its type and the type's module.
    4. The path is ``f"{module}.{name}"`` when the module is a non-empty string, else just the name — so a
       function or class renders exactly as ``f"{target.__module__}.{target.__name__}"`` always did.

    Args:
        obj: The object to name; usually the ``target`` given to ``@deprecated`` or ``deprecated_class``.

    Returns:
        Tuple of the display name and the dotted path used for the ``target_name`` / ``target_path`` placeholders.

    Example:
        >>> from functools import partial
        >>> _callable_display_name(max)
        ('max', 'builtins.max')
        >>> _callable_display_name(partial(max, default=0))
        ('max', 'builtins.max')

    """
    chain = _partial_chain(obj)
    base = chain[-1].func if chain else obj
    name = getattr(base, "__name__", None)
    if isinstance(name, str):
        module = getattr(base, "__module__", None)
    else:
        name = type(base).__name__
        module = type(base).__module__
    path = f"{module}.{name}" if isinstance(module, str) and module else name
    return name, path


def _source_display_name(source: Callable) -> str:
    """Return display name: class name for ``__init__``, function name otherwise."""
    return source.__qualname__.split(".")[-2] if source.__name__ == "__init__" else source.__name__


def _raise_warn_callable(
    stream: Optional[Callable],
    source: Callable,
    target: Union[None, bool, Callable, TargetMode, staticmethod, classmethod],
    deprecated_in: str,
    remove_in: str,
    message_template: Optional[str] = None,
    as_exception: bool = False,
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
        as_exception: Passed through to :func:`_raise_warn` — raise instead of warn; see its docstring.

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
    # Unwrap descriptor: _emit_call_warning passes the raw (pre-normalization) target so
    # the warning can name the class rather than __init__.  For descriptor targets,
    # callable(staticmethod(fn)) is False on Python 3.9 and callable(classmethod(fn))
    # is always False, so without this unwrap the no-target template fires incorrectly.
    target = _unwrap_descriptor_target(target)
    if callable(target):
        # ``functools.partial`` and callable-instance targets carry no ``__name__``; the helper names them.
        target_name, target_path = _callable_display_name(target)
        template_warn = TEMPLATE_WARNING_CALLABLE
    else:
        target_name, target_path = "", ""
        template_warn = TEMPLATE_WARNING_NO_TARGET
    _raise_warn(
        stream=stream,
        source=source,
        message_template=message_template or template_warn,
        as_exception=as_exception,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        target_name=target_name,
        target_path=target_path,
    )


def _raise_warn_arguments(
    stream: Optional[Callable],
    source: Callable,
    arguments: Mapping[str, Optional[str]],
    deprecated_in: str,
    remove_in: str,
    message_template: Optional[str] = None,
    as_exception: bool = False,
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
        as_exception: Passed through to :func:`_raise_warn` — raise instead of warn; see its docstring.

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
        as_exception=as_exception,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        argument_map=args_map,
    )


def _eval_skip_if(skip_if: Union[bool, Callable[[], bool]]) -> bool:
    """Evaluate a ``skip_if`` condition; ``True`` means the deprecation machinery stays inactive for this use.

    The one implementation shared by the callable wrappers and the deprecation proxy.  A non-callable value is
    converted with ``bool()``; a callable is invoked with no arguments and must return a strict ``bool``.

    Args:
        skip_if: A static condition or a zero-argument callable returning ``bool``.

    Returns:
        The evaluated condition.

    Raises:
        TypeError: If ``skip_if`` is a callable that does not return a ``bool``.

    Example:
        >>> _eval_skip_if(False)
        False
        >>> _eval_skip_if(lambda: True)
        True
        >>> _eval_skip_if(lambda: 1)
        Traceback (most recent call last):
        ...
        TypeError: User function 'skip_if' shall return bool, but got: <class 'int'>

    """
    shall_skip = skip_if() if callable(skip_if) else bool(skip_if)
    if not isinstance(shall_skip, bool):
        raise TypeError(f"User function 'skip_if' shall return bool, but got: {type(shall_skip)}")
    return shall_skip


def _consume_warn_budget(
    state: _WrapperState,
    num_warns: int,
    *,
    reason_callable: bool,
    reason_argument: Optional[Mapping[str, Optional[str]]] = None,
) -> bool:
    """Check the warn budget and consume one unit from it when a warning may fire.

    The one budget implementation for ``@deprecated`` wrappers, ``deprecated_class`` / ``deprecated_instance``
    proxies and deprecated modules.  Must be called while holding ``state.lock`` — the read-check-increment sequence
    is exactly what the lock protects (see the thread-safety note at the call site).

    Args:
        state: Mutable per-wrapper counters.
        num_warns: Configured budget; negative means unlimited.
        reason_callable: Warning is for the deprecated callable itself — consumes ``warned_calls``.
        reason_argument: Deprecated argument names present in this call — consumes per-argument
            budgets in ``warned_args`` through :func:`_consume_argument_warn_budget` when ``reason_callable`` is
            ``False``.  A proxy passes ``{key: None}`` for its per-argument or ``__attr__:`` attribute key.

    Returns:
        True when the caller should emit the warning (budget available and now consumed).  For the argument reason
        this means at least one argument is still under budget; a caller that names the arguments in its warning
        uses :func:`_consume_argument_warn_budget` directly to learn which ones.

    Example:
        >>> state = _WrapperState()
        >>> [_consume_warn_budget(state, 1, reason_callable=True) for _ in range(2)]
        [True, False]

    """
    # The budget must track the warning variant that will be emitted:
    # - callable-level warnings consume warned_calls
    # - argument-level warnings consume warned_args
    if reason_argument and not reason_callable:
        return bool(_consume_argument_warn_budget(state, num_warns, reason_argument))
    if num_warns >= 0 and state.warned_calls >= num_warns:
        return False
    if reason_callable:
        state.warned_calls += 1
    return True


def _consume_argument_warn_budget(
    state: _WrapperState,
    num_warns: int,
    reason_argument: Mapping[str, Optional[str]],
) -> dict[str, Optional[str]]:
    """Consume one unit from each deprecated argument's own budget and return the arguments that may be named.

    Each deprecated argument has its own budget in ``state.warned_args``.  Only the arguments still under budget are
    returned and consumed; an argument whose budget is spent is left out, so a later warning does not name it again.
    Must be called while holding ``state.lock``, like :func:`_consume_warn_budget`.

    Args:
        state: Mutable per-wrapper counters.
        num_warns: Configured budget; negative means unlimited.
        reason_argument: Deprecated argument names present in this call, mapped to their new names.

    Returns:
        The entries of ``reason_argument`` still under budget, in their original order.  An empty dict means no
        warning should be emitted.

    Example:
        >>> state = _WrapperState()
        >>> _consume_argument_warn_budget(state, 1, {"old_x": "x"})
        {'old_x': 'x'}
        >>> _consume_argument_warn_budget(state, 1, {"old_x": "x", "old_y": "y"})
        {'old_y': 'y'}
        >>> _consume_argument_warn_budget(state, 1, {"old_x": "x", "old_y": "y"})
        {}

    """
    under_budget = {
        arg: new_arg
        for arg, new_arg in reason_argument.items()
        if num_warns < 0 or state.warned_args.get(arg, 0) < num_warns
    }
    for arg in under_budget:
        state.warned_args[arg] = state.warned_args.get(arg, 0) + 1
    return under_budget


def _release_warn_budget(
    state: _WrapperState,
    num_warns: int,
    *,
    reason_callable: bool,
    reason_argument: Optional[Mapping[str, Optional[str]]] = None,
) -> None:
    """Give back the unit a successful :func:`_consume_warn_budget` claim took, when its warning was never delivered.

    Used by the proxy when its ``stream`` raises (a ``FutureWarning`` filter set to ``"error"``, a failing logging
    sink): the caller did not receive the notice, so the next use warns again.  Takes the same reason arguments as the
    claim it undoes, and is exact for the callable counter and for a single argument key (the claims the proxy
    makes).  An unlimited budget (``num_warns < 0``) has no unit to return, so nothing changes.  Must be called while
    holding ``state.lock``.

    Args:
        state: Mutable per-wrapper counters.
        num_warns: Configured budget; negative means unlimited.
        reason_callable: The claim was for the callable-level counter ``warned_calls``.
        reason_argument: The argument keys the claim consumed in ``warned_args``.

    Example:
        >>> state = _WrapperState()
        >>> _consume_warn_budget(state, 1, reason_callable=True)
        True
        >>> _release_warn_budget(state, 1, reason_callable=True)
        >>> _consume_warn_budget(state, 1, reason_callable=True)
        True

    """
    if num_warns < 0:
        return
    if reason_callable:
        state.warned_calls -= 1
    elif reason_argument:
        for arg in reason_argument:
            state.warned_args[arg] -= 1
