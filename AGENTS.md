# Agent Configuration for pyDeprecate

> [!IMPORTANT]
>
> **For detailed coding standards, testing requirements, and contribution workflows**, see the [Contributing Guide](.github/CONTRIBUTING.md). This file provides agent-specific behavioral instructions and quick reference only.

## 🎯 Design Principles

- **Simplicity**: the API surface must be learnable in minutes; a single decorator covers the common case with no config files required.
- **Robustness**: deprecated code is on the removal path; correctness must hold regardless of call order or framework integration; audit tools must surface all live deprecations.
- **Flexibility**: work with any Python callable — functions, class methods, async, properties, dataclasses, enums — without special-casing the caller.

When a proposed feature conflicts with simplicity, complexity wins only when robustness or flexibility requires it.

## 👀 Code Review

For pull request or changed-code reviews, follow the [code-review skill](.github/skills/code-review/SKILL.md). Keep reviews read-only; implementation, dependency installation, commits, and PR creation require a separate implementation task. Project-wide coding and documentation invariants below remain applicable review criteria.

## ⚓ Before Any Work: Anchor with Repository Context

> [!IMPORTANT]
>
> **Before starting any work, agents MUST anchor themselves with the repository context.** Read these files in order:

1. **Configuration files** (source of truth):
   - `pyproject.toml` — package metadata, extras, entry point and tool settings (ruff, mypy, pytest)
   - `.pre-commit-config.yaml` — pre-commit hooks configuration
   - `setup.py` — shim that only builds the PyPI long description from `README.md`
   - `.github/workflows/*.yml` — CI/CD pipeline configuration
2. **Documentation and guidelines**:
   - [CONTRIBUTING.md](.github/CONTRIBUTING.md) — contribution workflow, coding standards, testing requirements
   - `README.md` — project usage and API
   - `src/deprecate/` — core library code to understand existing patterns
