"""Collection of PEP 702 static-checker fixtures — pyDeprecate stacked under ``typing_extensions.deprecated``.

PEP 702 type checkers (pyright, mypy, IDEs) recognise a deprecation only when the decorator is literally
``warnings.deprecated`` / ``typing_extensions.deprecated``. pyDeprecate's own decorators stay invisible to them, so the
documented pattern is to stack the PEP 702 decorator **directly above** the pyDeprecate one, with ``category=None``
(static-only — pyDeprecate keeps owning the runtime warning) and a **string-literal** message (mypy ignores anything
else).

Two groups live here:

| Fixture                           | Decorators                                             | Audit reports it as   |
| --------------------------------- | ------------------------------------------------------ | --------------------- |
| ``stacked_callable``              | PEP 702 (``category=None``) over ``@deprecated``       | pyDeprecate wrapper   |
| ``StackedAlias``                  | PEP 702 (``category=None``) over ``@deprecated_class`` | pyDeprecate proxy     |
| ``StackedMembers.*``              | PEP 702 (``category=None``) over ``@deprecated``       | pyDeprecate wrappers  |
| ``pep702_only_function``          | PEP 702 only                                           | ``callable`` (opt-in) |
| ``pep702_empty_message``          | PEP 702 only, with an empty message                    | ``callable`` (opt-in) |
| ``Pep702OnlyClass``               | PEP 702 only                                           | ``class`` (opt-in)    |
| ``Pep702OnlySubclass``            | none — inherits ``__deprecated__`` (MRO)               | never reported        |
| ``Pep702DefaultCategoryClass``    | PEP 702 only, default category                         | one ``class`` row     |
| ``Pep702DefaultCategorySubclass`` | none — inherits the class hooks (MRO)                  | never reported        |
| ``Pep702CallableClass``           | PEP 702 only, on a callable class                      | ``class`` (opt-in)    |
| ``pep702_callable_instance``      | none — an instance of ``Pep702CallableClass``          | never reported        |
| ``Pep702LibrarySubclass``         | none — inherits PEP 702 methods (MRO)                  | never reported        |
| ``Pep702OnlyMembers.*``           | PEP 702 only, on methods and a property getter         | member rows (opt-in)  |

Opt-in rows (``include_pep702=True``) classify ``api_type`` by shape like any other row and carry the decorator's
message in ``pep702_message``, which is ``None`` on every pyDeprecate row.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import warnings
from typing import Any

import typing_extensions

from deprecate import deprecated, deprecated_class, void
from tests.collection_targets import Pep702LibraryBase, Pep702StaticTarget, pep702_target

#: Shared schedule for every stacked wrapper here; ``num_warns=-1`` so runtime tests never depend on call order.
_DEPRS_CASE_STACKED_ARGS: dict[str, Any] = {"deprecated_in": "1.0", "remove_in": "2.0", "num_warns": -1}


@typing_extensions.deprecated("Use `pep702_target` instead.", category=None)
@deprecated(target=pep702_target, **_DEPRS_CASE_STACKED_ARGS)
def stacked_callable(x: int) -> int:
    """Old helper renamed to ``pep702_target`` — flagged by type checkers and warned about at runtime.

    A library wants editors to strike through ``stacked_callable(...)`` before any code runs, while still forwarding
    existing calls with a ``FutureWarning``. The PEP 702 decorator carries the static signal only; pyDeprecate warns.

    """
    return void(x)


@typing_extensions.deprecated("Use `Pep702StaticTarget` instead.", category=None)
@deprecated_class(target=Pep702StaticTarget, **_DEPRS_CASE_STACKED_ARGS)
class StackedAlias:
    """Old class name kept as a forwarding alias for ``Pep702StaticTarget``.

    Constructing ``StackedAlias(3)`` builds a ``Pep702StaticTarget`` with a runtime warning; mypy flags the name. The
    replacement class must not inherit the deprecation marker. The body keeps the old API surface so type checkers,
    which ignore a class decorator's return type, still see the constructor and ``doubled``; it never runs.

    """

    def __init__(self, value: int = 0) -> None:
        """Mirror the replacement constructor for type checkers."""
        self.value = value

    def doubled(self) -> int:
        """Mirror the replacement method for type checkers."""
        return self.value * 2


class StackedMembers:
    """Service class whose legacy members are deprecated for both runtime and static checkers."""

    @typing_extensions.deprecated("Use `pep702_target` instead.", category=None)
    @deprecated(**_DEPRS_CASE_STACKED_ARGS)
    def legacy_method(self, x: int) -> int:
        """Legacy method kept for one release; callers see a strikethrough and a runtime warning."""
        return x

    @typing_extensions.deprecated("Read `value` instead.", category=None)  # type: ignore[prop-decorator]
    @deprecated(**_DEPRS_CASE_STACKED_ARGS)
    @property
    def legacy_value(self) -> int:
        """Legacy property — PEP 702 sits above pyDeprecate's outer-order ``@deprecated @property``."""
        return 1

    @staticmethod
    @typing_extensions.deprecated("Use `pep702_target` instead.", category=None)
    @deprecated(**_DEPRS_CASE_STACKED_ARGS)
    def legacy_static(x: int) -> int:
        """Legacy static helper; the PEP 702 decorator goes inside ``@staticmethod``, directly above ``@deprecated``."""
        return x


