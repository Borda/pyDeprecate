"""Check that both agent hosts receive the same self-contained plugin skills."""

import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import deprecate
from deprecate import TargetMode, deprecated, deprecated_class, deprecated_instance, validate_deprecation_expiry
from tests import collection_deprecate

_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN = _ROOT / "plugins" / "pydeprecate"
_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None

try:
    from packaging.specifiers import SpecifierSet
except ImportError:  # pragma: no cover - guarded by _PACKAGING_AVAILABLE skipif below
    SpecifierSet = None  # type: ignore[assignment,misc]


def _load_manifest(host: str) -> dict[str, Any]:
    """Load the plugin manifest for an agent host."""
    return json.loads((_PLUGIN / f".{host}-plugin/plugin.json").read_text())


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
    catalog = json.loads((_ROOT / catalog_path).read_text())
    assert catalog["name"] == "pydeprecate"
    (entry,) = catalog["plugins"]
    source = entry["source"]["path"] if host == "codex" else entry["source"]
    assert (_ROOT / source).resolve() == _PLUGIN
    manifest = _load_manifest(host)
    assert manifest["name"] == entry["name"] == "pydeprecate"
    skills = (_PLUGIN / manifest["skills"]).resolve()
    assert skills == _PLUGIN / "skills"
    assert {path.name for path in skills.iterdir() if path.is_dir() and not path.name.startswith(".")} == {
        "deprecate",
        "remove",
    }
    for name in ("deprecate", "remove"):
        skill_path = skills / name / "SKILL.md"
        content = skill_path.read_text()
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
    top_level = {child.name for child in _PLUGIN.iterdir()}
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

    codex_entry = json.loads((_ROOT / ".agents" / "plugins" / "marketplace.json").read_text())["plugins"][0]
    claude_entry = json.loads((_ROOT / ".claude-plugin" / "marketplace.json").read_text())["plugins"][0]
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
    the plugin's own ``version``, so an agent installing this plugin against an incompatible
    package gets a clear signal instead of SKILL.md instructions that reference behavior the
    loaded package doesn't have. ``prereleases=True`` is required here: this repo's own
    installed version is a ``.dev`` build, and a bare floor like ``>=0.12.0`` excludes
    prereleases by default under PEP 440.
    """
    manifest = _load_manifest(host)
    spec = SpecifierSet(manifest["compatible_package_version"])  # type: ignore[misc]
    assert spec.contains(deprecate.__version__, prereleases=True)


def test_skill_docs_reference_real_api() -> None:
    """Guard against stale API references in the agent-facing SKILL.md docs.

    This repository has renamed public API twice in its last five commits
    (``Deprecated`` -> ``DeprecationProxy``, the ``__deprecated__`` split).
    Both SKILL.md files hardcode ~14 ``deprecate.*`` identifiers as instructions
    for an AI agent to follow; a future rename could silently break those
    instructions while this plugin's own test suite stays green, since nothing
    ties the skill prose back to the installed package today.
    """
    accepted_kwargs: set[str] = set()
    for fn in (deprecated, deprecated_class, deprecated_instance):
        accepted_kwargs |= set(inspect.signature(fn).parameters)
    module_exports = set(deprecate.__all__)
    enum_members = set(TargetMode.__members__)

    # Prose words and stdlib/foreign names the skill bodies also backtick — not deprecate.* API surface.
    skip_tokens = {
        "DeprecationWarning",
        "FutureWarning",
        "convert",
        "keep",
        "warnings.warn",
        "pyDeprecate",
        "deprecate",
        "deprecate.__version__",
    }

    skill_paths = [_PLUGIN / "skills" / "deprecate" / "SKILL.md", _PLUGIN / "skills" / "remove" / "SKILL.md"]
    unresolved = [
        f"{skill_path.name}:{token}"
        for skill_path in skill_paths
        for token in re.findall(r"`([A-Za-z_][A-Za-z0-9_.]*)`", skill_path.read_text())
        if token not in skip_tokens
        and token.rsplit(".", 1)[-1] not in module_exports
        and token.rsplit(".", 1)[-1] not in enum_members
        and token.rsplit(".", 1)[-1] not in accepted_kwargs
    ]

    assert not unresolved, f"SKILL.md references identifiers not found in the installed API: {unresolved}"


@pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
def test_cli_expiry_preserves_multi_digit_minor_version(tmp_path: Path) -> None:
    """A quoted-literal ``--version`` value must not be coerced to float by CLI parsing.

    skills/remove/SKILL.md warns that CLI argument parsing can coerce "0.10" to
    0.1 and that "shell quoting alone does not guarantee preservation" —
    confirmed: Fire infers a bare ``--version 0.10`` as the float 0.1 before it
    ever reaches this package's own ``str(version)`` normalization. Passing the
    value as an embedded string literal (``'"0.10"'``) is the escape the skill
    alludes to; this exercises the actual ``python -m deprecate expiry`` entry
    point to confirm that escaped form reaches ``validate_deprecation_expiry``
    with "0.10" intact and yields the same expired verdict as the Python API.
    """
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from deprecate import deprecated\n\n\n"
        "def new_fn(x: int) -> int:\n    return x\n\n\n"
        '@deprecated(target=new_fn, deprecated_in="0.1", remove_in="0.2")\n'
        "def old_fn(x: int) -> int:\n    pass\n"
    )
    src_dir = str(_ROOT / "src")
    existing_pythonpath = os.environ.get("PYTHONPATH")
    pythonpath = f"{src_dir}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else src_dir
    env = {**os.environ, "PYTHONPATH": pythonpath}

    result = subprocess.run(
        [sys.executable, "-m", "deprecate", "expiry", str(pkg), "--version", '"0.10"'],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )

    # returncode alone doesn't discriminate this from an unrelated malformed-version ValueError
    # (both exit 1) — pin the actual expiry message so a regression can't hide behind a coincident exit code.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "expired wrapper" in result.stdout, result.stdout + result.stderr


@pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
@pytest.mark.parametrize(
    "current_version",
    [pytest.param("not-a-version", id="non-numeric"), pytest.param("", id="empty-string")],
)
def test_validate_expiry_rejects_malformed_version(current_version: str) -> None:
    """A malformed ``current_version`` fails fast instead of silently misjudging expiry.

    The removal skill instructs an agent to pass an arbitrary target-release
    string straight into ``validate_deprecation_expiry``; garbage input must
    raise clearly, not be silently treated as "not expired" or fail elsewhere.
    """
    with pytest.raises(ValueError, match="Invalid current_version"):
        validate_deprecation_expiry(collection_deprecate, current_version=current_version, recursive=False)
