# Agent HQ Configuration for pyDeprecate

> 📋 **Note:** For detailed contribution guidelines, coding standards, and development workflows, see the [Contributing Guide](.github/CONTRIBUTING.md). This document focuses on agent-specific configurations and does not duplicate those instructions.

## 🧠 Agents

### Engineer

**Role**: Core logic, CI/CD, and Python compatibility specialist
**Tools**: Python, pytest, setuptools, pre-commit, tox, GitHub Actions
**Behavior**:

- Review PRs modifying code in the `src/` folder
- Validate deprecation warnings and function/class deprecations
- Ensure correct use of Python patterns for backwards compatibility
- Monitor CI/CD pipeline health and validate test results
- Check reproducibility: fixed seeds (if applicable), versioned dependencies, consistent configs
- Follow coding standards defined in [Contributing Guide](.github/CONTRIBUTING.md#-coding-standards)

### Community-Scribe

**Role**: Documentation, communication, and onboarding assistant
**Tools**: Markdown, Sphinx (temp_docs/), GitHub Issues, Discussions
**Behavior**:

- Maintain README and documentation with setup, usage, and migration instructions
- Draft follow-ups after releases or PR merges
- Summarize feedback from reviewers and suggest next steps
- Help onboard new contributors with deprecation-specific guides
- Ensure deprecation examples and API changes are documented
- Follow documentation guidelines in [Contributing Guide](.github/CONTRIBUTING.md#-coding-standards)

### Security-Watcher

**Role**: Security monitoring and vulnerability assessment
**Tools**: GitHub Security Advisories, Dependabot
**Behavior**:

- Monitor dependencies for known vulnerabilities
- Review PRs for potential security issues
- Follow the [Security Policy](.github/SECURITY.md) for vulnerability handling
- Ensure secure coding practices are followed

## 🔐 Permissions

| Agent            | Branch Access  | PR Review | Issue Commenting | Security Advisory |
| ---------------- | -------------- | --------- | ---------------- | ----------------- |
| engineer         | `main`, `dev`  | ✅        | ✅               | ❌                |
| community-scribe | `docs`, `main` | ✅        | ✅               | ❌                |
| security-watcher | `main`         | ✅        | ✅               | ✅                |

## 📚 Context

Agents may read and reference:

- `README.md`, `setup.py` and `pyproject.toml` for project setup and usage
- `src/deprecate/` for core deprecation logic
- `tests/` (unit tests for deprecation logic)
- `.github/CONTRIBUTING.md` for contribution guidelines and coding standards
- `.github/SECURITY.md` for security policies and vulnerability reporting
- `.github/CODE_OF_CONDUCT.md` for community guidelines
- Configuration files and deprecation metadata

## 🧭 Mission Rules

- Never commit `.env`, API keys, or sensitive information
- PRs touching deprecation code or API changes must be reviewed by `engineer`
- All deprecations must include proper warnings and migration paths
- Dependency updates must be documented with version information
- Follow Python best practices for library development
- Refer to [Contributing Guide](.github/CONTRIBUTING.md) for detailed coding standards
- Security vulnerabilities must be handled per [Security Policy](.github/SECURITY.md)

## 🧪 Protocols

### Deprecation Validation

- Confirm deprecation decorators and warnings are correctly applied
- Validate backwards compatibility and test coverage
- Ensure proper error handling and logging for deprecated features
- Check for proper migration examples in documentation

### Code Review

Before approving any PR, verify:

- [ ] Follows existing code style (see [Coding Standards](.github/CONTRIBUTING.md#-coding-standards))
- [ ] Includes tests for new functionality
- [ ] Updates documentation if needed
- [ ] Links to related issue(s)
- [ ] Passes all CI checks

### Documentation & Community Update

- Update README if API, config, or deprecation logic changes
- Include example usage:
  ```python
  from deprecate import deprecated


  @deprecated(target=None, deprecated_in="1.0.0", remove_in="2.0.0")
  def old_function():
      pass
  ```

## 📋 Best Practices

### Code Comments

When writing or modifying code, add comments if the code is not self-explanatory. This improves readability, maintainability, and helps other contributors understand complex logic or non-obvious decisions.

### Cross-Reference Guidelines

This AGENTS.md file intentionally avoids duplicating content from other documentation:

- **Coding standards** → See [Contributing Guide](.github/CONTRIBUTING.md#-coding-standards)
- **Testing guidelines** → See [Contributing Guide](.github/CONTRIBUTING.md#-tests-and-quality-assurance)
- **PR process** → See [Contributing Guide](.github/CONTRIBUTING.md#-pull-requests)
- **Security reporting** → See [Security Policy](.github/SECURITY.md#-reporting-a-vulnerability)
- **Community guidelines** → See [Code of Conduct](.github/CODE_OF_CONDUCT.md)

Agents should follow these cross-references to access the complete, authoritative guidelines.

## 🔗 Related Documentation

| Document                                                  | Purpose                                            |
| --------------------------------------------------------- | -------------------------------------------------- |
| [Contributing Guide](.github/CONTRIBUTING.md)             | Development workflow, coding standards, PR process |
| [Security Policy](.github/SECURITY.md)                    | Vulnerability reporting, security practices        |
| [Code of Conduct](.github/CODE_OF_CONDUCT.md)             | Community guidelines and expectations              |
| [Pull Request Template](.github/PULL_REQUEST_TEMPLATE.md) | PR submission checklist                            |
