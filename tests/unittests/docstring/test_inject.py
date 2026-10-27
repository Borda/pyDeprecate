"""Unit tests for the docstring injection helpers in :mod:`deprecate.docstring.inject`."""

import sys
from typing import Union, cast

import pytest

from deprecate import TargetMode, deprecated
from deprecate._types import DeprecationConfig, _DeprecatedCallable
from deprecate.docstring.inject import (
    _annotate_google_style_arg,
    _annotate_sphinx_style_arg,
    _build_arg_deprecation_note,
    _find_google_arg_line,
    _find_google_args_section,
    _get_google_arg_indents,
    _has_deprecation_block,
    _update_docstring_with_deprecation,
    find_docstring_insertion_index,
    is_numpy_underline,
    normalize_docstring_style,
)


class TestDocstringStyleValidation:
    """Validation for ``docstring_style`` values."""

    _NOTIFY_PARAMS = [
        pytest.param(TargetMode.NOTIFY, id="TargetMode.NOTIFY"),
        pytest.param(None, marks=pytest.mark.filterwarnings("ignore::FutureWarning"), id="legacy-None"),
    ]

    @pytest.mark.parametrize("target_val", _NOTIFY_PARAMS)
    def test_invalid_docstring_style_raises_value_error(self, target_val: Union[TargetMode, None]) -> None:
        """Unsupported ``docstring_style`` values should fail fast at decoration time."""
        with pytest.raises(ValueError, match="Invalid `docstring_style` value"):

            @deprecated(
                target=target_val,
                deprecated_in="1.0",
                remove_in="2.0",
                update_docstring=True,
                docstring_style="unsupported-style",  # type: ignore[arg-type, unused-ignore]
            )
            def some_func() -> None:
                """A function."""

    @pytest.mark.parametrize("target_val", _NOTIFY_PARAMS)
    def test_invalid_docstring_style_raises_even_without_update_docstring(
        self, target_val: Union[TargetMode, None]
    ) -> None:
        """``docstring_style`` is validated eagerly regardless of ``update_docstring``."""
        with pytest.raises(ValueError, match="Invalid `docstring_style` value"):

            @deprecated(
                target=target_val,
                deprecated_in="1.0",
                docstring_style="unsupported-style",  # type: ignore[arg-type, unused-ignore]
            )
            def some_func() -> None:
                """A function."""

    @pytest.mark.parametrize("style", ["RST", "MKDOCS", "Markdown", "MkDocs", "AUTO", "Auto"])
    def test_case_insensitive_normalization(self, style: str) -> None:
        """``docstring_style`` values are matched case-insensitively."""
        assert normalize_docstring_style(style) in ("rst", "mkdocs")

    def test_auto_style_resolves_to_rst_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``"auto"`` resolves to ``"rst"`` when no env var is set and argv is not mkdocs."""
        monkeypatch.setattr(sys, "argv", ["pytest"])
        monkeypatch.delenv("DEPRECATE_DOCSTRING_STYLE", raising=False)
        monkeypatch.delitem(sys.modules, "mkdocs", raising=False)
        result = normalize_docstring_style("auto")
        assert result == "rst"

    def test_auto_style_env_var_mkdocs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``DEPRECATE_DOCSTRING_STYLE=mkdocs`` forces MkDocs format."""
        monkeypatch.setenv("DEPRECATE_DOCSTRING_STYLE", "mkdocs")
        assert normalize_docstring_style("auto") == "mkdocs"

    def test_auto_style_env_var_rst(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``DEPRECATE_DOCSTRING_STYLE=rst`` forces RST format."""
        monkeypatch.setenv("DEPRECATE_DOCSTRING_STYLE", "rst")
        assert normalize_docstring_style("auto") == "rst"

    def test_auto_style_detects_mkdocs_from_argv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``sys.argv[0]`` containing ``mkdocs`` resolves ``"auto"`` to ``"mkdocs"``."""
        monkeypatch.setattr(sys, "argv", ["/usr/local/bin/mkdocs", "build"])
        monkeypatch.delenv("DEPRECATE_DOCSTRING_STYLE", raising=False)
        assert normalize_docstring_style("auto") == "mkdocs"

    @pytest.mark.parametrize("target_val", _NOTIFY_PARAMS)
    def test_update_docstring_idempotent(self, target_val: Union[TargetMode, None]) -> None:
        """Calling ``_update_docstring_with_deprecation`` twice must not duplicate the notice."""

        @deprecated(target=target_val, deprecated_in="1.0", update_docstring=True)
        def some_func() -> None:
            """A function."""

        original_doc = some_func.__doc__
        _update_docstring_with_deprecation(some_func)
        assert some_func.__doc__ == original_doc

    @pytest.mark.parametrize("target_val", _NOTIFY_PARAMS)
    def test_idempotency_guard_no_false_positive_on_version_prefix(self, target_val: Union[TargetMode, None]) -> None:
        """Guard must not suppress injection when the docstring mentions a longer version.

        ``deprecated_in="1"`` should inject ``.. deprecated:: 1`` even when the
        existing docstring contains ``.. deprecated:: 1.0`` in prose — the "1"
        string is a substring of "1.0" so a naive ``in`` check would cause a
        false positive.

        """

        @deprecated(target=target_val, deprecated_in="1", update_docstring=True, docstring_style="rst")
        def some_func() -> None:
            """Summary.

            See also .. deprecated:: 1.0 handling.

            """

        assert some_func.__doc__ is not None
        lines = [line.strip() for line in some_func.__doc__.splitlines()]
        assert ".. deprecated:: 1" in lines


class TestNumpyUnderlineDetection:
    """Tests for NumPy section underline detection helper."""

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("---", True),
            ("----------", True),
            (" -- ", False),
            ("===", False),
            ("abc", False),
            ("--", False),
        ],
    )
    def test_is_numpy_underline(self, line: str, expected: bool) -> None:
        """Underline helper should accept only 3+ dashes."""
        assert is_numpy_underline(line) is expected


