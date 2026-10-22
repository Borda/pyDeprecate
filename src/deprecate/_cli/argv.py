"""Command-line argument handling for the CLI: UTF-8 streams, boolean flags and argument binding."""

import contextlib
import inspect
import sys
from collections.abc import Callable, Sequence
from typing import NamedTuple, Optional, Union

from deprecate._cli.config import _ConfigBool


def _ensure_utf8_streams() -> None:
    """Reconfigure stdout/stderr to UTF-8 on platforms where the default encoding may reject non-ASCII characters.

    On Windows the default console codec (``charmap``) cannot encode Unicode emoji used by
    :class:`~deprecate.audit.DeprecationStatus`.  Calling ``reconfigure`` before any output is written ensures emoji
    reach the terminal (or CI log) without a :exc:`UnicodeEncodeError`.

    The call is a no-op when the streams are already UTF-8 or when they lack a ``reconfigure`` method (binary streams,
    pytest capture wrappers).

    """
    for stream in (sys.stdout, sys.stderr):
        # ``encoding`` may be *present but None* (e.g. some redirected/binary-ish wrappers); ``getattr`` with a
        # default only covers a missing attribute, so guard the ``None`` case with ``or "utf-8"`` before ``.lower()``.
        encoding = (getattr(stream, "encoding", None) or "utf-8").lower()
        if hasattr(stream, "reconfigure") and encoding != "utf-8":
            with contextlib.suppress(Exception):
                stream.reconfigure(encoding="utf-8")


#: Spellings accepted as a boolean value after a boolean flag, keyed by lower-cased text. Fire only parses the
#: capitalised ``True``/``False``; any other spelling would bind as a ``str``/``int`` (``--exit-zero=true``) or be
#: read as the next positional argument (``--exit-zero 0``), so :func:`_normalize_bool_flags` rewrites them first.
_BOOL_LITERALS: dict[str, bool] = {"true": True, "false": False, "1": True, "0": False}


class _BoolFlag(NamedTuple):
    """A boolean flag recognised in one argument.

    ``name`` is the parameter name, ``value`` the boolean it sets, and ``takes_next`` whether a ``true``/``false``/
    ``1``/``0`` token that follows is that flag's value.

    """

    name: str
    value: bool
    takes_next: bool


def _bool_params(fn: Callable[..., int]) -> frozenset[str]:
    """Return the names of the CLI-visible parameters of *fn* annotated as ``bool`` or :data:`_ConfigBool`.

    :data:`_ConfigBool` is a ``bool`` whose omitted value comes from ``pyproject.toml`` (``policy``'s
    ``message_required``); the other :data:`_ConfigFlag` parameters (``min_grace``, ``exclude``) are not booleans.
    Underscore-prefixed parameters are internal (``cmd_all`` plumbing) and never typed on the command line:
    :func:`cli` rejects such an option before Fire parses it (see :func:`_internal_option`).

    Examples:
        >>> from deprecate._cli.commands import cmd_check, cmd_policy
        >>> sorted(_bool_params(cmd_check))
        ['exit_zero', 'include_members', 'recursive']
        >>> sorted(_bool_params(cmd_policy))
        ['exit_zero', 'include_members', 'message_required', 'recursive']

    """
    return frozenset(
        name
        for name, param in inspect.signature(fn).parameters.items()
        if param.annotation in (bool, _ConfigBool) and not name.startswith("_")
    )


def _match_bool_flag(arg: str, bool_params: frozenset[str]) -> Optional[_BoolFlag]:
    """Recognise *arg* as a boolean flag of the subcommand, or return ``None`` to leave it untouched.

    Examples:
        >>> _match_bool_flag("--exit-zero", frozenset({"exit_zero"}))
        _BoolFlag(name='exit_zero', value=True, takes_next=True)
        >>> _match_bool_flag("--noexit-zero", frozenset({"exit_zero"}))
        _BoolFlag(name='exit_zero', value=False, takes_next=False)
        >>> _match_bool_flag("--exit-zero=FALSE", frozenset({"exit_zero"}))
        _BoolFlag(name='exit_zero', value=False, takes_next=False)
        >>> _match_bool_flag("--exit-zero=yes", frozenset({"exit_zero"})) is None
        True

    """
    if not arg.startswith("--"):
        return None
    flag, has_value, attached = arg[2:].partition("=")
    name = flag.replace("-", "_")
    if has_value:
        literal = _BOOL_LITERALS.get(attached.lower())
        return _BoolFlag(name, literal, False) if name in bool_params and literal is not None else None
    if name in bool_params:
        return _BoolFlag(name, True, True)
    if name.startswith("no") and name[2:] in bool_params:
        return _BoolFlag(name[2:], False, False)
    return None


