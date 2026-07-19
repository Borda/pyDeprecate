"""Unit tests for docstring injection helpers (:mod:`deprecate.docstring.inject`)."""

import sys
from typing import Union

import pytest

from deprecate import TargetMode, deprecated
from deprecate.docstring.inject import (
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
    moved to :mod:`tests.collection_deprecate` without losing the per-case configuration.
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
        """Notice is placed before ``Args:`` regardless of style."""

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