3. **Project structure**: For codebase layout and file organization, explore the repository structure directly or see [Test Organization](.github/CONTRIBUTING.md#test-organization) for test file structure

### Configuration Files Are Source of Truth

> [!WARNING]
>
> **If documentation contradicts actual configuration**, the configuration files have **higher authority**. When you detect a mismatch:
>
> 1. **Trust the config file** (e.g., `pyproject.toml`, `.pre-commit-config.yaml`)
> 2. **Suggest updating the documentation** to match reality
> 3. **Report the mismatch** clearly to maintainers

**When you find a mismatch:**

Write a clear explanation linking to both sources, then let maintainers decide on the update action:

*"I notice [CONTRIBUTING.md:45](.github/CONTRIBUTING.md#L45) mentions using black for formatting, but [pyproject.toml:23](pyproject.toml#L23) actually configures ruff. The config file should be considered correct. Should the documentation be updated to reflect that ruff handles formatting?"*

## 🧭 Agent Behavioral Rules

### Branch Management

- Use [branch naming convention](.github/CONTRIBUTING.md#-branch-naming-convention): `{type}/{issue-nb}-description`
- Types: `fix/`, `feat/`, `docs/`, `refactor/`, `test/`, `chore/`

### Bug Fixes

- **Use Test-Driven Development (TDD)**: Write a failing test that reproduces the bug first, then implement the fix
- See [Fixing Bugs](.github/CONTRIBUTING.md#-fixing-bugs) for complete workflow

### New Features

- **Require approval first**: See [Building Features](.github/CONTRIBUTING.md#-building-features-with-consensus)
- **Mandatory test coverage**: Happy path, failure path, and edge cases
- See [Test Requirements](.github/CONTRIBUTING.md#-tests-and-quality-assurance)

### Code Quality

- **Zero runtime dependencies** — never add to `install_requires`
- **Type hints required** — all function signatures must have type hints
- **No bare `except:`** — always catch specific exceptions
- **Fast imports** — no expensive module-level code
- See [Coding Standards](.github/CONTRIBUTING.md#-coding-standards) for complete rules

### Test Organization

- **Three-layer separation**: targets in `api_current/`, deprecated wrappers in `api_deprecated/`, test logic in `test_*.py`
- **Do not** define targets or `@deprecated` wrappers directly in test files, except in the two cases listed under [Three-layer rule](.github/CONTRIBUTING.md#test-organization):
  - a test under `tests/unittests/` may define a tiny class or function inline when it exists only to drive the code under test;
  - in any layer the definition stays in the test when the fresh definition itself is the behaviour under test (a decoration-time warning or error, a `pytest.warns` or `pytest.raises` around the definition, or a warning location that is the definition line)
- **No local imports inside test functions** — all `import` / `from … import` statements at module level; never inside a test method or helper function
- **Private test helpers** — every helper function in a `test_*.py` module carries a leading underscore (`_build_target`, not `build_target`), module-level or nested inside a test (`_worker`, `_counting_stream`); only `test_*` functions and pytest fixtures stay public. A nested function that is the subject under test (a source, target or decorated callable whose name reaches the asserted output) keeps its name. A method on a fake class keeps the name of the interface it stands in for (`read_text`, `reconfigure`)
- **Public collection objects** — in `tests/collections/`, every target, deprecated source and wrapper carries a public name (`batches_source`, not `_batches_source`), including a placeholder source that exists only to be wrapped; the leading underscore is kept for real helpers (builders, shared private state) and for a source whose public name is already taken by its own wrapper (`SelfMappedEnum = deprecated_class(...)(_SelfMappedEnum)`)
- **No redundant naming** — test method names must not repeat the class name; in `TestFooBar` write `test_returns_value` not `test_foo_bar_returns_value` (see [CONTRIBUTING.md: Tests and quality assurance](.github/CONTRIBUTING.md#-tests-and-quality-assurance))
- **Scenario description required** — every non-trivial test method docstring must include a prose paragraph describing the real-world situation being tested; a one-line summary alone is not sufficient (see [Test Requirements](.github/CONTRIBUTING.md#-tests-and-quality-assurance))
- **Unification pattern**: when 3+ call sites share the same `(deprecated_in, remove_in[, num_warns])` combo, extract to `_DEPRS_CASE_<SLUG>_ARGS: dict[str, Any]` and splat with `**`. Hoist `_class_deprecation_*` shared instances to the top constants block. See [Unification pattern](.github/CONTRIBUTING.md#unification-pattern--shared-version-kwargs-and-hoisted-instances) for full rules.
- See [Test Organization](.github/CONTRIBUTING.md#test-organization) for details

### Coding-Agent Plugin (`plugins/pydeprecate/`)

- **Three-way sync**: `src/deprecate/` (code) ↔ `README.md`/`docs/llms.txt`/`docs/{usage,audit,integrations,adoption,migration}/*.md` (docs) ↔ `plugins/pydeprecate/skills/*/SKILL.md` (plugin).
  - A public API change updates all three; a SKILL.md referencing an identifier not in the installed API is a bug.
  - `test_skill_docs_reference_real_api` in `tests/integration/test_agent_plugin.py` guards backticked identifiers and call forms against the installed API:
    - bare tokens (module exports, `TargetMode` members, decorator kwargs such as `deprecated_class`, `TargetMode.ARGS_REMAP` or `skip_if`);
    - the `deprecate.`/`TargetMode.` prefix of dotted tokens;
    - for each backticked call such as `find_deprecation_wrappers(..., recursive=True)`, the callee, every `kw=` name and every `TargetMode.` member inside it.
  - Version strings, extras such as `[audit]`, and every behavioural prose claim (warning category, counter semantics, forwarding side effects) are not machine-checked and are reviewed by hand on any API change.
- **Docs and this file outrank plugin guidance** — `docs/llms.txt` and `AGENTS.md` are the authoritative contract; `plugins/pydeprecate/skills/*/SKILL.md` is a cached, separately-installed copy that can lag the actual installed package version. On any conflict, trust docs/`AGENTS.md` and fix the SKILL.md, never the reverse.
- **No extra blank-line spacing in `plugins/**/*.md`** — single blank line between blocks, no blank line between list items in a tight list (see existing SKILL.md files for the pattern).
- **Version policy**:
  - Plugin `version` (in both `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`) bumps on its own cadence — skill content changed — independent of the package's `__version__`.
  - Each manifest also declares `compatible_package_version` (PEP 440 specifier, e.g. `">=0.10"`): a floating support window whose floor is the current minor version minus three (`0.14` → `>=0.11`), bumped at every minor release.
  - The window never reaches back across a major release: while the current minor is below three the floor is clamped to `>=X.0` (`1.0`, `1.1` and `1.2` → `>=1.0`).
  - Identifiers the SKILL.md files reference that are newer than the floor must carry an inline since-note naming the release and the older spelling or fallback (e.g. `message_template` (since v0.12; `template_mgs` before v0.12), `get_deprecation_config` (since v0.13)) — check `CHANGELOG.md`, not guesswork.
  - Release references in docs prose, SKILL.md and docstrings carry a `v` prefix ("since v0.16", "removed in v1.0"); write "since vX" for availability that continues to the latest release and "before vX" for anything lower, never "from vX", "vX+", "vX or later", "pre-vX" or "in vX" meaning "since vX" ("in vX" stays right for a one-time event such as "renamed in v0.12"); code values, version specifiers, printed output and the "Verified against pyDeprecate" sentence keep the bare number (see [Code Example Conventions](.github/CONTRIBUTING.md#code-example-conventions)).
  - The field is a repo-side maintainer convention, not a host signal: `claude plugin validate` reports it as an unknown field that Claude Code ignores at load time and Codex has no validator.
  - So the only enforcement is repo-side: the `plugin-compat-floor` pre-commit hook (`.github/scripts/check_plugin_compat_floor.py`, self-contained so it also runs on pre-commit.ci) checks that both manifests agree, that the floor sits within three minors of `__version__` in `src/deprecate/__about__.py`, and that the SKILL.md sentence mirrors it.
  - This is deliberately a pre-commit policy check, not a pytest test — a stale floor is maintainer housekeeping, not a package failure, and belongs out of the test suite's noise.
  - Because hosts never surface it, the same range is repeated in prose as the first body paragraph of both SKILL.md files ("Verified against pyDeprecate …"), which is where agents actually read it.
  - Bump `compatible_package_version` (not `version`) at each minor release and whenever a skill drops support for an older spelling, and mirror the new floor in that SKILL.md sentence in both files.
  - Never collapse the two fields into one shared number.
  - Both skills fetch the root agent guide `https://borda.github.io/pyDeprecate/llms.txt`, which tracks the development line and therefore covers every since-noted identifier. The since-notes plus the `deprecate.__version__` check are what keep an agent on an older release inside the window from calling an API it does not have.

### Documentation Site

- **README ≠ docs/index.md** — README is the PyPI page (do not copy it to docs); `docs/index.md` is a curated overview
- **Never add `cp README.md docs/index.md` to CI** — `docs/index.md` is tracked in git directly
- **After any API change**: update `README.md` AND the relevant `docs/` topic page
- **New troubleshooting item**: add a `### question` section under the matching topic `##` heading of `docs/troubleshooting.md`, whose first prose paragraph is the answer — the FAQPage JSON-LD is generated from it at build time by `docs/mkdocs_hooks.py`, never edit it by hand
- **`docs/overrides/main.html`** is Jinja2 (prettier-excluded); do not put content files there
- **`docs/robots.txt`** — AI crawler access policy; add a `User-agent: <bot> / Allow: /` block when a new mainstream AI crawler is released. The comment referencing the `docs/llms.txt` URL must stay current.
- **Honest and optimistic docs** — describe what the library really does, and cover every supported user scenario in the topic pages and the [use-cases](docs/usage/index.md) map. Present a limitation as a small known edge with its workaround: a short plain paragraph under its own heading, with a block only for the few that can silently give a wrong result, and do not dwell on it. Where a limitation can block real work, invite the reader to [open an issue](https://github.com/Borda/pyDeprecate/issues) with their practical use case; we are happy to discuss it and integrate it when it fits
- **Keep `docs/llms.txt` compact** — agents pay for every token, so the file spends none on layout:
  - break a code fence or flowchart at logical steps, one step per line, and never hard-wrap a long step onto hanging-indent continuation lines; split a long note at its `;` into separate `Note:` lines instead;
  - leave tables unpadded (mdformat skips this file on purpose), use one blank line between sections and none inside a block, and indent only what the syntax needs;
  - never edit or commit `docs/llms-full.txt`: it is gitignored and generated from `docs/llms.txt` plus an API map by `make llms-full` (also run by `make docs-build` and the docs workflow)
- **Admonitions are the exception, typed by meaning** — most text is plain prose; a block that appears on every item stops signalling anything, so never give each list item or limitation its own block, never stack two blocks, and keep to a page budget:
  - at most 4 blocks on a page under 250 lines and 8 on a longer one, collapsibles and blocks inside tabs included, with at most 2 `warning` and 1 `abstract` per page;
  - `docs/troubleshooting.md` allows at most one block per `###` question, never before the first prose paragraph (the FAQPage JSON-LD is built from it);
  - a `note` or `tip` that orients the reader at the start of a section, bridges from the old way of thinking to the new, or makes a step easier is welcome and is the last block to drop;
  - a block holding a fenced code block that phmdoctest runs, and a block that mirrors library output such as `!!! warning "Deprecated in …"`, keep their form;
  - use the full range the site supports (`admonition`, `pymdownx.details`, `pymdownx.tabbed`), typed by meaning:
  - `note` for a neutral aside, `info` for background or a version fact such as "Changed in v0.15", `tip` for a shortcut or a workaround, `example` for a worked scenario, `abstract` for one summary at the top of a long page, `success` for the outcome to expect, `question` for an FAQ entry, `quote` for an external source;
  - `warning` only for behaviour that silently gives a wrong result, two per page at most; `danger` only for data loss, security or an irreversible step; `failure` or `bug` for an error to expect or a known defect;
  - `??? type "Title"` makes long optional detail collapsible, and `=== "Title"` tabs hold parallel variants such as pip and uv, or Python and CLI;
  - give a block a short title, and use none for a single sentence that is not a hazard
- **Never break an old docs URL** — moving, renaming or deleting a page, or moving a heading to another page, must redirect readers coming from past versions, README links, bookmarks and `docs/llms.txt`:
  - add `old.md: new.md` to `plugins.redirects.redirect_maps` in `mkdocs.yml` and never remove a line from it;
  - add `old-anchor: new/page.html` under `extra.anchor_moves` in `mkdocs.yml` for a section that now lives on another page;
  - update every link, the nav and the CONTRIBUTING structure tree, then run `make docs-build` and open the old URL in the built site to confirm it lands on the new page
  - see [Moving or Renaming Pages](.github/CONTRIBUTING.md#moving-or-renaming-pages)
- **Active emphasis in human-facing docs** — break up monotone prose with markup:
  - **bold** marks the decision, rule or consequence the reader must not miss, typically the lead-in phrase of a key bullet;
  - *italic* marks a term at its first use, or a contrast such as *when*, *only*, *before*;
  - underline, written `<u>…</u>`, marks the single most critical caveat of a section, at most two per page;
  - keep it to about one emphasised phrase per three sentences, never a whole sentence or list, and never inside code, headings or link text;
  - use underline only in `docs/` pages, never in `README.md` (PyPI), `docs/llms*.txt` or `plugins/**`
- **Tight lists in every `*.md`** — no blank line between list items or before a nested list; keep blank lines only when an item holds a block that needs them (code block, table, image, admonition); write a follow-on point as a nested bullet, not an indented second paragraph; never nest a single bullet, fold it into its parent item
- See [Documentation Site](.github/CONTRIBUTING.md#-documentation-site) for the full consistency rules

## 🚫 Critical Constraints

### Never:

- Add runtime dependencies
- Commit sensitive information (`.env`, API keys)
- Use bare `except:` clauses
- Define deprecated wrappers inside test files, other than in the two cases named under Test Organization
- Use `with warnings.catch_warnings(...)` in any `.md` documentation example in any form — neither `simplefilter("always")` for capturing nor `simplefilter("ignore", ...)` for suppressing; annotate the call with `# warns: FutureWarning` or `# warns: UserWarning` instead; output blocks show only return values
- Use bare `assert` statements in `.md` documentation examples outside of `def test_...` bodies (e.g. `assert pt.x == 1.0`, `assert isinstance(obj, MyClass)`) — use `print()` instead and follow with a `<details><summary>Output: <code>expression</code></summary>` block showing expected output. **Exception:** bare `assert` inside `def test_...` function bodies shown as pytest integration examples is allowed and idiomatic
- Import a fictional package name in runnable `.md` examples — executable examples must import from actual test collection modules (`from tests.collections.api_deprecated import core`, `forwarding` or `regressions`, `misconfigured`, `chains`, `policy`, or `pep702`). For CI-template snippets that intentionally show a placeholder import, add `# phmdoctest:skip — CI template: replace my_package with your actual package` as the first line so phmdoctest skips execution
- Let `plugins/pydeprecate/skills/*/SKILL.md` contradict `docs/llms.txt` or this file — those are authoritative; the installed plugin and the installed package can drift independently
- Add extra blank-line spacing inside `plugins/**/*.md`
- Skip test coverage for new features or bug fixes
- Implement features without maintainer approval
- Start work without first reading config files and guidelines

### Always:

- **Anchor with repository context first** — read config files (`pyproject.toml`, `.pre-commit-config.yaml`) and guidelines before any work
- **Trust config files over documentation** — when mismatches occur, config files are the source of truth
- **Suggest documentation updates** when you find mismatches between docs and actual configuration
- **Update docs immediately after any structural change** — adding, moving, renaming, or deleting files/folders/modules must be followed by updating `.github/CONTRIBUTING.md` (project structure tree, Test Organisation table) and any other `*.md` that references affected paths or names. Do not wait to be asked.
- **Keep README and docs site in sync** — any API addition, rename, or behavior change must be reflected in `README.md` **and** the relevant `docs/` topic page; new troubleshooting items go in `docs/troubleshooting.md` only (the FAQPage JSON-LD is generated from it)
- **Update `CHANGELOG.md` at PR-creation time** — once the PR number is known and the diff is stable, add or re-assess the entry under `[UnReleased]` (new features → `Added`, fixes → `Fixed`, behavior changes → `Changed`); always include `(#N)` PR link; skip docs-only, test-only, CI, and pure internal refactoring changes; do not write entries during iterative development commits — content may pivot before the PR is opened
- **Update inline comments when changing behavior** — after any logic change, scan changed files for comments describing that behavior; update stale ones (stale comments mislead more than none)
- **Keep AI-agent documentation in sync** — `docs/llms.txt` is a machine-readable contract read by AI agents before generating code; it must reflect actual behavior at all times.
  - Apply this sync table on every relevant change:
    - **Public API behavior change** — affected module docstrings (`routine.py`, `audit/__init__.py`, `proxy/__init__.py`, `utils.py`) · inline comments in changed `src/` files · `README.md` Quick Start · relevant `docs/usage/*.md` topic page · `docs/llms.txt` § Agent Notes · `docs/troubleshooting.md` (add or update Q&A) · FAQPage JSON-LD in `docs/overrides/main.html`
    - **New deprecation pattern** — `docs/llms.txt` Decision Flowchart · relevant `docs/usage/*.md` topic page (functions, classes, properties, async, or advanced)
    - **New anti-pattern discovered** — `docs/llms.txt` § Anti-Patterns · relevant `docs/usage/*.md` topic page
    - **`TargetMode` enum value added or removed** — `docs/llms.txt` Critical Mental Model · Decision Flowchart · relevant `docs/usage/*.md` topic page
    - **New mainstream AI crawler released** — `docs/robots.txt` (new `User-agent: <bot> / Allow: /` block)
  - `docs/llms.txt` is the highest-leverage surface for AI agents — a stale entry there produces wrong code at scale. `docs/robots.txt` is the access gateway — an unlisted bot gets the wildcard `Allow: /` but explicit entries signal intent to AI platforms.
- **Follow code example naming conventions** — human-facing docs (`docs/{usage,audit,integrations,adoption,migration}/*.md`, `README.md`) use domain-realistic story-telling names; AI-facing docs (`docs/llms.txt`, `docs/llms-full.txt`) use generic names (`old_func`/`new_func`, `old_arg`/`new_arg`); every paired example carries `# NEW API —` / `# DEPRECATED API —` orientation comments. See [Code Example Conventions](.github/CONTRIBUTING.md#code-example-conventions). `attrs_mapping` keys must be the original names callers use today — never a meta-name like `legacy_X` or `deprecated_X`.
- Ensure pre-commit hooks are installed (they run automatically on commit)
- Provide `deprecated_in` and `remove_in` versions
- Include migration messages in deprecation warnings
- Write tests first when fixing bugs (TDD)
- Cover happy path, failure path, and edge cases in tests

## 🔗 Complete Guidelines

This file provides quick reference for agents. For complete, authoritative guidelines:

- **Contribution workflow** → [Contributing Guide](.github/CONTRIBUTING.md)
- **Coding standards** → [Contributing: Coding Standards](.github/CONTRIBUTING.md#-coding-standards)
- **Testing requirements** → [Contributing: Tests and Quality](.github/CONTRIBUTING.md#-tests-and-quality-assurance)
- **PR process** → [Contributing: Pull Requests](.github/CONTRIBUTING.md#-pull-requests)
- **PR review process** → [Code-review skill](.github/skills/code-review/SKILL.md)
- **Security reporting** → [Security Policy](.github/SECURITY.md)
- **Community guidelines** → [Code of Conduct](.github/CODE_OF_CONDUCT.md)
- **Documentation site** → [Contributing: Documentation Site](.github/CONTRIBUTING.md#-documentation-site)
