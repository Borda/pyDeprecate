"""Helpers for annotating function docstrings with deprecation information.

This module owns all logic that reads or modifies a callable's ``__doc__``
attribute during deprecation decoration.  It supports both Google-style
(``Args:`` / ``Arguments:``) and Sphinx-style (``:param ...::``) docstrings.

Key Components:
    - String constants (``TEMPLATE_DOC_*``) for reusable message fragments.
    - Per-argument note builder: :func:`_build_arg_deprecation_note`
    - Google-style section helpers: :func:`_find_google_args_section`,
      :func:`_get_google_arg_indents`, :func:`_find_google_arg_line`
    - Shared continuation-line helper: :func:`_find_entry_end`
    - Annotators: :func:`_annotate_google_style_arg`,
      :func:`_annotate_sphinx_style_arg`
    - Orchestrator: :func:`_update_docstring_with_deprecation`

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>
"""

import inspect
import re
from typing import Callable, Optional, cast

from deprecate._types import DeprecationConfig

#: Default template for documentation with deprecated callable
TEMPLATE_DOC_DEPRECATED = """
.. deprecated:: %(deprecated_in)s
   %(remove_text)s
   %(target_text)s
"""
#: Inline docstring note for a deprecated argument — with ``deprecated_in`` version
TEMPLATE_DOC_ARG_DEPRECATED_SINCE = "Deprecated since v%(deprecated_in)s \u2014 %(reason)s."
#: Inline docstring note for a deprecated argument — without version information
TEMPLATE_DOC_ARG_DEPRECATED = "Deprecated \u2014 %(reason)s."
#: Suffix appended to arg deprecation note when ``remove_in`` is provided
TEMPLATE_DOC_ARG_REMOVE = " Will be removed in v%(remove_in)s."
#: Reason phrase used when the deprecated argument is simply removed
TEMPLATE_DOC_ARG_REASON_REMOVED = "no longer used"
#: Reason phrase used when the deprecated argument is renamed; ``%(new_arg)s`` is substituted
TEMPLATE_DOC_ARG_REASON_RENAMED = "use `%(new_arg)s` instead"


def _build_arg_deprecation_note(new_arg: Optional[str], deprecated_in: str, remove_in: str) -> str:
    """Build an inline deprecation note string for a single deprecated argument.

    Args:
        new_arg: Replacement argument name, or ``None`` when the argument is simply removed.
        deprecated_in: Version string when the argument was deprecated (e.g. ``"1.8"``).
        remove_in: Version string when the argument will be removed (e.g. ``"1.9"``).

    Returns:
        A one-line deprecation note suitable for embedding in a docstring.

    Example:
        >>> _build_arg_deprecation_note(None, "1.8", "1.9")
        'Deprecated since v1.8 — no longer used. Will be removed in v1.9.'
        >>> _build_arg_deprecation_note("new_arg", "1.8", "1.9")
        'Deprecated since v1.8 — use `new_arg` instead. Will be removed in v1.9.'

    """
    reason = TEMPLATE_DOC_ARG_REASON_RENAMED % {"new_arg": new_arg} if new_arg else TEMPLATE_DOC_ARG_REASON_REMOVED
    template = TEMPLATE_DOC_ARG_DEPRECATED_SINCE if deprecated_in else TEMPLATE_DOC_ARG_DEPRECATED
    note = template % {"deprecated_in": deprecated_in, "reason": reason}
    if remove_in:
        note += TEMPLATE_DOC_ARG_REMOVE % {"remove_in": remove_in}
    return note


def _find_google_args_section(lines: list[str]) -> tuple[int, int]:
    """Return ``(section_start, section_indent)`` for a Google-style ``Args:`` header.

    Scans *lines* for an ``Args:`` or ``Arguments:`` header and returns its line
    index and leading indentation.  Returns ``(-1, 0)`` when not found.

    Args:
        lines: Docstring already split into individual lines.

    Returns:
        A 2-tuple ``(section_start, section_indent)``.

    """
    for i, line in enumerate(lines):
        if line.strip() in ("Args:", "Arguments:"):
            return i, len(line) - len(line.lstrip())
    return -1, 0


