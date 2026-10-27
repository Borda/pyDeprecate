"""Command-line parsing for the CLI: UTF-8 streams and the ``argparse`` parser of every subcommand.

The parser is built from the ``cmd_*`` signatures, so a subcommand's options and its direct-call keyword arguments are
one list: ``path`` (and ``version`` where the subcommand takes it) are positional and every other parameter is a
``--flag``. Option values stay strings; only boolean flags are converted, from ``true``/``false``/``1``/``0``.

"""

import argparse
import contextlib
import inspect
import re
import sys
from collections.abc import Callable, Sequence
from typing import Any, NamedTuple, Optional

from deprecate._cli import _REPORT_OPTION, commands
from deprecate._cli.config import _ConfigBool, _ConfigFlag

#: The subcommands, in the order ``--help`` lists them; each runs the ``cmd_<name>`` function of :mod:`commands`.
_SUBCOMMANDS = ("check", "expiry", "policy", "chains", "all", "status")
#: Parameters taken as positional operands, in this order, by every subcommand that has them.
_POSITIONALS = ("path", "version")
#: Spellings accepted as a boolean flag's value, keyed by lower-cased text.
_BOOL_LITERALS: dict[str, bool] = {"true": True, "false": False, "1": True, "0": False}
#: Values of a ``pyproject.toml``-backed option that switch it off, as ``None`` does in a direct call.
_OFF_LITERALS = frozenset({"none", "false"})
#: Namespace attribute of the positional version, kept apart from ``--version`` so that giving both is caught.
_VERSION_OPERAND = "version_operand"


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


def _parse_bool(text: str) -> bool:
    """Read a boolean flag's value: ``true``, ``false``, ``1`` or ``0`` in any case.

    Raises:
        argparse.ArgumentTypeError: For any other spelling, which argparse reports as a usage error (exit 2).

    Examples:
        >>> _parse_bool("FALSE"), _parse_bool("1")
        (False, True)

    """
    try:
        return _BOOL_LITERALS[text.lower()]
    except KeyError:
        raise argparse.ArgumentTypeError(
            f"expects True or False (or 1/0), got {text!r}; write an explicit value as `--flag=true`"
        ) from None


def _parse_switchable(text: str) -> Optional[str]:
    """Read the value of a ``pyproject.toml``-backed option: ``None`` or ``false`` switches it off, as ``None`` does.

    Examples:
        >>> _parse_switchable("None") is None, _parse_switchable("0.3")
        (True, '0.3')

    """
    return None if text.lower() in _OFF_LITERALS else text


def _parse_file_path(text: str) -> str:
    """Read a file-path option's value, refusing an empty one (``--report-json=``) that names no file.

    Raises:
        argparse.ArgumentTypeError: For an empty value, which argparse reports as a usage error (exit 2).

    Examples:
        >>> _parse_file_path("report.json")
        'report.json'

    """
    if not text:
        raise argparse.ArgumentTypeError("expects a file path, got an empty value")
    return text


def _positional_dest(name: str) -> str:
    """Return the namespace attribute a positional operand is parsed into: ``path`` as is, the version apart."""
    return _VERSION_OPERAND if name == "version" else name


class _CliCommand(NamedTuple):
    """A subcommand as the parser sees it: its function, its positional operands and its boolean flags."""

    function: Callable[..., int]
    positionals: tuple[str, ...]
    bool_params: frozenset[str]


def _cli_command(name: str) -> _CliCommand:
    """Describe the ``cmd_<name>`` function: which parameters are positional and which are boolean flags.

    Examples:
        >>> command = _cli_command("expiry")
        >>> command.positionals, sorted(command.bool_params)
        (('path', 'version'), ['exit_zero', 'include_members', 'recursive'])

    """
    function = getattr(commands, f"cmd_{name}")
    params = inspect.signature(function).parameters
    return _CliCommand(
        function,
        tuple(param for param in _POSITIONALS if param in params),
        frozenset(param_name for param_name, param in params.items() if param.annotation in (bool, _ConfigBool)),
    )


