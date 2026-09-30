"""Collection of PEP 702 static-checker fixtures — pyDeprecate stacked under ``typing_extensions.deprecated``.

PEP 702 type checkers (pyright, mypy, IDEs) recognise a deprecation only when the decorator is literally
``warnings.deprecated`` / ``typing_extensions.deprecated``. pyDeprecate's own decorators stay invisible to them, so the
documented pattern is to stack the PEP 702 decorator **directly above** the pyDeprecate one, with ``category=None``
(static-only — pyDeprecate keeps owning the runtime warning) and a **string-literal** message (mypy ignores anything
else).

Two groups live here:

| Fixture                  | Decorators                                              | Audit reports it as          |
| ------------------------ | ------------------------------------------------------- | ---------------------------- |
| ``stacked_callable``     | PEP 702 (``category=None``) over ``@deprecated``         | pyDeprecate wrapper          |
| ``StackedAlias``         | PEP 702 (``category=None``) over ``@deprecated_class``   | pyDeprecate wrapper (proxy)  |
| ``StackedMembers.*``     | PEP 702 (``category=None``) over ``@deprecated`` members | pyDeprecate wrappers         |
| ``pep702_only_function`` | PEP 702 only                                            | ``pep702`` (opt-in)          |
| ``Pep702OnlyClass``      | PEP 702 only                                            | ``pep702`` (opt-in)          |
| ``Pep702OnlySubclass``   | none — inherits ``__deprecated__`` through the MRO       | never reported               |
| ``Pep702OnlyMembers.*``  | PEP 702 only, on a method and a property getter         | ``pep702`` (opt-in)          |

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

from typing import Any

import typing_extensions

from deprecate import deprecated, deprecated_class, void
from tests.collection_targets import Pep702StaticTarget, pep702_target

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


@typing_extensions.deprecated("Use `Pep702StaticTarget` instead.", category=None)
class Pep702OnlyClass:
    """Class deprecated with the PEP 702 decorator only.

    ``category=None`` keeps the fixture import-silent: with the default category, defining ``Pep702OnlySubclass``
    below would emit a ``DeprecationWarning`` at import time.

    """


class Pep702OnlySubclass(Pep702OnlyClass):
    """Subclass that is NOT itself deprecated — ``__deprecated__`` reaches it only through the MRO."""


class Pep702OnlyMembers:
    """Class whose members are deprecated with the PEP 702 decorator only."""

    @typing_extensions.deprecated("Use `pep702_target` instead.")
    def old_method(self, x: int) -> int:
        """Legacy method deprecated for static checkers only."""
        return x

    @property
    @typing_extensions.deprecated("Read `value` instead.")
    def old_value(self) -> int:
        """Legacy property — PEP 702 can only decorate the getter, since a ``property`` rejects new attributes."""
        return 1
