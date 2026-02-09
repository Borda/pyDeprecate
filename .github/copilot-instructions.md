# Copilot Instructions for pyDeprecate

> **Coding guidelines:** See [AGENTS.md](../AGENTS.md) for key constraints and [CONTRIBUTING.md](CONTRIBUTING.md) for full details.

## PR Review Guidelines

When reviewing PRs, follow this structured format for consistent, actionable feedback.

### 1. Overall Recommendation

Start with a clear, actionable recommendation and a **specific** justification:

- 🟢 **Approve** — ready to merge as-is
- 🟡 **Minor Suggestions** — minor improvements recommended but not blocking
- 🟠 **Request Changes** — significant issues must be addressed before merge
- 🔴 **Block** — critical issues require major rework

### 2. PR Completeness Check

Verify the PR includes (mark ✅ complete, ⚠️ incomplete, ❌ missing, 🔵 N/A):

- [ ] Clear description of what changed and why
- [ ] Link to related issue (`Fixes #N` or `Relates to #N`)
- [ ] Tests added/updated for new functionality
- [ ] Docstrings for new public functions/classes (Google-style)
- [ ] All CI checks pass

Call out missing items explicitly with inline comments on relevant files.

### 3. Quality Assessment

Score each dimension (n/5) with specific feedback via **GitHub inline comments**:

- **Code quality** — correctness, edge case handling, idiomatic Python, type hints
- **Testing quality** — coverage of happy path, failure path, and edge cases; specific assertions; correct test file placement (`collection_targets.py` / `collection_deprecate.py` / `test_*.py`)
- **Documentation quality** — complete docstrings, updated docs for new features

### 4. Risk Assessment

Flag any risks with severity:

- **Breaking changes** — changes to public APIs, removed features (must include migration instructions)
- **Performance impact** — inefficient algorithms, memory-intensive operations
- **Compatibility** — new Python version requirements, platform-specific code
- **Architecture** — new runtime dependencies (not allowed), circular imports, expensive module-level code

### 5. Suggestions

Provide **specific, actionable** improvements using GitHub inline comments with suggestion format:

````markdown
```suggestion
if data is None:
    return None
return process(data)
```
````

Reference suggestions in the review summary with permalinks.

### Review Best Practices

- Explain *why* something is a problem, not just *what* is wrong
- Distinguish between blocking issues and nice-to-haves
- Acknowledge good work — don't focus only on what's wrong
- Be pragmatic — don't let perfect be the enemy of good
- Use inline comments/suggestions directly on code (they persist across edits)

### Review Summary Template

```markdown
## Review Summary

### Recommendation
[emoji] [Status] — [One-sentence justification]

### PR Completeness
- ✅ Complete: [list]
- ❌ Missing: [list with links to inline comments]

### Quality Scores
- Code Quality: n/5 — [reason]
- Testing: n/5 — [reason]
- Documentation: n/5 — [reason]

### Risk Level: n/5
[Brief risk description]

### Critical Issues (Must Fix)
1. [Issue with link to inline comment]

### Suggestions (Nice to Have)
1. [Suggestion with link to inline comment]
```
