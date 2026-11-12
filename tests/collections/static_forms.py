"""Collection of deprecated wrappers spelled in every shape the CLI's ``--static`` source reader handles.

``pydeprecate expiry --static`` and ``pydeprecate policy --static`` read a package's source with ``ast`` instead of
importing it. The first part of this module spells each recognized shape with literal settings, so the static read
yields the same findings, word for word, as an import-mode scan; the last part spells each form static mode cannot
settle, which it reports as a ``not statically resolvable`` gap. The unit tests scan this module both ways.

| Wrapper                         | Shape                                                   | Static read            |
| ------------------------------- | ------------------------------------------------------- | ---------------------- |
| ``forward_sum``                 | ``@deprecated(target=..., ...)`` on a function          | resolved               |
| ``positional_notify``           | positional ``target``, ``deprecated_in``, ``remove_in`` | resolved               |
| ``templated_double``            | ``@dep.deprecated`` through a module alias              | resolved               |
| ``LegacyHolder.*``              | members: method, classmethod, staticmethod, property    | resolved               |
| ``LegacyHolder.legacy_level``   | deprecated getter and setter: the getter's settings     | resolved               |
| ``LegacyHolder.legacy_mode``    | deleter declared before setter: the setter's settings   | resolved               |
| ``WarnOnlyProxyClass``          | ``@deprecated_class`` imported from ``deprecate.proxy`` | resolved               |
| ``LegacyRecord``                | ``@deprecated_class`` on a ``@dataclass``               | resolved               |
| ``wrapped_double``              | ``deprecated(...)(source)`` with a module constant      | resolved               |
| ``legacy_settings``             | ``deprecated_instance(obj, ...)``                       | resolved               |
| ``shared_first``/``_second``    | one configured decorator applied twice                  | resolved               |
| ``public_alias``                | private wrapper bound to a public alias                 | resolved               |
| ``splat_forward``               | ``**`` of a module-level dict literal                   | resolved               |
| ``warnings_block``              | defined inside ``with catch_warnings():``               | resolved               |
| ``typed_redeclared``            | redeclared for type checkers under ``if TYPE_CHECKING`` | resolved               |
| ``ambiguous_removal``           | ``remove_in`` from a name bound twice                   | gap (``remove_in``)    |
| ``formatted_removal``           | ``remove_in`` as an f-string                            | gap (``remove_in``)    |
| ``called_splat``                | ``**dict(...)`` splat                                   | gap (every setting)    |
| ``looked_up_target``            | ``target`` from a subscript                             | gap (``target``)       |
| ``partial_built``               | built through ``partial(deprecated, ...)``              | gap (unrecognized use) |

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any
from warnings import catch_warnings, simplefilter

import deprecate as dep
from deprecate import TargetMode, deprecated, deprecated_class, deprecated_instance, void
from deprecate.proxy import deprecated_class as proxy_class
from tests.collections.api_current.core import base_sum_kwargs, double_value, increment_value

#: Introduction release shared through a module constant bound exactly once, which static mode resolves.
VERSION_INTRODUCED = "1.0"
#: Removal release bound twice, so static mode cannot tell which value a wrapper reads.
REMOVAL = "2.0"
REMOVAL = "3.0"
#: Major release spliced into an f-string version, which static mode does not evaluate.
_MAJOR = 2
#: Settings splatted into a call from a dict literal, which static mode expands.
_STATIC_ARGS: dict[str, Any] = {"deprecated_in": "1.0", "remove_in": "2.0"}
#: Replacements looked up by key, which static mode cannot evaluate.
_REPLACEMENTS = {"double": double_value}


@deprecated(target=base_sum_kwargs, deprecated_in="1.0", remove_in="2.0")
def forward_sum(a: int = 0, b: int = 3) -> int:
    """Forward to ``base_sum_kwargs`` with literal keyword settings."""
    return void(a, b)


@deprecated(TargetMode.NOTIFY, "1.0", "1.1")
def positional_notify(x: int) -> int:
    """Warn only, with the target and both versions passed positionally."""
    return void(x)


@dep.deprecated(deprecated_in="1.0", remove_in="2.0", message_template="Use `double_value` instead.")
def templated_double(x: int) -> int:
    """Warn with a custom message, decorated through the ``dep`` module alias."""
    return void(x)


class LegacyHolder:
    """Plain class whose deprecated members are each read as ``LegacyHolder.<member>``."""

    @deprecated(target=increment_value, deprecated_in="1.0", remove_in="2.0")
    def bump(self, x: int) -> int:
        """Forward a method call to ``increment_value``."""
        return void(x)

    @classmethod
    @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
    def legacy_factory(cls) -> "LegacyHolder":
        """Warn-only classmethod."""
        return cls()

    @staticmethod
    @deprecated(target=double_value, deprecated_in="1.0", remove_in="2.0")
    def legacy_double(x: int) -> int:
        """Forward a staticmethod call to ``double_value``."""
        return void(x)

    @deprecated(deprecated_in="1.0", remove_in="2.0", message_template="Read `size` instead.")  # type: ignore[prop-decorator]
    @property
    def legacy_size(self) -> int:
        """Warn on a property read, with a custom message."""
        return 1

    @property
    @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
    def legacy_level(self) -> int:
        """Warn on a read; the row carries this getter's settings, not those of the deprecated setter below."""
        return 1

    @legacy_level.setter
    @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="3.0")
    def legacy_level(self, value: int) -> None:
        """Warn on a write, with a later removal release than the getter."""

    @property
    def legacy_mode(self) -> int:
        """A getter that is not deprecated."""
        return 0

    @legacy_mode.deleter
    @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="4.0")
    def legacy_mode(self) -> None:
        """Warn on delete; declared before the setter, yet the setter is the accessor the row reads."""

    @legacy_mode.setter
    @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.5")
    def legacy_mode(self, value: int) -> None:
        """Warn on a write: the first deprecated accessor in getter, setter, deleter order."""

    def current(self) -> int:
        """A member that is not deprecated and yields no row."""
        return 0


