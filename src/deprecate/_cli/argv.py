"""Command-line parsing for the CLI: UTF-8 streams and the ``argparse`` parser of every subcommand.

The parser is built from the ``cmd_*`` signatures, so a subcommand's options and its direct-call keyword arguments are
one list: ``path`` is positional and takes any number of paths, and every other parameter is a ``--flag``. Option values
stay strings; only boolean flags are converted, from ``true``/``false``/``1``/``0``. The legacy positional version of
``expiry``, ``all`` and ``status`` (``expiry pkg 2.0``) is recognised after parsing and deprecated in favour of
``--version``.

"""

import argparse
import contextlib
import inspect
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Optional

from deprecate._cli import _REPORT_OPTION, _SARIF_OPTION, commands
from deprecate._cli.config import _ConfigBool, _ConfigFlag, _ConfigList
from deprecate._cli.report import _print
from deprecate._cli.scan import _top_level_findable
from deprecate.audit._lifecycle import _parse_version

#: The subcommands, in the order ``--help`` lists them; each runs the ``cmd_<name>`` function of :mod:`commands`.
_SUBCOMMANDS = ("check", "expiry", "policy", "chains", "all", "status")
#: The parameter every subcommand takes as its positional operands: any number of paths.
_PATH = "path"
#: The parameter the legacy second positional binds to, on the subcommands that compare against a version.
_VERSION = "version"
#: Spellings accepted as a boolean flag's value, keyed by lower-cased text.
_BOOL_LITERALS: dict[str, bool] = {"true": True, "false": False, "1": True, "0": False}
#: Values of a ``pyproject.toml``-backed option that switch it off, as ``None`` does in a direct call.
_OFF_LITERALS = frozenset({"none", "false"})
#: Every spelling of the ``--static`` flag, after :func:`_normalize_options` respelled it.
_STATIC_SPELLINGS = frozenset({"static", "no-static", "nostatic"})
#: Metavar of each accumulating list option in ``--help``; any other one reads ``PATTERNS``.
_LIST_METAVARS: dict[str, str] = {"gate": "CHECKS"}
#: A version-shaped token, the stand-in for a PEP 440 parse when the ``packaging`` library is not installed.
_VERSION_SHAPE = re.compile(r"[vV]?\d[0-9A-Za-z.!+_-]*")


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


def _parses_as_version(text: str) -> bool:
    """Tell whether *text* is a PEP 440 version, silently: a parse failure prints nothing.

    Without the ``packaging`` library a version-shaped token (``2.0``, ``v2``, ``1.0.0rc1``) counts as one.

    Examples:
        >>> _parses_as_version("1.0.0rc1"), _parses_as_version("src/pkg_b")
        (True, False)

    """
    try:
        _parse_version(text)
    except ImportError:
        return _VERSION_SHAPE.fullmatch(text) is not None
    except ValueError:
        return False
    return True


def _is_legacy_version(text: str) -> bool:
    """Tell whether the second positional of ``expiry``, ``all`` or ``status`` is the deprecated positional version.

    It is when it names no existing path and no findable module and it parses as a PEP 440 version, so
    ``expiry pkg 2.0`` keeps working while a mistyped second path is still reported as no such path.

    Examples:
        >>> _is_legacy_version("2.0"), _is_legacy_version("json")
        (True, False)

    """
    return not Path(text).exists() and not _top_level_findable(text) and _parses_as_version(text)


class _ReportTargets(NamedTuple):
    """The files ``cli()`` writes after the subcommand: the ``--report-json`` and the ``--report-sarif`` file."""

    json: Optional[str]
    sarif: Optional[str]


class _CliCommand(NamedTuple):
    """A subcommand as the parser sees it: its function, whether it takes a version, and its boolean flags."""

    function: Callable[..., int]
    takes_version: bool
    bool_params: frozenset[str]


