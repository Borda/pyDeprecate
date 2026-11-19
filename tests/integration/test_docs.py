"""Tests for deprecation documentation strings."""

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.collections.docstrings import (
    OldClass,
    OldClassPlain,
    args_not_in_docstring,
    callable_target_with_args_mapping,
    google_args_multiline,
    google_args_removed,
    google_args_renamed,
    google_arguments_header,
    google_multi_args_all_found,
    google_partial_annotation,
    mkdocs_no_target_with_args_mapping,
    no_target_with_args_mapping,
    old_function,
    old_function_plain,
    old_google_no_sections_function,
    old_google_style_function,
    old_markdown_alias_function,
    old_mkdocs_style_function,
    old_no_remove_version_function,
    old_no_target_function,
    old_numpy_no_sections_function,
    old_numpy_style_function,
    sphinx_arg_not_in_docstring,
    sphinx_args_multiline,
    sphinx_args_removed,
)

_ROOT = Path(__file__).resolve().parents[2]
_GENERATOR_SPEC = spec_from_file_location("generate_llms_full", _ROOT / ".github/scripts/generate_llms_full.py")
assert _GENERATOR_SPEC is not None
assert _GENERATOR_SPEC.loader is not None
_GENERATOR = module_from_spec(_GENERATOR_SPEC)
_GENERATOR_SPEC.loader.exec_module(_GENERATOR)


def test_llms_full_render_holds_contract_and_api_map() -> None:
    """Render the full agent context from the contract and the public exports.

    A docs build generates the file that agents download next to the compact contract.
    The render must carry the whole contract verbatim and an API map entry for every public export.
    """
    rendered = _GENERATOR.render()
    contract = (_ROOT / "docs/llms.txt").read_text(encoding="utf-8")
    assert contract in rendered
    assert "## API Map" in rendered
    assert "- `deprecate.deprecated` (function)" in rendered


def _run_generator(monkeypatch: pytest.MonkeyPatch, *flags: str) -> int:
    monkeypatch.setattr(sys, "argv", ["generate_llms_full.py", *flags])
    return _GENERATOR.main()


