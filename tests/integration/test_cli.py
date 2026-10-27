"""Integration tests for the CLI — real subprocess invocations only."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

from tests._helpers import write_pkg

_SRC_DIR = str(Path(__file__).resolve().parent.parent.parent / "src")
_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None
_MYPKG_INIT = """\
from deprecate import deprecated


def new_fn(x: int) -> int:
    return x


@deprecated(target=new_fn, deprecated_in="1.0", remove_in="9.0", args_mapping={"old": "x"})
def old_fn(old: int) -> int:
    pass
"""

# Package with an invalid args_mapping (the mapped key is not a parameter of old_fn).
# cmd_check exits 1 for this package without --exit-zero.
_MYPKG_INIT_INVALID = """\
from deprecate import deprecated


def new_fn(x: int) -> int:
    return x


@deprecated(target=new_fn, deprecated_in="1.0", remove_in="9.0", args_mapping={"nonexistent": "x"})
def old_fn(old: int) -> int:
    pass
"""

# Package whose remove_in="0.2" discriminates the PEP 440 string "0.10" from the float 0.1 that the old Fire
# CLI parsing coerced an unquoted --version 0.10 into (0.1 < 0.2, so it would wrongly read as not expired).
_MYPKG_INIT_MULTI_DIGIT_MINOR = """\
from deprecate import deprecated


def new_fn(x: int) -> int:
    return x


@deprecated(target=new_fn, deprecated_in="0.1", remove_in="0.2")
def old_fn(x: int) -> int:
    pass
"""


# Package with a notify-only wrapper that also declares an args_mapping, which that mode ignores (misconfigured).
_MYPKG_INIT_NOTIFY_MAPPING = """\
from deprecate import TargetMode, deprecated


@deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="9.0", args_mapping={"old": "x"})
def old_fn(x: int = 0) -> int:
    return x
"""


# Package whose wrapper is deprecated and removed in the very same release — trips the ``min-grace`` rule.
_MYPKG_INIT_AGGRESSIVE = """\
from deprecate import deprecated


def new_fn(x: int) -> int:
    return x


@deprecated(target=new_fn, deprecated_in="1.0", remove_in="1.0", args_mapping={"old": "x"})
def old_fn(old: int) -> int:
    pass
