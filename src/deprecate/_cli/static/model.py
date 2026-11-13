"""The values the static reader builds and passes around: wrappers, per-module facts and one read setting."""

import ast
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, Optional, Union

from deprecate._cli.static.consts import _DEPRECATE_MODULES, _Factory
from deprecate._cli.static.scope import _dotted, _globals_rebinds, _NameBinding, _scope_events, _single
from deprecate._types import DeprecationConfig
from deprecate.audit import DeprecationWrapperInfo
from deprecate.audit._version import _absolute_import


@dataclass(frozen=True)
class _StaticWrapperInfo(DeprecationWrapperInfo):
    """A wrapper read from source; ``unresolved`` maps each setting the source does not settle to its gap detail."""

    unresolved: Mapping[str, str] = field(default_factory=dict)


class _StaticTarget:
    """A replacement target named in source but never evaluated: callable, and named by its source text.

    Being callable, it counts as a forwarding target for the ``message-required`` rule, as the real callable would; its
    ``__qualname__`` and empty ``__module__`` make a report name it by what the source wrote.

    Examples:
        >>> target = _StaticTarget("new_func")
        >>> callable(target), target.__qualname__
        (True, 'new_func')

    """

    def __init__(self, text: str) -> None:
        self.__qualname__ = text
        self.__name__ = text
        self.__module__ = ""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Refuse to run: the replacement was only read, never imported."""
        raise TypeError(f"`{self.__qualname__}` was read from source by `--static` and cannot be called")


class _StaticDataTarget:
    """A replacement object named in source but never evaluated: plain data, so never callable.

    A ``deprecated_instance`` target that is not callable is served under the old name, but the built-in warning cannot
    name it, so ``message-required`` counts only an ``attrs_mapping`` or a custom message as guidance, as at runtime.

    Examples:
        >>> target = _StaticDataTarget("{'a': 1}")
        >>> callable(target), target.__qualname__
        (False, "{'a': 1}")

    """

    def __init__(self, text: str) -> None:
        self.__qualname__ = text
        self.__name__ = text
        self.__module__ = ""


#: Expressions that build a plain data object (a literal, a display, a comprehension): never callable.
_DATA_DISPLAYS = (
    ast.Constant,
    ast.Dict,
    ast.List,
    ast.Set,
    ast.Tuple,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.JoinedStr,
)


def _may_be_callable_class(node: ast.ClassDef) -> bool:
    """Tell whether instances of a same-file class could be called.

    That holds when the class defines ``__call__`` or has bases that may define it.

    Examples:
        >>> _may_be_callable_class(ast.parse("class C: __call__ = lambda self: None").body[0])
        True
        >>> _may_be_callable_class(ast.parse("class C: pass").body[0])
        False

    """
    if node.keywords or any(not (isinstance(base, ast.Name) and base.id == "object") for base in node.bases):
        return True
    stack: list[ast.AST] = list(node.body)
    while stack:
        stmt = stack.pop()
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if stmt.name == "__call__":
                return True
            continue  # bindings inside a nested scope do not belong to this class
        if isinstance(stmt, ast.Name) and stmt.id == "__call__" and isinstance(stmt.ctx, ast.Store):
            return True
        if isinstance(stmt, (ast.Import, ast.ImportFrom)) and any(
            (alias.asname or alias.name.split(".")[0]) == "__call__" for alias in stmt.names
        ):
            return True
        if not isinstance(stmt, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            stack.extend(ast.iter_child_nodes(stmt))
    return False


@dataclass(eq=False)
class _SourceWrapper:
    """One wrapper object the source builds; bound to every name an assignment or import gives it."""

    module: str
    #: Whether the wrapper is a proxy (``None``: built from an imported object whose kind the source does not show).
    proxy: Optional[bool]
    api_type: str
    config: DeprecationConfig
    unresolved: dict[str, str] = field(default_factory=dict)
    #: The ``__name__`` the import scan reads off the wrapper: the name of the decorated ``def``; ``None`` where the
    #: source does not settle it, so such a wrapper never owns a canonical binding.
    own_name: Optional[str] = None


@dataclass(eq=False)
class _PlainClass:
    """A plain class: its own member bindings (``None`` for a member that is not deprecated) and its bases."""

    module: str
    members: dict[str, Optional[_SourceWrapper]]
    bases: list[ast.expr]


@dataclass(frozen=True)
class _ImportRef:
    """A name imported from another module of the scanned tree, resolved when rows are built."""

    module: str
    name: str


_Value = Union[_SourceWrapper, _PlainClass, _ImportRef, None]


@dataclass
class _ModuleFacts:
    """What one module binds at module level, its ``deprecated_module`` wrapper and its unrecognized factory uses."""

    bindings: dict[str, _Value]
    module_wrapper: Optional[_SourceWrapper]
    gaps: list[str]
    #: Each in-tree ``from X import *`` as ``(X, statement position)``, in statement order.
    star_sources: list[tuple[str, int]]
    #: Names bound to a configured factory, for importers that apply it as a decorator.
    configured: dict[str, tuple["_Factory", "_FactorySettings"]] = field(default_factory=dict)
    #: The statement position of each name's last module-level binding, so a star import binds in statement order.
    positions: dict[str, int] = field(default_factory=dict)


class _ParsedModule(NamedTuple):
    """One source file: its module name, path and syntax tree."""

    name: str
    path: Path
    tree: ast.Module

    @property
    def is_package(self) -> bool:
        """Whether the file is a package's ``__init__.py``."""
        return self.path.name == "__init__.py"


