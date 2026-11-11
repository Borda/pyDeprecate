"""Fixed names and tables the static reader shares: the factories it recognizes, its routes, and its limits."""

import ast
import functools
import importlib
import importlib.util
import inspect
from enum import Enum
from typing import Any

from deprecate import TargetMode, deprecated, deprecated_callable, deprecated_class, deprecated_instance
from deprecate.module import deprecated_module

#: The settings each check reads; a wrapper with one of them unresolved is left out of that check, with a gap.
_EXPIRY_KEYWORDS = ("remove_in",)
_GRACE_KEYWORDS = ("deprecated_in", "remove_in")
_GUIDANCE_KEYWORDS = ("target", "args_mapping", "attrs_mapping", "message_template")
#: Why a name re-exported through in-tree modules is not followed: the re-exports lead back to where they started.
_CYCLE = "is re-exported in an import cycle; not followed"
#: Base-class levels followed for inherited member rows; past it, a gap.
_BASE_DEPTH = 8
#: Methods that change a dict in place, so a dict a module calls them on is no literal the source settles.
_DICT_MUTATORS = frozenset({"update", "setdefault", "pop", "popitem", "clear", "__setitem__", "__delitem__", "__ior__"})
#: Decorators that keep the object they receive reachable as the import scan reads it, so one stacked over a factory
#: is no gap; any other may replace the wrapper (a CLI ``@command`` turns it into a command object).
_TRANSPARENT_DECORATORS = frozenset(
    {
        "property",
        "classmethod",
        "staticmethod",
        "functools.lru_cache",
        "functools.cache",
        "functools.wraps",
        "abc.abstractmethod",
        "dataclasses.dataclass",
        "typing.final",
        "typing_extensions.final",
        "typing.override",
        "typing_extensions.override",
        "typing_extensions.deprecated",
        "warnings.deprecated",
    }
)
#: Property accessors, ``@old.setter``, which rebuild a property around the getter rather than replace it.
_ACCESSORS = frozenset({"getter", "setter", "deleter"})
#: The property slot each accessor decorator fills, in the order the import scan reads a property's accessors.
_ACCESSOR_SLOTS = {"getter": "fget", "setter": "fset", "deleter": "fdel"}
#: The decorator that makes a class a dataclass, by the dotted name its import resolves to.
_DATACLASS = "dataclasses.dataclass"
#: The flag an ``if`` tests to hold code for type checkers only: that body never runs, so it binds nothing at runtime.
_TYPE_CHECKING_FLAGS = frozenset({"typing.TYPE_CHECKING", "typing_extensions.TYPE_CHECKING"})
#: Statements whose bodies may or may not run, so a name they rebind holds a value the source does not settle.
_CONDITIONAL_STATEMENTS: tuple[type, ...] = tuple(
    getattr(ast, name) for name in ("If", "For", "AsyncFor", "While", "Try", "TryStar", "Match") if hasattr(ast, name)
)
#: Modules outside the standard library whose classes carry no pyDeprecate members, so inheriting from them is no gap.
_MEMBERLESS_MODULES = frozenset({"typing_extensions", "abc", "builtins"})


class _Factory(str, Enum):
    """A pyDeprecate name the reader recognizes, by the object it is bound to; ``TargetMode`` names the enum."""

    DEPRECATED = "deprecated"
    CALLABLE = "deprecated_callable"
    CLASS = "deprecated_class"
    INSTANCE = "deprecated_instance"
    MODULE = "deprecated_module"
    TARGET_MODE = "TargetMode"


#: The real objects behind each recognized name, so ``from deprecate.proxy import deprecated_class`` is identified by
#: what the name holds, wherever pyDeprecate exports it from.
_FACTORY_OBJECTS: tuple[tuple[Any, _Factory], ...] = (
    (deprecated, _Factory.DEPRECATED),
    (deprecated_callable, _Factory.CALLABLE),
    (deprecated_class, _Factory.CLASS),
    (deprecated_instance, _Factory.INSTANCE),
    (deprecated_module, _Factory.MODULE),
    (TargetMode, _Factory.TARGET_MODE),
)
#: pyDeprecate modules a recognized name can be imported from.
_DEPRECATE_MODULES = frozenset(
    {
        "deprecate",
        "deprecate.deprecation",
        "deprecate.routine",
        "deprecate.proxy",
        "deprecate.proxy.factories",
        "deprecate.module",
        "deprecate._types",
    }
)


class _Route(str, Enum):
    """How the factory that built a wrapper normalises its ``target``, mirroring the runtime decorators."""

    CALLABLE = "callable"  # ``@deprecated`` on a function or method
    CALLABLE_STRICT = "callable strict"  # ``@deprecated_callable``
    CLASS_FRONT = "class front"  # ``@deprecated`` on a class, which delegates to ``deprecated_class``
    CLASS = "class"  # ``deprecated_class``
    UNKNOWN_FRONT = "unknown front"  # ``deprecated(...)(obj)`` where the source cannot tell whether ``obj`` is a class
    INSTANCE = "instance"  # ``deprecated_instance``
    MODULE = "module"  # ``deprecated_module``


@functools.cache
def _module_exports(module_path: str, star: bool = False) -> dict[str, _Factory]:
    """Return the recognized names *module_path* binds, or those ``from <module_path> import *`` binds with *star*.

    Examples:
        >>> _module_exports("deprecate.proxy")["deprecated_class"]
        <_Factory.CLASS: 'deprecated_class'>
        >>> _module_exports("json")
        {}

    """
    if module_path not in _DEPRECATE_MODULES:
        return {}
    module = importlib.import_module(module_path)
    kinds = {id(obj): kind for obj, kind in _FACTORY_OBJECTS}
    public = getattr(module, "__all__", None) if star else None
    names = list(public) if public is not None else [name for name in vars(module) if not name.startswith("_")]
    return {name: kinds[id(getattr(module, name, None))] for name in names if id(getattr(module, name, None)) in kinds}


@functools.cache
def _positional_params(factory: _Factory) -> tuple[str, ...]:
    """Return the parameters of *factory* a positional argument fills, in order, read from its real signature.

    Examples:
        >>> _positional_params(_Factory.DEPRECATED)[:3]
        ('target', 'deprecated_in', 'remove_in')
        >>> _positional_params(_Factory.CLASS)
        ('target',)

    """
    function = {kind: obj for obj, kind in _FACTORY_OBJECTS}[factory]
    positional = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    return tuple(param.name for param in inspect.signature(function).parameters.values() if param.kind in positional)