"""


def _cli_env(**extra: str) -> dict[str, str]:
    """Build env dict with PYTHONPATH pointing at src/ so subprocess can find deprecate."""
    existing_pythonpath = os.environ.get("PYTHONPATH")
    pythonpath = f"{_SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else _SRC_DIR
    return {**os.environ, "PYTHONPATH": pythonpath, **extra}


def _make_pkg(tmp_path: Path, name: str = "mypkg", content: str = _MYPKG_INIT) -> Path:
    """Create a minimal importable package with one deprecated wrapper."""
    return write_pkg(tmp_path, name, content)


def _run_cli(
    *args: str, env: Optional[dict[str, str]] = None, cwd: Optional[Path] = None
) -> subprocess.CompletedProcess[str]:
    """Run 'python -m deprecate <args>' as a real subprocess, always decoding output as UTF-8.

    Without an explicit ``encoding``, ``text=True`` falls back to the platform's default locale
    encoding (``cp1252`` on Windows), which cannot decode the UTF-8 bytes this CLI always writes
    (``_ensure_utf8_streams`` reconfigures its own stdout/stderr to UTF-8 regardless of console
    codepage) — corrupting ``result.stdout``/``result.stderr`` or raising ``UnicodeDecodeError``
    inside the subprocess pipe-reader thread before ``subprocess.run`` ever returns.
    """
    return subprocess.run(
        [sys.executable, "-m", "deprecate", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env if env is not None else _cli_env(),
        cwd=cwd,
    )


@pytest.mark.subprocess
class TestCliInvocation:
    """Tests for real CLI invocations via subprocess."""

    def test_no_args_shows_help(self) -> None:
        """CLI with no arguments prints help and exits 0 (Fire shows component help)."""
        result = _run_cli()
        assert result.returncode == 0
        assert "check" in (result.stdout + result.stderr).lower()

    def test_help(self) -> None:
        """CLI --help exits 0 and lists subcommands."""
        result = _run_cli("--help")
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "check" in combined.lower()

    @pytest.mark.parametrize("subcommand", ["check", "expiry", "policy", "chains", "all", "status"])
    def test_nonexistent_module(self, subcommand: str) -> None:
        """A name that is neither an existing path nor an importable module exits 2 on every subcommand.

        A typo in the argument is a usage error, which a CI script can tell from findings (1) and from a package that
        exists but fails to import (3).

        """
        result = _run_cli(subcommand, "nonexistent_module_xyz")
        assert result.returncode == 2, result.stdout + result.stderr
        assert "No such path or importable module: 'nonexistent_module_xyz'" in result.stderr

    def test_package_failing_to_import_exits_three(self, tmp_path: Path) -> None:
        """A package whose ``__init__`` raises exits 3 with a scan error and writes no report.

        Nothing could be audited, so the run is neither clean nor a finding; a report would publish a table of a scan
        that never happened.

        """
        pkg = _make_pkg(tmp_path, name="brokenpkg", content="raise RuntimeError('broken on import')\n")
        report = tmp_path / "report.json"
        result = _run_cli("check", str(pkg), "--exit-zero", f"--report-json={report}", cwd=tmp_path)
        assert result.returncode == 3, result.stdout + result.stderr
        assert "pydeprecate: scan error: could not import" in result.stderr
        assert "RuntimeError: broken on import" in result.stderr
        assert not report.exists()

    @pytest.mark.parametrize("subcommand", ["check", "expiry", "status", "all"])
    def test_package_exiting_on_import_exits_three(self, tmp_path: Path, subcommand: str) -> None:
        """A package whose module-level code calls ``sys.exit(0)`` exits 3 on every subcommand and writes no report.

        ``expiry``, ``status`` and ``all`` import the package once more to auto-detect its version; that import let the
        package's own exit code through, so ``sys.exit(0)`` passed the gate silently with nothing audited.

        """
        pkg = _make_pkg(tmp_path, name="exitpkg", content="import sys\nsys.exit(0)\n")
        report = tmp_path / "report.json"
        result = _run_cli(subcommand, str(pkg), f"--report-json={report}", cwd=tmp_path)
        assert result.returncode == 3, result.stdout + result.stderr
        assert "pydeprecate: scan error: importing" in result.stderr
        assert "ran module-level code that exited (0)" in result.stderr
        assert not report.exists()

    def test_submodule_exiting_on_import_is_a_gap(self, tmp_path: Path) -> None:
        """A submodule that calls ``sys.exit()`` on import is a ``module not audited`` gap, not a failed run.

        An unguarded ``__main__.py`` (``sys.exit(main())``) is a common layout; the run exited 3 naming the root
        package, which the user could neither mask nor tell which module to ``--exclude``.

        """
        pkg = _make_pkg(tmp_path)
        (pkg / "__main__.py").write_text("import sys\nsys.exit(4)\n", encoding="utf-8")
        result = _run_cli("check", str(pkg), cwd=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "pydeprecate gap: module not audited: mypkg.__main__" in result.stderr

    @pytest.mark.parametrize("path", ["./typo_dir", "../typo_dir", ".typo"])
    def test_relative_typo_exits_two(self, tmp_path: Path, path: str) -> None:
        """A relative spelling that names no existing path is a usage error, never a scan error.

        ``./typo_dir`` was handed to the importer, which failed with a ``TypeError`` about relative imports and exited
        3, as if an existing package had failed to import.

        """
        result = _run_cli("check", path, cwd=tmp_path)
        assert result.returncode == 2, result.stdout + result.stderr
        assert f"No such path or importable module: {path!r}" in result.stderr

    def test_exclude_list_literal_exits_two(self, tmp_path: Path) -> None:
        """A list literal in ``--exclude`` is refused instead of becoming one pattern that matches nothing.

        Fire parsed ``--exclude='["pkg.tests"]'`` into a list; as a plain string it excluded nothing, so the package
        meant to be left out was imported and audited without a word.

        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli("check", str(pkg), '--exclude=["mypkg.tests"]', cwd=tmp_path)
        assert result.returncode == 2, result.stdout + result.stderr
        assert "Invalid `--exclude` value" in result.stderr

    def test_exclude_character_class_glob_is_a_pattern(self, tmp_path: Path) -> None:
        """An ``--exclude`` glob that starts with a character class excludes its modules instead of exiting 2.

        ``fnmatch`` reads ``[m]ypkg.broken`` as a pattern matching ``mypkg.broken``; the list-literal guard treated
        every value starting with ``[`` as a list and refused it, so a valid glob could not be passed on the command
        line. The submodule holds an invalid wrapper, so it is absent from the scan only when the pattern applies.

        """
        pkg = _make_pkg(tmp_path)
        (pkg / "broken.py").write_text(_MYPKG_INIT_INVALID)
        result = _run_cli("check", str(pkg), "--exclude=[m]ypkg.broken", cwd=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Exclude: [m]ypkg.broken (flag)" in result.stdout

    def test_all_rejects_malformed_policy_before_importing(self, tmp_path: Path) -> None:
        """``all`` reports a malformed ``[tool.pydeprecate.policy]`` value as exit 2 before importing the package.

        The rule values were resolved only in the policy pass after the scan, so the package's module-level code ran and
        the check and expiry results printed before the run exited 2.

        """
        pkg = _make_pkg(tmp_path, content='print("MODULE CODE RAN")\n' + _MYPKG_INIT)
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pydeprecate.policy]\nmessage-required = "maybe"\n', encoding="utf-8"
        )
        result = _run_cli("all", str(pkg), "--version=1.0", cwd=tmp_path)
        assert result.returncode == 2, result.stdout + result.stderr
        assert "Invalid `message_required` value" in result.stderr
        assert "MODULE CODE RAN" not in result.stdout

    @pytest.mark.parametrize(
        ("subcommand", "extra"),
        [pytest.param("check", (), id="check"), pytest.param("all", ("--version=1.0",), id="all")],
    )
    def test_misconfigured_wrapper_fails_the_gate(
        self, tmp_path: Path, subcommand: str, extra: tuple[str, ...]
    ) -> None:
        """A NOTIFY wrapper with an ``args_mapping`` fails ``check`` and ``all`` with a misconfigured-target section.

        The notify-only mode ignores the mapping, so callers of the old argument name are never migrated; the gate
        reported "All deprecated wrappers look correct!" and exited 0.

        """
        pkg = _make_pkg(tmp_path, content=_MYPKG_INIT_NOTIFY_MAPPING)
        result = _run_cli(subcommand, str(pkg), *extra, cwd=tmp_path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "[ERROR] Found deprecated wrappers with a misconfigured target:" in result.stdout
        assert "old_fn: `TargetMode.NOTIFY` with `args_mapping`" in result.stdout


@pytest.mark.subprocess
class TestCliSubcommands:
    """Integration tests for the four CLI subcommands via subprocess."""

    def test_check_subcommand_explicit(self, tmp_path: Path) -> None:
        """'pydeprecate check <path>' scans and exits 0 for a clean package."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli("check", str(pkg), cwd=tmp_path)
        assert result.returncode == 0
        assert "Scanning:" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_expiry_subcommand_no_expired(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <path> --version 1.0' exits 0 when nothing is expired."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli("expiry", str(pkg), "--version", "1.0", cwd=tmp_path)
        assert result.returncode == 0
        assert "No expired" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_expiry_subcommand_expired(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <path> --version 9.0' exits 1 when wrapper is past remove_in."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli("expiry", str(pkg), "--version", "9.0", cwd=tmp_path)
        assert result.returncode == 1

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    @pytest.mark.parametrize("subcommand", ["expiry", "all", "status"])
    def test_dotted_distribution_version(self, tmp_path: Path, subcommand: str) -> None:
        """Use a dotted component's distribution version instead of its top-level package's release.

        Independently released namespace components can share an import prefix while publishing different
        distribution versions. A component at 3.0 must report its wrapper due at 2.0 as expired even when the
        top-level distribution is only at 1.0; both the gate and its recorded status table must use 3.0.
        """
        pkg = _make_pkg(tmp_path, name="versioned_namespace", content="")
        (pkg / "component.py").write_text(_MYPKG_INIT.replace('remove_in="9.0"', 'remove_in="2.0"'), encoding="utf-8")
        for name, version, source in [
            ("versioned_namespace", "1.0", "versioned_namespace/__init__.py"),
            ("versioned_namespace_component", "3.0", "versioned_namespace/component.py"),
        ]:
            info = tmp_path / f"{name}-{version}.dist-info"
            info.mkdir()
            (info / "METADATA").write_text(
                f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8"
            )
            (info / "top_level.txt").write_text("versioned_namespace\n", encoding="utf-8")
            (info / "RECORD").write_text(f"{source},,\n", encoding="utf-8")
        report = tmp_path / "report.json"
        result = _run_cli(subcommand, "versioned_namespace.component", f"--report-json={report}", cwd=tmp_path)

        assert result.returncode == (0 if subcommand == "status" else 1), result.stdout + result.stderr
        assert "Version: 3.0 (auto-detected)" in result.stdout
        data = json.loads(report.read_text(encoding="utf-8"))
        assert data["gaps"] == []
        assert "Current version: 3.0" in data["table"]
        assert "Past Removal Date" in data["table"]

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    @pytest.mark.parametrize("subcommand", ["expiry", "all"])
    @pytest.mark.parametrize("implicit_path", [False, True])
    @pytest.mark.parametrize("dynamic_version", [False, True])
    def test_expiry_gates_fall_back_to_package_version(
        self, tmp_path: Path, subcommand: str, implicit_path: bool, dynamic_version: bool
    ) -> None:
        """Both expiry gates enforce removal deadlines using an uninstalled package's ``__version__``.

        A local checkout may have no project metadata, or a dynamic distribution name that differs from its
        import name. An explicit package path from another directory and a hook invocation without a path must
        both detect an overdue wrapper, and the JSON report must agree with the gate rather than record an
        unresolved version. ``all`` is the Action's default subcommand and the ``pydeprecate-all`` hook runs in an
        isolated environment where the scanned package is not installed, so it must resolve the version exactly
        like standalone ``expiry`` — otherwise it reports a gap and exits 0 on code that ``expiry`` fails.
        """
        pkg = _make_pkg(tmp_path, content='__version__ = "2.0"\n' + _MYPKG_INIT_AGGRESSIVE)
        if dynamic_version:
            (tmp_path / "pyproject.toml").write_text(
                '[project]\nname = "uninstalled-cli-fixture-dist"\ndynamic = ["version"]\n', encoding="utf-8"
            )
        report = tmp_path / "report.json"
        args = () if implicit_path else (str(pkg),)
        result = _run_cli(
            subcommand, *args, f"--report-json={report}", cwd=tmp_path if implicit_path else tmp_path.parent
        )

        assert result.returncode == 1, result.stdout + result.stderr
        assert "Version: 2.0 (auto-detected)" in result.stdout
        assert "1 expired wrapper(s) found." in result.stdout
        assert "version not resolved" not in result.stderr
        data = json.loads(report.read_text(encoding="utf-8"))
        assert data["gaps"] == []
        assert "Current version: 2.0" in data["table"]
        assert "Past Removal Date" in data["table"]

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_status_header_reports_package_version(self, tmp_path: Path) -> None:
        """``status`` names the same ``__version__``-derived version in its header that its table compares against.

        An uninstalled checkout with only ``__version__`` is rendered by ``status`` with the overdue wrapper marked
        past its removal date, so the header must show the version that verdict was made against instead of leaving
        it out as if the version were unknown.
        """
        pkg = _make_pkg(tmp_path, content='__version__ = "2.0"\n' + _MYPKG_INIT_AGGRESSIVE)
        result = _run_cli("status", str(pkg), env=_cli_env(COLUMNS="200"), cwd=tmp_path.parent)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "Version: 2.0 (auto-detected)" in result.stdout
        assert "Past Removal Date" in result.stdout

    @pytest.mark.parametrize(
        "version_line",
        [
            pytest.param("", id="no-version-attr"),
            pytest.param("__version__ = (1, 2)\n", id="non-string-version-attr"),
        ],
    )
    def test_expiry_unresolved_package_version_remains_advisory(self, tmp_path: Path, version_line: str) -> None:
        """Standalone expiry reports one advisory gap when no version source yields a version string.

        An uninstalled checkout without metadata cannot supply a removal comparison when it has no ``__version__``,
        or only a non-string one such as a tuple. Keeping that case advisory (instead of crashing on the tuple) must
        not hide the fallback when a package does publish a version string.
        """
        pkg = _make_pkg(tmp_path, content=version_line + _MYPKG_INIT_AGGRESSIVE)
        report = tmp_path / "report.json"
        result = _run_cli("expiry", str(pkg), f"--report-json={report}", cwd=tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stderr.count("pydeprecate gap: version not resolved: ") == 1
        data = json.loads(report.read_text(encoding="utf-8"))
        assert [gap["kind"] for gap in data["gaps"]] == ["version not resolved"]

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    @pytest.mark.parametrize("subcommand", ["expiry", "all"])
    @pytest.mark.parametrize(
        "version_args",
        [
            pytest.param(("--version", "0.10"), id="flag"),
            pytest.param(("--version=0.10",), id="attached"),
            pytest.param(("0.10",), id="positional"),
        ],
    )
    def test_expiry_gates_keep_multi_digit_minor_version(
        self, tmp_path: Path, subcommand: str, version_args: tuple[str, ...]
    ) -> None:
        """``--version 0.10`` gates against release 0.10, never against 0.1.

        The Fire-based CLI coerced an unquoted ``0.10`` to the float ``0.1``. A wrapper with ``remove_in="0.2"`` is
        overdue at 0.10 but not at 0.1, so a coerced version silently passed the gate and CI went green on code that
        should have been deleted. Option values now stay strings in every spelling users and the Action ``args``
        input type, so the wrapper must be reported as expired and the version printed unchanged.
        """
        pkg = _make_pkg(tmp_path, content=_MYPKG_INIT_MULTI_DIGIT_MINOR)
        result = _run_cli(subcommand, str(pkg), *version_args, cwd=tmp_path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Version: 0.10 (user-provided)" in result.stdout, result.stdout + result.stderr
        assert "1 expired wrapper(s) found." in result.stdout, result.stdout + result.stderr

    def test_chains_subcommand_no_chains(self, tmp_path: Path) -> None:
        """'pydeprecate chains <path>' exits 0 for a package with no deprecation chains."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli("chains", str(pkg), cwd=tmp_path)
        assert result.returncode == 0
        assert "No deprecation chains" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_all_subcommand_clean(self, tmp_path: Path) -> None:
        """'pydeprecate all <path> --version 1.0' exits 0 when all checks pass."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli("all", str(pkg), "--version", "1.0", cwd=tmp_path)
        assert result.returncode == 0

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_status_subcommand_exits_0(self, tmp_path: Path) -> None:
        """'pydeprecate status <path> --version 1.0' exits 0 and prints a markdown table."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli("status", str(pkg), "--version", "1.0", cwd=tmp_path)
        assert result.returncode == 0
        assert "Original API" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_expiry_broken_user_import_propagates(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <path> --version 9.0' exits non-zero when the scanned package has a broken import.

        When ``validate_deprecation_expiry`` imports the user's module and the module raises an ImportError
        (e.g. ``from _nonexistent_module_xyz_ import something``), the CLI must not silently swallow that
        error and exit 0 with a misleading "Could not determine version" message.  The real broken-import
        error must reach the user so they can fix their package, not get confused about version detection.

        """
        broken_pkg = tmp_path / "broken_pkg"
        broken_pkg.mkdir()
        (broken_pkg / "__init__.py").write_text("from _nonexistent_module_xyz_ import something\n")
        result = _run_cli("expiry", str(broken_pkg), "--version", "9.0", cwd=tmp_path)
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "_nonexistent_module_xyz_" in combined
        assert "Could not determine the current package version" not in combined

    def test_all_plain_directory_exits_0(self, tmp_path: Path) -> None:
        """'pydeprecate all <plaindir>' exits 0 when every check passes on a plain dir without __init__.py.

        cmd_check already scans plain directories; cmd_all appends a status table afterwards. Resolving the
        module name for that advisory table must not turn a clean run into exit 1 on a directory that has no
        importable package — the table is a display artifact, not a pass/fail gate.
        """
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "mod.py").write_text("x = 1\n")
        result = _run_cli("all", str(plain), cwd=tmp_path)
        assert result.returncode == 0

    def test_status_plain_directory_exits_0(self, tmp_path: Path) -> None:
        """'pydeprecate status <plaindir>' exits 0 on a plain dir; status generation is never a pass/fail gate."""
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "mod.py").write_text("x = 1\n")
        result = _run_cli("status", str(plain), cwd=tmp_path)
        assert result.returncode == 0

    def test_check_plain_directory_warns_nested_files_on_stderr(self, tmp_path: Path) -> None:
        """'pydeprecate check <plaindir>' warns on stderr when the directory contains nested .py files.

        Plain directories are scanned one level deep only. When nested ``.py`` files are present in
        sub-directories they are silently skipped, but users must be informed via stderr so they know
        to switch to an importable package layout if they want full recursive coverage.

        """
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "mod.py").write_text("x = 1\n")
        sub = plain / "sub"
        sub.mkdir()
        (sub / "nested.py").write_text("y = 2\n")
        result = _run_cli("check", str(plain), cwd=tmp_path)
        assert result.returncode == 0
        assert "Skipping nested Python files" in result.stderr

    def test_help_lists_subcommands(self) -> None:
        """'pydeprecate --help' output includes the six subcommand names."""
        result = _run_cli("--help")
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        for name in ("check", "expiry", "policy", "chains", "all", "status"):
            assert name in combined, f"subcommand '{name}' missing from --help output"

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_policy_subcommand_clean_package(self, tmp_path: Path) -> None:
        """'pydeprecate policy <path>' exits 0 for a package whose wrapper respects the default policy.

        The fixture package deprecates in `1.0` with a forwarding target and schedules removal at the `9.0`
        major — the disciplined shape the default rules are written to wave through without any flags.
        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli("policy", str(pkg), cwd=tmp_path)
        assert result.returncode == 0
        assert "No deprecation policy violations" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_policy_subcommand_reports_violation(self, tmp_path: Path) -> None:
        """'pydeprecate policy <path>' exits 1 and names the broken rule for an aggressive removal schedule.

        The package deprecates and removes inside the same `1.x` line, which is the schedule a reviewer is
        meant to catch before release: callers get no version they can upgrade through.
        """
        pkg = _make_pkg(tmp_path, name="aggressivepkg", content=_MYPKG_INIT_AGGRESSIVE)
        result = _run_cli("policy", str(pkg), cwd=tmp_path)
        assert result.returncode == 1
        assert "min-grace" in (result.stdout or ""), result

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_policy_subcommand_rule_can_be_disabled(self, tmp_path: Path) -> None:
        """'--min-grace=None' drops the grace-window rule so a same-line removal passes the gate.

        A project that ships removals inside a release line still wants the remaining rules; without a working
        opt-out flag the whole subcommand would be unusable for it.
        """
        pkg = _make_pkg(tmp_path, name="aggressivepkg2", content=_MYPKG_INIT_AGGRESSIVE)
        result = _run_cli("policy", str(pkg), "--min-grace=None", cwd=tmp_path)
        assert result.returncode == 0

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    @pytest.mark.parametrize(
        "flag",
        [
            pytest.param("--min-grace=0.2", id="float-minor-delta"),
            pytest.param("--min-grace=1.0", id="float-major-delta"),
        ],
    )
    def test_policy_subcommand_accepts_numeric_grace_delta(self, flag: str, tmp_path: Path) -> None:
        """An unquoted numeric '--min-grace' reaches the rule as a float and still enforces the window.

        Fire converts ``0.2`` and ``1.0`` on the command line into floats before the subcommand sees them; the
        grace rule must accept those as the delta they spell instead of rejecting them as malformed (exit 2) or
        silently skipping the rule (exit 0).
        """
        pkg = _make_pkg(tmp_path, name="aggressivepkg3", content=_MYPKG_INIT_AGGRESSIVE)
        result = _run_cli("policy", str(pkg), flag, cwd=tmp_path)
        assert result.returncode == 1
        assert "min-grace" in (result.stdout or ""), result

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_policy_subcommand_reads_pyproject_table(self, tmp_path: Path) -> None:
        """'pydeprecate policy <path>' applies ``[tool.pydeprecate.policy]`` from the project's ``pyproject.toml``.

        The aggressive package fails the built-in window; with the project declaring ``min-grace = false`` next
        to it, the same bare invocation passes and the header attributes the setting to ``pyproject.toml`` —
        the shape a repository uses so CI and every developer run the one policy without repeating flags.
        """
        pkg = _make_pkg(tmp_path, name="aggressivepkg4", content=_MYPKG_INIT_AGGRESSIVE)
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmin-grace = false\n")
        result = _run_cli("policy", str(pkg), cwd=tmp_path)
        assert result.returncode == 0, result
        assert "min-grace=None (pyproject.toml)" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_policy_flag_overrides_pyproject_table(self, tmp_path: Path) -> None:
        """A typed '--min-grace' beats the value ``pyproject.toml`` declares for the same rule.

        The project disabled the window in its config; a maintainer re-enabling it for one run from the command
        line must see the violation, otherwise the flag would silently lose to the file.
        """
        pkg = _make_pkg(tmp_path, name="aggressivepkg5", content=_MYPKG_INIT_AGGRESSIVE)
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmin-grace = false\n")
        result = _run_cli("policy", str(pkg), "--min-grace=0.1", cwd=tmp_path)
        assert result.returncode == 1, result
        assert "min-grace=0.1 (flag)" in result.stdout

    def test_policy_malformed_pyproject_value_exits_two(self, tmp_path: Path) -> None:
        """A malformed ``min-grace`` in ``pyproject.toml`` exits 2 and names the file, before any scan.

        A usage error sourced from the file must point the reader at the file, not at a flag they never typed.
        """
        pkg = _make_pkg(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate.policy]\nmin-grace = "bogus"\n')
        result = _run_cli("policy", str(pkg), cwd=tmp_path)
        assert result.returncode == 2, result
        assert "pyproject.toml" in result.stderr

    def test_exclude_from_pyproject_skips_package(self, tmp_path: Path) -> None:
        """'pydeprecate check <path>' leaves out the packages listed under ``exclude`` in ``pyproject.toml``.

        The fixture package carries an invalid mapping inside ``mypkg.tests``; with the project excluding that
        subtree the same scan is clean and exits 0, and the header shows the exclusion came from the file.
        """
        pkg = _make_pkg(tmp_path, content="")
        (pkg / "tests").mkdir()
        (pkg / "tests" / "__init__.py").write_text(_MYPKG_INIT_INVALID)
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate]\nexclude = ["mypkg.tests"]\n')
        result = _run_cli("check", str(pkg), cwd=tmp_path)
        assert result.returncode == 0, result
        assert "Exclude: mypkg.tests (pyproject.toml)" in result.stdout
        assert "No deprecated callables found" in result.stdout

    def test_exclude_flag_overrides_pyproject(self, tmp_path: Path) -> None:
        """A typed '--exclude' replaces the file's list, so the invalid mapping is found again.

        Overriding with a pattern that matches nothing brings the excluded subtree back into the scan.
        """
        pkg = _make_pkg(tmp_path, content="")
        (pkg / "tests").mkdir()
        (pkg / "tests" / "__init__.py").write_text(_MYPKG_INIT_INVALID)
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate]\nexclude = ["mypkg.tests"]\n')
        result = _run_cli("check", str(pkg), "--exclude=mypkg.nothing", cwd=tmp_path)
        assert result.returncode == 1, result
        assert "Exclude: mypkg.nothing (flag)" in result.stdout

    def test_all_subcommand_reads_pyproject_table(self, tmp_path: Path) -> None:
        """'pydeprecate all <path>' runs its advisory policy pass with the ``pyproject.toml`` settings.

        ``all`` never fails on policy, but its printed advisory must reflect the project's declared rules,
        otherwise the summary a developer reads locally disagrees with the dedicated ``policy`` gate in CI.
        """
        pkg = _make_pkg(tmp_path)
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmessage-required = false\n")
        result = _run_cli("all", str(pkg), cwd=tmp_path)
        assert result.returncode == 0, result
        assert "message-required=False (pyproject.toml)" in result.stdout

    def test_policy_subcommand_bare_major_exits_two(self, tmp_path: Path) -> None:
        """'--min-grace=1' is rejected as ambiguous with a hint to write '1.0'.

        Fire hands ``1`` over as an ``int``; one *what*? The gate refuses to guess and names the accepted
        spellings so the author writes ``1.0`` (or a unit table in ``pyproject.toml``) instead.
        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli("policy", str(pkg), "--min-grace=1", cwd=tmp_path)
        assert result.returncode == 2, result
        assert "1.0" in result.stderr

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_policy_pyproject_table_form_min_grace(self, tmp_path: Path) -> None:
        """``min-grace = { major = 1 }`` in ``pyproject.toml`` is read as a one-major window.

        The inline-table spelling names the unit outright, so a reader never has to decode ``"1.0"``; it arrives
        from the TOML parser as a dict and must enforce the same window as the dotted form.
        """
        pkg = _make_pkg(tmp_path, name="aggressivepkg6", content=_MYPKG_INIT_AGGRESSIVE)
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmin-grace = { major = 1 }\n")
        result = _run_cli("policy", str(pkg), cwd=tmp_path)
        assert result.returncode == 1, result
        assert "min-grace={'major': 1} (pyproject.toml)" in result.stdout
        assert "min-grace" in result.stdout

    def test_policy_subcommand_invalid_min_grace_exits_two(self, tmp_path: Path) -> None:
        """'--min-grace=bogus' exits 2 and names the accepted spellings instead of scanning anything.

        package scanning starts, so the user gets a usage error (exit 2) naming the `1.0` / `0.1` / `0.0.1`
        spellings rather than a scan failure, a stack trace, or a silently-ignored flag.
        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli("policy", str(pkg), "--min-grace=bogus", cwd=tmp_path)
        assert result.returncode == 2
        combined = result.stdout + result.stderr
        assert "min_grace" in combined
        assert "0.0.1" in combined

    def test_subcommand_help(self) -> None:
        """'pydeprecate expiry --help' shows expiry-specific options."""
        result = _run_cli("expiry", "--help")
        assert result.returncode == 0
        assert "version" in (result.stdout + result.stderr).lower()

    def test_check_no_recursive_flag(self, tmp_path: Path) -> None:
        """'pydeprecate check <path> --no-recursive' is accepted and exits 0."""
        pkg = _make_pkg(tmp_path)
        result = _run_cli("check", str(pkg), "--no-recursive", cwd=tmp_path)
        assert result.returncode == 0

    def test_legacy_negation_spelling_is_accepted_but_unlisted(self, tmp_path: Path) -> None:
        """The older ``--norecursive`` spelling still negates the flag, while ``--help`` lists only ``--no-recursive``.

        Command lines written for 0.14 and earlier use the ``--no<flag>`` form; they must keep working after the
        documented spelling became ``--no-<flag>``, without the help showing two names for one switch.

        """
        pkg = _make_pkg(tmp_path)
        (pkg / "sub.py").write_text(_MYPKG_INIT_INVALID)
        scan = _run_cli("check", "--norecursive", str(pkg), cwd=tmp_path)
        help_text = _run_cli("check", "--help").stdout
        assert scan.returncode == 0, scan.stdout + scan.stderr
        assert "--no-recursive" in help_text
        assert "--norecursive" not in help_text

    def test_check_exit_zero_dash_form(self, tmp_path: Path) -> None:
        """'--exit-zero' (dash form) forces exit 0 even when invalid args are found."""
        pkg = tmp_path / "badpkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(_MYPKG_INIT_INVALID)
        result = _run_cli("check", str(pkg), "--exit-zero", cwd=tmp_path)
        assert result.returncode == 0

    def test_check_exit_zero_underscore_form(self, tmp_path: Path) -> None:
        """Fire also accepts '--exit_zero' (underscore) as an alias for '--exit-zero'."""
        pkg = tmp_path / "badpkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(_MYPKG_INIT_INVALID)
        result = _run_cli("check", str(pkg), "--exit_zero", cwd=tmp_path)
        assert result.returncode == 0


