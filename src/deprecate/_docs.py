"""Docstring formatting and insertion helpers for deprecation notices."""

from typing import Literal, cast

#: Default templates for documentation with deprecated callable
TEMPLATE_DOC_DEPRECATED_RST = [
    ".. deprecated:: %(deprecated_in)s",
    "   %(remove_text)s",
    "   %(target_text)s",
]
TEMPLATE_DOC_DEPRECATED_MKDOCS = [
    '!!! warning "Deprecated in %(deprecated_in)s"',
    "    %(remove_text)s",
    "    %(target_text)s",
]
DOCSTRING_STYLE_ALIASES = {"rst": "rst", "mkdocs": "mkdocs", "markdown": "mkdocs"}
SUPPORTED_DOCSTRING_STYLES = "', '".join(sorted(DOCSTRING_STYLE_ALIASES))
GOOGLE_DOCSTRING_SECTIONS = {
    "args:",
    "arguments:",
    "attributes:",
    "examples:",
    "notes:",
    "parameters:",
    "raises:",
    "returns:",
    "warns:",
    "yields:",
}
NUMPY_DOCSTRING_SECTIONS = {
    "attributes",
    "examples",
    "methods",
    "notes",
    "other parameters",
    "parameters",
    "raises",
    "returns",
    "warns",
    "yields",
}


def is_numpy_underline(line: str) -> bool:
    """Return ``True`` when the line is a NumPy-style section underline."""
    stripped = line.strip()
    return len(stripped) >= 3 and all(char == "-" for char in stripped)


def find_docstring_insertion_index(lines: list[str]) -> int:
    """Find insertion index before first Google/NumPy section header."""
    for idx, line in enumerate(lines):
        if line.strip().lower() in GOOGLE_DOCSTRING_SECTIONS:
            return idx
        if (
            idx + 1 < len(lines)
            and line.strip().lower() in NUMPY_DOCSTRING_SECTIONS
            and is_numpy_underline(lines[idx + 1])
        ):
            return idx
    return len(lines)


def normalize_docstring_style(docstring_style: str) -> Literal["rst", "mkdocs"]:
    """Validate and normalize docstring style aliases."""
    if not isinstance(docstring_style, str):
        raise ValueError(
            "Invalid `docstring_style` value "
            f"{docstring_style!r}. Supported styles are: '{SUPPORTED_DOCSTRING_STYLES}'."
        )
    normalized_style = DOCSTRING_STYLE_ALIASES.get(docstring_style.lower())
    if normalized_style is None:
        raise ValueError(
            "Invalid `docstring_style` value "
            f"{docstring_style!r}. Supported styles are: '{SUPPORTED_DOCSTRING_STYLES}'."
        )
    return cast(Literal["rst", "mkdocs"], normalized_style)
