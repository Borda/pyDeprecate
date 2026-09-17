"""Check that both agent hosts receive the same self-contained plugin skills and up-to-date SKILL.md prose."""

import importlib.util
import inspect
import json
import re
from pathlib import Path
from typing import Any

import pytest

from deprecate import (
    TargetMode,
    deprecated,
    deprecated_class,
    deprecated_instance,
    find_deprecation_wrappers,
    validate_deprecation_expiry,
)
from deprecate import (
    __all__ as _deprecate_all,
)
from deprecate import (
    __version__ as _deprecate_version,
)

_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN = _ROOT / "plugins" / "pydeprecate"
_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None

try:
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
except ImportError:  # pragma: no cover - guarded by _PACKAGING_AVAILABLE skipif below
    SpecifierSet = None  # type: ignore[assignment,misc]
    Version = None  # type: ignore[assignment,misc]

#: Floating support window: the manifest floor may trail the installed minor by at most this many minors.
_SUPPORT_WINDOW_MINORS = 3


def _load_manifest(host: str) -> dict[str, Any]:
    """Load the plugin manifest for an agent host."""
    return json.loads((_PLUGIN / f".{host}-plugin/plugin.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "host",
    [pytest.param("codex", id="codex"), pytest.param("claude", id="claude")],
)
def test_catalog_resolves_shared_skills(host: str) -> None:
    """Resolve each host's catalog exactly as a repository installation would.

    A developer installs the plugin from a checkout. Both catalogs must lead to
    the same portable skills, with no references outside the cached plugin.
    """
    catalog_path = ".agents/plugins/marketplace.json" if host == "codex" else ".claude-plugin/marketplace.json"
    catalog = json.loads((_ROOT / catalog_path).read_text(encoding="utf-8"))
    assert catalog["name"] == "pydeprecate"
    (entry,) = catalog["plugins"]
    source = entry["source"]["path"] if host == "codex" else entry["source"]
    assert (_ROOT / source).resolve() == _PLUGIN
    manifest = _load_manifest(host)
    assert manifest["name"] == entry["name"] == "pydeprecate"
    skills = (_PLUGIN / manifest["skills"]).resolve()
    assert skills == _PLUGIN / "skills"
    assert {path.name for path in skills.iterdir() if path.is_dir() and not path.name.startswith(".")} == {
        "sunset",
        "prune",
    }
    for name in ("sunset", "prune"):
        skill_path = skills / name / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        assert content.startswith("---"), f"{skill_path} must start with YAML frontmatter"
        marker, metadata, body = content.split("---", 2)
        assert not marker
        assert f"name: {name}" in metadata.splitlines()
        assert any(
            line.startswith("description: ") and line.removeprefix("description: ").strip()
            for line in metadata.splitlines()
        )
        assert body.strip()
    assert not {"hooks", "mcpServers", "apps"} & manifest.keys()
    # Filesystem noise (macOS Finder metadata, Python bytecode cache) is not a "plugin entry" — filter
    # it out before the exact-set comparison so the assertion still catches genuinely unexpected entries.
    ignored_entries = {".DS_Store", "__pycache__"}
    top_level = {child.name for child in _PLUGIN.iterdir() if child.name not in ignored_entries}
    assert top_level == {"skills", ".claude-plugin", ".codex-plugin"}, (
        f"plugins/pydeprecate must not carry extra top-level entries on disk (e.g. hooks/, commands/, "
        f"agents/, .mcp.json); found {top_level}"
    )


def test_host_versions_agree() -> None:
    """Keep host releases aligned so installation cannot select divergent instructions.

    A maintainer updates one host manifest before publishing a plugin release. Both hosts must
    retain identical release metadata so users receive the same plugin, and each host's catalog
    entry (when it carries its own ``description`` key) must quote the same canonical description
    as that host's plugin manifest — otherwise browsing the catalog shows different text than
    installing the plugin.
    """
    manifests = {host: _load_manifest(host) for host in ("codex", "claude")}
    shared_keys = (
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "skills",
        "compatible_package_version",
    )
    assert {key: manifests["codex"][key] for key in shared_keys} == {
        key: manifests["claude"][key] for key in shared_keys
    }

    codex_entry = json.loads((_ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))[
        "plugins"
    ][0]
    claude_entry = json.loads((_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))["plugins"][0]
    # Codex's catalog entry has no "description" key today — guard rather than assume its presence.
    if "description" in codex_entry:
        assert codex_entry["description"] == manifests["codex"]["description"]
    if "description" in claude_entry:
        assert claude_entry["description"] == manifests["claude"]["description"]


@pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
@pytest.mark.parametrize(
    "host",
    [pytest.param("codex", id="codex"), pytest.param("claude", id="claude")],
)
def test_plugin_declares_compatible_package_version(host: str) -> None:
    """Catch a plugin release drifting out of range of the installed package.

    A maintainer changes public API without updating the plugin's declared compatibility
    floor. Both hosts pin a ``compatible_package_version`` specifier, kept independent of
    the plugin's own ``version``. This is a repo-side maintainer convention, not a runtime
    guarantee: ``claude plugin validate`` reports ``compatible_package_version`` as an
    unknown field it ignores at load time, and Codex has no validator for it either — no
    host actually blocks installation on a mismatch. SKILL.md prose is the only channel
    that reaches the installing agent, so this test is what keeps the declared floor
    trustworthy for a maintainer deciding when to bump it. ``prereleases=True`` is required
    here: this repo's own installed version is a ``.dev`` build, and a bare floor like
    ``>=0.12.0`` excludes prereleases by default under PEP 440. An empty string is also
    guarded against explicitly: ``SpecifierSet("")`` matches every version, so a manifest
    that silently lost its floor would still pass a bare ``.contains()`` check. The floor is a
    floating window — at most ``_SUPPORT_WINDOW_MINORS`` minors behind the installed version —
    so a maintainer who forgets to bump it at a minor release, or bumps it past the window,
    gets told here rather than by a confused agent on an older release.
    """
    manifest = _load_manifest(host)
    raw_spec = manifest["compatible_package_version"]
    assert raw_spec, "compatible_package_version must not be empty (an empty SpecifierSet matches anything)"
    spec = SpecifierSet(raw_spec)  # type: ignore[misc]
    assert spec.contains(_deprecate_version, prereleases=True)
    assert not spec.contains("0.0.0", prereleases=True), f"{raw_spec!r} does not actually bound the floor"
    assert not spec.contains("0.1.0", prereleases=True), f"{raw_spec!r} does not actually bound the floor"
    floors = [Version(item.version) for item in spec if item.operator in (">=", "==", "~=")]  # type: ignore[misc]
    assert len(floors) == 1, f"{raw_spec!r} must declare exactly one lower bound"
    installed = Version(_deprecate_version)  # type: ignore[misc]
    oldest_supported = installed.minor - _SUPPORT_WINDOW_MINORS
    assert (floors[0].major, floors[0].minor) >= (installed.major, oldest_supported), (
        f"{raw_spec!r} trails the installed {installed} by more than {_SUPPORT_WINDOW_MINORS} minors — bump the floor"
    )


_SKILL_PATHS = (_PLUGIN / "skills" / "sunset" / "SKILL.md", _PLUGIN / "skills" / "prune" / "SKILL.md")
# Prose words, stdlib/foreign names, and the wrapper attribute `__deprecated__` the skill bodies also backtick —
# none of them is a deprecate.* module export.
_SKILL_SKIP_TOKENS = frozenset(
    {
        "__deprecated__",
        "DeprecationWarning",
        "FutureWarning",
        "convert",
        "keep",
        "warnings.warn",
        "pyDeprecate",
        "deprecate",
        "deprecate.__version__",
    }
)
_SKILL_CALLABLES = {
    fn.__name__: fn
    for fn in (
        deprecated,
        deprecated_class,
        deprecated_instance,
        find_deprecation_wrappers,
        validate_deprecation_expiry,
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