def _flag(name: str) -> str:
    """Return the option spelling of a parameter or option name: underscores between words become dashes.

    Examples:
        >>> _flag("exit_zero"), _flag("report_json")
        ('exit-zero', 'report-json')

    """
    return re.sub(r"(?<=[A-Za-z0-9])_(?=[A-Za-z0-9])", "-", name)


def _docstring_parts(function: Callable[..., int]) -> tuple[str, dict[str, str]]:
    """Split a ``cmd_*`` docstring into its description and the help text of each argument in its ``Args`` section.

    Double backticks become single ones and ``%`` is escaped, since argparse formats help strings with ``%``.

    """
    doc = (inspect.getdoc(function) or "").replace("``", "`").replace("%", "%%")
    description, _, rest = doc.partition("\nArgs:\n")
    args_block = re.split(r"\n(?=\S)", rest, maxsplit=1)[0]
    helps: dict[str, str] = {}
    for match in re.finditer(r"^ {4}(\w+): (.*(?:\n {8}.*)*)", args_block, flags=re.MULTILINE):
        helps[match.group(1)] = " ".join(line.strip() for line in match.group(2).splitlines())
    return description.strip(), helps


def _add_subcommand(subparsers: Any, name: str) -> argparse.ArgumentParser:  # noqa: ANN401
    """Declare one subcommand from its ``cmd_*`` signature and docstring.

    Every option defaults to :data:`argparse.SUPPRESS`, so the namespace holds only what was typed and an omitted option
    keeps the ``cmd_*`` default (``pyproject.toml`` for the config-backed ones).

    """
    command = _cli_command(name)
    description, helps = _docstring_parts(command.function)
    parser: argparse.ArgumentParser = subparsers.add_parser(
        name,
        help=description.split("\n", 1)[0],
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    for positional in command.positionals:
        parser.add_argument(
            _positional_dest(positional),
            nargs="?",
            metavar=positional,
            default=argparse.SUPPRESS,
            help=helps.get(positional),
        )
    for param_name, param in inspect.signature(command.function).parameters.items():
        if param_name == "path":
            continue
        flag = _flag(param_name)
        if param_name in command.bool_params:
            parser.add_argument(
                f"--{flag}",
                dest=param_name,
                nargs="?",
                const=True,
                type=_parse_bool,
                metavar="BOOL",
                default=argparse.SUPPRESS,
                help=helps.get(param_name),
            )
            parser.add_argument(
                f"--no-{flag}",
                dest=param_name,
                action="store_false",
                default=argparse.SUPPRESS,
                help=f"Same as `--{flag}=false`.",
            )
            # the older `--no<flag>` spelling (Fire's) keeps working for existing command lines, unlisted in --help
            parser.add_argument(
                f"--no{flag}", dest=param_name, action="store_false", default=argparse.SUPPRESS, help=argparse.SUPPRESS
            )
            continue
        parser.add_argument(
            f"--{flag}",
            dest=param_name,
            type=_parse_switchable if param.annotation is _ConfigFlag else str,
            metavar=param_name.upper(),
            default=argparse.SUPPRESS,
            help=helps.get(param_name),
        )
    parser.add_argument(
        _REPORT_OPTION,
        dest="report_json",
        type=_parse_file_path,
        metavar="FILE",
        default=None,
        help="Write the run's machine-readable record (deprecation table and gaps) to FILE on exit 0 or 1.",
    )
    return parser


def _build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    """Build the ``pydeprecate`` parser and one subparser per subcommand.

    Returns:
        The top-level parser (for ``--help``) and the subparsers by name, which do the actual parsing.

    """
    parser = argparse.ArgumentParser(
        prog="pydeprecate",
        description=(
            "Audit the @deprecated wrappers of a Python package. Without a subcommand, `check` runs; pass"
            " `<subcommand> --help` for its options."
        ),
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(title="subcommands", metavar="<subcommand>")
    return parser, {name: _add_subcommand(subparsers, name) for name in _SUBCOMMANDS}


def _normalize_options(argv: Sequence[str], bool_params: frozenset[str]) -> list[str]:
    """Respell options for argparse: dashes in every name, and every boolean flag with its value attached.

    Both spellings of an option name are accepted (``--exit_zero`` and ``--exit-zero``), so underscores between the
    words of a name become dashes; any other spelling passes through for argparse to judge. A boolean flag takes
    an optional value, and argparse would take *any* next argument as that value, swallowing the path in
    ``check --exit-zero src/pkg``; so a bare flag becomes ``--flag=true``, and only a following ``true``, ``false``,
    ``1`` or ``0`` (any case) is consumed as its value. Everything after a ``--`` separator is left untouched.

    Examples:
        >>> _normalize_options(["--exit_zero", "src/pkg", "--recursive", "0"], frozenset({"exit_zero", "recursive"}))
        ['--exit-zero=true', 'src/pkg', '--recursive=0']
        >>> _normalize_options(["--report_json=a_b.json"], frozenset())
        ['--report-json=a_b.json']

    """
    bool_flags = {_flag(name) for name in bool_params}
    result: list[str] = []
    idx = 0
    while idx < len(argv):
        arg = argv[idx]
        idx += 1
        if arg == "--":
            return [*result, arg, *argv[idx:]]
        if not arg.startswith("--"):
            result.append(arg)
            continue
        name, sep, value = arg[2:].partition("=")
        name = _flag(name)
        if not sep and name in bool_flags:
            sep = "="
            if idx < len(argv) and argv[idx].lower() in _BOOL_LITERALS:
                value = argv[idx]
                idx += 1
            else:
                value = "true"
        result.append(f"--{name}{sep}{value}")
    return result


def _parse_cli_args(argv: Sequence[str]) -> Optional[tuple[_CliCommand, dict[str, Any], Optional[str]]]:
    """Parse the command line into the subcommand to run, its keyword arguments and the ``--report-json`` path.

    A first argument that names no subcommand selects ``check``, so ``pydeprecate src/pkg`` checks ``src/pkg``. The
    subcommand's own parser runs ``parse_intermixed_args``, which binds the path and version positionals correctly
    with options between them, also on Python 3.9. Arguments after a ``--`` separator are operands only, never options;
    they are bound here, since ``parse_known_intermixed_args`` on Python 3.9 still parses options after it.

    Returns:
        The subcommand, its keyword arguments (only those typed) and the report path, or ``None`` after printing the
        help for an empty command line. ``--help`` and every usage error exit through argparse (0 and 2).

    """
    parser, subparsers = _build_parser()
    if not argv:
        parser.print_help()
        return None
    if argv[0] in ("-h", "--help"):
        parser.parse_args(argv)
    name, rest = (argv[0], argv[1:]) if argv[0] in subparsers else ("check", argv)
    command = _cli_command(name)
    subparser = subparsers[name]
    options, operands = (rest[: rest.index("--")], rest[rest.index("--") + 1 :]) if "--" in rest else (rest, [])
    namespace, extras = subparser.parse_known_intermixed_args(_normalize_options(options, command.bool_params))
    kwargs = vars(namespace)
    for operand in operands:
        free = [_positional_dest(name) for name in command.positionals if _positional_dest(name) not in kwargs]
        if free:
            kwargs[free[0]] = operand
        else:
            extras.append(operand)
    if extras:
        # an extra positional is a second path; one that follows an unknown option is likely that option's value
        hint = "" if any(extra.startswith("-") for extra in extras) else "; pass one path per run"
        subparser.error(f"unrecognized arguments: {' '.join(extras)}{hint}")
    if _VERSION_OPERAND in kwargs:
        if "version" in kwargs:
            subparser.error("the version was given twice, as an argument and as `--version`; pass it once")
        kwargs["version"] = kwargs.pop(_VERSION_OPERAND)
    report_path = kwargs.pop("report_json")
    return command, kwargs, report_path
