"""Unit tests for private helpers in deprecate._docs."""

from deprecate._docs import (
    _annotate_google_style_arg,
    _annotate_sphinx_style_arg,
    _build_arg_deprecation_note,
    _find_google_arg_line,
    _find_google_args_section,
    _get_google_arg_indents,
    _update_docstring_with_deprecation,
)
from deprecate._types import DeprecationConfig


class TestBuildArgDeprecationNote:
    """Tests for _build_arg_deprecation_note — builds the inline arg-deprecation string."""

    def test_removed_arg_with_versions(self) -> None:
        """Removed arg (new_arg=None) uses 'no longer used' reason."""
        note = _build_arg_deprecation_note(None, "1.8", "1.9")
        assert note == "Deprecated since v1.8 — no longer used. Will be removed in v1.9."

    def test_renamed_arg_with_versions(self) -> None:
        """Renamed arg includes the new argument name in the reason."""
        note = _build_arg_deprecation_note("new_arg", "1.8", "1.9")
        assert note == "Deprecated since v1.8 — use `new_arg` instead. Will be removed in v1.9."

    def test_no_deprecated_in(self) -> None:
        """When deprecated_in is empty, 'since v...' is omitted."""
        note = _build_arg_deprecation_note(None, "", "2.0")
        assert "since" not in note
        assert "Will be removed in v2.0." in note

    def test_no_remove_in(self) -> None:
        """When remove_in is empty, 'Will be removed...' is omitted."""
        note = _build_arg_deprecation_note(None, "1.8", "")
        assert "Will be removed" not in note
        assert "Deprecated since v1.8" in note


class TestFindGoogleArgLine:
    """Tests for _find_google_arg_line — locates an arg entry inside a Google-style Args section."""

    def test_finds_exact_match(self) -> None:
        """Returns the line index of the exact arg name."""
        lines = [
            "    Args:",
            "        lr (float): Learning rate.",
            "        batch_size (int): Batch size.",
        ]
        section_start, section_indent = _find_google_args_section(lines)
        arg_indent, _ = _get_google_arg_indents(lines, section_start, section_indent)
        idx = _find_google_arg_line(lines, section_start, section_indent, arg_indent, "lr")
        assert idx == 1

    def test_prefix_collision_not_matched(self) -> None:
        """'lr' must not match 'lr_decay' — the boundary character check must reject the prefix."""
        lines = [
            "    Args:",
            "        lr (float): Learning rate.",
            "        lr_decay (float): Decay factor.",
        ]
        section_start, section_indent = _find_google_args_section(lines)
        arg_indent, _ = _get_google_arg_indents(lines, section_start, section_indent)
        idx = _find_google_arg_line(lines, section_start, section_indent, arg_indent, "lr")
        # Must match line 1 ("lr"), not line 2 ("lr_decay")
        assert idx == 1
        idx_decay = _find_google_arg_line(lines, section_start, section_indent, arg_indent, "lr_decay")
        assert idx_decay == 2

    def test_returns_minus_one_when_not_found(self) -> None:
        """Returns -1 when the arg name is absent from the section."""
        lines = [
            "    Args:",
            "        alpha (int): First.",
        ]
        section_start, section_indent = _find_google_args_section(lines)
        arg_indent, _ = _get_google_arg_indents(lines, section_start, section_indent)
        idx = _find_google_arg_line(lines, section_start, section_indent, arg_indent, "beta")
        assert idx == -1


class TestAnnotateGoogleStyleArg:
    """Tests for _annotate_google_style_arg — injects a note into a Google-style Args: section."""

    def test_found_and_inserts_note(self) -> None:
        """The note is inserted on a continuation-indented line after the matched arg."""
        lines = [
            "Summary.",
            "",
            "    Args:",
            "        my_arg (int): Description.",
            "    ",
        ]
        new_lines, found = _annotate_google_style_arg(lines, "my_arg", "Deprecated note.")
        assert found
        assert "            Deprecated note." in new_lines

    def test_not_found_returns_unchanged(self) -> None:
        """When the arg is absent the original lines and found=False are returned."""
        lines = ["    Args:", "        other (int): desc.", "    "]
        new_lines, found = _annotate_google_style_arg(lines, "missing", "note")
        assert not found
        assert new_lines == lines

    def test_no_args_section_returns_unchanged(self) -> None:
        """When there is no Args: header the original lines are returned unchanged."""
        lines = ["Summary.", "", "No args here."]
        new_lines, found = _annotate_google_style_arg(lines, "x", "note")
        assert not found
        assert new_lines == lines

    def test_note_placed_after_continuation_lines(self) -> None:
        """The note is appended after existing continuation lines, not before them."""
        lines = [
            "    Args:",
            "        my_arg (int): First line of description.",
            "            Continuation line.",
            "    ",
        ]
        new_lines, found = _annotate_google_style_arg(lines, "my_arg", "Deprecated.")
        assert found
        cont_idx = new_lines.index("            Continuation line.")
        note_idx = new_lines.index("            Deprecated.")
        assert note_idx > cont_idx

    def test_multiple_args_only_target_annotated(self) -> None:
        """Only the matched argument entry receives the note; others are left untouched."""
        lines = [
            "    Args:",
            "        alpha (int): First arg.",
            "        beta (str): Second arg.",
            "    ",
        ]
        new_lines, found = _annotate_google_style_arg(lines, "beta", "Note for beta.")
        assert found
        assert any("Note for beta." in ln for ln in new_lines)
        assert not any("Note for beta." in ln for ln in new_lines if "alpha" in ln)

    def test_idempotent_when_note_already_present(self) -> None:
        """Calling annotate twice does not insert the note a second time."""
        lines = [
            "    Args:",
            "        my_arg (int): Description.",
            "    ",
        ]
        lines, _ = _annotate_google_style_arg(lines, "my_arg", "Deprecated note.")
        lines, found = _annotate_google_style_arg(lines, "my_arg", "Deprecated note.")
        assert found
        assert sum("Deprecated note." in ln for ln in lines) == 1


