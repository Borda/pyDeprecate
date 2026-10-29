"""Check that both agent hosts receive the same self-contained plugin skills and up-to-date SKILL.md prose."""

import inspect
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from deprecate import (
    TargetMode,
    as_exceptions,
    deprecated,
    deprecated_class,
    deprecated_instance,
    find_deprecation_wrappers,
    validate_deprecation_expiry,
    validate_deprecation_policy,
)
from deprecate import (
    __all__ as _deprecate_all,
)

_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN = _ROOT / "plugins" / "pydeprecate"


_CLAUDE_CLI = shutil.which("claude")


@pytest.mark.skipif(_CLAUDE_CLI is None, reason="requires the Claude Code CLI on PATH")
@pytest.mark.parametrize(
    "target",
    [
        pytest.param("plugins/pydeprecate", id="plugin"),
        pytest.param(".claude-plugin/marketplace.json", id="marketplace"),
    ],
)
def test_claude_host_validates_plugin(target: str) -> None:
    """Run the Claude Code host validator against the plugin manifest and the marketplace catalog.

    A maintainer edits a manifest field or moves a skill directory. A JSON read
    cannot tell whether the host still accepts the result, so when the ``claude`` CLI is present
    this runs ``claude plugin validate`` on both entry points and requires a passing result;
    Codex ships no equivalent validator, and hosts without the CLI installed skip this check.
    """
    result = subprocess.run(
        [str(_CLAUDE_CLI), "plugin", "validate", str(_ROOT / target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Validation passed" in result.stdout, result.stdout


_SKILL_PATHS = (_PLUGIN / "skills" / "sunset" / "SKILL.md", _PLUGIN / "skills" / "prune" / "SKILL.md")
# Prose words, stdlib/foreign names, environment-variable names, and the wrapper attribute `__deprecated__` the
# skill bodies also backtick — none of them is a deprecate.* module export.
_SKILL_SKIP_TOKENS = frozenset(
    {
        "__deprecated__",
        "DeprecationWarning",
        "FutureWarning",
        "convert",
        "keep",
        "warnings.warn",
        "warnings.deprecated",
        "typing_extensions.deprecated",
        "pyDeprecate",
        "deprecate",
        "deprecate.__version__",
        "pyproject.toml",
        "DEPRECATE_AS_EXCEPTIONS",
    }
)
_SKILL_CALLABLES = {
    fn.__name__: fn
    for fn in (
        as_exceptions,
        deprecated,
        deprecated_class,
        deprecated_instance,
        find_deprecation_wrappers,
        validate_deprecation_expiry,
        validate_deprecation_policy,
    )
}
_SKILL_KWARGS = frozenset(name for fn in _SKILL_CALLABLES.values() for name in inspect.signature(fn).parameters)
_BARE_TOKEN_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)`")
_CALL_FORM_RE = re.compile(r"`@?([A-Za-z_][A-Za-z0-9_]*)\(([^`]*)\)`")
_KWARG_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=")
_ENUM_REF_RE = re.compile(r"\bTargetMode\.([A-Za-z_][A-Za-z0-9_]*)")


def _bare_token_resolves(token: str) -> bool:
    """Resolve a backticked identifier against the installed API; dotted tokens must use a known prefix."""
    if token in _SKILL_SKIP_TOKENS:
        return True
    if "." not in token:
        return token in _deprecate_all or token in TargetMode.__members__ or token in _SKILL_KWARGS
    prefix, name = token.rsplit(".", 1)
    if prefix == "deprecate":
        return name in _deprecate_all
    return prefix == "TargetMode" and name in TargetMode.__members__


def _call_form_problems(callee: str, args: str) -> list[str]:
    """List every callee, kwarg, or ``TargetMode`` member in a backticked call form that the installed API lacks."""
    fn = _SKILL_CALLABLES.get(callee)
    if fn is None:
        return [callee]
    params = inspect.signature(fn).parameters
    problems = [f"{callee}({kw}=)" for kw in _KWARG_RE.findall(args) if kw not in params]
    problems += [
        f"TargetMode.{member}" for member in _ENUM_REF_RE.findall(args) if member not in TargetMode.__members__
    ]
    return problems


def test_skill_docs_reference_real_api() -> None:
    """Guard against stale API references in the agent-facing SKILL.md docs.

    Both SKILL.md files hardcode ``deprecate.*`` identifiers and call forms such as
    ``find_deprecation_wrappers(..., recursive=True)`` as instructions for an AI agent to
    follow; a rename of a public symbol, keyword argument, or ``TargetMode`` member would
    silently break those instructions while the library's own suite stays green. This
    checks bare identifiers (with their ``deprecate.``/``TargetMode.`` prefix), the callee
    of every backticked call form, and every ``kw=`` inside it against the installed
    package. Behavioural prose claims are not machine-checked here.
    """
    unresolved: list[str] = []
    for skill_path in _SKILL_PATHS:
        body = skill_path.read_text(encoding="utf-8")
        unresolved += [f"{skill_path.name}:{t}" for t in _BARE_TOKEN_RE.findall(body) if not _bare_token_resolves(t)]
        for callee, args in _CALL_FORM_RE.findall(body):
            unresolved += [f"{skill_path.name}:{p}" for p in _call_form_problems(callee, args)]

    assert not unresolved, f"SKILL.md references identifiers not found in the installed API: {unresolved}"


_PYTHON_FENCE_RE = re.compile(r"^```python\n(.*?)^```", re.DOTALL | re.MULTILINE)


def _uncompilable_fences(body: str) -> list[str]:
    """Describe every Python fence in a Markdown body that is not syntactically valid Python."""
    problems: list[str] = []
    for index, code in enumerate(_PYTHON_FENCE_RE.findall(body)):
        try:
            compile(code, f"fence {index}", "exec")
        except SyntaxError as err:
            problems.append(f"fence {index}: {err.msg} (line {err.lineno})")
    return problems


@pytest.mark.parametrize("skill_path", [pytest.param(path, id=path.parent.name) for path in _SKILL_PATHS])
def test_skill_python_fences_compile(skill_path: Path) -> None:
    """Every Python fence in an agent-facing SKILL.md is syntactically valid.

    Agents copy these snippets verbatim into a consumer's code. A duplicated `if` line once shipped a fallback
    snippet that raised `IndentationError` on `compile()`, and no other check reads fence bodies: the identifier
    guard above only matches backticked tokens, and the docs example runner does not cover `plugins/`.
    """
    problems = _uncompilable_fences(skill_path.read_text(encoding="utf-8"))
    assert not problems, f"{skill_path.name} has Python fences that do not compile: {problems}"
