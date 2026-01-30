"""Package metadata and version information.

This module contains all package metadata including version, author information,
and links to documentation and source code.
"""

__version__ = "0.5.0rc0"
__docs__ = "Deprecation tooling"
__author__ = "Jiri Borovec"
__author_email__ = "j.borovec+github[at]gmail.com"
__homepage__ = "https://borda.github.io/pyDeprecate"
__source_code__ = "https://github.com/Borda/pyDeprecate"
__license__ = "Apache-2.0"
__copyright__ = f"Copyright (C) 2020-2026 {__author__}."
__long_doc__ = """
The pyDeprecate is a lightweight Python library for managing function and class deprecations with zero dependencies.
 It provides automatic call forwarding to replacement functions, argument mapping between old and new APIs,
 and configurable warning controls to prevent log spam. Key features include support for cross-module deprecations,
 conditional deprecation logic, automatic docstring updates, and testing utilities. Perfect for library maintainers
 who need to evolve their APIs while maintaining backward compatibility and providing clear migration paths for users.
"""

__all__ = [
    "__version__",
    "__docs__",
    "__author__",
    "__author_email__",
    "__homepage__",
    "__source_code__",
    "__license__",
    "__copyright__",
    "__long_doc__",
]
