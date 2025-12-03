# Contributing to pyDeprecate

Thank you for considering contributing to pyDeprecate! We welcome contributions from everyone.

## Code of Conduct

This project adheres to the Contributor Covenant [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## How Can I Contribute?

### Reporting Bugs

Before creating bug reports, please check existing issues to avoid duplicates. When creating a bug report, include:

- **Clear title and description** of the issue
- **Steps to reproduce** the behavior
- **Expected behavior** vs actual behavior
- **Environment details** (Python version, OS, package version)
- **Code samples** if applicable

### Suggesting Enhancements

Enhancement suggestions are tracked as GitHub issues. When creating an enhancement suggestion, include:

- **Clear description** of the proposed feature
- **Use cases** explaining why this would be useful
- **Possible implementation** approach (if you have ideas)

### Pull Requests

1. **Fork the repository** and create your branch from `main`
2. **Install dependencies**: `pip install -e ".[test]"`
3. **Make your changes** following our coding standards
4. **Add tests** for any new functionality
5. **Ensure tests pass**: `pytest src/ tests/`
6. **Update documentation** if needed
7. **Commit your changes** with clear, descriptive commit messages
8. **Push to your fork** and submit a pull request

#### Pull Request Guidelines

- Keep changes focused - one feature/fix per PR
- Write clear commit messages describing what and why
- Include tests for new features or bug fixes
- Update docstrings and documentation as needed
- Ensure all tests pass and code follows the existing style
- Reference any related issues in the PR description

## Development Setup

```bash
# Clone your fork
git clone https://github.com/YOUR-USERNAME/pyDeprecate.git
cd pyDeprecate

# Install in development mode
pip install -e ".[test]"

# Run tests
pytest src/ tests/

# Run linting (if configured)
pre-commit run --all-files
```

## Coding Standards

- Follow [PEP 8](https://pep8.org/) style guidelines
- Write clear, descriptive docstrings (Google or NumPy style)
- Keep functions focused and modular
- Add type hints where appropriate
- Write meaningful variable and function names

## Testing

- Write tests for new features and bug fixes
- Ensure existing tests still pass
- Aim for good test coverage
- Test edge cases and error conditions

## Documentation

- Update docstrings for any modified functions/classes
- Update README.md if adding new features
- Include examples in docstrings when helpful
- Keep documentation clear and concise

## Questions?

Feel free to open an issue for questions or clarifications. We're here to help!

## License

By contributing, you agree that your contributions will be licensed under the same license as the project (see [LICENSE](../LICENSE) file).