@proxy_class(deprecated_in="1.0", remove_in="2.0")
class WarnOnlyProxyClass:
    """Warn-only class proxy, with ``deprecated_class`` imported from ``deprecate.proxy``."""


@deprecated_class(deprecated_in="1.0", remove_in="2.0", message_template="Use `NewRecord` instead.")
@dataclass
class LegacyRecord:
    """Deprecated dataclass, read with the ``dataclass`` api_type."""

    label: str = ""


def original_double(x: int) -> int:
    """Source that ``wrapped_double`` wraps."""
    return void(x)


wrapped_double = deprecated(target=double_value, deprecated_in=VERSION_INTRODUCED, remove_in="2.0")(original_double)

legacy_settings = deprecated_instance(
    {"colour": "red"}, deprecated_in="1.0", remove_in="2.0", message_template="Use `settings` instead."
)

shared_deprecation = deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="3.0")


@shared_deprecation
def shared_first(x: int) -> int:
    """First function decorated with the configured ``shared_deprecation``."""
    return void(x)


@shared_deprecation
def shared_second(x: int) -> int:
    """Second function decorated with the configured ``shared_deprecation``."""
    return void(x)


def _private_source(x: int) -> int:
    """Source of the private wrapper published as ``public_alias``."""
    return void(x)


_private_wrapper = deprecated(target=double_value, deprecated_in="1.0", remove_in="2.0")(_private_source)
public_alias = _private_wrapper


@deprecated(target=base_sum_kwargs, **_STATIC_ARGS)
def splat_forward(a: int = 0, b: int = 3) -> int:
    """Forward with both versions splatted from the ``_STATIC_ARGS`` dict literal."""
    return void(a, b)


with catch_warnings():
    simplefilter("ignore", UserWarning)

    @deprecated(target=double_value, deprecated_in="1.0", remove_in="2.0")
    def warnings_block(x: int) -> int:
        """Defined inside a ``with`` block, which always runs and binds at module level."""
        return void(x)


@deprecated(target=double_value, deprecated_in="1.0", remove_in="2.0")
def typed_redeclared(x: int) -> int:
    """Redeclared below for type checkers, in a block that never runs, so this wrapper is what callers get."""
    return void(x)


if TYPE_CHECKING:

    def typed_redeclared(x: int) -> int:  # type: ignore[no-redef]
        """The signature type checkers read in place of the wrapper's."""


# ---------------------------------------------------------------------------------------------------------------------
# Forms static mode cannot settle: each is a gap, never a silent skip.
# ---------------------------------------------------------------------------------------------------------------------


@deprecated(target=double_value, deprecated_in="1.0", remove_in=REMOVAL)
def ambiguous_removal(x: int) -> int:
    """``remove_in`` read from ``REMOVAL``, which the module binds twice."""
    return void(x)


@deprecated(target=double_value, deprecated_in="1.0", remove_in=f"{_MAJOR}.0")
def formatted_removal(x: int) -> int:
    """``remove_in`` spelled as an f-string."""
    return void(x)


@deprecated(target=double_value, **dict(deprecated_in="1.0", remove_in="2.0"))  # type: ignore[arg-type]  # noqa: C408
def called_splat(x: int) -> int:
    """Every setting splatted from a ``dict(...)`` call."""
    return void(x)


@deprecated(target=_REPLACEMENTS["double"], deprecated_in="1.0", remove_in="2.0")
def looked_up_target(x: int) -> int:
    """``target`` looked up by key, so whether it names a replacement is not settled."""
    return void(x)


deprecate_until_two = partial(deprecated, deprecated_in="1.0", remove_in="2.0")


@deprecate_until_two(target=double_value)
def partial_built(x: int) -> int:
    """Built through ``partial(deprecated, ...)``, a use of the factory static mode does not follow."""
    return void(x)
