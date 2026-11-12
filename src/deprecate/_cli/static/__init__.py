"""Import-free source reading for the CLI's ``--static`` mode.

``pydeprecate expiry --static`` and ``pydeprecate policy --static`` read a package's source with :mod:`ast` and import
and execute nothing from it, so a package whose import needs runtime dependencies the audit environment lacks can still
be audited. Every wrapper the source builds with a pyDeprecate factory becomes a synthetic
:class:`~deprecate.audit.DeprecationWrapperInfo`, named and ordered as the import scan names it, so the expiry and
policy checks run unchanged and word their findings identically. Whatever the source alone cannot settle — a version
held in a variable, settings splatted from an expression, a factory used outside a recognized shape — is reported as a
``not statically resolvable`` gap, never skipped silently. A factory imported from a module outside the scanned tree is
not read: its call is a gap only when it passes ``remove_in`` by keyword, so one spelled with positional settings, or a
configured decorator imported from such a module, can pass unseen.

The identity rules follow the import scan (:func:`deprecate.audit.find_deprecation_wrappers`): one row per public
module-level binding, the binding named like a decorated ``def`` when it is bound under several names (else the
alphabetically-first binding), a non-proxy wrapper re-exported inside its own package attributed to the module that
defines it, a proxy to the first module in walk order that binds it, class members per class binding with the members
of same-tree base classes.

This package is private (no ``__all__``): a caller imports each name from the submodule that defines it, and the
surface may change without notice.

The submodules, from the foundations up: ``consts`` (fixed names and routes), ``scope`` (how a source binds names),
``model`` (the values the reader builds), ``targets`` (reading a ``target``), ``analyzer`` (one module's wrappers),
``discovery`` (locating the source), ``reader`` (a tree of modules), ``rows`` (the rows handed to the checks) and
``checks`` (the checks' view of those rows).

"""
