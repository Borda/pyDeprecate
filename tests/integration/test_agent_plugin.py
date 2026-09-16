"""Check that both agent hosts receive the same self-contained plugin skills."""

import json
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN = _ROOT / "plugins" / "pydeprecate"


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
    shared_keys = ("name", "version", "description", "author", "homepage", "repository", "license", "skills")
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
