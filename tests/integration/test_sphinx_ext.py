"""Unit tests for deprecate.docstring.sphinx_ext."""

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from deprecate import deprecated_class
from deprecate.docstring.sphinx_ext import _PROXY_AVAILABLE, _SPHINX_AVAILABLE, setup

# Optional-dependency imports: ``sphinx`` (and therefore ``_DeprecatedProxyClassDocumenter``) may be absent.
# Guard at module level so collection never fails; the tests below are skipped via ``_skipif_sphinx_missing``.
if _SPHINX_AVAILABLE:
    from sphinx.util.docstrings import prepare_docstring
if _SPHINX_AVAILABLE and _PROXY_AVAILABLE:
    from deprecate.docstring.sphinx_ext import _DeprecatedProxyClassDocumenter

_skipif_sphinx_missing = pytest.mark.skipif(
    not (_SPHINX_AVAILABLE and _PROXY_AVAILABLE), reason="sphinx or _DeprecatedProxy not available"
)


def _make_proxy() -> tuple[object, type]:
    """Return an (OldClass, NewClass) pair where OldClass is a _DeprecatedProxy."""

    class _New:
        """New implementation."""

    old = deprecated_class(target=_New, deprecated_in="1.0", remove_in="2.0", update_docstring=True)(_New)
    return old, _New


@_skipif_sphinx_missing
class TestCanDocumentMember:
    """Tests for _DeprecatedProxyClassDocumenter.can_document_member."""

    def test_returns_true_for_proxy(self) -> None:
        """Returns True when member is a _DeprecatedProxy instance."""
        proxy, _ = _make_proxy()
        assert _DeprecatedProxyClassDocumenter.can_document_member(proxy, "OldClass", False, MagicMock()) is True

    def test_delegates_to_base_for_regular_class(self) -> None:
        """Falls through to ClassDocumenter.can_document_member for non-proxy objects."""

        class Regular:
            pass

        base = _DeprecatedProxyClassDocumenter.__bases__[0]
        with patch.object(base, "can_document_member", return_value=False) as mock_base:
            result = _DeprecatedProxyClassDocumenter.can_document_member(Regular, "Regular", False, MagicMock())
        mock_base.assert_called_once()
        assert result is False


@_skipif_sphinx_missing
class TestImportObject:
    """Tests for _DeprecatedProxyClassDocumenter.import_object."""

    def test_swaps_proxy_for_wrapped_class_and_captures_doc(self) -> None:
        """Proxy is replaced by the wrapped class; proxy __doc__ is stashed in _proxy_doc."""
        proxy, wrapped = _make_proxy()
        doc = object.__new__(_DeprecatedProxyClassDocumenter)
        doc.doc_as_attr = True

        base = _DeprecatedProxyClassDocumenter.__bases__[0]

        def fake_super_import(self_inner: Any, raiseerror: bool = False) -> bool:  # noqa: ANN401
            self_inner.object = proxy
            return True

        with patch.object(base, "import_object", fake_super_import):
            result = doc.import_object()

        assert result is True
        assert doc.object is wrapped
        assert ".. deprecated::" in doc._proxy_doc
        assert doc.doc_as_attr is False

    def test_non_proxy_object_is_left_unchanged(self) -> None:
        """When the imported object is not a proxy, nothing is swapped."""

        class Regular:
            """Regular class."""

        doc = object.__new__(_DeprecatedProxyClassDocumenter)
        doc.doc_as_attr = False

        base = _DeprecatedProxyClassDocumenter.__bases__[0]

        def fake_super_import(self_inner: Any, raiseerror: bool = False) -> bool:  # noqa: ANN401
            self_inner.object = Regular
            return True

        with patch.object(base, "import_object", fake_super_import):
            result = doc.import_object()

        assert result is True
        assert doc.object is Regular
        assert not hasattr(doc, "_proxy_doc")

    def test_super_returns_false_skips_proxy_swap(self) -> None:
        """When super().import_object() returns False, no proxy swap occurs."""
        proxy, _ = _make_proxy()
        doc = object.__new__(_DeprecatedProxyClassDocumenter)

        base = _DeprecatedProxyClassDocumenter.__bases__[0]

        def fake_super_import(self_inner: Any, raiseerror: bool = False) -> bool:  # noqa: ANN401
            self_inner.object = proxy
            return False

        with patch.object(base, "import_object", fake_super_import):
            result = doc.import_object()

        assert result is False
        assert getattr(doc, "object") is proxy
        assert not hasattr(doc, "_proxy_doc")