class TestLlmsFullGenerator:
    """Command-line behaviour of the full agent context generator run by the docs build."""

    @pytest.fixture
    def generator_paths(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
        """Point the generator at a temporary contract and output file."""
        source = tmp_path / "llms.txt"
        output = tmp_path / "llms-full.txt"
        source.write_bytes(b"# Contract\n\n- rule one\n")
        monkeypatch.setattr(_GENERATOR, "SOURCE", source)
        monkeypatch.setattr(_GENERATOR, "OUTPUT", output)
        return source, output

    def test_writes_lf_output_from_crlf_source(
        self, generator_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Generate LF-only output from a contract checked out with CRLF line endings.

        A maintainer on Windows regenerates the full context from a CRLF checkout.
        The written file must use LF only and match the render of the same contract with LF endings.
        """
        source, output = generator_paths
        source.write_bytes(b"# Contract\r\n\r\n- rule one\r\n")
        assert _run_generator(monkeypatch) == 0
        written = output.read_bytes()
        assert b"\r" not in written
        source.write_bytes(b"# Contract\n\n- rule one\n")
        assert written == _GENERATOR.render().encode("utf-8")


_HOOKS_SPEC = spec_from_file_location("mkdocs_hooks", _ROOT / "docs/mkdocs_hooks.py")
assert _HOOKS_SPEC is not None
assert _HOOKS_SPEC.loader is not None
_HOOKS = module_from_spec(_HOOKS_SPEC)
_HOOKS_SPEC.loader.exec_module(_HOOKS)
_pydeprecate_faq_entries = _HOOKS._pydeprecate_faq_entries
_pydeprecate_plain_text = _HOOKS._pydeprecate_plain_text
_pydeprecate_copy_redirect_mirrors = _HOOKS._pydeprecate_copy_redirect_mirrors


def _first_question_answers(markdown: str) -> list[str]:
    """Read each question's opening prose paragraph without including its optional Q label."""
    answers: list[str] = []
    awaiting_answer = False
    paragraph: list[str] = []
    for line in [*markdown.splitlines(), ""]:
        if paragraph and (not line.strip() or line.startswith(("## ", "### "))):
            answer = " ".join(paragraph)
            assert not answer.endswith(":"), answer
            answers.append(_pydeprecate_plain_text(answer))
            paragraph.clear()
            awaiting_answer = False
        if line.startswith("### "):
            awaiting_answer = True
        elif line.startswith("## "):
            awaiting_answer = False
        elif awaiting_answer and line.strip() and not line.startswith("**Q:**"):
            # The page requires prose before examples, notes, or detail labels.
            assert not line.startswith(("```", "~~~", "!!!", "<", "|", ">", "-")), line
            paragraph.append(line.removeprefix("**A:** ").strip())
    return answers


class TestTroubleshootingFaq:
    """Check the troubleshooting page's generated FAQ entries."""

    def test_topics_and_fenced_headings_do_not_become_questions(self) -> None:
        """Only questions beneath topics contribute FAQ entries.

        Topic introductions, example code, and the closing help section must not become answers or questions in
        search metadata. Each real question retains its own first prose answer across topic boundaries.

        """
        markdown = """# Troubleshooting

### Orphan question?

Not beneath a topic.

## Functions

This introduction is not an answer.

### Why does `render` warn?

**Q:** A longer description of the problem.

!!! note
    This note is not an answer.

```python
## Fake topic
### Fake question?
print("not an answer")
```

**A:** Use [the replacement](usage/functions.md) and **migrate** `render`.

This later paragraph must not be selected.

### Why does it fail?

**Details**

> Quoted context is not an answer.

- A list is not an answer.

Use a supported target.

## Classes

A different topic introduction.

### Can I subclass it?

Yes, subclass the replacement.

### Still stuck?

Contact the maintainers.

## Still stuck?

Closing help is not an answer.
"""
        assert _pydeprecate_faq_entries(markdown) == [
            {"name": "Why does render warn?", "text": "Use the replacement and migrate render."},
            {"name": "Why does it fail?", "text": "Use a supported target."},
            {"name": "Can I subclass it?", "text": "Yes, subclass the replacement."},
        ]

    def test_missing_answer_does_not_borrow_the_next_topic_intro(self) -> None:
        """A missing answer fails instead of borrowing unrelated topic prose.

        A maintainer can accidentally leave a question with only an example. The following topic introduction must
        not hide that omission by becoming the question's structured-data answer.

        """
        markdown = """## Functions

### Why does it fail?

```python
print("example only")
```

## Classes

This is not the previous question's answer.
"""
        with pytest.raises(ValueError, match="section 'Why does it fail\\?' has no answer paragraph"):
            _pydeprecate_faq_entries(markdown)

    def test_answer_leadin_preserves_existing_continuation_rules(self) -> None:
        """Lead-in answers retain the following prose and discard a dangling colon.

        Existing FAQ answers may introduce a list or example with a colon. Moving questions beneath topics must
        preserve the continuation rule and its fallback when no following prose exists.

        """
        markdown = """## Functions

### Which target?

**A:** Choose a target:

- A supported function.

Use its canonical import.

### Which mode?

**A:** Choose one:
"""
        assert _pydeprecate_faq_entries(markdown) == [
            {"name": "Which target?", "text": "Choose a target: Use its canonical import."},
            {"name": "Which mode?", "text": "Choose one"},
        ]

    def test_wrapped_answer_matches_paragraph(self) -> None:
        """Compare an answer across physical lines as one paragraph.

        A prose paragraph can be wrapped by an editor without changing the answer shown in the FAQ.

        """
        markdown = "## Topic\n\n### Question?\n\nFirst line\ncontinues here.\n"
        assert _first_question_answers(markdown) == ["First line continues here."]

    def test_every_page_question_has_an_entry(self) -> None:
        """Every question on the actual troubleshooting page appears exactly once.

        Grouping the page must preserve every existing question while topic headings and the final help section
        stay outside the FAQ. This checks the real source alongside the parser's controlled examples.

        """
        page = _ROOT / "docs" / "troubleshooting.md"
        markdown = page.read_text(encoding="utf-8")
        questions = [line[4:].replace("`", "") for line in markdown.splitlines() if line.startswith("### ")]
        entries = _pydeprecate_faq_entries(markdown)
        assert len(entries) == len(questions)
        assert [entry["name"] for entry in entries] == questions
        assert [entry["text"] for entry in entries] == _first_question_answers(markdown)


class TestDeprecationDocstrings:
    """Tests for deprecation documentation strings."""

    def test_deprecated_func_docstring(self) -> None:
        """Deprecated function docstring gets a deprecated block appended."""
        expected = """An old function that is deprecated.

.. deprecated:: 0.1
   Will be removed in 0.3.
   Use :func:`tests.collections.docstrings.new_function` instead."""
        assert old_function.__doc__ == expected

    def test_deprecated_class_docstring(self) -> None:
        """Deprecated ``__init__`` gets a deprecated block appended."""
        expected = """Initialize the old class.

.. deprecated:: 0.2
   Will be removed in 0.4.
   Use :class:`tests.collections.docstrings.NewClass` instead."""
        assert OldClass.__init__.__doc__ == expected

    def test_deprecated_func_docstring_plain(self) -> None:
        """Function without docstring is left with ``__doc__ = None``."""
        # old_function_plain has no docstring by design; update_docstring=True
        # must not inject anything when there is no existing docstring to annotate.
        assert old_function_plain.__doc__ is None

    def test_deprecated_class_docstring_plain(self) -> None:
        """__init__ without docstring is left with ``__doc__ = None``."""
        # OldClassPlain.__init__ has no docstring by design; same guard as above.
        assert OldClassPlain.__init__.__doc__ is None

    def test_google_docstring_inserts_before_args_section(self) -> None:
        """Deprecation notice is injected before Google-style ``Args:`` section.

        A library author deprecates a function documented in Google style and asks for the docstring to be updated.
        Readers of the rendered docs must see the ``.. deprecated::`` block, with its removal version and the
        replacement reference, right after the summary line and ahead of ``Args:``, while the original sections
        (``Args:`` and ``Returns:``) stay intact below it.

        """
        expected = """An old Google-style function.

.. deprecated:: 0.1
   Will be removed in 0.3.
   Use :func:`tests.collections.docstrings.new_function` instead.

Args:
    a: Number argument.
    b: Text argument.

Returns:
    A formatted output."""
        assert old_google_style_function.__doc__ == expected

    def test_google_docstring_without_sections_appends_notice_to_end(self) -> None:
        """Without sections, deprecation notice is appended to the docstring tail."""
        expected = """Old Google-style function without explicit sections.

.. deprecated:: 0.1
   Will be removed in 0.3.
   Use :func:`tests.collections.docstrings.new_function` instead."""
        assert old_google_no_sections_function.__doc__ == expected

    def test_numpy_docstring_inserts_before_parameters_section(self) -> None:
        """Deprecation notice is injected before NumPy-style ``Parameters`` section.

        A scientific-Python library documents its functions in NumPy style and deprecates one with docstring updating
        enabled.  The generated docs must show the ``.. deprecated::`` block, with removal version and replacement
        reference, between the summary and the underlined ``Parameters`` section, which keeps its original content
        below the notice.

        """
        expected = """An old NumPy-style function.

.. deprecated:: 0.1
   Will be removed in 0.3.
   Use :func:`tests.collections.docstrings.new_function` instead.

Parameters
----------
a : int
    Number argument.
b : str
    Text argument."""
        assert old_numpy_style_function.__doc__ == expected

    def test_numpy_docstring_without_sections_appends_notice_to_end(self) -> None:
        """Without NumPy headers, deprecation notice is appended to the docstring tail."""
        expected = """Old NumPy-style function without explicit sections.

.. deprecated:: 0.1
   Will be removed in 0.3.
   Use :func:`tests.collections.docstrings.new_function` instead."""
        assert old_numpy_no_sections_function.__doc__ == expected

    def test_mkdocs_docstring_uses_admonition_format(self) -> None:
        """MkDocs style emits admonition syntax inserted before ``Args:``.

        A project built with MkDocs deprecates a function with ``docstring_style="mkdocs"`` because the reStructuredText
        ``.. deprecated::`` directive would render as plain text on its site.  The docstring must carry a
        ``!!! warning "Deprecated in ..."`` admonition with the removal version and a backtick-quoted replacement
        (no Sphinx ``:func:`` role) placed before ``Args:``, leaving the other sections unchanged.

        """
        expected = """An old MkDocs-style function.

!!! warning "Deprecated in 0.1"
    Will be removed in 0.3.
    Use `tests.collections.docstrings.new_function` instead.

Args:
    a: Number argument.
    b: Text argument.

Returns:
    A formatted output."""
        assert old_mkdocs_style_function.__doc__ == expected

    def test_markdown_alias_produces_mkdocs_admonition(self) -> None:
        """``docstring_style="markdown"`` is an alias for ``"mkdocs"`` and renders the same admonition.

        A maintainer who thinks of the target format as "Markdown" rather than "MkDocs" passes
        ``docstring_style="markdown"``.  The resulting docstring must be the same admonition block, with removal
        version and backtick-quoted replacement placed before ``Args:``, that the ``"mkdocs"`` style produces, so
        both spellings of the option give identical documentation.

        """
        expected = """An old function using the ``markdown`` style alias.

!!! warning "Deprecated in 0.1"
    Will be removed in 0.3.
    Use `tests.collections.docstrings.new_function` instead.

Args:
    a: Number argument.
    b: Text argument.

Returns:
    A formatted output."""
        assert old_markdown_alias_function.__doc__ == expected

    def test_remove_version_line_omitted_when_remove_in_is_empty(self) -> None:
        """Docstring notice omits remove-version line when remove_in is not provided."""
        expected = """Old function without remove version.

.. deprecated:: 0.1
   Use :func:`tests.collections.docstrings.new_function` instead."""
        assert old_no_remove_version_function.__doc__ == expected

    def test_target_line_omitted_when_target_is_none(self) -> None:
        """Docstring notice omits target line when no target callable is provided."""
        expected = """Old function without target.

.. deprecated:: 0.1"""
        assert old_no_target_function.__doc__ == expected


class TestArgsDocstringAnnotation:
    """Full-docstring equality checks for inline arg deprecation annotations."""

    def test_google_args_removed(self) -> None:
        """Removed arg: inline note inserted under the arg; no general block appended."""
        expected = """Train the model.

Args:
    lr: Learning rate for training.
    train_config: Training configuration object.
        Deprecated since v1.8 — no longer used. Will be removed in v1.9.

Returns:
    Training result."""
        assert google_args_removed.__doc__ == expected

    def test_google_args_renamed(self) -> None:
        """Renamed arg: inline note names the replacement; no general block appended."""
        expected = """Train the model.

Args:
    lr: Learning rate for training.
    train_config: Old configuration parameter.
        Deprecated since v1.8 — use `config` instead. Will be removed in v1.9.
    config: New configuration parameter.

Returns:
    Training result."""
        assert google_args_renamed.__doc__ == expected

    def test_sphinx_args_removed(self) -> None:
        """Sphinx-style: note inserted under ``:param``; no general block appended."""
        expected = """Train the model.

:param lr: Learning rate for training.
:param train_config: Training configuration object.
    Deprecated since v1.8 — no longer used. Will be removed in v1.9.
:returns: Training result."""
        assert sphinx_args_removed.__doc__ == expected

    def test_args_not_in_docstring(self) -> None:
        """Arg absent from the docstring falls back to a general deprecated block."""
        expected = """Train the model.

Args:
    lr: Learning rate for training.

.. deprecated:: 1.8
   Will be removed in 1.9."""
        assert args_not_in_docstring.__doc__ == expected

    def test_google_multi_args_all_found(self) -> None:
        """Both deprecated args annotated inline in declaration order; no general block."""
        expected = """Run with two deprecated args, both present in the docstring.

Args:
    new_a (int): The replacement for old_a.
    old_a (int): The first deprecated argument.
        Deprecated since v1.8 — use `new_a` instead. Will be removed in v1.9.
    old_b (str): The second deprecated argument.
        Deprecated since v1.8 — no longer used. Will be removed in v1.9.

Returns:
    Result."""
        assert google_multi_args_all_found.__doc__ == expected

    def test_google_partial_annotation(self) -> None:
        """One arg found inline, one missing: inline note present AND general block appended.

        A function renames ``old_a`` to ``new_a`` and also lists a second deprecated argument (``missing_b``) that its
        docstring never documents.  The documented argument must get its inline "Deprecated since ..." note under its
        own ``Args:`` entry, and because one deprecated argument has no entry to annotate, the general
        ``.. deprecated::`` block must also be appended at the end so that nothing is left undocumented.

        """
        expected = """Run with two deprecated args, only one present in the docstring.

Args:
    new_a: The replacement for old_a.
    old_a: The first deprecated argument.
        Deprecated since v1.8 — use `new_a` instead. Will be removed in v1.9.

Returns:
    Result.

.. deprecated:: 1.8
   Will be removed in 1.9."""
        assert google_partial_annotation.__doc__ == expected

    def test_google_arguments_header(self) -> None:
        """``Arguments:`` header treated identically to ``Args:``."""
        expected = """Train the model using the ``Arguments:`` section header variant.

Arguments:
    lr: Learning rate for training.
    train_config: Training configuration object.
        Deprecated since v1.8 — no longer used. Will be removed in v1.9.

Returns:
    Training result."""
        assert google_arguments_header.__doc__ == expected

    def test_sphinx_arg_not_in_docstring(self) -> None:
        """Sphinx-style: absent param falls back to a general ``.. deprecated::`` block appended at end."""
        expected = """Train the model.

:param lr: Learning rate for training.
:returns: Training result.

.. deprecated:: 1.8
   Will be removed in 1.9."""
        assert sphinx_arg_not_in_docstring.__doc__ == expected

    def test_google_args_multiline(self) -> None:
        """Note appended after all continuation lines of a multiline arg description.

        A deprecated ``train_config`` argument is documented over three lines (a summary plus two continuation
        lines).  The inline "Deprecated since ..." note must land after the last continuation line of that entry,
        at the same indentation, so the description is not split in two, while the neighbouring ``lr`` entry and its
        own continuation line are left untouched.

        """
        expected = """Train the model with a multiline arg description.

Args:
    lr: Learning rate for training.
        Must be a positive float.
    train_config: Training configuration object.
        Passed directly to the trainer.
        Ignored when ``None``.
        Deprecated since v1.8 — no longer used. Will be removed in v1.9.

Returns:
    Training result."""
        assert google_args_multiline.__doc__ == expected

    def test_sphinx_args_multiline(self) -> None:
        """Note appended after all continuation lines of a multiline Sphinx param."""
        expected = """Train the model with a multiline Sphinx param description.

:param lr: Learning rate for training. Must be a positive float.
:param train_config: Training configuration object. Passed directly to the trainer. Ignored when ``None``.
    Deprecated since v1.8 — no longer used. Will be removed in v1.9.
:returns: Training result."""
        assert sphinx_args_multiline.__doc__ == expected

    def test_callable_target_with_args_mapping(self) -> None:
        """Callable target: inline note inserted AND general block appended with :func: ref.

        A function forwards to a replacement callable (``new_function``) and also retires its ``b`` argument through
        ``args_mapping``.  Readers must see the inline note under ``b`` explaining that the argument is no longer
        used, plus the function-level ``.. deprecated::`` block at the end, which, because a callable target exists,
        points to the replacement with a ``:func:`` reference.

        """
        expected = """Forward calls to new_function with a deprecated argument removed.

Args:
    a: The main integer input.
    b: Deprecated configuration string — will be removed.
        Deprecated since v1.8 — no longer used. Will be removed in v1.9.

Returns:
    Forwarded result.

.. deprecated:: 1.8
   Will be removed in 1.9.
   Use :func:`tests.collections.docstrings.new_function` instead."""
        assert callable_target_with_args_mapping.__doc__ == expected

    def test_no_target_with_args_mapping(self) -> None:
        """``target=None``: inline note inserted AND general block appended (no :func: ref).

        A function is only flagged as deprecated (``TargetMode.NOTIFY``, no replacement callable) while one of its
        arguments, ``b``, is retired through ``args_mapping``.  The docstring must get the inline note under ``b`` and
        the general ``.. deprecated::`` block with the removal version, but without a "Use ... instead" line because
        there is nothing to point to.

        """
        expected = """Warning-only deprecation with a deprecated argument.

Args:
    a: The main integer input.
    b: Deprecated configuration string — will be removed.
        Deprecated since v1.8 — no longer used. Will be removed in v1.9.

Returns:
    Result.

.. deprecated:: 1.8
   Will be removed in 1.9."""
        assert no_target_with_args_mapping.__doc__ == expected

    def test_mkdocs_no_target_with_args_mapping(self) -> None:
        """MkDocs style: inline arg note inserted AND general block uses ``!!! warning``.

        A MkDocs-documented function with no replacement target (``TargetMode.NOTIFY``) retires its ``b`` argument
        through ``args_mapping`` and selects ``docstring_style="mkdocs"``.  The inline note under ``b`` is the same
        as in the reStructuredText case, but the function-level notice at the end must be a
        ``!!! warning "Deprecated in ..."`` admonition rather than a ``.. deprecated::`` directive, and it carries no
        replacement reference.

        """
        expected = """Warning-only deprecation with a deprecated argument (MkDocs style).

Args:
    a: The main integer input.
    b: Deprecated configuration string — will be removed.
        Deprecated since v1.8 — no longer used. Will be removed in v1.9.

Returns:
    Result.

!!! warning "Deprecated in 1.8"
    Will be removed in 1.9."""
        assert mkdocs_no_target_with_args_mapping.__doc__ == expected


class TestRedirectMirrors:
    """Markdown mirrors of moved pages stay reachable at their released addresses."""

    @staticmethod
    def _config(site: Path, redirect_maps: dict[str, str] | None) -> dict:
        plugins = (
            {} if redirect_maps is None else {"redirects": SimpleNamespace(config={"redirect_maps": redirect_maps})}
        )
        return {"site_dir": str(site), "plugins": plugins}

    def test_old_mirror_serves_the_new_page(self, tmp_path: Path) -> None:
        """Serve the new page's markdown at the old mirror address after a page moves.

        An agent holds a link such as ``guide/audit.html.md`` from the previous release.
        The built site must answer there with the moved page, because a static host cannot redirect it.
        """
        (tmp_path / "audit").mkdir()
        (tmp_path / "audit" / "index.html.md").write_text("# Audit\n", encoding="utf-8")
        _pydeprecate_copy_redirect_mirrors(self._config(tmp_path, {"guide/audit.md": "audit/index.md"}))
        assert (tmp_path / "guide" / "audit.html.md").read_text(encoding="utf-8") == "# Audit\n"

    def test_existing_old_mirror_is_kept(self, tmp_path: Path) -> None:
        """Leave an old mirror that the build already wrote untouched.

        A redirect entry names a path that still has its own page, so the mirror there is real content.
        The hook must not overwrite it with the target page.
        """
        (tmp_path / "audit").mkdir()
        (tmp_path / "audit" / "index.html.md").write_text("new\n", encoding="utf-8")
        (tmp_path / "old.html.md").write_text("own\n", encoding="utf-8")
        _pydeprecate_copy_redirect_mirrors(self._config(tmp_path, {"old.md": "audit/index.md"}))
        assert (tmp_path / "old.html.md").read_text(encoding="utf-8") == "own\n"

    def test_no_redirects_plugin_is_a_no_op(self, tmp_path: Path) -> None:
        """Do nothing when the site is built without the redirects plugin.

        A contributor builds the docs with a trimmed plugin list.
        The hook must return without error and write nothing.
        """
        _pydeprecate_copy_redirect_mirrors(self._config(tmp_path, None))
        assert list(tmp_path.iterdir()) == []