class TestAnnotateSphinxStyleArg:
    """Tests for _annotate_sphinx_style_arg — injects a note under a Sphinx :param: field."""

    def test_found_and_inserts_note(self) -> None:
        """The note is inserted as an indented continuation line after the matched :param."""
        lines = [
            "Summary.",
            "",
            ":param my_arg: Description.",
            ":returns: Result.",
        ]
        new_lines, found = _annotate_sphinx_style_arg(lines, "my_arg", "Deprecated note.")
        assert found
        assert "    Deprecated note." in new_lines

    def test_not_found_returns_unchanged(self) -> None:
        """When the param is absent the original lines and found=False are returned."""
        lines = [":param other: desc.", ":returns: val."]
        new_lines, found = _annotate_sphinx_style_arg(lines, "missing", "note")
        assert not found
        assert new_lines == lines

    def test_typed_param_form(self) -> None:
        """The ``:param SomeType arg_name:`` form is also matched."""
        lines = [":param int my_arg: Description.", ":returns: Result."]
        new_lines, found = _annotate_sphinx_style_arg(lines, "my_arg", "Note.")
        assert found
        assert any("    Note." in ln for ln in new_lines)

    def test_note_placed_after_multiline_param(self) -> None:
        """The note is appended after existing indented continuation text."""
        lines = [
            ":param my_arg: First line.",
            "    Continued here.",
            ":returns: val.",
        ]
        new_lines, found = _annotate_sphinx_style_arg(lines, "my_arg", "Note.")
        assert found
        cont_idx = new_lines.index("    Continued here.")
        note_idx = new_lines.index("    Note.")
        assert note_idx > cont_idx

    def test_idempotent_when_note_already_present(self) -> None:
        """Calling annotate twice does not insert the note a second time."""
        lines = [":param my_arg: Description.", ":returns: val."]
        lines, _ = _annotate_sphinx_style_arg(lines, "my_arg", "Deprecated note.")
        lines, found = _annotate_sphinx_style_arg(lines, "my_arg", "Deprecated note.")
        assert found
        assert sum("Deprecated note." in ln for ln in lines) == 1


class TestUpdateDocstringIdempotent:
    """_update_docstring_with_deprecation called twice must not duplicate the inline note."""

    def test_google_style_double_call_deduplicates(self) -> None:
        """Two consecutive calls annotate the arg exactly once (Google-style docstring)."""

        def my_fn(old: str = "") -> str:
            """Do something.

            Args:
                old: Old argument.
            """
            return old

        config = DeprecationConfig(
            deprecated_in="1.0",
            remove_in="2.0",
            target=True,
            args_mapping={"old": None},
        )
        my_fn.__deprecated__ = config  # type: ignore[attr-defined]
        _update_docstring_with_deprecation(my_fn)
        _update_docstring_with_deprecation(my_fn)
        assert my_fn.__doc__ is not None
        assert my_fn.__doc__.count("Deprecated since v1.0") == 1

    def test_sphinx_style_double_call_deduplicates(self) -> None:
        """Two consecutive calls annotate the param exactly once (Sphinx-style docstring)."""

        def my_fn(old: str = "") -> str:
            """Do something.

            :param old: Old argument.
            :returns: Result.
            """
            return old

        config = DeprecationConfig(
            deprecated_in="1.0",
            remove_in="2.0",
            target=True,
            args_mapping={"old": None},
        )
        my_fn.__deprecated__ = config  # type: ignore[attr-defined]
        _update_docstring_with_deprecation(my_fn)
        _update_docstring_with_deprecation(my_fn)
        assert my_fn.__doc__ is not None
        assert my_fn.__doc__.count("Deprecated since v1.0") == 1