@_skipif_sphinx_missing
class TestGetDoc:
    """Tests for _DeprecatedProxyClassDocumenter.get_doc."""

    def test_returns_prepared_proxy_doc_when_set(self) -> None:
        """Returns prepare_docstring() of _proxy_doc when available."""
        doc = object.__new__(_DeprecatedProxyClassDocumenter)
        doc._proxy_doc = "Proxy docstring."

        result = doc.get_doc()

        assert result == [prepare_docstring("Proxy docstring.")]

    def test_delegates_to_super_when_no_proxy_doc(self) -> None:
        """Falls through to ClassDocumenter.get_doc when _proxy_doc is absent."""
        doc = object.__new__(_DeprecatedProxyClassDocumenter)
        expected = [["fallback doc line"]]

        base = _DeprecatedProxyClassDocumenter.__bases__[0]
        with patch.object(base, "get_doc", return_value=expected):
            result = doc.get_doc()

        assert result == expected

    def test_empty_proxy_doc_delegates_to_super(self) -> None:
        """An empty _proxy_doc string is treated as absent and delegates to super."""
        doc = object.__new__(_DeprecatedProxyClassDocumenter)
        doc._proxy_doc = ""
        expected = [["fallback from super"]]

        base = _DeprecatedProxyClassDocumenter.__bases__[0]
        with patch.object(base, "get_doc", return_value=expected):
            result = doc.get_doc()

        assert result == expected


@_skipif_sphinx_missing
class TestSetup:
    """``setup(app)`` is the Sphinx extension entry point: it registers the documenter and returns the metadata."""

    def test_registers_proxy_documenter_over_autodoc(self) -> None:
        """Loading the extension enables autodoc and registers the proxy documenter with ``override=True``.

        A docs build lists ``deprecate.docstring.sphinx_ext`` in ``extensions``; Sphinx then calls ``setup(app)``. The
        autodoc extension must be loaded first, and the proxy documenter has to replace the stock class documenter
        for ``autoclass::`` (hence ``override=True``), or deprecated class aliases render without their notice.

        """
        app = MagicMock()

        setup(app)

        app.setup_extension.assert_called_once_with("sphinx.ext.autodoc")
        app.add_autodocumenter.assert_called_once_with(_DeprecatedProxyClassDocumenter, override=True)

    def test_returns_parallel_safe_metadata(self) -> None:
        """The returned dict tells Sphinx the extension's version and that parallel reads are safe."""
        assert setup(MagicMock()) == {"version": "0.1", "parallel_read_safe": True}

    def test_without_proxy_support_warns_and_registers_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        """When the proxy type is unavailable, the extension still loads, warns once, and registers no documenter.

        A partial installation must not break the docs build: ``setup`` logs that the documenter is not registered
        and still hands Sphinx valid metadata.

        """
        app = MagicMock()

        with (
            patch("deprecate.docstring.sphinx_ext._PROXY_AVAILABLE", False),
            caplog.at_level(logging.WARNING, logger="deprecate.docstring.sphinx_ext"),
        ):
            result = setup(app)

        app.add_autodocumenter.assert_not_called()
        assert "_DeprecatedProxyClassDocumenter not registered" in caplog.text
        assert result == {"version": "0.1", "parallel_read_safe": True}