def _qualified_name(parsed: _ParsedModule, scope: Mapping[str, list[_NameBinding]], node: ast.expr) -> Optional[str]:
    """Return the dotted name *node* spells in *parsed*, its root resolved through the one import that binds it."""
    dotted = _dotted(node)
    if dotted is None:
        return None
    root, dot, rest = dotted.partition(".")
    event = _single(scope, root)
    if event is not None and isinstance(event.value, ast.ImportFrom):
        origin = _absolute_import(parsed.name, parsed.is_package, event.value)
        source = next((alias.name for alias in event.value.names if (alias.asname or alias.name) == root), root)
        return f"{origin}.{source}{dot}{rest}"
    if event is not None and isinstance(event.value, ast.Import):  # ``import dataclasses as dc``
        source = next((alias.name for alias in event.value.names if alias.asname == root), root)
        return f"{source}{dot}{rest}"
    return dotted


class _FactoryCallArgs(NamedTuple):
    """The arguments of a factory call by parameter name, and an unresolved splat that may carry any of the others."""

    given: dict[str, ast.expr]
    #: How the unresolved splat reads in source, ``*args`` or ``**kwargs``; ``None`` when every argument is spelled.
    splat: Optional[str]


class _SettingValue(NamedTuple):
    """A setting read from source: its value, or the reason the source does not settle it."""

    value: Any
    reason: Optional[str] = None


class _FactorySettings(NamedTuple):
    """The settings of one factory call, read in the module that spells them.

    ``target`` is classified but not normalised: ``("default",)``, ``("const", value)`` or ``("replacement", text,
    kind)``, since how a factory stores it depends on what the configured decorator is later applied to.

    """

    where: str
    deprecated_in: _SettingValue
    remove_in: _SettingValue
    template: _SettingValue
    args_mapping: _SettingValue
    attrs_mapping: _SettingValue
    target: _SettingValue


def _display(path: Path) -> str:
    """Return *path* relative to the working directory when it lies below it, for gap lines."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


#: A configured factory an in-tree module binds, or why the reader could not follow the name to one.
_Lookup = Union[tuple[_Factory, _FactorySettings], str, None]


class _ModuleScope(NamedTuple):
    """Every binding of a module's top-level names, and what the source leaves unsettled about them."""

    events: dict[str, list[_NameBinding]]
    #: Whether the module writes its globals under a computed key, so no module-level name is settled.
    unsettled: bool
    #: Whether a star import may bind names the reader cannot list (pyDeprecate's own star imports aside).
    foreign_star: bool


def _module_scope(parsed: _ParsedModule) -> _ModuleScope:
    """Return the bindings of *parsed*'s module-level names, rebinding through ``global`` or ``globals()`` included."""
    events = _scope_events(parsed.tree.body)
    for node in ast.walk(parsed.tree):  # a function's ``global name`` may rebind it at runtime
        for name in node.names if isinstance(node, ast.Global) else ():
            events.setdefault(name, []).append(_NameBinding("other"))
    rebinds = _globals_rebinds(parsed.tree)
    for name in rebinds or ():  # as may ``globals()["NAME"] = ...``
        events.setdefault(name, []).append(_NameBinding("other"))
    foreign_star = any(
        isinstance(node, ast.ImportFrom)
        and any(alias.name == "*" for alias in node.names)
        and _absolute_import(parsed.name, parsed.is_package, node) not in _DEPRECATE_MODULES
        for node in parsed.tree.body
    )
    return _ModuleScope(events, rebinds is None, foreign_star)