def _cli_command(name: str) -> _CliCommand:
    """Describe the ``cmd_<name>`` function: whether it takes a version and which parameters are boolean flags.

    Examples:
        >>> command = _cli_command("expiry")
        >>> command.takes_version, sorted(command.bool_params)
        (True, ['exit_zero', 'include_members', 'recursive', 'static', 'strict'])

    """
    function = getattr(commands, f"cmd_{name}")
    params = inspect.signature(function).parameters
    return _CliCommand(
        function,
        _VERSION in params,
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
    parser.add_argument(_PATH, nargs="*", metavar=_PATH, default=argparse.SUPPRESS, help=helps.get(_PATH))
    for param_name, param in inspect.signature(command.function).parameters.items():
        if param_name == _PATH:
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
        if param.annotation is _ConfigList:
            # repeatable, each value comma-separated; `--flag=` alone gives an empty list that switches the file off
            parser.add_argument(
                f"--{flag}",
                dest=param_name,
                action="append",
                metavar=_LIST_METAVARS.get(param_name, "PATTERNS"),
                default=argparse.SUPPRESS,
                help=helps.get(param_name),
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
    if name != "status":  # `status` gates on nothing, so it has no findings to report as SARIF
        parser.add_argument(
            _SARIF_OPTION,
            dest="report_sarif",
            type=_parse_file_path,
            metavar="FILE",
            default=None,
            help="Write the run's findings as a SARIF 2.1.0 file to FILE on exit 0 or 1, for code-scanning tools.",
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


def _bind_legacy_version(command: _CliCommand, kwargs: dict[str, Any], subparser: argparse.ArgumentParser) -> None:
    """Move a legacy positional version (``expiry pkg 2.0``) from the paths to ``version``, with a deprecation notice.

    Only exactly two positionals of a subcommand that takes a version qualify, and only when the second one is a version
    and no path (:func:`_is_legacy_version`). Giving the version that way and as ``--version`` too exits 2.

    """
    paths = kwargs.get(_PATH, [])
    if not command.takes_version or len(paths) != 2 or not _is_legacy_version(paths[1]):
        return
    if _VERSION in kwargs:
        subparser.error("the version was given twice, as an argument and as `--version`; pass it once")
    kwargs[_VERSION] = paths.pop()
    _print(
        f"pydeprecate: passing the version as a second positional argument is deprecated and will stop working in 1.0;"
        f" pass `--version {kwargs[_VERSION]}` instead.",
        stderr=True,
    )


def _parse_cli_args(argv: Sequence[str]) -> Optional[tuple[_CliCommand, dict[str, Any], _ReportTargets]]:
    """Parse the command line into the subcommand to run, its keyword arguments and the report files to write.

    A first argument that names no subcommand selects ``check``, so ``pydeprecate src/pkg`` checks ``src/pkg``. The
    subcommand's own parser runs ``parse_intermixed_args``, which binds every path correctly with options between them,
    also on Python 3.9. Arguments after a ``--`` separator are paths only, never options; they are bound here, since
    ``parse_known_intermixed_args`` on Python 3.9 still parses options after it. With exactly two positionals on
    ``expiry``, ``all`` or ``status``, a second one that is a version and no path is the deprecated positional version,
    unless the line has a ``--`` separator with operands after it: those are paths, so nothing binds as a version.

    Returns:
        The subcommand, its keyword arguments (only those typed; ``path`` as a list) and the
        ``--report-json``/``--report-sarif`` paths, or ``None`` after printing the help for an empty command line.
        ``--help`` and every usage error exit through argparse (0 and 2).

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
    normalized = _normalize_options(options, command.bool_params)
    if "static" not in command.bool_params and any(
        arg.startswith("--") and arg[2:].partition("=")[0] in _STATIC_SPELLINGS for arg in normalized
    ):
        # `check`, `chains`, `all` and `status` need the imported objects; refuse the flag, in any spelling, rather
        # than ignore it, and name both its spellings so `--no-static` is not read as an accepted no-op
        subparser.error(
            f"--static, --no-static and --nostatic are supported by `expiry` and `policy` only, not `{name}`"
        )
    namespace, extras = subparser.parse_known_intermixed_args(normalized)
    if extras:
        subparser.error(f"unrecognized arguments: {' '.join(extras)}")
    kwargs = vars(namespace)
    if operands:
        kwargs[_PATH] = [*kwargs.get(_PATH, []), *operands]
    else:
        # an operand after `--` is a path whatever it looks like, so only a line without one has a legacy version
        _bind_legacy_version(command, kwargs, subparser)
    report_path = kwargs.pop("report_json")
    sarif_path = kwargs.pop("report_sarif", None)
    return command, kwargs, _ReportTargets(report_path, sarif_path)