class TestDocstringInsertionIndex:
    """Tests for Google/NumPy insertion index detection."""

    def test_detects_google_header_with_whitespace_and_case(self) -> None:
        """Google headers are detected case-insensitively with surrounding whitespace."""
        lines = ["Summary", "", "  ArGs:  ", "    a: value"]
        assert find_docstring_insertion_index(lines) == 2

    def test_detects_numpy_header_with_underline(self) -> None:
        """NumPy header should be detected only when followed by dashed underline."""
        lines = ["Summary", "", "Parameters", "----------", "a : int"]
        assert find_docstring_insertion_index(lines) == 2

    def test_does_not_detect_numpy_header_without_underline(self) -> None:
        """NumPy-like header without underline should fall back to append-at-end."""
        lines = ["Summary", "", "Parameters", "a : int"]
        assert find_docstring_insertion_index(lines) == len(lines)

    def test_boundary_header_last_line_does_not_crash(self) -> None:
        """Header on final line should not index past bounds and should append at end."""
        lines = ["Summary", "Parameters"]
        assert find_docstring_insertion_index(lines) == len(lines)


class TestDocstringStyleOutput:
    """Verify each docstring style alias produces the correct notice format.

    The inline ``@deprecated`` decorators in this class are parametrize-coupled
    (``docstring_style=style`` resolves from the parametrize fixture), so they cannot be
    moved to :mod:`tests.collections.api_deprecated` without losing the per-case configuration.
    This is one of the AGENTS.md three-layer-rule exceptions: a decorator whose config
    depends on the parametrize value must be defined inside the test method itself.
    """

    @pytest.mark.parametrize(
        ("style", "expected_marker"),
        [
            ("rst", ".. deprecated:: 1.0"),
            ("mkdocs", '!!! warning "Deprecated in 1.0"'),
            ("markdown", '!!! warning "Deprecated in 1.0"'),
        ],
    )
    def test_notice_marker_for_explicit_style(self, style: str, expected_marker: str) -> None:
        """Each explicit style injects the expected notice format into the docstring."""

        @deprecated(target=None, deprecated_in="1.0", update_docstring=True, docstring_style=style)  # type: ignore[arg-type, unused-ignore]
        def _fn() -> None:
            """A simple function."""

        assert _fn.__doc__ is not None
        assert expected_marker in _fn.__doc__

    @pytest.mark.parametrize(
        ("style", "expected_marker"),
        [
            ("rst", ".. deprecated:: 1.0"),
            ("mkdocs", '!!! warning "Deprecated in 1.0"'),
            ("markdown", '!!! warning "Deprecated in 1.0"'),
        ],
    )
    def test_notice_inserted_before_google_args_for_style(self, style: str, expected_marker: str) -> None:
        """Notice is placed before ``Args:`` regardless of style.

        A project documents a Google-style function and picks one of the supported notice styles: ``rst``,
        ``mkdocs`` or its ``markdown`` alias.  Whichever style is chosen, the generated notice (identified by its
        marker text) must appear in the docstring ahead of the ``Args:`` section, so readers see the deprecation
        before the parameter list.

        """

        @deprecated(target=None, deprecated_in="1.0", update_docstring=True, docstring_style=style)  # type: ignore[arg-type, unused-ignore]
        def _fn(x: int) -> None:
            """Summary.

            Args:
                x: A value.

            """

        assert _fn.__doc__ is not None
        doc = _fn.__doc__
        assert expected_marker in doc
        assert doc.index(expected_marker) < doc.index("Args:")

    @pytest.mark.parametrize(
        ("style", "absent_marker"),
        [
            ("rst", "!!! warning"),
            ("mkdocs", ".. deprecated::"),
            ("markdown", ".. deprecated::"),
        ],
    )
    def test_other_style_marker_absent(self, style: str, absent_marker: str) -> None:
        """The notice uses exactly one format — the other style's marker is absent."""

        @deprecated(target=None, deprecated_in="1.0", update_docstring=True, docstring_style=style)  # type: ignore[arg-type, unused-ignore]
        def _fn() -> None:
            """A simple function."""

        assert absent_marker not in (_fn.__doc__ or "")


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


