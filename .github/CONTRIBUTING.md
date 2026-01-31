# Contributing Guide

Thank you for your interest in contributing to pyDeprecate! We appreciate all contributions and welcome everyone, regardless of experience level. Your help makes this project better for everyone.

> 💡 **First time contributing to open source?** Check out [First Contributions](https://github.com/firstcontributions/first-contributions) for a beginner-friendly guide that walks you through the entire process.

## 📖 Code of Conduct

Please read and follow our [Code of Conduct](CODE_OF_CONDUCT.md). We expect all contributors to be respectful, considerate, and help create a welcoming environment for everyone. This ensures our community remains inclusive and supportive for people from all backgrounds.

## 🎯 Ways to Contribute

There are many ways to contribute beyond writing code. Every contribution, no matter how small, makes a difference:

| Contribution            | Description                                                                                                                     |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| 🐛 Report bugs          | Found an issue? Let us know! Detailed bug reports help us fix problems faster.                                                  |
| 🔧 Fix bugs             | Implement fixes for reported issues. Great way to start contributing!                                                           |
| 💡 Suggest improvements | Propose enhancements, optimizations, or better approaches to existing functionality                                             |
| ✨ Build features       | Implement new features after getting maintainer approval                                                                        |
| 📚 Improve docs         | Fix typos, clarify explanations, or add examples. Good documentation makes the project accessible to more people.               |
| 👀 Review PRs           | Provide feedback on pull requests. Code reviews help maintain quality and catch potential issues early.                         |
| 💬 Answer questions     | Help others in discussions and issues. Your knowledge can help someone overcome a problem.                                      |
| ⭐ Spread the word      | Star the repo, share it with others, or write about your experience. This helps the project grow and attract more contributors. |

## 💭 Before You Start

### Read First

Taking time to understand the project first helps you contribute more effectively:

- **Documentation** — Learn how the project works and its key concepts. This prevents misunderstandings and ensures your contributions align with project goals.
- **Existing issues** — Your idea might already be discussed or even implemented. Searching first saves you time and effort.
- **Codebase** — Familiarize yourself with the code structure and style. This makes it easier to make changes that fit well with the existing code.

### Ask Questions

Don't hesitate to ask! Open an issue or use discussions to:

- Clarify project goals and scope — Make sure your contributions are aligned with the project's direction
- Understand implementation details — Get help with specific technical questions
- Get guidance on where to contribute — Find areas where your skills and interests can make the biggest impact

> 💡 Asking questions shows you're thoughtful and helps everyone learn together.

## 🐛 Reporting Bugs

When reporting a bug, providing clear and detailed information helps us fix it faster. Here's how:

1. **Search existing issues** — The bug might already be reported and being worked on.
2. **Create a minimal reproduction** — Provide a simple example that demonstrates the bug. This makes it easier for us to reproduce and fix the issue.
3. **Include environment details** — Tell us your operating system, Python version, and pyDeprecate version.
4. **Describe expected vs actual behavior** — Be specific about what you expected to happen and what actually occurred.

## 🔧 Fixing Bugs

Bug fixing is a great way to contribute! Here's how to get started:

1. **Find a bug to fix** — Look for issues labeled `bug` or `help wanted` in the issue tracker.
   - Check if the issue is already assigned to someone
   - If it's assigned but has no activity for a while, comment to ask if you can take it over
2. **Understand the problem** — Read the issue carefully and try to reproduce the bug.
3. **Investigate and fix** — Identify the root cause and implement a fix. Keep your changes focused on the specific issue.
4. **Test your fix** — Write or update tests to verify your fix works and prevents the bug from recurring.
5. **Submit a PR** — Create a pull request with your fix, linking to the issue it addresses.

> 🚀 **Pro tip:** Fixes with tests are more likely to be merged quickly!

## 💡 Suggesting Improvements

Improvements are enhancements to existing functionality that make the project better. They could be:

- Performance optimizations
- Code refactoring for better maintainability
- User experience enhancements
- Documentation improvements
- Process or workflow improvements

Here's how to suggest and implement improvements:

1. **Open an issue** — Describe the improvement you're suggesting. Explain the current situation, why it needs improvement, and your proposed solution.
2. **Provide context** — Include examples, use cases, or data to support your suggestion.
3. **Discuss and refine** — Engage in the issue discussion to clarify requirements and explore alternatives.
4. **Get feedback** — Wait for input from maintainers and the community. For small improvements, you may get rapid approval.
5. **Implement if approved** — If your suggestion is accepted, follow the standard development process.

## ✨ Building Features with Consensus

**Before implementing any new feature:**

1. **Open an issue first** — Clearly describe your idea, use case, and how it benefits the project.
   - Check if the issue is already assigned to someone
   - If it's assigned but has no activity for a while, comment to ask if you can take it over
2. **Wait for maintainer approval** — Look for a **👍 "go-ahead" reaction** or explicit approval from a maintainer.
3. **Discuss and refine** — Engage in the issue discussion to clarify requirements and explore alternatives.
4. **Get consensus** — Ensure there's general agreement from the community before starting implementation.

> ⚠️ **Critical:** Always get maintainer approval before implementing new features! This ensures your work aligns with project direction and won't be wasted effort. Features implemented without prior approval may be rejected.

**When you have approval:**

1. **Implement the feature** — Build the feature following the project's coding style and guidelines.
2. **Add tests** — Write comprehensive tests to ensure your feature works correctly.
3. **Update documentation** — Document how to use the new feature.
4. **Submit a PR** — Create a pull request, linking to the approved issue.

## 📬 Pull Requests

### Before Opening a PR

Complete this checklist before opening a pull request to ensure quality and smooth review:

- [ ] Followed existing code style
- [ ] Added tests for new functionality
- [ ] Ran tests locally and they pass
- [ ] Updated documentation if needed
- [ ] Self-reviewed my code
- [ ] Linked to related issue(s)

### Linking Issues

**Every PR should reference an issue.** This provides context and helps track progress. Use keywords like:

- `Fixes #123` — Closes the issue when PR is merged
- `Relates to #456` — Links without auto-closing

If no issue exists, open one first to discuss the change before implementing it.

### Keep PRs Focused

- **One PR = one logical change** — This makes your PR easier to review and understand
- **Smaller PRs are easier to review** — Large PRs can be overwhelming and take longer to merge
- **Split large changes into multiple PRs** — Break complex features into smaller, manageable pieces

## ✅ Tests and Quality Assurance

Tests and quality improvements are **always welcome**! These contributions are highly valuable because they:

- Improve project stability — Well-tested code is more reliable for everyone
- Help catch future regressions — Tests prevent issues from reoccurring
- Reduce maintainer burden — Comprehensive tests require less ongoing debugging

### Running Tests

```bash
# Install in development mode
pip install -e ".[test]"

# Run tests
pytest src/ tests/

# Run linting
pre-commit run --all-files
```

## 💎 Quality Expectations

> 🙌 **Always do your best — that's the essential spirit of OSS contributions.**

We value all levels of contribution and want to encourage everyone, regardless of skill level or time available. What matters is being reasonable and meaningful about what you can deliver:

- **Start small and iterate** — Better to do smaller tasks piece by piece than take too much and leave it unfinished. Small contributions add up over time.
- **Break work into steps** — This allows others to take over and continue if needed. It also makes your work more approachable for reviewers.
- **Avoid abandoned PRs** — Forked PRs are difficult to carry over except by maintainers, creating significant burden. If you can't complete a PR, let us know.
- **Be meaningful and reasonable** — Contribute what you can realistically complete. Even small improvements make a difference.

### Deprecation Specifics

When contributing to `pyDeprecate`, ensure:

- New deprecation logic is tested against multiple Python versions (if possible).
- Warnings are correctly triggered and contain helpful migration messages.
- The `target` and `deprecated_in` / `remove_in` arguments are used consistently.

We don't expect perfection. We expect genuine effort. If you're unsure about something, ask! The community is here to help.

## 🚀 Quick Start

```bash
# 1. Fork the repository

# 2. Clone your fork
git clone https://github.com/YOUR-USERNAME/pyDeprecate.git
cd pyDeprecate

# 3. Create a feature branch
git checkout -b feature/amazing-feature

# 4. Install in development mode
pip install -e . "pre-commit" -r tests/requirements.txt
pre-commit install

# 5. Make your changes

# 6. Run tests
pytest tests/

# 7. Stage and commit your changes
git status        # Check which files have been changed
git add -A        # Stage all modified and new files
git commit -m "Add amazing feature"  # Commit the staged changes

# 8. Push to your fork
git push origin feature/amazing-feature

# 9. Open a Pull Request
```

## 📋 Coding Standards

- Follow [PEP 8](https://pep8.org/) style guidelines
- Write clear, descriptive docstrings (Google or NumPy style)
- Keep functions focused and modular
- Add type hints where appropriate
- Write meaningful variable and function names
- Add comments if the code is not self-explanatory

## 📄 License

By contributing, you agree that your contributions will be licensed under the same license as the project (see [LICENSE](../LICENSE) file).

______________________________________________________________________

<div align="center">

**Questions about security?** See our [Security Policy](SECURITY.md) for reporting vulnerabilities.

Made with 💙 by pyDeprecate contributors.

</div>