@pytest.mark.subprocess
class TestCliPreCommitArgs:
    """Tests for argument orders a pre-commit hook produces (``entry`` + ``args``, no filenames)."""

    def test_exit_zero_before_path_scans_path(self, tmp_path: Path) -> None:
        """``check --exit-zero <pkg>`` scans ``<pkg>`` and reports its findings without failing.

        A ``pydeprecate-check`` hook config ``args: [--exit-zero, src/mypkg]`` puts the flag first. The run must
        scan the named package — the header names it and the invalid mapping is reported — instead of binding the
        path to ``--exit-zero`` and scanning the current directory with exit 0.

        """
        pkg = _make_pkg(tmp_path, content=_MYPKG_INIT_INVALID)
        result = _run_cli("check", "--exit-zero", str(pkg), env=_cli_env(COLUMNS="500"), cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert f"Scanning: {pkg}" in result.stdout
        assert "nonexistent" in result.stdout

    @pytest.mark.parametrize(
        ("flags", "exit_code", "submodule_reported"),
        [
            pytest.param(("--no-recursive",), 0, False, id="norecursive-skips-submodule"),
            pytest.param((), 1, True, id="default-scans-submodule"),
        ],
    )
    def test_norecursive_before_path_scans_path(
        self, tmp_path: Path, flags: tuple[str, ...], exit_code: int, submodule_reported: bool
    ) -> None:
        """``check --no-recursive <pkg>`` scans ``<pkg>`` top-level only instead of exiting 2.

        Fire reads the token after a bare negated flag as its value and then cannot consume it; the CLI rewrites
        the flag so the order of flags and path does not matter. The package root is valid while its submodule
        holds an invalid wrapper, so the flag is observable: the submodule finding is absent under
        ``--no-recursive`` and present (exit 1) on the default recursive scan.

        """
        pkg = _make_pkg(tmp_path)
        (pkg / "sub.py").write_text(_MYPKG_INIT_INVALID)
        result = _run_cli("check", *flags, str(pkg), env=_cli_env(COLUMNS="500"), cwd=tmp_path)
        assert result.returncode == exit_code, result.stderr
        assert f"Scanning: {pkg}" in result.stdout
        assert ("nonexistent" in result.stdout) is submodule_reported

    @pytest.mark.parametrize(
        ("args", "flag"),
        [
            pytest.param(("{a}", "{b}"), "one path per run", id="second-path-rejected-before-binding"),
            pytest.param(("-r", "{b}"), "--recursive", id="short-flag-swallows-path"),
            pytest.param(("{a}", "--exit-zero=yes"), "--exit-zero", id="non-bool-value"),
        ],
    )
    def test_non_bool_value_exits_two(self, tmp_path: Path, args: tuple[str, ...], flag: str) -> None:
        """A stray token reaching a boolean flag exits 2 without scanning anything.

        The second path used to bind to ``recursive`` and the run exited 0 having scanned one package; a hook
        listing several packages in ``args`` needs one hook entry per package. Short ``-r <path>`` and a
        non-boolean ``--exit-zero=yes`` must also fail before any scan output, so a hook never
        reports a pass or findings for a run it should have rejected.

        """
        pkg_a = _make_pkg(tmp_path, name="pkg_a")
        pkg_b = _make_pkg(tmp_path, name="pkg_b", content=_MYPKG_INIT_INVALID)
        argv = [arg.format(a=pkg_a, b=pkg_b) for arg in args]
        result = _run_cli("check", *argv, env=_cli_env(COLUMNS="500"), cwd=tmp_path)
        assert result.returncode == 2, result.stderr
        assert flag in result.stderr
        assert "Scanning:" not in result.stdout

    @pytest.mark.parametrize("subcommand", ["check", "chains"])
    def test_explicit_flags_do_not_hide_second_path(self, tmp_path: Path, subcommand: str) -> None:
        """Two package paths remain a usage error when both boolean options are explicit.

        A hook listing two packages must not treat the second package as an exclusion after Fire skips the
        already-bound recursive and exit-zero slots. Reject the invocation before importing either package.

        """
        pkg_a = _make_pkg(tmp_path, name="pkg_a")
        pkg_b = _make_pkg(tmp_path, name="pkg_b", content=_MYPKG_INIT_INVALID)
        result = _run_cli(subcommand, "--no-recursive", "--no-exit-zero", str(pkg_a), str(pkg_b), cwd=tmp_path)
        assert result.returncode == 2, result
        assert "Scanning:" not in result.stdout
        assert "nonexistent" not in result.stdout

    @pytest.mark.parametrize("option", ["--no-wrappers", "--no_wrappers", "-no-wrappers"])
    def test_negated_private_option_does_not_skip_scan(self, tmp_path: Path, option: str) -> None:
        """Private wrapper injection cannot be selected through Fire's negated aliases.

        An invalid package normally fails its audit. A negated private option used to inject False as the
        prescanned wrapper list and turn that same audit into an empty successful scan.

        """
        pkg = _make_pkg(tmp_path, content=_MYPKG_INIT_INVALID)
        control = _run_cli("check", str(pkg), cwd=tmp_path)
        assert control.returncode == 1, control
        assert "nonexistent" in control.stdout
        result = _run_cli("check", str(pkg), option, cwd=tmp_path)
        assert result.returncode == 2, result
        assert "Scanning:" not in result.stdout


#: Every subcommand with the exit code it gives `_MYPKG_INIT` (one wrapper, ``remove_in="9.0"``) at version ``9.0``.
_PARITY_SUBCOMMANDS = [
    pytest.param("check", (), 0, id="check"),
    pytest.param("expiry", ("--version=9.0",), 1, id="expiry"),
    pytest.param("policy", (), 0, id="policy"),
    pytest.param("chains", (), 0, id="chains"),
    pytest.param("all", ("--version=9.0",), 1, id="all"),
    pytest.param("status", ("--version=9.0",), 0, id="status"),
]


@pytest.mark.subprocess
class TestCliParserParity:
    """Golden command lines every subcommand keeps across a change of the argument parser."""

    @pytest.mark.parametrize(("subcommand", "extra", "exit_code"), _PARITY_SUBCOMMANDS)
    @pytest.mark.parametrize(
        "layout",
        [
            pytest.param(("--no-recursive", "{pkg}"), id="flag-before-path"),
            pytest.param(("{pkg}", "--no-recursive"), id="flag-after-path"),
            pytest.param(("--norecursive", "{pkg}"), id="legacy-negation"),
            pytest.param(("--recursive=false", "{pkg}"), id="attached-literal"),
            pytest.param(("--recursive", "0", "{pkg}"), id="separate-literal"),
        ],
    )
    def test_flag_position_keeps_path_and_exit_code(
        self, tmp_path: Path, subcommand: str, extra: tuple[str, ...], exit_code: int, layout: tuple[str, ...]
    ) -> None:
        """A boolean flag before or after the path, in any accepted spelling, scans that path with the usual outcome.

        Pre-commit hooks build ``entry + args`` and so put flags before the path, while users type them after it. Both
        orders and every boolean spelling (``--no-recursive``, the older ``--norecursive``, an attached or a
        separate ``true``/``false``/``1``/``0`` literal) must scan the named package and give the subcommand's normal
        exit code, never scan the current directory or swallow the path as the flag's value.

        """
        pkg = _make_pkg(tmp_path)
        argv = [arg.format(pkg=pkg) for arg in layout]
        result = _run_cli(subcommand, *argv, *extra, env=_cli_env(COLUMNS="1000"), cwd=tmp_path)
        assert result.returncode == exit_code, result.stdout + result.stderr
        assert f"Scanning: {pkg}" in result.stdout

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    @pytest.mark.parametrize(
        ("subcommand", "exit_code"),
        [
            pytest.param("expiry", 1, id="expiry"),
            pytest.param("all", 1, id="all"),
            pytest.param("status", 0, id="status"),
        ],
    )
    def test_flag_between_positionals_keeps_version(self, tmp_path: Path, subcommand: str, exit_code: int) -> None:
        """``<sub> <pkg> --no-recursive 9.0`` binds the second positional to the version, the flag in between.

        Python 3.9's argparse loses a positional that follows an optional between two optional positionals unless the
        arguments are parsed intermixed; the version must still be the user-provided ``9.0`` on every interpreter.

        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli(subcommand, str(pkg), "--no-recursive", "9.0", env=_cli_env(COLUMNS="1000"), cwd=tmp_path)
        assert result.returncode == exit_code, result.stdout + result.stderr
        assert "Version: 9.0 (user-provided)" in result.stdout

    @pytest.mark.parametrize(("subcommand", "extra", "exit_code"), _PARITY_SUBCOMMANDS)
    @pytest.mark.parametrize("option", ["--bogus-flag", "--exit-zeros"])
    def test_unknown_option_exits_two(
        self, tmp_path: Path, subcommand: str, extra: tuple[str, ...], exit_code: int, option: str
    ) -> None:
        """An unknown option exits 2 on every subcommand without scanning anything.

        A typo must fail the CI step rather than run the gate with the option silently dropped.

        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli(subcommand, str(pkg), *extra, option, env=_cli_env(COLUMNS="1000"), cwd=tmp_path)
        assert result.returncode == 2, result.stdout + result.stderr
        assert option.partition("=")[0] in result.stderr
        assert "Scanning:" not in result.stdout

    @pytest.mark.parametrize(("subcommand", "extra", "exit_code"), _PARITY_SUBCOMMANDS)
    def test_underscore_spelling_matches_dashes(
        self, tmp_path: Path, subcommand: str, extra: tuple[str, ...], exit_code: int
    ) -> None:
        """Option names spelled with underscores (``--include_members``) act like the dashed spelling.

        Fire accepted both spellings for every option, so CI configs written either way have to keep working.

        """
        pkg = _make_pkg(tmp_path)
        report = tmp_path / "report.json"
        result = _run_cli(
            subcommand, str(pkg), *extra, "--include_members=true", f"--report_json={report}", cwd=tmp_path
        )
        assert result.returncode == exit_code, result.stdout + result.stderr
        assert report.is_file()


@pytest.mark.subprocess
class TestCliArgumentValidation:
    """Unknown or misspelled flags and version auto-detection must fail safe, never silently mislead."""

    def test_check_unknown_flag_exits_nonzero(self, tmp_path: Path) -> None:
        """'pydeprecate check <path> --bogusflag' exits non-zero with a diagnostic.

        A CI pipeline invoking the CLI with an unknown flag must fail the job before anything is scanned; a flag
        that is silently ignored lets the gate pass with exit 0 on unvalidated input.

        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli("check", str(pkg), "--bogusflag", cwd=tmp_path)
        assert result.returncode == 2
        assert "unrecognized arguments: --bogusflag" in result.stderr
        assert "Scanning:" not in result.stdout

    def test_expiry_misspelled_version_flag_exits_nonzero(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <path> --verison 9.0' (typo) exits non-zero instead of dropping the value.

        A user pinning the comparison version with a typo'd flag must get a hard error; silently dropping
        the flag would run the expiry gate against an auto-detected (wrong) version — false pass or false
        fail with zero diagnostics.

        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli("expiry", str(pkg), "--verison", "9.0", cwd=tmp_path)
        assert result.returncode == 2
        assert "unrecognized arguments: --verison" in result.stderr

    def test_expiry_module_name_ignores_unrelated_cwd_pyproject(self, tmp_path: Path) -> None:
        """'pydeprecate expiry <module_name>' run from an unrelated project must not steal its version.

        Scanning an importable module *name* (not a filesystem path) from a directory that happens to
        contain another project's ``pyproject.toml`` must not auto-detect that project's version — doing
        so compares deprecation deadlines against a foreign version and flips the CI gate arbitrarily.

        """
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_pkg(proj)
        decoy = tmp_path / "otherproj"
        decoy.mkdir()
        (decoy / "pyproject.toml").write_text('[project]\nname = "fakeproj"\nversion = "9.9.9"\n')
        env = _cli_env()
        env["PYTHONPATH"] = f"{proj}{os.pathsep}{env['PYTHONPATH']}"
        result = _run_cli("expiry", "mypkg", env=env, cwd=decoy)
        assert "9.9.9" not in result.stdout + result.stderr
        assert result.returncode == 0


@pytest.mark.subprocess
class TestCliBoolLiteralValues:
    """Boolean flags take ``True``/``False``/``1``/``0`` in any case, as a separate token or after ``=``."""

    @pytest.mark.parametrize(
        ("literal", "returncode"),
        [
            pytest.param("0", 1, id="zero-gates"),
            pytest.param("false", 1, id="lowercase-false-gates"),
            pytest.param("False", 1, id="capitalised-false-gates"),
            pytest.param("1", 0, id="one-passes"),
            pytest.param("true", 0, id="lowercase-true-passes"),
        ],
    )
    def test_expiry_exit_zero_literal_is_the_flag_value(self, tmp_path: Path, literal: str, returncode: int) -> None:
        """``expiry <pkg> --version 9.0 --exit-zero <literal>`` applies the literal to ``--exit-zero``.

        The package holds one wrapper due for removal in ``9.0``. A literal such as ``0`` used to stay a free token
        that bound to ``version``, so the expiry check compared against version ``0`` and exited 0 although the
        wrapper was expired and ``--exit-zero 0`` asked for the gating exit code.

        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli("expiry", str(pkg), "--version", "9.0", "--exit-zero", literal, cwd=tmp_path)
        assert result.returncode == returncode, result.stderr
        assert "1 expired wrapper(s) found." in result.stdout


# Inert project-root file: it sits beside the packages but must never be imported by a package-root scan.
_SETUP_PY_INERT = "# placeholder for a project-root setup script\n"


def _make_flat_root(tmp_path: Path, *package_names: str) -> Path:
    """Create a project root holding the named invalid-wrapper packages beside an inert ``setup.py``.

    A package named ``tests`` is created empty, the way a flat-layout project keeps its test suite importable.
    """
    for name in package_names:
        _make_pkg(tmp_path, name=name, content="" if name == "tests" else _MYPKG_INIT_INVALID)
    (tmp_path / "setup.py").write_text(_SETUP_PY_INERT)
    return tmp_path


@pytest.mark.subprocess
class TestCliPackageRootScan:
    """A no-path scan from a project root resolves one package, and never silently audits nothing."""

    @pytest.mark.parametrize("subcommand", ["check", "expiry", "policy", "chains", "status", "all"])
    def test_multi_package_root_fails_loudly(self, tmp_path: Path, subcommand: str) -> None:
        """``pydeprecate <subcommand>`` at a root with two packages exits 2 (usage error) and names both.

        The shipped pre-commit hook runs ``pydeprecate check`` with no path. At a flat-layout root holding ``mypkg/``
        and ``tests/`` the scan used to fall back to the top-level ``*.py`` files, import only ``setup.py`` and
        report "No deprecated callables found." with exit 0 — a green hook that audited nothing. The argument is
        ambiguous, not a finding, so every subcommand refuses it the same way, as a usage error with a plain message
        rather than an uncaught ``ValueError``, and before it prints a scan header.

        """
        root = _make_flat_root(tmp_path, "mypkg", "tests")
        result = _run_cli(subcommand, cwd=root)
        assert result.returncode == 2, result.stdout + result.stderr
        assert "contains multiple packages (mypkg, tests)" in result.stderr
        assert "ValueError" not in result.stderr
        assert "Scanning:" not in result.stdout

    def test_single_package_root_still_reports(self, tmp_path: Path) -> None:
        """``pydeprecate check`` at a root with exactly one package scans it and reports its findings.

        The no-path default has to keep resolving the lone package of a flat-layout project root, or a hook
        configured with no ``args`` could not audit anything.

        """
        root = _make_flat_root(tmp_path, "mypkg")
        result = _run_cli("check", env=_cli_env(COLUMNS="500"), cwd=root)
        assert result.returncode == 1, result.stderr
        assert "nonexistent" in result.stdout

    def test_plain_directory_without_packages_exits_zero(self, tmp_path: Path) -> None:
        """``pydeprecate check`` in a directory with no package keeps scanning its top-level modules.

        Only two or more packages are ambiguous. A scripts directory with no ``__init__.py`` has nothing to choose
        between, so the top-level-module fallback still applies and a clean directory exits 0.

        """
        (tmp_path / "plain_script.py").write_text("VALUE = 1\n")
        result = _run_cli("check", cwd=tmp_path)
        assert result.returncode == 0, result.stderr


# Top-level module of a plain directory (no ``__init__.py``): one wrapper overdue at 3.0, clean for check/policy.
_PLAIN_MODULE_SRC = """\
from deprecate import deprecated


def new_fn(x: int) -> int:
    return x


@deprecated(target=new_fn, deprecated_in="1.0", remove_in="2.0", args_mapping={"old": "x"})
def old_fn(old: int) -> int:
    pass
"""

# Module-level code that reports which command-line arguments an imported module can see.
_ARGV_PROBE_SRC = 'import sys\n\nprint("ARGV:", sys.argv[1:], file=sys.stderr)\n'

# Package whose only deprecations are class members: a stacked-remap chain overdue at 3.0, and a warn-only method
# with a one-minor grace window (a policy violation). Every gate fails on them unless members are left out.
_MEMBERS_ONLY_SRC = '''\
from deprecate import TargetMode, deprecated


class Legacy:
    """Holds the package's only deprecations."""

    @deprecated(target=TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"c1": "nc2"})
    @deprecated(target=TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"nc1": "nc2"})
    def scale(self, base: int, c1: int = 0, nc1: int = 0, nc2: int = 2) -> int:
        return base

    @deprecated(target=None, deprecated_in="1.0", remove_in="1.1")
    def old_reset(self) -> None:
        """Warn-only member."""
'''

# (subcommand, extra args, exit code with members scanned) for the member-only package above.
_MEMBER_GATES = [
    pytest.param("check", (), 0, id="check"),
    pytest.param("expiry", ("--version", "3.0"), 1, id="expiry"),
    pytest.param("policy", (), 1, id="policy"),
    pytest.param("chains", (), 1, id="chains"),
    pytest.param("all", ("--version", "3.0"), 1, id="all"),
    pytest.param("status", ("--version", "3.0"), 0, id="status"),
]


@pytest.mark.subprocess
class TestCliScanTarget:
    """Every subcommand turns the same path into the same scan."""

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    @pytest.mark.parametrize(
        ("subcommand", "extra", "returncode", "expected"),
        [
            pytest.param("check", (), 0, "All deprecated wrappers look correct!", id="check"),
            pytest.param("expiry", ("--version", "3.0"), 1, "1 expired wrapper(s) found.", id="expiry"),
            pytest.param("policy", (), 0, "No deprecation policy violations found.", id="policy"),
            pytest.param("chains", (), 0, "No deprecation chains found.", id="chains"),
            pytest.param("all", ("--version", "3.0"), 1, "1 expired wrapper(s) found.", id="all"),
            pytest.param("status", ("--version", "3.0"), 0, "Past Removal Date", id="status"),
        ],
    )
    def test_plain_directory_scanned_by_every_subcommand(
        self, tmp_path: Path, subcommand: str, extra: tuple[str, ...], returncode: int, expected: str
    ) -> None:
        """A plain directory of top-level modules is scanned the same way by every subcommand.

        A scripts directory without ``__init__.py`` used to pass ``check`` and render in ``status`` while standalone
        ``expiry`` and ``chains`` refused it with an uncaught ``ValueError`` — yet ``all`` ran both of those checks on
        the very same directory. Each gate must reach the module's wrapper and report its own verdict on it.

        """
        plain = tmp_path / "scripts"
        plain.mkdir()
        (plain / "legacy_mod.py").write_text(_PLAIN_MODULE_SRC)
        result = _run_cli(subcommand, str(plain), *extra, env=_cli_env(COLUMNS="200"), cwd=tmp_path)
        assert result.returncode == returncode, result.stdout + result.stderr
        assert expected in result.stdout
        assert "ValueError" not in result.stderr

    @pytest.mark.parametrize("subcommand", ["check", "expiry", "policy", "chains", "status", "all"])
    def test_file_path_exits_two(self, tmp_path: Path, subcommand: str) -> None:
        """A single ``.py`` file is refused by every subcommand as a usage error before any scan starts.

        Wrappers are resolved through importable modules, never single files; a hook or CI step that passes one is
        misconfigured, so it must get a plain message and exit 2 — not an uncaught ``ValueError`` at exit 1 that
        reads like a failed audit.

        """
        module = tmp_path / "legacy_mod.py"
        module.write_text(_PLAIN_MODULE_SRC)
        result = _run_cli(subcommand, str(module), cwd=tmp_path)
        assert result.returncode == 2, result.stdout + result.stderr
        assert "File paths are not supported" in result.stderr
        assert "ValueError" not in result.stderr
        assert "Scanning:" not in result.stdout

    @pytest.mark.parametrize("layout", ["package", "plain-directory"])
    def test_scanned_module_never_sees_cli_arguments(self, tmp_path: Path, layout: str) -> None:
        """Module-level code of the scanned package sees no pydeprecate arguments, whatever the path layout.

        A module that parses ``sys.argv`` at import time (a script entry point, an ``absl`` flag module) must behave
        the same during a scan of a package directory as during a scan of a plain directory; leaking ``--exit-zero``
        and the scan path into it changes its import-time side effects depending only on how the path was given.

        """
        if layout == "package":
            target = _make_pkg(tmp_path, name="argvpkg", content=_ARGV_PROBE_SRC)
        else:
            target = tmp_path / "argvdir"
            target.mkdir()
            (target / "argv_mod.py").write_text(_ARGV_PROBE_SRC)
        result = _run_cli("check", str(target), "--exit-zero", cwd=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "ARGV: []" in result.stderr

    @pytest.mark.parametrize("subcommand", ["expiry", "status", "all"])
    @pytest.mark.parametrize("dynamic_version", [False, True])
    def test_version_import_hides_arguments(self, tmp_path: Path, subcommand: str, dynamic_version: bool) -> None:
        """Version detection imports parser-sensitive packages with the same empty argv as scanning.

        An uninstalled checkout may parse command-line flags while publishing its version. Both dynamic project
        metadata and the fallback version import must allow the expiry gate and report to audit its wrappers.

        """
        content = (
            "import argparse\nimport sys\n"
            'print("ARGV:", sys.argv[1:], file=sys.stderr)\n'
            "argparse.ArgumentParser().parse_args()\n"
            '__version__ = "3.0"\n' + _PLAIN_MODULE_SRC
        )
        pkg = _make_pkg(tmp_path, name="argvpkg", content=content)
        if dynamic_version:
            (tmp_path / "pyproject.toml").write_text(
                '[project]\nname = "argvpkg"\ndynamic = ["version"]\n', encoding="utf-8"
            )
        report = tmp_path / "report.json"
        result = _run_cli(subcommand, str(pkg), f"--report-json={report}", cwd=tmp_path)
        assert result.returncode == (0 if subcommand == "status" else 1), result.stdout + result.stderr
        assert result.stderr.count("ARGV: []") == 1
        assert "unrecognized arguments" not in result.stderr
        assert "Version: 3.0 (auto-detected)" in result.stdout
        data = json.loads(report.read_text(encoding="utf-8"))
        assert data["gaps"] == []
        assert "Current version: 3.0" in data["table"]
        assert "old_fn" in data["table"]

    @pytest.mark.parametrize("module_name", ["json", "os", "sys", "unique_scan_fixture"])
    @pytest.mark.parametrize("excluded", [False, True])
    def test_plain_module_import_origin_is_reported(self, tmp_path: Path, module_name: str, excluded: bool) -> None:
        """Plain-directory scans disclose names already imported from another file.

        A scripts directory can contain json.py or os.py with deprecations, while the CLI has already imported
        the standard-library names. The report must disclose that those local wrappers were not audited.

        """
        plain = tmp_path / "scripts"
        plain.mkdir()
        source = plain / f"{module_name}.py"
        source.write_text(_PLAIN_MODULE_SRC, encoding="utf-8")
        report = tmp_path / "report.json"
        extra = [f"--exclude={module_name}"] if excluded else []
        result = _run_cli(
            "check", str(plain), *extra, f"--report-json={report}", env=_cli_env(COLUMNS="1000"), cwd=tmp_path
        )
        assert result.returncode == 0, result.stdout + result.stderr
        data = json.loads(report.read_text(encoding="utf-8"))
        if excluded:
            assert data["gaps"] == []
            assert "old_fn" not in data["table"]
            assert "other copy audited" not in result.stderr
        elif module_name == "unique_scan_fixture":
            assert data["gaps"] == []
            assert "old_fn" in data["table"]
            assert "other copy audited" not in result.stderr
        else:
            module = sys.modules[module_name]
            imported = getattr(module, "__file__", None)
            if imported:
                loaded_from = str(Path(imported).resolve())
            else:
                assert module.__spec__ is not None
                assert module.__spec__.origin is not None
                loaded_from = module.__spec__.origin
            detail = (
                f"`{module_name}` was imported from {loaded_from}, not from {source.resolve()};"
                " the scan audited that copy."
            )
            assert f"pydeprecate gap: other copy audited: {detail}" in result.stderr
            assert [gap["kind"] for gap in data["gaps"]] == ["other copy audited"]
            assert data["gaps"][0]["detail"] == detail
            assert "old_fn" not in data["table"]

    def test_header_names_imported_location(self, tmp_path: Path) -> None:
        """The scan header names the file the scanned package was actually imported from.

        A CI log must show which copy of a package was audited, so a run that picked up an installed copy instead
        of the checkout on disk can be told apart from a clean run of the checkout.

        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli("check", str(pkg), env=_cli_env(COLUMNS="1000"), cwd=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"Imported from: {(pkg / '__init__.py').resolve()}" in result.stdout

    def test_second_copy_of_loaded_package_reports_gap(self, tmp_path: Path) -> None:
        """A checkout of a package the CLI process already imported is reported as a gap naming both locations.

        The CLI itself imports ``deprecate``; scanning another checkout of it (a fork, an older release tree) audits
        the copy already in ``sys.modules`` and reports its wrappers as the checkout's. That copy is often the same
        code — CI installs a wheel built from the checkout, then scans ``src/`` — so the run continues, but the log
        and the report must say which copy was actually audited.

        """
        checkout = tmp_path / "other-checkout" / "deprecate"
        checkout.mkdir(parents=True)
        (checkout / "__init__.py").write_text(_MYPKG_INIT)
        report = tmp_path / "report.json"
        result = _run_cli("check", str(checkout), f"--report-json={report}", env=_cli_env(COLUMNS="1000"), cwd=tmp_path)
        loaded_from = Path(_SRC_DIR, "deprecate").resolve()
        assert f"Imported from: {loaded_from / '__init__.py'}" in result.stdout, result.stdout + result.stderr
        assert (
            f"pydeprecate gap: other copy audited: `deprecate` was imported from {loaded_from}, not from"
            f" {checkout.resolve()}; the scan audited that copy." in result.stderr
        )
        gaps = json.loads(report.read_text(encoding="utf-8"))["gaps"]
        assert [gap["kind"] for gap in gaps] == ["other copy audited"]


@pytest.mark.subprocess
@pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
class TestCliIncludeMembers:
    """Every scanning subcommand takes ``--no-include-members`` and scans class members by default."""

    @pytest.mark.parametrize(("subcommand", "extra", "returncode"), _MEMBER_GATES)
    def test_members_scanned_by_default(
        self, tmp_path: Path, subcommand: str, extra: tuple[str, ...], returncode: int
    ) -> None:
        """Each subcommand reaches deprecations defined on a class without any flag.

        A package that deprecates only methods (a common shape for an SDK client class) must be gated like one with
        module-level wrappers: the members are reported and every failing gate exits 1.

        """
        pkg = _make_pkg(tmp_path, name="memberpkg", content=_MEMBERS_ONLY_SRC)
        result = _run_cli(subcommand, str(pkg), *extra, env=_cli_env(COLUMNS="1000"), cwd=tmp_path)
        assert result.returncode == returncode, result.stdout + result.stderr
        assert "Legacy." in result.stdout

    @pytest.mark.parametrize(("subcommand", "extra", "returncode"), _MEMBER_GATES)
    def test_noinclude_members_leaves_members_out(
        self, tmp_path: Path, subcommand: str, extra: tuple[str, ...], returncode: int
    ) -> None:
        """``--no-include-members`` scopes every subcommand to module-level wrappers.

        The flag used to exist on ``status`` only, so a project could render a module-level table while ``expiry``
        and ``all`` on the same path still failed on member deprecations. With the flag, no gate sees the members:
        nothing is reported and every gate passes.

        """
        pkg = _make_pkg(tmp_path, name="memberpkg", content=_MEMBERS_ONLY_SRC)
        result = _run_cli(
            subcommand, str(pkg), *extra, "--no-include-members", env=_cli_env(COLUMNS="1000"), cwd=tmp_path
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Legacy." not in result.stdout


@pytest.mark.subprocess
class TestCliPolicyMessageRequiredFlag:
    """``policy --message-required`` binds like the other boolean flags, wherever it sits beside the path."""

    @pytest.mark.parametrize(
        ("flag", "header"),
        [
            pytest.param("--message-required", "message-required=True (flag)", id="bare"),
            pytest.param("--no-message-required", "message-required=False (flag)", id="negated"),
        ],
    )
    def test_flag_before_path_scans_path(self, tmp_path: Path, flag: str, header: str) -> None:
        """``policy <flag> <pkg>`` scans ``<pkg>`` and reports the flag as the rule's source.

        Before the flag was normalized, ``policy --message-required src/pkg`` bound the path to the flag and exited
        2 with ``Invalid message_required value``, while ``policy src/pkg --message-required`` worked.

        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli("policy", flag, str(pkg), env=_cli_env(COLUMNS="500"), cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert f"Scanning: {pkg}" in result.stdout
        assert header in result.stdout

    def test_omitted_flag_keeps_pyproject_value(self, tmp_path: Path) -> None:
        """A flag placed before the path leaves an omitted ``--message-required`` to ``[tool.pydeprecate.policy]``.

        Normalizing boolean flags must not turn the omitted ``message_required`` into a typed value, or the project's
        declared policy would silently stop applying whenever another boolean flag is on the command line.

        """
        pkg = _make_pkg(tmp_path)
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmessage-required = false\n")
        result = _run_cli("policy", "--exit-zero", str(pkg), cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "message-required=False (pyproject.toml)" in result.stdout


@pytest.mark.subprocess
class TestCliAttachedBoolLiterals:
    """``--flag=value`` accepts the same boolean spellings as ``--flag value`` instead of exiting 2."""

    @pytest.mark.parametrize(
        ("spelling", "returncode"),
        [
            pytest.param("--exit-zero=true", 0, id="lowercase-true"),
            pytest.param("--exit-zero=1", 0, id="one"),
            pytest.param("--exit-zero=0", 1, id="zero"),
            pytest.param("--exit-zero=false", 1, id="lowercase-false"),
        ],
    )
    def test_attached_literal_sets_exit_zero(self, tmp_path: Path, spelling: str, returncode: int) -> None:
        """``check <pkg> --exit-zero=<literal>`` honours the literal on a package with an invalid mapping.

        Before the CLI validated boolean flags, ``--exit-zero=true``, ``=1`` and ``=0`` bound a truthy or falsy value
        and worked; the first guard version exited 2 for them. A CI config written for the old behaviour keeps
        working only if the lowercase and numeric spellings still reach the flag as booleans.

        """
        pkg = _make_pkg(tmp_path, content=_MYPKG_INIT_INVALID)
        result = _run_cli("check", str(pkg), spelling, cwd=tmp_path)
        assert result.returncode == returncode, result.stderr

    def test_unrecognised_spelling_still_exits_two(self, tmp_path: Path) -> None:
        """``check <pkg> --exit-zero=yes`` exits 2 because only true/false/1/0 are boolean spellings.

        Anything else would bind a truthy string and make the run pass, which is the silent failure the guard exists
        to stop.

        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli("check", str(pkg), "--exit-zero=yes", cwd=tmp_path)
        assert result.returncode == 2
        assert "expects True or False" in result.stderr


@pytest.mark.subprocess
class TestCliReportJson:
    """``--report-json PATH`` writes one machine-readable record of the run: the status table and every gap."""

    @pytest.mark.parametrize(
        ("subcommand", "args", "exit_code"),
        [
            pytest.param("check", (), 0, id="check"),
            pytest.param("expiry", ("--version=1.0",), 0, id="expiry"),
            pytest.param("policy", (), 0, id="policy"),
            pytest.param("chains", (), 0, id="chains"),
            pytest.param("all", ("--version=1.0",), 0, id="all"),
            pytest.param("status", ("--version=1.0",), 0, id="status"),
        ],
    )
    def test_every_subcommand_writes_table_of_its_own_scan(
        self, tmp_path: Path, subcommand: str, args: tuple[str, ...], exit_code: int
    ) -> None:
        """Each subcommand writes a report whose table lists the wrapper its own scan found.

        A CI wrapper publishing a job summary after a single gate used to re-scan with ``status`` and guess the gate's
        scope from its argv. The report must come from the very scan that gated, so every subcommand carries it.

        """
        pkg = _make_pkg(tmp_path)
        report = tmp_path / "report.json"
        result = _run_cli(subcommand, str(pkg), *args, f"--report-json={report}", cwd=tmp_path)
        data = json.loads(report.read_text(encoding="utf-8"))
        assert result.returncode == exit_code, result.stderr
        assert data["schema"] == 1
        assert "old_fn" in data["table"]

    def test_failing_gate_still_writes_report(self, tmp_path: Path) -> None:
        """A gate that finds a hard error (exit 1) still writes its report, with the table of the failing scan.

        Findings are exactly when a job summary matters most; a report written only on success would hide the table
        of the failing run.

        """
        pkg = _make_pkg(tmp_path, content=_MYPKG_INIT_INVALID)
        report = tmp_path / "report.json"
        result = _run_cli("check", str(pkg), "--report-json", str(report), cwd=tmp_path)
        assert result.returncode == 1
        assert "old_fn" in json.loads(report.read_text(encoding="utf-8"))["table"]

    def test_usage_error_writes_no_report(self, tmp_path: Path) -> None:
        """A usage error (exit 2) writes no report, so a consumer never publishes a scan that did not run.

        ``--version`` that is not PEP 440 stops the run before scanning; any report would describe nothing.

        """
        pkg = _make_pkg(tmp_path)
        report = tmp_path / "report.json"
        result = _run_cli("expiry", str(pkg), "--version=not-a-version", f"--report-json={report}", cwd=tmp_path)
        assert result.returncode == 2
        assert not report.exists()

    def test_records_gaps_with_kind_and_hint(self, tmp_path: Path) -> None:
        """A plain directory with nested files records a ``nested files not audited`` gap with a hint.

        The human stderr line stays as before; the report adds the same gap as data, so CI tooling no longer has to
        match stderr text to learn that part of the project was never audited.

        """
        plain = tmp_path / "plain"
        (plain / "sub").mkdir(parents=True)
        (plain / "mod.py").write_text("x = 1\n")
        (plain / "sub" / "nested.py").write_text("y = 2\n")
        report = tmp_path / "report.json"
        result = _run_cli("check", str(plain), f"--report-json={report}", cwd=tmp_path)
        gaps = json.loads(report.read_text(encoding="utf-8"))["gaps"]
        assert [gap["kind"] for gap in gaps] == ["nested files not audited"]
        assert gaps[0]["hint"]
        assert "pydeprecate gap: nested files not audited: " in result.stderr

    def test_records_unparsable_remove_in_gap(self, tmp_path: Path) -> None:
        """A wrapper whose ``remove_in`` is not PEP 440 is recorded as a ``remove_in not PEP 440`` gap, exit 0.

        The audit layer skips such a wrapper with a warning only, and a package can silence that warning (here
        ``PYTHONWARNINGS=ignore`` stands in for its own filters). The gap must still reach stderr and the report, so
        CI tooling can tell that one removal deadline was never checked.

        """
        content = _MYPKG_INIT.replace('remove_in="9.0"', 'remove_in="next-release"')
        pkg = _make_pkg(tmp_path, content=content)
        report = tmp_path / "report.json"
        result = _run_cli(
            "expiry",
            str(pkg),
            "--version=1.0",
            f"--report-json={report}",
            env=_cli_env(PYTHONWARNINGS="ignore"),
            cwd=tmp_path,
        )
        gaps = json.loads(report.read_text(encoding="utf-8"))["gaps"]
        assert result.returncode == 0
        assert [gap["kind"] for gap in gaps] == ["remove_in not PEP 440"]
        assert "old_fn" in gaps[0]["detail"]
        assert gaps[0]["hint"]
        assert "pydeprecate gap: remove_in not PEP 440: " in result.stderr

    def test_records_skipped_submodule_gap(self, tmp_path: Path) -> None:
        """A submodule that fails to import is recorded as a ``module not audited`` gap naming the module.

        These gaps are printed only after the subcommand returns, so the report must be written after them, or the
        most common gap of all would be missing from it.

        """
        pkg = _make_pkg(tmp_path)
        (pkg / "broken.py").write_text("raise RuntimeError('boom')\n")
        report = tmp_path / "report.json"
        _run_cli("check", str(pkg), f"--report-json={report}", cwd=tmp_path)
        gaps = json.loads(report.read_text(encoding="utf-8"))["gaps"]
        assert [gap["kind"] for gap in gaps] == ["module not audited"]
        assert gaps[0]["detail"].startswith("mypkg.broken: ")

    def test_clean_scan_records_no_gap(self, tmp_path: Path) -> None:
        """A fully audited package records an empty gap list, so consumers can tell "no gaps" from "unknown".

        A maintainer scans a package whose every module imports cleanly and publishes the report as a job summary.
        The ``gaps`` key must be present and empty rather than absent, otherwise the summary cannot distinguish a
        complete audit from a report format that never recorded gaps at all.

        """
        pkg = _make_pkg(tmp_path)
        report = tmp_path / "report.json"
        _run_cli("status", str(pkg), "--version=1.0", f"--report-json={report}", cwd=tmp_path)
        assert json.loads(report.read_text(encoding="utf-8"))["gaps"] == []

    def test_unwritable_report_keeps_exit_code(self, tmp_path: Path) -> None:
        """A report path that cannot be written prints an advisory and keeps the gate's own exit code.

        The report is a by-product for CI tooling; letting its failure turn a passing gate red (as ``status --output``
        does for a report the user asked to see) would make the gate result depend on the summary.

        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli("check", str(pkg), f"--report-json={tmp_path}", cwd=tmp_path)
        assert result.returncode == 0
        assert "Could not write the JSON report" in result.stderr

    def test_underscore_spelling_writes_report(self, tmp_path: Path) -> None:
        """``--report_json`` writes the report like ``--report-json``, as every other flag takes either spelling.

        A user who types the underscore form out of habit (Fire accepts it for ``--exit_zero`` and the like) used to
        get a loud usage error and no report; the option is now read in both spellings.

        """
        pkg = _make_pkg(tmp_path)
        report = tmp_path / "report.json"
        result = _run_cli("check", str(pkg), f"--report_json={report}", cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "old_fn" in json.loads(report.read_text(encoding="utf-8"))["table"]

    @pytest.mark.parametrize(
        "following",
        [
            pytest.param((), id="nothing-follows"),
            pytest.param(("--exit-zero",), id="flag-follows"),
            pytest.param(("--",), id="separator-follows"),
        ],
    )
    def test_missing_value_exits_two(self, tmp_path: Path, following: tuple[str, ...]) -> None:
        """A ``--report-json`` without a path is a usage error, not a silently skipped report.

        The path is missing when the option ends the command line, and also when the next argument is another option
        or the ``--`` separator: taking it as the path would swallow ``--exit-zero`` (turning an advisory run into a
        failing gate) and write the report to a file named like a flag.

        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli("check", str(pkg), "--report-json", *following, cwd=tmp_path)
        assert result.returncode == 2
        assert "argument --report-json: expected one argument" in result.stderr

    def test_dash_leading_path_needs_equals_form(self, tmp_path: Path) -> None:
        """``--report-json=-name`` still names a report file that starts with a dash.

        A bare ``--report-json`` never takes a dash-leading next argument as its value, so a path like ``-report.json``
        is written with ``=``; the report lands in that file under the working directory.

        """
        pkg = _make_pkg(tmp_path)
        result = _run_cli("check", str(pkg), "--report-json=-report.json", cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "old_fn" in json.loads((tmp_path / "-report.json").read_text(encoding="utf-8"))["table"]

    def test_nested_run_writes_no_report(self, tmp_path: Path) -> None:
        """A CLI run started from inside the audited package's import never scans or writes the outer run's report.

        The package's ``__init__`` calls the CLI entry point. A report from that nested run would pass for the outer
        scan's, so the nested run must not see the outer arguments (it gets none and only prints help) and only the
        outermost run may write the report — the one of its own scan, after the import returned.

        """
        pkg = tmp_path / "reentrantpkg"
        pkg.mkdir()
        marker = tmp_path / "import-started.marker"
        (pkg / "__init__.py").write_text(
            f"from pathlib import Path\nfrom deprecate._cli import cli\nPath({str(marker)!r}).write_text('x')\ncli()\n"
        )
        report = tmp_path / "report.json"
        result = _run_cli("check", str(pkg), f"--report-json={report}", cwd=tmp_path)
        assert marker.exists(), "the package import never reached the nested cli() call"
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.count("Scanning:") == 1
        assert json.loads(report.read_text(encoding="utf-8"))["gaps"] == []