class TestHasDeprecationBlock:
    """Tests for _has_deprecation_block — detects already-injected notices."""

    def test_empty_block_lines_returns_false(self) -> None:
        """Empty block_lines triggers fast-return False regardless of doc_lines content."""
        assert not _has_deprecation_block(["some content", "more content"], [])


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
        """Two consecutive calls annotate the arg exactly once (Google-style docstring).

        Docstring updating may run more than once on the same function.  Updating a function whose deprecated
        argument ``old`` is documented in an ``Args:`` section twice must leave a single "Deprecated since v1.0"
        note, so the rendered docs never repeat the same warning.

        """

        def my_fn(old: str = "") -> str:
            """Do something.

            Args:
                old: Old argument.

            """
            return old

        config = DeprecationConfig(
            deprecated_in="1.0", remove_in="2.0", target=TargetMode.ARGS_REMAP, args_mapping={"old": None}
        )
        cast(_DeprecatedCallable, my_fn).__deprecation_config__ = config
        _update_docstring_with_deprecation(my_fn)
        _update_docstring_with_deprecation(my_fn)
        assert my_fn.__doc__ is not None
        assert my_fn.__doc__.count("Deprecated since v1.0") == 1

    def test_sphinx_style_double_call_deduplicates(self) -> None:
        """Two consecutive calls annotate the param exactly once (Sphinx-style docstring).

        Docstring updating may run more than once on the same function.  Updating a function whose deprecated
        argument ``old`` is documented with a ``:param old:`` field twice must leave a single "Deprecated since v1.0"
        note, so the rendered docs never repeat the same warning.

        """

        def my_fn(old: str = "") -> str:
            """Do something.

            :param old: Old argument.
            :returns: Result.

            """
            return old

        config = DeprecationConfig(
            deprecated_in="1.0", remove_in="2.0", target=TargetMode.ARGS_REMAP, args_mapping={"old": None}
        )
        cast(_DeprecatedCallable, my_fn).__deprecation_config__ = config
        _update_docstring_with_deprecation(my_fn)
        _update_docstring_with_deprecation(my_fn)
        assert my_fn.__doc__ is not None
        assert my_fn.__doc__.count("Deprecated since v1.0") == 1