@typing_extensions.deprecated("Use `pep702_target` instead.")
def pep702_only_function(x: int) -> int:
    """Helper deprecated with the stdlib-style decorator only — no pyDeprecate metadata, no version schedule.

    A project that has not adopted pyDeprecate for this symbol still wants its audit report to list it, so a maintainer
    sees every live deprecation in one place.

    """
    return x


@typing_extensions.deprecated("", category=None)
def pep702_empty_message() -> None:
    """Keep an empty but valid PEP 702 marker visible to the audit scanner."""


@typing_extensions.deprecated("Use `Pep702StaticTarget` instead.", category=None)
class Pep702OnlyClass:
    """Class deprecated with the PEP 702 decorator only.

    ``category=None`` keeps the fixture import-silent: with the default category, defining ``Pep702OnlySubclass``
    below would emit a ``DeprecationWarning`` at import time.

    """


class Pep702OnlySubclass(Pep702OnlyClass):
    """Subclass that is NOT itself deprecated — ``__deprecated__`` reaches it only through the MRO."""


@typing_extensions.deprecated("Use `Pep702StaticTarget` instead.")
class Pep702DefaultCategoryClass:
    """Class deprecated with the decorator's default ``DeprecationWarning`` category — the common spelling.

    With a category set, the decorator also installs ``__new__`` and ``__init_subclass__`` on the class and stamps both
    with the class's own message. An audit must list the class once, not those two hooks as extra deprecated members.

    """


# Subclassing a default-category class runs the installed ``__init_subclass__``, which warns; silence that one warning
# so importing this fixture module stays quiet like every other fixture here.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)

    class Pep702DefaultCategorySubclass(Pep702DefaultCategoryClass):
        """Undecorated subclass — the hooks and ``__deprecated__`` reach it only through the MRO."""


@typing_extensions.deprecated("Use `pep702_target` instead.", category=None)
class Pep702CallableClass:
    """Callable class deprecated with the PEP 702 decorator only — the type behind a ready-made function object."""

    def __call__(self, x: int) -> int:
        """Apply the legacy transformation."""
        return x


#: Ready-made callable a library exposes, built from the deprecated class. The instance is not deprecated itself:
#: ``__deprecated__`` reaches it only through its type, just as a subclass reaches it only through the MRO.
pep702_callable_instance = Pep702CallableClass()


class Pep702LibrarySubclass(Pep702LibraryBase):
    """Project model built on a library base whose methods are PEP 702-deprecated; it deprecates nothing itself.

    The inherited ``export_legacy`` and ``_iter_legacy`` are the library's deprecations. Listing them here would repeat
    them once per project subclass — the pydantic ``BaseModel`` case, where a one-field model used to yield a row for
    every deprecated base method.

    """

    def export(self) -> dict[str, Any]:
        """Project's own, non-deprecated replacement API."""
        return {}


class Pep702OnlyMembers:
    """Class whose members are deprecated with the PEP 702 decorator only."""

    @typing_extensions.deprecated("Use `pep702_target` instead.")
    def old_method(self, x: int) -> int:
        """Legacy method deprecated for static checkers only."""
        return x

    @typing_extensions.deprecated("Use the public method instead.", category=None)
    def _old_method(self) -> None:
        """Private PEP 702 method still needs an audit row while it remains live."""

    @property
    @typing_extensions.deprecated("Use the public value instead.", category=None)
    def _old_value(self) -> int:
        """Private property getter carrying a PEP 702 marker."""
        return 1

    def _ordinary(self) -> None:
        """Private member without a marker must remain absent from the audit report."""

    @staticmethod
    @typing_extensions.deprecated("Use `pep702_target` instead.", category=None)
    def old_static(x: int) -> int:
        """Legacy static helper deprecated for static checkers only — its row reads as a ``staticmethod``."""
        return x

    @property
    @typing_extensions.deprecated("Read `value` instead.")
    def old_value(self) -> int:
        """Legacy property — PEP 702 can only decorate the getter, since a ``property`` rejects new attributes."""
        return 1
