"""Pre-commit guard for the coding-agent plugin's ``compatible_package_version`` floor.

Self-contained on purpose: pre-commit.ci has no project venv, so this script never imports
``deprecate`` or ``packaging``. It regex-parses the installed ``__version__`` from
``src/deprecate/__about__.py`` and checks that:

- both host manifests declare the same ``>=X.Y`` floor,
- the floor is at most ``SUPPORT_WINDOW_MINORS`` minors behind the package version and never ahead of it,
- every ``SKILL.md`` mirrors the same floor in its "Verified against pyDeprecate ..." sentence.

This is a maintainer policy check rather than a package failure mode, so it lives here instead of the
pytest suite: it fails the "rolling to next X.Y.dev" commit locally instead of adding noise to CI.

"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ABOUT = ROOT / "src" / "deprecate" / "__about__.py"
PLUGIN = ROOT / "plugins" / "pydeprecate"
MANIFESTS = (PLUGIN / ".claude-plugin" / "plugin.json", PLUGIN / ".codex-plugin" / "plugin.json")
SKILLS = sorted(PLUGIN.glob("skills/*/SKILL.md"))
SUPPORT_WINDOW_MINORS = 3

_VERSION_RE = re.compile(r'^__version__\s*=\s*"(\d+)\.(\d+)', re.MULTILINE)
_FLOOR_RE = re.compile(r"^>=(\d+)\.(\d+)$")
_SKILL_RE = re.compile(r"Verified against pyDeprecate `([^`]+)`")


def _package_minor() -> tuple[int, int]:
    match = _VERSION_RE.search(ABOUT.read_text(encoding="utf-8"))
    if match is None:
        sys.exit(f'{ABOUT}: cannot find `__version__ = "X.Y..."`')
    return int(match.group(1)), int(match.group(2))


def _manifest_floor(path: Path) -> tuple[str, tuple[int, int]]:
    spec = json.loads(path.read_text(encoding="utf-8")).get("compatible_package_version", "")
    match = _FLOOR_RE.match(spec)
    if match is None:
        sys.exit(f"{path}: compatible_package_version must be a bare `>=X.Y` floor, got {spec!r}")
    return spec, (int(match.group(1)), int(match.group(2)))


def main() -> int:
    """Run all floor checks and return a process exit code."""
    errors: list[str] = []
    installed = _package_minor()
    oldest_supported = (installed[0], installed[1] - SUPPORT_WINDOW_MINORS)

    specs = {path: _manifest_floor(path) for path in MANIFESTS}
    if len({spec for spec, _ in specs.values()}) != 1:
        errors.append("manifests disagree: " + ", ".join(f"{p.relative_to(ROOT)}={s!r}" for p, (s, _) in specs.items()))
    for path, (spec, floor) in specs.items():
        rel = path.relative_to(ROOT)
        if floor > installed:
            errors.append(f"{rel}: {spec!r} is ahead of the package version {installed[0]}.{installed[1]}")
        if floor < oldest_supported:
            errors.append(
                f"{rel}: {spec!r} trails the package version {installed[0]}.{installed[1]} by more than "
                f"{SUPPORT_WINDOW_MINORS} minors — bump the floor to >={oldest_supported[0]}.{oldest_supported[1]}"
            )

    expected = next(iter(specs.values()))[0]
    for skill in SKILLS:
        match = _SKILL_RE.search(skill.read_text(encoding="utf-8"))
        rel = skill.relative_to(ROOT)
        if match is None:
            errors.append(f"{rel}: missing the `Verified against pyDeprecate \\`>=X.Y\\`` sentence")
        elif match.group(1) != expected:
            errors.append(f"{rel}: says {match.group(1)!r} but manifests declare {expected!r}")

    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