def _normalize_bool_flags(argv: list[str], bool_params: frozenset[str]) -> list[str]:
    """Rewrite boolean flags into the explicit ``--name=True``/``--name=False`` form.

    Fire reads the token after a bare flag as the flag's value unless that token is itself a flag, so
    ``check --exit-zero src/pkg`` binds ``exit_zero='src/pkg'`` and scans the current directory. A pre-commit hook
    builds its command as ``entry + args``, which puts such flags before the path by default. The explicit form binds
    correctly in any position. Two flag forms are recognised, each with a dash or underscore in ``<name>``: ``--name``
    and ``--no<name>``, where ``no`` attaches directly to the name (``--norecursive``, ``--noexit-zero``). The spaced
    ``--no-recursive`` and ``--no_recursive`` are not recognised and reach Fire unchanged. A value is accepted as
    ``true``, ``false``, ``1`` or ``0`` in any case, either attached (``--exit-zero=false``) or as the next token
    (``--exit-zero 0``, which is consumed); ``--no<name>`` never consumes the next token. Any other value is left for
    :func:`_non_bool_flag_error` to reject, and everything after a ``--`` separator is left untouched.

    Args:
        argv: Arguments after the subcommand name.
        bool_params: Names of the subcommand's ``bool`` parameters, from :func:`_bool_params`.

    Returns:
        A new argument list with every boolean flag made explicit.

    Examples:
        >>> _normalize_bool_flags(["--exit-zero", "src/pkg", "--norecursive"], frozenset({"exit_zero", "recursive"}))
        ['--exit_zero=True', 'src/pkg', '--recursive=False']
        >>> _normalize_bool_flags(["--exit-zero", "false", "src/pkg"], frozenset({"exit_zero"}))
        ['--exit_zero=False', 'src/pkg']
        >>> _normalize_bool_flags(["--no-recursive", "src/pkg"], frozenset({"recursive"}))
        ['--no-recursive', 'src/pkg']

    """
    result: list[str] = []
    idx = 0
    while idx < len(argv):
        arg = argv[idx]
        idx += 1
        if arg == "--":
            return [*result, arg, *argv[idx:]]
        flag = _match_bool_flag(arg, bool_params)
        if flag is None:
            result.append(arg)
            continue
        value = flag.value
        if flag.takes_next and idx < len(argv) and argv[idx].lower() in _BOOL_LITERALS:
            value = _BOOL_LITERALS[argv[idx].lower()]
            idx += 1
        result.append(f"--{flag.name}={value}")
    return result


def _non_bool_flag_error(fn: Callable[..., int], args: tuple[object, ...], kwargs: dict[str, object]) -> Optional[str]:
    """Return a usage error when a non-boolean value reached one of *fn*'s ``bool`` parameters, else ``None``.

    :func:`_normalize_bool_flags` leaves short flags (``-r src/pkg``) and unrecognised explicit values untouched.
    Without this check they could bind truthy strings and let the run pass while scanning something other than what the
    user named. Positional overflow is rejected separately by the CLI adapter before command dispatch.

    """
    signature = inspect.signature(fn)
    bound = signature.bind_partial(*args, **kwargs).arguments
    for name in sorted(_bool_params(fn)):
        # Fire passes omitted positional parameters through as their defaults, so a flag left to ``pyproject.toml``
        # arrives as the sentinel default object — never typed by the user, so never a stray value.
        if (
            name in bound
            and bound[name] is not signature.parameters[name].default
            and not isinstance(bound[name], bool)
        ):
            flag = name.replace("_", "-")
            return (
                f"`--{flag}` expects True or False, got {bound[name]!r}. A second path, a short flag's argument, or a "
                f"spelling other than True/False can land here even if you did not type `--{flag}`. Pass one path per "
                f"run, prefer long flags, and write explicit values as `--{flag}=True` or `--{flag}=False`."
            )
    return None


def _internal_option(argv: Sequence[str]) -> Optional[str]:
    """Return the first private option or its Fire negation before a ``--`` separator, else ``None``.

    Underscore-prefixed parameters of a ``cmd_*`` function (``_advisory``, ``_wrappers``, ``_config``) are in-process
    plumbing for ``cmd_all``, but Fire exposes every keyword-only parameter as a flag. Typing one would bind a string
    to a parameter nothing validates (``policy pkg --_advisory=yes`` made the run advisory). Fire strips leading
    hyphens and changes remaining hyphens to underscores, so ``--no-wrappers`` also targets ``_wrappers``.

    Examples:
        >>> _internal_option(["policy", "pkg", "--_advisory=yes"])
        '--_advisory'
        >>> _internal_option(["check", "pkg", "--", "--_advisory"]) is None
        True

    """
    for arg in argv:
        if arg == "--":
            return None
        if arg.startswith("-"):
            option = arg.partition("=")[0]
            name = option.lstrip("-").replace("-", "_")
            if name.startswith("_") or name.startswith("no_"):
                return option
    return None


def _cli_signature(fn: Callable[..., int]) -> inspect.Signature:
    """Expose path and version as positional operands and keep public options keyword-only."""
    signature = inspect.signature(fn)
    positional = [param for name, param in signature.parameters.items() if name in {"path", "version"}]
    if not positional:
        return signature
    options = [
        param.replace(kind=inspect.Parameter.KEYWORD_ONLY)
        for name, param in signature.parameters.items()
        if name not in {"path", "version"} and not name.startswith("_")
    ]
    # Fire can dispatch before reporting leftover operands, so capture overflow for rejection before scanning.
    return signature.replace(
        parameters=[*positional, inspect.Parameter("_extra_paths", inspect.Parameter.VAR_POSITIONAL), *options]
    )


def _bind_cli_arguments(
    fn: Callable[..., int], signature: inspect.Signature, args: tuple[object, ...], kwargs: dict[str, object]
) -> Union[inspect.BoundArguments, str]:
    """Bind CLI arguments and reject overflow or invalid booleans before invoking the command."""
    bound = signature.bind(*args, **kwargs)
    if "_extra_paths" in signature.parameters:
        if bound.arguments.pop("_extra_paths", ()):
            return "Pass one path per run; options other than path and version require flags."
        bound.apply_defaults()
    error = _non_bool_flag_error(fn, bound.args, bound.kwargs)
    return error if error is not None else bound