def _get_google_arg_indents(lines: list[str], section_start: int, section_indent: int) -> tuple[int, int]:
    """Return ``(arg_indent, continuation_indent)`` for a Google-style Args section.

    *arg_indent* is the column at which individual argument entries begin.
    *continuation_indent* is the column used for continuation lines within an
    argument entry (defaults to ``arg_indent + 4`` when not detectable).
    Returns ``(-1, -1)`` when the section has no non-empty child lines.

    Args:
        lines: Docstring already split into individual lines.
        section_start: Line index of the ``Args:`` / ``Arguments:`` header.
        section_indent: Leading indentation of that header line.

    Returns:
        A 2-tuple ``(arg_indent, continuation_indent)``.

    """
    arg_indent = -1
    for i in range(section_start + 1, len(lines)):
        if lines[i].strip():
            current_indent = len(lines[i]) - len(lines[i].lstrip())
            if current_indent > section_indent:
                arg_indent = current_indent
            break

    if arg_indent == -1:
        return -1, -1

    continuation_indent = arg_indent + 4
    for i in range(section_start + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        current_indent = len(line) - len(line.lstrip())
        if current_indent <= section_indent:
            break
        if current_indent > arg_indent:
            continuation_indent = current_indent
            break

    return arg_indent, continuation_indent


def _find_google_arg_line(
    lines: list[str], section_start: int, section_indent: int, arg_indent: int, arg_name: str
) -> int:
    """Return the line index of *arg_name* inside a Google-style Args section.

    Returns ``-1`` when *arg_name* is not found within the section.

    Args:
        lines: Docstring already split into individual lines.
        section_start: Line index of the ``Args:`` / ``Arguments:`` header.
        section_indent: Leading indentation of that header line.
        arg_indent: Leading indentation of argument entry lines.
        arg_name: Name of the argument to locate.

    Returns:
        Line index of the matching argument entry, or ``-1``.

    """
    for i in range(section_start + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        current_indent = len(line) - len(line.lstrip())
        if current_indent <= section_indent:
            break
        if current_indent == arg_indent:
            rest = line[arg_indent:]
            has_arg_boundary = len(rest) > len(arg_name) and rest[len(arg_name)] in " :(,"
            if rest == arg_name or (rest.startswith(arg_name) and has_arg_boundary):
                return i
    return -1


def _find_entry_end(lines: list[str], entry_idx: int, entry_indent: int) -> int:
    """Return the index of the last continuation line for a docstring entry.

    Scans forward from *entry_idx + 1* and stops at the first blank line or
    the first line whose indentation is ``<= entry_indent``.

    Args:
        lines: Docstring already split into individual lines.
        entry_idx: Line index of the entry's opening line.
        entry_indent: Leading indentation of that opening line.

    Returns:
        Index of the last line belonging to the entry (>= *entry_idx*).

    """
    end_idx = entry_idx
    for i in range(entry_idx + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            break
        if len(line) - len(line.lstrip()) <= entry_indent:
            break
        end_idx = i
    return end_idx


def _annotate_google_style_arg(lines: list[str], arg_name: str, note: str) -> tuple[list[str], bool]:
    """Find *arg_name* in a Google-style ``Args:`` section and insert *note* below it.

    Supports both ``Args:`` and ``Arguments:`` section headers.  Returns the
    original list unchanged when no matching section or argument entry is found.

    Args:
        lines: Docstring already split into individual lines.
        arg_name: Name of the deprecated argument to locate.
        note: Text to insert as a continuation line under the matched entry.

    Returns:
        A 2-tuple ``(new_lines, found)`` where *found* is ``True`` when the
        argument was located and the note was successfully inserted.

    The note is inserted as a continuation line directly after the matched entry::

        Args:
            lr (float): Learning rate.
            old_cfg (object): Old config.
                Deprecated — use cfg instead.  # <-- inserted here

    """
    section_start, section_indent = _find_google_args_section(lines)
    if section_start == -1:
        return lines, False

    arg_indent, continuation_indent = _get_google_arg_indents(lines, section_start, section_indent)
    if arg_indent == -1:
        return lines, False

    arg_line_idx = _find_google_arg_line(lines, section_start, section_indent, arg_indent, arg_name)
    if arg_line_idx == -1:
        return lines, False

    end_idx = _find_entry_end(lines, arg_line_idx, arg_indent)
    # Idempotency guard: skip insertion when the note is already present under this arg entry.
    if any(note in ln for ln in lines[arg_line_idx : end_idx + 1]):
        return lines, True
    note_line = " " * continuation_indent + note
    new_lines = lines[: end_idx + 1] + [note_line] + lines[end_idx + 1 :]
    return new_lines, True


def _annotate_sphinx_style_arg(lines: list[str], arg_name: str, note: str) -> tuple[list[str], bool]:
    """Find ``:param arg_name:`` in a Sphinx-style docstring and insert *note* below it.

    Supports both ``:param arg_name:`` and ``:param SomeType arg_name:`` forms.
    Returns the original list unchanged when no matching ``:param`` field is found.

    Args:
        lines: Docstring already split into individual lines.
        arg_name: Name of the deprecated argument to locate.
        note: Text to insert as a continuation line under the matched field.

    Returns:
        A 2-tuple ``(new_lines, found)`` where *found* is ``True`` when the
        parameter was located and the note was successfully inserted.

    The note is inserted as a continuation line directly after the matched field::

        :param lr: Learning rate.
        :param old_cfg: Old config.
            Deprecated — use cfg instead.  # <-- inserted here
        :returns: Result.

    """
    pattern = re.compile(r"^(\s*):param\s+(?:\S+\s+)?" + re.escape(arg_name) + r"\s*:")

    param_line_idx = -1
    param_indent = 0
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if m:
            param_line_idx = i
            param_indent = len(m.group(1))
            break

    if param_line_idx == -1:
        return lines, False

    end_idx = _find_entry_end(lines, param_line_idx, param_indent)
    # Idempotency guard: skip insertion when the note is already present under this param entry.
    if any(note in ln for ln in lines[param_line_idx : end_idx + 1]):
        return lines, True
    note_line = " " * (param_indent + 4) + note
    new_lines = lines[: end_idx + 1] + [note_line] + lines[end_idx + 1 :]
    return new_lines, True


def _update_docstring_with_deprecation(wrapped_fn: Callable) -> None:
    """Annotate a function's docstring with deprecation information.

    Two paths are taken depending on whether ``args_mapping`` is set:

    - **Inline arg path** (``args_mapping`` present): each deprecated argument is
      located in the ``Args:`` / ``Arguments:`` (Google style) or ``:param``
      (Sphinx style) section and a one-line deprecation note is inserted directly
      beneath it.  When ``target`` is ``True`` (self-deprecation) and all deprecated
      args are found, the function returns early and no general ``.. deprecated::``
      block is appended.  When ``target`` is a callable or ``None``, the general
      block is always appended after the inline annotations because the function
      itself is deprecated and Sphinx tooling expects the directive.
    - **General notice path** (no ``args_mapping``, or at least one arg was not
      found in the docstring): a Sphinx ``.. deprecated::`` directive is appended
      at the end of the docstring.

    Args:
        wrapped_fn: Function whose docstring should be updated. Must have
            ``__deprecated__`` attribute set with deprecation metadata.

    Returns:
        None. Modifies the function's ``__doc__`` attribute in-place.

    Metadata Used:
        The function's ``__deprecated__`` attribute should be a
        :class:`~deprecate._types.DeprecationConfig` instance with:
        - deprecated_in: Version when deprecated
        - remove_in: Version when will be removed
        - target: Replacement callable (optional)
        - args_mapping: Mapping of old → new argument names (optional)

    Example:
        General notice path — no ``args_mapping``:

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
           Use :func:`deprecate._docs.new_func` instead.

        Inline arg path — self-deprecation (``target=True``) with all args found:

        >>> def fn_with_args(x: int, old_arg: str = "") -> str:
        ...     '''Do something.
        ...
        ...     Args:
        ...         x: The main input.
        ...         old_arg: The old argument.
        ...     '''
        ...     return str(x)
        >>> fn_with_args.__deprecated__ = DeprecationConfig(
        ...     deprecated_in='1.0',
        ...     remove_in='2.0',
        ...     target=True,
        ...     args_mapping={'old_arg': None},
        ... )
        >>> _update_docstring_with_deprecation(fn_with_args)
        >>> 'Deprecated since v1.0' in fn_with_args.__doc__
        True
        >>> '.. deprecated::' in fn_with_args.__doc__
        False

    Note:
        Does nothing if the function has no docstring or no ``__deprecated__`` attribute.

    """
    if not hasattr(wrapped_fn, "__doc__") or not wrapped_fn.__doc__:
        return
    if not hasattr(wrapped_fn, "__deprecated__"):
        return
    lines = wrapped_fn.__doc__.splitlines()
    dep_info = cast(DeprecationConfig, getattr(wrapped_fn, "__deprecated__"))

    # When args_mapping is present, try to annotate each deprecated argument inline.
    if dep_info.args_mapping:
        all_args_found = True
        for arg_name, new_arg in dep_info.args_mapping.items():
            note = _build_arg_deprecation_note(new_arg, dep_info.deprecated_in, dep_info.remove_in)
            lines, found = _annotate_google_style_arg(lines, arg_name, note)
            if not found:
                lines, found = _annotate_sphinx_style_arg(lines, arg_name, note)
            if not found:
                # Arg not found in docstring — the general notice is needed.
                # Note: `lines` may already contain inline notes from earlier
                # iterations (args that *were* found).  The general notice is
                # appended on top of those partial annotations.
                all_args_found = False
        # Only skip the general .. deprecated:: block for self-deprecation
        # (target=True).  When target is a callable or None the function
        # itself is deprecated and the general directive must be kept.
        if all_args_found and dep_info.target is True:
            wrapped_fn.__doc__ = "\n".join(lines)
            return

    remove_in_val = dep_info.remove_in
    target_val = dep_info.target
    remove_text = f"Will be removed in {remove_in_val}." if remove_in_val else ""
    target_text = ""
    if callable(target_val):
        ref_type = "class" if inspect.isclass(target_val) else "func"
        target_text = f"Use :{ref_type}:`{target_val.__module__}.{target_val.__name__}` instead."
    lines.append(
        TEMPLATE_DOC_DEPRECATED
        % {
            "deprecated_in": dep_info.deprecated_in,
            "remove_text": remove_text,
            "target_text": target_text,
        }
    )
    wrapped_fn.__doc__ = "\n".join(lines)
