"""Sphinx autodoc extension: expose runtime-modified docstrings for _DeprecatedProxy.

.. note::
    **Beta feature** — the public API of this module (documenter class, priority
    value) may change in a minor release while it stabilises.

``@deprecated_class(update_docstring=True)`` writes the deprecation notice into
the proxy's ``__doc__`` at decoration time.  Sphinx's autodoc sees the proxy as
a non-class instance and renders it as ``"alias of <target>"`` without showing
the docstring.

This extension bridges the gap by registering a custom :class:`ClassDocumenter`
that:

1. Recognises :class:`~deprecate.proxy._DeprecatedProxy` objects via
   :meth:`can_document_member`.
2. Swaps the proxy for the underlying (wrapped) class so that Sphinx can
   introspect members and build the correct signature.
3. Returns the proxy's ``__doc__`` (which already contains the injected
   ``.. deprecated::`` block) from :meth:`get_doc`, so the notice appears in
   the rendered output.

Usage in ``conf.py``::

    extensions = [
        'sphinx.ext.autodoc',
        ...
        'deprecate.docstring.sphinx_ext',
    ]

Requirements:
    ``sphinx`` with ``sphinx.ext.autodoc`` is required.  Importing this module
    without Sphinx installed is safe — the custom documenter is simply not
    registered.


"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)

_PROXY_AVAILABLE: bool = False

try:
    from sphinx.ext.autodoc import ClassDocumenter, prepare_docstring

    _SPHINX_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SPHINX_AVAILABLE = False


if _SPHINX_AVAILABLE:
    try:
        from deprecate.proxy import _DeprecatedProxy

        _PROXY_AVAILABLE = True
    except ImportError:  # pragma: no cover
        _PROXY_AVAILABLE = False

    if _PROXY_AVAILABLE:

        class _DeprecatedProxyClassDocumenter(ClassDocumenter):
            """ClassDocumenter subclass that handles pyDeprecate ``_DeprecatedProxy`` objects.

            Registered with a priority one step above the standard
            :class:`~sphinx.ext.autodoc.ClassDocumenter` so that it is selected
            whenever ``autoclass::`` is used on a :class:`~deprecate.proxy._DeprecatedProxy`
            instance, while all ordinary classes continue to be handled by the
            built-in documenter.
            """

            objtype = "class"
            priority = ClassDocumenter.priority + 5

            @classmethod
            def can_document_member(
                cls,
                member: Any,
                membername: str,
                isattr: bool,
                parent: Any,
            ) -> bool:
                """Return ``True`` for proxies and for all types the base class accepts."""
                return isinstance(member, _DeprecatedProxy) or super().can_document_member(
                    member, membername, isattr, parent
                )

            def import_object(self, raiseerror: bool = False) -> bool:
                """Import the object and, when it is a proxy, swap it for the wrapped class.

                The proxy's ``__doc__`` (which contains the injected deprecation notice)
                is saved in ``self._proxy_doc`` before the swap so that :meth:`get_doc`
                can return it verbatim.

                ``doc_as_attr`` is reset to ``False`` after the swap because the base
                :class:`~sphinx.ext.autodoc.ClassDocumenter` computes it using
                ``self.object.__name__``.  On a :class:`~deprecate.proxy._DeprecatedProxy`
                that attribute is forwarded to the *target* class (via
                ``__getattr__``), which makes Sphinx believe the object is an alias
                for that target and adds an ``alias of …`` paragraph.  After the swap
                ``self.object`` is the real class whose ``__name__`` matches the
                documented path, so ``doc_as_attr`` should be ``False``.
                """
                result = super().import_object(raiseerror=raiseerror)
                # Use getattr/setattr to avoid mypy confusing the Sphinx-defined
                # `object` attribute with the Python builtin `object` ([has-type]).
                obj: Any = getattr(self, "object", None)
                if result and isinstance(obj, _DeprecatedProxy):
                    # Capture the proxy docstring (contains the .. deprecated:: block).
                    self._proxy_doc: str = getattr(obj, "__doc__", "") or ""
                    # Swap the proxy for the original wrapped class so that
                    # ClassDocumenter can introspect members, __init__ signature, etc.
                    if hasattr(obj, "_cfg"):
                        self.object = obj._cfg.obj
                    # Reset doc_as_attr: the base class set it to True because
                    # proxy.__name__ (forwarded to the target) differed from the
                    # documented name.  Now that we have swapped to the real class
                    # the name matches and this is not an alias.
                    self.doc_as_attr = False
                return result

            def get_doc(self) -> list[list[str]] | None:
                """Return the proxy docstring so the ``.. deprecated::`` block is rendered."""
                proxy_doc: str = getattr(self, "_proxy_doc", "")
                if proxy_doc:
                    return [prepare_docstring(proxy_doc)]
                return super().get_doc()


def setup(app: Any) -> dict[str, Any]:
    """Register the ``_DeprecatedProxy`` documenter with Sphinx.

    Args:
        app: The Sphinx application instance.

    Returns:
        Extension metadata dict consumed by Sphinx.

    """
    if _SPHINX_AVAILABLE and _PROXY_AVAILABLE:
        app.setup_extension("sphinx.ext.autodoc")
        # override=True is required because sphinx.ext.autodoc already registers
        # ClassDocumenter for objtype="class" and the autoclass directive.
        app.add_autodocumenter(_DeprecatedProxyClassDocumenter, override=True)
    elif _SPHINX_AVAILABLE:
        _logger.warning(
            "deprecate.docstring.sphinx_ext: _DeprecatedProxy unavailable — "
            "extension loaded but _DeprecatedProxyClassDocumenter not registered."
        )
    return {"version": "0.1", "parallel_read_safe": True}
