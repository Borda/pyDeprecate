"""Unit tests for deprecate.docstring.sphinx_ext."""

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("sphinx", reason="sphinx not installed")

from deprecate.docstring.sphinx_ext import _PROXY_AVAILABLE, _SPHINX_AVAILABLE

pytestmark = pytest.mark.skipif(
    not (_SPHINX_AVAILABLE and _PROXY_AVAILABLE),
    reason="sphinx or _DeprecatedProxy not available",
)


def _make_proxy() -> tuple[object, type]:
    """Return an (OldClass, NewClass) pair where OldClass is a _DeprecatedProxy."""
    from deprecate import deprecated_class

    class _New:
        """New implementation."""

    old = deprecated_class(target=_New, deprecated_in="1.0", remove_in="2.0", update_docstring=True)(_New)
    return old, _New


class TestCanDocumentMember:
    """Tests for _DeprecatedProxyClassDocumenter.can_document_member."""

    def test_returns_true_for_proxy(self) -> None:
        """Returns True when member is a _DeprecatedProxy instance."""
        from deprecate.docstring.sphinx_ext import _DeprecatedProxyClassDocumenter

        proxy, _ = _make_proxy()
        assert _DeprecatedProxyClassDocumenter.can_document_member(proxy, "OldClass", False, MagicMock()) is True

    def test_delegates_to_base_for_regular_class(self) -> None:
        """Falls through to ClassDocumenter.can_document_member for non-proxy objects."""
        from deprecate.docstring.sphinx_ext import _DeprecatedProxyClassDocumenter

        class Regular:
            pass

        base = _DeprecatedProxyClassDocumenter.__bases__[0]
        with patch.object(base, "can_document_member", return_value=False) as mock_base:
            result = _DeprecatedProxyClassDocumenter.can_document_member(Regular, "Regular", False, MagicMock())
        mock_base.assert_called_once()
        assert result is False


class TestImportObject:
    """Tests for _DeprecatedProxyClassDocumenter.import_object."""

    def test_swaps_proxy_for_wrapped_class_and_captures_doc(self) -> None:
        """Proxy is replaced by the wrapped class; proxy __doc__ is stashed in _proxy_doc."""
        from deprecate.docstring.sphinx_ext import _DeprecatedProxyClassDocumenter

        proxy, wrapped = _make_proxy()
        doc = object.__new__(_DeprecatedProxyClassDocumenter)
        doc.doc_as_attr = True

        base = _DeprecatedProxyClassDocumenter.__bases__[0]

        def fake_super_import(self_inner: object, raiseerror: bool = False) -> bool:
            self_inner.object = proxy  # type: ignore[attr-defined]
            return True

        with patch.object(base, "import_object", fake_super_import):
            result = doc.import_object()

        assert result is True
        assert doc.object is wrapped
        assert ".. deprecated::" in doc._proxy_doc
        assert doc.doc_as_attr is False

    def test_non_proxy_object_is_left_unchanged(self) -> None:
        """When the imported object is not a proxy, nothing is swapped."""
        from deprecate.docstring.sphinx_ext import _DeprecatedProxyClassDocumenter

        class Regular:
            """Regular class."""

        doc = object.__new__(_DeprecatedProxyClassDocumenter)
        doc.doc_as_attr = False

        base = _DeprecatedProxyClassDocumenter.__bases__[0]

        def fake_super_import(self_inner: object, raiseerror: bool = False) -> bool:
            self_inner.object = Regular  # type: ignore[attr-defined]
            return True

        with patch.object(base, "import_object", fake_super_import):
            result = doc.import_object()

        assert result is True
        assert doc.object is Regular
        assert not hasattr(doc, "_proxy_doc")

    def test_super_returns_false_skips_proxy_swap(self) -> None:
        """When super().import_object() returns False, no proxy swap occurs."""
        from deprecate.docstring.sphinx_ext import _DeprecatedProxyClassDocumenter

        proxy, _ = _make_proxy()
        doc = object.__new__(_DeprecatedProxyClassDocumenter)

        base = _DeprecatedProxyClassDocumenter.__bases__[0]

        def fake_super_import(self_inner: object, raiseerror: bool = False) -> bool:
            setattr(self_inner, "object", proxy)
            return False

        with patch.object(base, "import_object", fake_super_import):
            result = doc.import_object()

        assert result is False
        assert getattr(doc, "object") is proxy
        assert not hasattr(doc, "_proxy_doc")


class TestGetDoc:
    """Tests for _DeprecatedProxyClassDocumenter.get_doc."""

    def test_returns_prepared_proxy_doc_when_set(self) -> None:
        """Returns prepare_docstring() of _proxy_doc when available."""
        from sphinx.ext.autodoc import prepare_docstring

        from deprecate.docstring.sphinx_ext import _DeprecatedProxyClassDocumenter

        doc = object.__new__(_DeprecatedProxyClassDocumenter)
        doc._proxy_doc = "Proxy docstring."

        result = doc.get_doc()

        assert result == [prepare_docstring("Proxy docstring.")]

    def test_delegates_to_super_when_no_proxy_doc(self) -> None:
        """Falls through to ClassDocumenter.get_doc when _proxy_doc is absent."""
        from deprecate.docstring.sphinx_ext import _DeprecatedProxyClassDocumenter

        doc = object.__new__(_DeprecatedProxyClassDocumenter)
        expected = [["fallback doc line"]]

        base = _DeprecatedProxyClassDocumenter.__bases__[0]
        with patch.object(base, "get_doc", return_value=expected):
            result = doc.get_doc()

        assert result == expected

    def test_empty_proxy_doc_delegates_to_super(self) -> None:
        """An empty _proxy_doc string is treated as absent and delegates to super."""
        from deprecate.docstring.sphinx_ext import _DeprecatedProxyClassDocumenter

        doc = object.__new__(_DeprecatedProxyClassDocumenter)
        doc._proxy_doc = ""
        expected = [["fallback from super"]]

        base = _DeprecatedProxyClassDocumenter.__bases__[0]
        with patch.object(base, "get_doc", return_value=expected):
            result = doc.get_doc()

        assert result == expected
