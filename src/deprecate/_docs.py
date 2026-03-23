"""Docstring formatting and insertion helpers for deprecation notices."""

import inspect
from typing import Callable, Literal, cast

from deprecate._types import DeprecationConfig

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


def _detect_body_indent(lines: list[str]) -> str:
    """Return the common indentation prefix used in the docstring body.

    Examines lines after the first (which is always unindented in ``__doc__``)
    and returns the leading whitespace of the first non-empty body line.
    """
    for line in lines[1:]:
        if line.strip():
            return line[: len(line) - len(line.lstrip())]
    return ""


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


def _update_docstring_with_deprecation(wrapped_fn: Callable) -> None:
    """Inject deprecation notice into function's docstring.

    This helper automatically generates and injects a deprecation notice into the
    wrapped function's docstring. The notice includes version information and target
    replacement (if applicable), making it visible in generated API documentation.

    By default, the notice uses Sphinx's deprecated directive format:
        .. deprecated:: <version>
           Will be removed in <version>.
           Use `<target>` instead.

    When ``docstring_style`` is set to ``"mkdocs"``/``"markdown"``, it emits a
    Markdown admonition:
        !!! warning "Deprecated in <version>"
            Will be removed in <version>.
            Use `<target>` instead.

    Args:
        wrapped_fn: Function whose docstring should be updated. Must have
            __deprecated__ attribute set with deprecation metadata.

    Returns:
        None. Modifies the function's __doc__ attribute in-place.

    Metadata Used:
        The function's ``__deprecated__`` attribute should be a
        :class:`~deprecate._types.DeprecationConfig` instance with:
        - deprecated_in: Version when deprecated
        - remove_in: Version when will be removed
        - target: Replacement callable (optional)

    Example:
        >>> def new_func(): pass
        >>> def old_func():
        ...     '''Original docstring.'''
        ...     pass
        >>> old_func.__deprecated__ = DeprecationConfig(
        ...     deprecated_in='1.0',
        ...     remove_in='2.0',
        ...     target=new_func,
        ... )
        >>> _update_docstring_with_deprecation(old_func)
        >>> print(old_func.__doc__) # doctest: +ELLIPSIS +NORMALIZE_WHITESPACE
        Original docstring.
        <BLANKLINE>
        .. deprecated:: 1.0
           Will be removed in 2.0.
           Use :func:`...new_func` instead.

    Note:
        Does nothing if the function has no docstring or no ``__deprecated__`` attribute.
        To preserve Google/NumPy parsing, insertion happens before the first
        section header (for example ``Args:`` or ``Parameters``) when detected.

    """
    if not hasattr(wrapped_fn, "__doc__") or not wrapped_fn.__doc__:
        return
    if not hasattr(wrapped_fn, "__deprecated__"):
        return
    lines = wrapped_fn.__doc__.splitlines()
    dep_info = cast(DeprecationConfig, getattr(wrapped_fn, "__deprecated__"))
    remove_in_val = dep_info.remove_in
    target_val = dep_info.target
    remove_text = f"Will be removed in {remove_in_val}." if remove_in_val else ""
    target_text_rst = ""
    target_text_mkdocs = ""
    if callable(target_val):
        full_target_name = f"{target_val.__module__}.{target_val.__name__}"
        ref_type = "class" if inspect.isclass(target_val) else "func"
        target_text_rst = f"Use :{ref_type}:`{full_target_name}` instead."
        target_text_mkdocs = f"Use `{full_target_name}` instead."

    docstring_style = normalize_docstring_style(dep_info.docstring_style)
    docstring_template = TEMPLATE_DOC_DEPRECATED_RST
    target_text = target_text_rst
    if docstring_style == "mkdocs":
        docstring_template = TEMPLATE_DOC_DEPRECATED_MKDOCS
        target_text = target_text_mkdocs

    deprecation_lines = []
    for line in docstring_template:
        if line.strip().endswith("%(remove_text)s") and not remove_text:
            continue
        if line.strip().endswith("%(target_text)s") and not target_text:
            continue
        deprecation_lines.append(
            line
            % {
                "deprecated_in": dep_info.deprecated_in,
                "remove_text": remove_text,
                "target_text": target_text,
            }
        )
    insert_idx = find_docstring_insertion_index(lines)
    body_indent = _detect_body_indent(lines)
    deprecation_lines = [body_indent + ln for ln in deprecation_lines]
    prefix = lines[:insert_idx]
    suffix = lines[insert_idx:]
    if prefix and prefix[-1].strip():
        prefix.append("")
    prefix.extend(deprecation_lines)
    if suffix:
        if suffix[0].strip():
            prefix.append("")
        prefix.extend(suffix)
    wrapped_fn.__doc__ = "\n".join(prefix)
