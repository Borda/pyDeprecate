"""Import-free source reading for the CLI's ``--static`` mode.

``pydeprecate expiry --static`` and ``pydeprecate policy --static`` read a package's source with :mod:`ast` and import
and execute nothing from it, so a package whose import needs runtime dependencies the audit environment lacks can still
be audited. Every wrapper the source builds with a pyDeprecate factory becomes a synthetic
:class:`~deprecate.audit.DeprecationWrapperInfo`, named and ordered as the import scan names it, so the expiry and
policy checks run unchanged and word their findings identically. Whatever the source alone cannot settle — a version
held in a variable, settings splatted from an expression, a factory used outside a recognized shape — is reported as a
``not statically resolvable`` gap, never skipped silently.

The identity rules follow the import scan (:func:`deprecate.audit.find_deprecation_wrappers`): one row per public
module-level binding, the alphabetically-first binding of a wrapper bound under several names, a non-proxy wrapper
re-exported inside its own package attributed to the module that defines it, a proxy to the first module in walk order
that binds it, class members per class binding with the members of same-tree base classes.

This module is private (no ``__all__``); its surface may change without notice.

"""

import ast
import builtins
import functools
import importlib
import importlib.util
import inspect
import os
import sys
import sysconfig
import types
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional, Union

from deprecate import TargetMode, deprecated, deprecated_callable, deprecated_class, deprecated_instance
from deprecate._cli.report import _GapKind, _print_gap
from deprecate._types import DeprecationConfig
from deprecate._validate import _resolve_legacy_proxy_target, _resolve_legacy_target
from deprecate.audit import DeprecationWrapperInfo
from deprecate.audit._findings import _Finding, _Rule
from deprecate.audit._policy import _has_migration_guidance, _policy_findings, _PolicySpec
from deprecate.audit._scan import _is_excluded
from deprecate.audit._version import _absolute_import, _module_source_file
from deprecate.audit._wrappers import _classify_member_api_type, _format_subject
from deprecate.module import deprecated_module

#: The settings each check reads; a wrapper with one of them unresolved is left out of that check, with a gap.
_EXPIRY_KEYWORDS = ("remove_in",)
_GRACE_KEYWORDS = ("deprecated_in", "remove_in")
_GUIDANCE_KEYWORDS = ("target", "args_mapping", "attrs_mapping", "message_template")
#: Re-export hops followed through in-tree modules (``from .core import old`` in ``__init__.py``); past it, a gap.
_IMPORT_HOPS = 5
#: Base-class levels followed for inherited member rows; past it, a gap.
_BASE_DEPTH = 8
#: Methods that change a dict in place, so a dict a module calls them on is no literal the source settles.
_DICT_MUTATORS = frozenset({"update", "setdefault", "pop", "popitem", "clear", "__setitem__", "__delitem__", "__ior__"})
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


def _dotted(node: ast.expr) -> Optional[str]:
    """Return ``a.b.c`` for a name or attribute chain, else ``None``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base is not None else None
    return None


@dataclass
class _Aliases:
    """The names a module binds to recognized pyDeprecate objects, and the pyDeprecate modules it binds.

    ``ambiguous`` holds the names a pyDeprecate import binds that the source does not settle: bound inside a function,
    or bound at module level more than once. What such a name holds depends on runtime, so each use of it is a gap.

    """

    names: dict[str, _Factory] = field(default_factory=dict)
    modules: dict[str, str] = field(default_factory=dict)
    ambiguous: dict[str, Optional[_Factory]] = field(default_factory=dict)

    def merge(self, bound: "_Aliases", *, settled: bool, scope: Mapping[str, list], star: bool) -> None:
        """Add what one import statement binds: settled when it is the name's only module-level binding."""
        for name, kind in bound.names.items():
            if settled and len(scope.get(name, [])) == (0 if star else 1):
                self.names[name] = kind
            else:
                self.ambiguous[name] = kind
        for name, module in bound.modules.items():
            if settled and (len(scope.get(name, [])) == 1 or _binds_package_only(scope.get(name, []), name)):
                self.modules[name] = module
            else:
                self.ambiguous[name] = None

    def settle(self) -> "_Aliases":
        """Drop from ``ambiguous`` the names a module-level import settles; a function-local import shadows locally."""
        self.ambiguous = {
            name: kind for name, kind in self.ambiguous.items() if name not in self.names and name not in self.modules
        }
        return self

    def kind(self, node: ast.expr) -> Optional[_Factory]:
        """Return what *node* (``deprecated``, ``d.deprecated``, ``deprecate.proxy.deprecated_class``) refers to."""
        if isinstance(node, ast.Name):
            return self.names.get(node.id)
        dotted = _dotted(node)
        if dotted is None or "." not in dotted:
            return None
        root, _, rest = dotted.partition(".")
        module = self.modules.get(root)
        if module is None:
            return None
        module_path, _, attr = f"{module}.{rest}".rpartition(".")
        return _module_exports(module_path).get(attr)

    def target_mode(self, node: ast.expr) -> Optional[TargetMode]:
        """Return the :class:`~deprecate.TargetMode` member *node* spells (``TargetMode.NOTIFY``), else ``None``."""
        if not isinstance(node, ast.Attribute) or self.kind(node.value) is not _Factory.TARGET_MODE:
            return None
        return TargetMode.__members__.get(node.attr)


def _binds_package_only(events: Sequence["_Event"], name: str) -> bool:
    r"""Tell whether every binding of *name* is a plain ``import deprecate[.sub]``, which all bind the same package.

    Examples:
        >>> tree = ast.parse("import deprecate\nimport deprecate.proxy")
        >>> _binds_package_only(_scope_events(tree.body)["deprecate"], "deprecate")
        True
        >>> tree = ast.parse("import deprecate\ndeprecate = None")
        >>> _binds_package_only(_scope_events(tree.body)["deprecate"], "deprecate")
        False

    """
    return name == "deprecate" and all(
        isinstance(event.value, ast.Import)
        and all(alias.asname is None for alias in event.value.names if alias.name.split(".")[0] == name)
        for event in events
    )


def _module_level_nodes(tree: ast.Module) -> set[int]:
    """Return the ids of the nodes that run at module level: everything outside function, class and lambda bodies."""
    found: set[int] = set()
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        found.add(id(node))
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))
    return found


def _direct_aliases(tree: ast.Module) -> _Aliases:
    """Collect the pyDeprecate names and modules a module's own import statements bind."""
    aliases = _Aliases()
    top = _module_level_nodes(tree)
    scope = _scope_events(tree.body)
    for node in ast.walk(tree):
        bound = _Aliases()
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] != "deprecate":
                    continue
                # ``import deprecate.proxy`` binds ``deprecate``
                bound.modules[alias.asname or "deprecate"] = alias.name if alias.asname else "deprecate"
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module in _DEPRECATE_MODULES:
            for alias in node.names:
                _bind_from_deprecate(bound, node.module, alias)
        else:
            continue
        star = any(alias.name == "*" for alias in node.names)
        aliases.merge(bound, settled=id(node) in top, scope=scope, star=star)
    return aliases.settle()


def _bind_from_deprecate(aliases: _Aliases, module: str, alias: ast.alias) -> None:
    """Record what one ``from <pyDeprecate module> import <alias>`` binds."""
    if alias.name == "*":
        aliases.names.update(_module_exports(module, star=True))
        return
    local = alias.asname or alias.name
    kind = _module_exports(module).get(alias.name)
    if kind is not None:
        aliases.names[local] = kind
    elif f"{module}.{alias.name}" in _DEPRECATE_MODULES:
        aliases.modules[local] = f"{module}.{alias.name}"


class _Event(NamedTuple):
    """One binding of a name in a scope: how it is bound, and the assigned value for a plain assignment."""

    kind: str  # "assign", "def", "class", "import" or "other"
    value: Optional[ast.AST] = None


def _node_events(node: ast.AST, events: dict[str, list[_Event]]) -> list[ast.AST]:
    """Record the bindings *node* itself makes in its scope and return the child nodes still to visit.

    A ``def``, ``class`` or lambda opens a scope of its own, so none of its children is visited. A plain ``name =
    value`` records its value; a star import binds names the reader cannot list here; any other store or delete (loop
    target, ``with ... as``, walrus, augmented assignment) binds opaquely.

    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        events[node.name].append(_Event("class" if isinstance(node, ast.ClassDef) else "def", node))
        return []
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        rest: list[ast.AST] = [node.value] if node.value is not None else []
        for target in targets:
            if not isinstance(target, ast.Name):
                rest.append(target)
            elif node.value is not None:
                events[target.id].append(_Event("assign", node.value))
        return rest
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        _import_events(node, events)
    elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        events[node.id].append(_Event("other"))
    elif not isinstance(node, ast.Lambda):
        return list(ast.iter_child_nodes(node))
    return []


def _import_events(node: Union[ast.Import, ast.ImportFrom], events: dict[str, list[_Event]]) -> None:
    """Record what an import binds: ``import a.b`` binds ``a``; ``from x import a as b`` binds ``b``."""
    for alias in node.names:
        if isinstance(node, ast.Import):
            events[alias.asname or alias.name.split(".")[0]].append(_Event("import", node))
        elif alias.name != "*":
            events[alias.asname or alias.name].append(_Event("import", node))


def _scope_events(body: Sequence[ast.stmt]) -> dict[str, list[_Event]]:
    """Return every binding of every name in a module or class body, compound statements included.

    The walk keeps its own stack, so a deeply nested expression (a long generated concatenation) cannot exhaust the
    interpreter's recursion limit.

    """
    events: dict[str, list[_Event]] = defaultdict(list)
    stack: list[ast.AST] = list(body)
    while stack:
        stack.extend(_node_events(stack.pop(), events))
    return dict(events)


def _mutated_names(tree: ast.Module) -> set[str]:
    """Return the names the module changes in place anywhere: ``name[key] = ...``, ``name.update(...)`` and the like."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Subscript, ast.Attribute)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            target = node.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _DICT_MUTATORS:
            target = node.func.value
        else:
            continue
        if isinstance(target, ast.Name):
            found.add(target.id)
    return found


def _literal_all(tree: ast.Module) -> Union[set[str], None, str]:
    """Return the names a module's ``__all__`` lists for a star import.

    ``None`` when the module defines no ``__all__`` (a star import copies every public name), else why the list is not
    readable from source.

    """
    events = _scope_events(tree.body).get("__all__", [])
    if not events:
        return None
    value = events[0].value if len(events) == 1 else None
    if isinstance(value, (ast.List, ast.Tuple)):
        names = [elt.value for elt in value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
        if len(names) == len(value.elts):
            return set(names)
    return "has an `__all__` that is not one literal list of names"


@functools.cache
def _is_memberless_module(module: str) -> bool:
    """Tell whether classes from *module* (the standard library, or a typing backport) can carry no pyDeprecate member.

    Located without importing anything: :data:`sys.stdlib_module_names` where the interpreter has it, else the
    location :func:`importlib.util.find_spec` reports for the top-level name, which runs finders but no module code.

    """
    top = module.split(".")[0]
    if top in _MEMBERLESS_MODULES or top in sys.builtin_module_names:
        return True
    names = getattr(sys, "stdlib_module_names", None)
    if names is not None:
        return top in names
    try:
        spec = importlib.util.find_spec(top)
    except (ImportError, ValueError):
        return False
    origin = spec.origin if spec is not None else None
    stdlib = os.path.normcase(sysconfig.get_paths()["stdlib"])
    return bool(origin) and os.path.normcase(str(origin)).startswith(stdlib) and "site-packages" not in str(origin)


def _single(events: Mapping[str, list[_Event]], name: str) -> Optional[_Event]:
    """Return the binding of *name* when the scope binds it exactly once, else ``None``."""
    found = events.get(name, [])
    return found[0] if len(found) == 1 else None


def _is_dataclass(node: ast.ClassDef) -> bool:
    """Tell whether a class definition carries ``@dataclass`` or ``@dataclasses.dataclass`` (called or not)."""
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if _dotted(target) in ("dataclass", "dataclasses.dataclass"):
            return True
    return False


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


@dataclass(eq=False)
class _Wrapper:
    """One wrapper object the source builds; bound to every name an assignment or import gives it."""

    module: str
    #: Whether the wrapper is a proxy (``None``: built from an imported object whose kind the source does not show).
    proxy: Optional[bool]
    api_type: str
    config: DeprecationConfig
    unresolved: dict[str, str] = field(default_factory=dict)


@dataclass(eq=False)
class _ClassInfo:
    """A plain class: its own member bindings (``None`` for a member that is not deprecated) and its bases."""

    module: str
    members: dict[str, Optional[_Wrapper]]
    bases: list[ast.expr]


@dataclass(frozen=True)
class _ImportRef:
    """A name imported from another module of the scanned tree, resolved when rows are built."""

    module: str
    name: str


_Value = Union[_Wrapper, _ClassInfo, _ImportRef, None]


@dataclass
class _ModuleFacts:
    """What one module binds at module level, its ``deprecated_module`` wrapper and its unrecognized factory uses."""

    bindings: dict[str, _Value]
    module_wrapper: Optional[_Wrapper]
    gaps: list[str]
    star_sources: list[str]
    #: Names bound to a configured factory, for importers that apply it as a decorator.
    configured: dict[str, tuple["_Factory", "_Settings"]] = field(default_factory=dict)


class _Parsed(NamedTuple):
    """One source file: its module name, path and syntax tree."""

    name: str
    path: Path
    tree: ast.Module

    @property
    def is_package(self) -> bool:
        """Whether the file is a package's ``__init__.py``."""
        return self.path.name == "__init__.py"


class _CallArgs(NamedTuple):
    """The arguments of a factory call by parameter name, and an unresolved splat that may carry any of the others."""

    given: dict[str, ast.expr]
    #: How the unresolved splat reads in source, ``*args`` or ``**kwargs``; ``None`` when every argument is spelled.
    splat: Optional[str]


class _Read(NamedTuple):
    """A setting read from source: its value, or the reason the source does not settle it."""

    value: Any
    reason: Optional[str] = None


class _Settings(NamedTuple):
    """The settings of one factory call, read in the module that spells them.

    ``target`` is classified but not normalised: ``("default",)``, ``("const", value)`` or ``("replacement", text,
    kind)``, since how a factory stores it depends on what the configured decorator is later applied to.

    """

    where: str
    deprecated_in: _Read
    remove_in: _Read
    template: _Read
    args_mapping: _Read
    attrs_mapping: _Read
    target: _Read


def _display(path: Path) -> str:
    """Return *path* relative to the working directory when it lies below it, for gap lines."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


#: A configured factory an in-tree module binds, or why the reader could not follow the name to one.
_Lookup = Union[tuple[_Factory, _Settings], str, None]


class _ModuleAnalyzer:
    """Read one module's top-level statements into :class:`_ModuleFacts`, recognizing every wrapper shape."""

    def __init__(
        self,
        parsed: _Parsed,
        aliases: _Aliases,
        in_tree: Callable[[str], bool],
        name_kind: Callable[[str, str], tuple[str, Optional[ast.AST]]],
        imported_configured: Callable[[str, str], _Lookup],
    ) -> None:
        self.parsed = parsed
        self.aliases = aliases
        self.in_tree = in_tree
        self.imported_kind = name_kind
        self.imported_configured = imported_configured
        self.scope = _scope_events(parsed.tree.body)
        for node in ast.walk(parsed.tree):  # a function's ``global name`` may rebind it at runtime
            for name in node.names if isinstance(node, ast.Global) else ():
                self.scope.setdefault(name, []).append(_Event("other"))
        #: Names changed in place somewhere (``KW["remove_in"] = ...``), so a dict they hold is no settled literal.
        self.mutated = _mutated_names(parsed.tree)
        #: Whether a star import may bind names the reader cannot list (pyDeprecate's own star imports aside).
        self.foreign_star = any(
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "*" for alias in node.names)
            and _absolute_import(parsed.name, parsed.is_package, node) not in _DEPRECATE_MODULES
            for node in parsed.tree.body
        )
        self.consumed: set[int] = set()
        self.bindings: dict[str, _Value] = {}
        #: Names bound to a configured factory call (``_dep = deprecated(...)``), later applied as ``@_dep``.
        self.configured: dict[str, tuple[_Factory, _Settings]] = {}
        #: Every name the module ever bound to a configured factory, whose unconsumed uses are gaps.
        self.ever_configured: set[str] = set()
        self.lookup_gaps: list[str] = []
        self.module_wrapper: Optional[_Wrapper] = None
        self.star_sources: list[str] = []
        self.where = _display(parsed.path)

    def run(self) -> _ModuleFacts:
        """Walk the module body in order, so the last binding of a name wins as at runtime."""
        for stmt in self.parsed.tree.body:
            self._statement(stmt)
        return _ModuleFacts(
            self.bindings, self.module_wrapper, self._unrecognized(), self.star_sources, dict(self.configured)
        )

    def _set(self, name: str, value: _Value) -> None:
        """Bind a module-level name, dropping any configured factory it held before."""
        self.bindings[name] = value
        self.configured.pop(name, None)

    def _statement(self, stmt: ast.stmt) -> None:
        """Record what one module-level statement binds."""
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._set(stmt.name, self._decorated_def(stmt, attr=None, shadow={}))
        elif isinstance(stmt, ast.ClassDef):
            self._set(stmt.name, self._class(stmt, attr=None, shadow={}))
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            self._assign(stmt)
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            self._module_call(stmt.value)
        elif isinstance(stmt, ast.ImportFrom):
            self._import_from(stmt)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            # a ``with`` body always runs (``with catch_warnings():`` around decorations is common), so it is read
            # as module-level code; its ``as`` targets bind opaque values
            for name in _scope_events([ast.Expr(item.optional_vars) for item in stmt.items if item.optional_vars]):
                self._set(name, None)
            for inner in stmt.body:
                self._statement(inner)
        else:
            for name in _scope_events([stmt]):
                self._set(name, None)

    def _assign(self, stmt: Union[ast.Assign, ast.AnnAssign]) -> None:
        """Bind each plain name target to the wrapper, class or import the value is (an alias shares the object)."""
        if stmt.value is None:
            return
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        value = stmt.value
        configured = self._configured_factory(value)
        result: _Value = None
        if configured is None:
            result = self._assigned_value(value)
        for target in targets:
            if isinstance(target, ast.Name):
                self._set(target.id, result)
                if configured is not None:
                    self.configured[target.id] = configured
                    self.ever_configured.add(target.id)
                continue
            for node in ast.walk(target):  # unpacking or attribute targets bind no readable value
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    self._set(node.id, None)

    def _configured_factory(self, value: ast.expr) -> Optional[tuple[_Factory, _Settings]]:
        """Recognize ``deprecated(...)`` or ``deprecated_class(...)`` assigned to a name to be applied later."""
        if not isinstance(value, ast.Call):
            return None
        kind = self.aliases.kind(value.func)
        if kind not in (_Factory.DEPRECATED, _Factory.CALLABLE, _Factory.CLASS):
            return None
        self.consumed.add(id(value.func))
        return kind, self._read_settings(self._bind(value, kind), value, {})

    def _assigned_value(self, value: ast.expr) -> _Value:
        """Return what an assigned value binds: an alias shares the object; ``typing.cast(T, x)`` aliases ``x``."""
        if isinstance(value, ast.Call) and _dotted(value.func) in ("cast", "typing.cast") and len(value.args) == 2:
            value = value.args[1]
        if isinstance(value, ast.Name):
            return self.bindings.get(value.id)
        return self._wrapper_expr(value, attr=None, shadow={})

    def _factory_call(
        self, node: ast.expr, kinds: tuple[_Factory, ...], shadow: Mapping[str, list]
    ) -> Optional[tuple[_Factory, _Settings]]:
        """Return the factory and settings of ``factory(...)``, or of a name bound to one here or in-tree."""
        configured: _Lookup = None
        if isinstance(node, ast.Name) and node.id in self.configured:
            configured = self.configured[node.id]
        elif isinstance(node, ast.Name):
            configured = self._imported_configured(node.id)
        if isinstance(configured, str):
            self.consumed.add(id(node))
            name = node.id if isinstance(node, ast.Name) else ast.unparse(node)
            self.lookup_gaps.append(f"{self.where}:{node.lineno}: `{name}` {configured}")
            return None
        if configured is not None:
            if configured[0] not in kinds:
                return None  # left unconsumed, so the mismatched use is a gap
            self.consumed.add(id(node))
            return configured
        if not isinstance(node, ast.Call):
            return None
        kind = self.aliases.kind(node.func)
        if kind not in kinds:
            return None
        self.consumed.add(id(node.func))
        return kind, self._read_settings(self._bind(node, kind), node, shadow)

    def _imported_configured(self, name: str) -> _Lookup:
        """Return the configured factory a name imported from an in-tree module holds, or why it was not followed."""
        origin = self._import_origin(name)
        return self.imported_configured(*origin) if origin is not None and self.in_tree(origin[0]) else None

    def _import_origin(self, name: str) -> Optional[tuple[str, str]]:
        """Return ``(module, name)`` a module-level name is imported from, when one ``from ...

        import`` binds it.

        """
        event = _single(self.scope, name)
        if event is None or not isinstance(event.value, ast.ImportFrom):
            return None
        origin = _absolute_import(self.parsed.name, self.parsed.is_package, event.value)
        source = next((alias.name for alias in event.value.names if (alias.asname or alias.name) == name), name)
        return origin, source

    def _import_from(self, stmt: ast.ImportFrom) -> None:
        """Bind names imported from another module of the scanned tree; any other import binds opaque values."""
        origin = _absolute_import(self.parsed.name, self.parsed.is_package, stmt)
        in_tree = self.in_tree(origin)
        for alias in stmt.names:
            if alias.name == "*":
                if in_tree:
                    self.star_sources.append(origin)
                continue
            local = alias.asname or alias.name
            self._set(local, _ImportRef(origin, alias.name) if in_tree else None)

    def _module_call(self, call: ast.Call) -> None:
        """Recognize ``deprecated_module(...)`` deprecating this module; the first call wins, as at runtime."""
        if self.aliases.kind(call.func) is not _Factory.MODULE:
            return
        args = self._bind(call, _Factory.MODULE)
        name = args.given.get("name")
        own = (
            name is None
            or (isinstance(name, ast.Name) and name.id == "__name__")
            or (isinstance(name, ast.Constant) and name.value == self.parsed.name)
        )
        if not own or args.splat is not None and "name" not in args.given:
            return
        self.consumed.add(id(call.func))
        if self.module_wrapper is None:
            settings = self._read_settings(args, call, {})
            self.module_wrapper = self._finish(settings, _Route.MODULE, lambda _: "module", proxy=False)

    def _decorated_def(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef], *, attr: Optional[str], shadow: Mapping[str, list]
    ) -> Optional[_Wrapper]:
        """Return the wrapper the outermost ``@deprecated``/``@deprecated_callable`` of a ``def`` builds, if any."""
        found: Optional[_Wrapper] = None
        descriptor = next(
            (name for d in node.decorator_list if (name := _dotted(d)) in ("classmethod", "staticmethod")), None
        )
        for decorator in node.decorator_list:  # outermost first; its configuration is the one the scan reads
            factory = self._factory_call(decorator, (_Factory.DEPRECATED, _Factory.CALLABLE), shadow)
            if factory is None or found is not None:
                continue
            kind, settings = factory
            route = _Route.CALLABLE if kind is _Factory.DEPRECATED else _Route.CALLABLE_STRICT
            api_type = _member_api_type(attr, descriptor) if attr is not None else _callable_api_type
            found = self._finish(settings, route, api_type, proxy=False)
        return found

    def _class(
        self, node: ast.ClassDef, *, attr: Optional[str], shadow: Mapping[str, list]
    ) -> Union[_Wrapper, _ClassInfo]:
        """Return the proxy a class decorator builds, else the class with its own member bindings."""
        for decorator in node.decorator_list:
            factory = self._factory_call(decorator, (_Factory.DEPRECATED, _Factory.CLASS), shadow)
            if factory is not None:
                kind, settings = factory
                route = _Route.CLASS_FRONT if kind is _Factory.DEPRECATED else _Route.CLASS
                api_type = _member_api_type(attr, None) if attr is not None else _class_api_type(_is_dataclass(node))
                return self._finish(settings, route, api_type, proxy=True)
        info = _ClassInfo(self.parsed.name, {}, list(node.bases))
        body_scope = _scope_events(node.body)
        for stmt in node.body:
            self._class_statement(info, stmt, body_scope)
        return info

    def _class_statement(self, info: _ClassInfo, stmt: ast.stmt, body_scope: Mapping[str, list]) -> None:
        """Record what one class-body statement binds as a member."""
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            wrapper = self._decorated_def(stmt, attr=stmt.name, shadow=body_scope)
            accessor = any(
                isinstance(d, ast.Attribute) and _dotted(d) == f"{stmt.name}.{d.attr}" for d in stmt.decorator_list
            )
            if wrapper is None and accessor and info.members.get(stmt.name) is not None:
                return  # ``@old.setter`` rebuilds a property from a deprecated getter; the getter still warns
            info.members[stmt.name] = wrapper
        elif isinstance(stmt, ast.ClassDef):
            nested = self._class(stmt, attr=stmt.name, shadow=body_scope)
            info.members[stmt.name] = nested if isinstance(nested, _Wrapper) else None
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None:
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    info.members[target.id] = self._wrapper_expr(stmt.value, attr=target.id, shadow=body_scope)
        else:
            for name in _scope_events([stmt]):
                info.members[name] = None

    def _wrapper_expr(self, value: ast.expr, *, attr: Optional[str], shadow: Mapping[str, list]) -> Optional[_Wrapper]:
        """Recognize ``factory(...)(obj)`` and ``deprecated_instance(obj, ...)``; any other value is no wrapper."""
        if not isinstance(value, ast.Call):
            return None
        if self.aliases.kind(value.func) is _Factory.INSTANCE:
            self.consumed.add(id(value.func))
            args = self._bind(value, _Factory.INSTANCE)
            api_type = _member_api_type(attr, None) if attr is not None else self._instance_api_type(args)
            return self._finish(self._read_settings(args, value, shadow), _Route.INSTANCE, api_type, proxy=True)
        if len(value.args) != 1 or value.keywords:
            return None
        factory = self._factory_call(value.func, (_Factory.DEPRECATED, _Factory.CALLABLE, _Factory.CLASS), shadow)
        if factory is None:
            return None
        kind, settings = factory
        route, api_type, proxy, module = self._wrapped_route(kind, value.args[0], shadow)
        if attr is not None:
            api_type = _member_api_type(attr, None)
        return replace(self._finish(settings, route, api_type, proxy=proxy), module=module)

    def _wrapped_route(
        self, kind: _Factory, obj: ast.expr, shadow: Mapping[str, list]
    ) -> tuple[_Route, Callable[[bool], str], Optional[bool], str]:
        """Return the route, api_type rule, proxy flag and module of ``factory(...)(obj)`` from what *obj* names.

        A function wrapper copies its source's ``__module__`` (``functools.wraps``), so wrapping an imported function
        gives a wrapper the import scan attributes to the module the function came from.

        """
        obj_kind, obj_node, origin = self._name_kind(obj, shadow)
        is_dataclass = isinstance(obj_node, ast.ClassDef) and _is_dataclass(obj_node)
        here = self.parsed.name
        if kind is _Factory.CLASS:
            return _Route.CLASS, _class_api_type(is_dataclass), True, here
        if kind is _Factory.CALLABLE:
            return _Route.CALLABLE_STRICT, _callable_api_type, False, origin
        if obj_kind == "class":
            return _Route.CLASS_FRONT, _class_api_type(is_dataclass), True, here
        if obj_kind == "def":
            return _Route.CALLABLE, _callable_api_type, False, origin
        return _Route.UNKNOWN_FRONT, _callable_api_type, None, origin

    def _name_kind(self, node: ast.expr, shadow: Mapping[str, list]) -> tuple[str, Optional[ast.AST], str]:
        """Return ``def``, ``class`` or ``other`` for what *node* names, its defining node and its defining module."""
        here = self.parsed.name
        if isinstance(node, ast.Lambda):
            return "def", node, here
        if not isinstance(node, ast.Name):
            return "other", None, here
        event = _single(shadow, node.id) if node.id in shadow else _single(self.scope, node.id)
        if event is not None and event.kind in ("def", "class"):
            return event.kind, event.value, here
        if event is not None and isinstance(event.value, ast.ImportFrom):
            origin = _absolute_import(here, self.parsed.is_package, event.value)
            source = next((alias.name for alias in event.value.names if (alias.asname or alias.name) == node.id), "")
            kind, defined = self.imported_kind(origin, source) if self.in_tree(origin) else ("other", None)
            return kind, defined, origin
        return "other", None, here

    def _instance_api_type(self, args: _CallArgs) -> Callable[[bool], str]:
        """``deprecated_instance`` of a same-file class proxies the class; of anything else, a data object."""
        obj = args.given.get("obj")
        kind, node, _ = self._name_kind(obj, {}) if obj is not None else ("other", None, "")
        if kind == "class":
            return _class_api_type(isinstance(node, ast.ClassDef) and _is_dataclass(node))
        return lambda _: "data"

    # -- argument reading -----------------------------------------------------------------------------------------

    def _bind(self, call: ast.Call, factory: _Factory) -> _CallArgs:
        """Map a factory call's arguments to parameter names; a same-file literal ``**dict`` splat is expanded."""
        given: dict[str, ast.expr] = {}
        splat: Optional[str] = None
        names = _positional_params(factory)
        for index, arg in enumerate(call.args):
            if isinstance(arg, ast.Starred):
                splat = f"*{ast.unparse(arg.value)}"
                break
            if index < len(names):
                given[names[index]] = arg
        for keyword in call.keywords:
            if keyword.arg is not None:
                given[keyword.arg] = keyword.value
                continue
            entries = self._literal_dict(keyword.value)
            if entries is None:
                splat = f"**{ast.unparse(keyword.value)}"
            else:
                for key, value in entries.items():
                    given.setdefault(key, value)
        return _CallArgs(given, splat)

    def _literal_dict(self, node: ast.expr) -> Optional[dict[str, ast.expr]]:
        """Return the entries of a dict literal, inline or a same-file name bound once to one, with string keys."""
        if isinstance(node, ast.Name):
            event = _single(self.scope, node.id) if node.id not in self.mutated else None
            node = event.value if event is not None and event.kind == "assign" else node
        if not isinstance(node, ast.Dict):
            return None
        entries: dict[str, ast.expr] = {}
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                return None
            entries[key.value] = value
        return entries

    def _setting(self, args: _CallArgs, name: str) -> Optional[ast.expr]:
        """Return the expression given for *name*, raising :class:`LookupError` when only a splat could carry it."""
        if name in args.given:
            return args.given[name]
        if args.splat is not None:
            raise LookupError(f"arguments come from `{args.splat}`")
        return None

    def _string(self, args: _CallArgs, name: str, shadow: Mapping[str, list], default: Optional[str]) -> _Read:
        """Read a string setting: a literal, or a same-file module-level name bound once to one."""
        try:
            node = self._setting(args, name)
        except LookupError as err:
            return _Read(default, str(err))
        if node is None or (isinstance(node, ast.Constant) and node.value is None and default is None):
            return _Read(default)
        literal = node
        if isinstance(node, ast.Name) and node.id not in shadow:
            event = _single(self.scope, node.id)
            literal = event.value if event is not None and event.kind == "assign" else node
        if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
            return _Read(literal.value)
        return _Read(default, f"`{name}` is `{ast.unparse(node)}`, not a literal")

    def _mapping(self, args: _CallArgs, name: str) -> _Read:
        """Read a mapping setting: ``None`` or a dict literal (inline or a same-file name bound once to one)."""
        try:
            node = self._setting(args, name)
        except LookupError as err:
            return _Read(None, str(err))
        if node is None or (isinstance(node, ast.Constant) and node.value is None):
            return _Read(None)
        entries = self._literal_dict(node)
        if entries is None:
            return _Read(None, f"`{name}` is `{ast.unparse(node)}`, not a literal")
        return _Read(dict.fromkeys(entries) or None)

    def _target(self, args: _CallArgs, shadow: Mapping[str, list]) -> _Read:
        """Read and classify the ``target`` setting; :func:`_store_target` stores it per factory route."""
        try:
            node = self._setting(args, "target")
        except LookupError as err:
            return _Read(None, str(err))
        return _Read(("default",)) if node is None else self._classify_target(node, shadow, depth=0)

    def _classify_target(self, node: ast.expr, shadow: Mapping[str, list], depth: int) -> _Read:
        """Classify a ``target`` expression as ``("const", value)`` or ``("replacement", text, kind)``."""
        if isinstance(node, ast.Constant) and (node.value is None or isinstance(node.value, bool)):
            return _Read(("const", node.value))
        member = self.aliases.target_mode(node)
        if member is not None:
            return _Read(("const", member))
        if isinstance(node, (ast.Lambda, ast.Attribute)):
            return _Read(("replacement", ast.unparse(node), "def" if isinstance(node, ast.Lambda) else "other"))
        if isinstance(node, ast.Name):
            return self._classify_target_name(node, shadow, depth)
        return _Read(("expr", ast.unparse(node)), f"`target` is `{ast.unparse(node)}`, not a literal or a name")

    def _classify_target_name(self, node: ast.Name, shadow: Mapping[str, list], depth: int) -> _Read:
        """Classify a ``target`` given by name, from how the class body or the module binds it."""
        events = shadow if node.id in shadow else self.scope
        bindings = events.get(node.id, [])
        if not bindings and self.foreign_star and events is self.scope:
            return _Read(("expr", node.id), f"`target` is `{node.id}`, which a star import may bind")
        if not bindings:  # a builtin, or a name the module never binds: a replacement callable at runtime
            return _Read(("replacement", node.id, "other"))
        event = _single(events, node.id)
        if event is None:
            return _Read(("expr", node.id), f"`target` is `{node.id}`, which is bound more than once")
        if event.kind in ("def", "class"):
            return _Read(("replacement", node.id, event.kind))
        if event.kind == "import":
            return _Read(("replacement", node.id, "other"))
        if event.kind == "assign" and depth == 0 and isinstance(event.value, ast.expr):
            read = self._classify_target(event.value, {}, depth + 1)
            if read.reason is None:
                return read if read.value[0] == "const" else _Read(("replacement", node.id, read.value[2]))
        return _Read(("expr", node.id), f"`target` is `{node.id}`, which is not bound to a literal or a definition")

    def _read_settings(self, args: _CallArgs, call: ast.Call, shadow: Mapping[str, list]) -> _Settings:
        """Read every setting the checks use from one factory call, in the scope it is spelled in."""
        template = self._string(args, "message_template", shadow, None)
        if template.reason is None and template.value is None:
            template = self._string(args, "template_mgs", shadow, None)
        return _Settings(
            where=f"{self.where}:{call.lineno}",
            deprecated_in=self._string(args, "deprecated_in", shadow, ""),
            remove_in=self._string(args, "remove_in", shadow, ""),
            template=template,
            args_mapping=self._mapping(args, "args_mapping"),
            attrs_mapping=self._mapping(args, "attrs_mapping"),
            target=self._target(args, shadow),
        )

    def _finish(
        self, settings: _Settings, route: _Route, api_type: Callable[[bool], str], *, proxy: Optional[bool]
    ) -> _Wrapper:
        """Turn read settings into the configuration the factory of *route* stores, recording what stays unsettled."""
        mappings = (settings.args_mapping.value, settings.attrs_mapping.value)
        target = _store_target(settings.target, route, mappings)
        reads = {
            "deprecated_in": settings.deprecated_in,
            "remove_in": settings.remove_in,
            "message_template": settings.template,
            "args_mapping": settings.args_mapping,
            "attrs_mapping": settings.attrs_mapping,
            "target": target,
        }
        config = DeprecationConfig(
            deprecated_in=settings.deprecated_in.value,
            remove_in=settings.remove_in.value,
            target=target.value,
            args_mapping=settings.args_mapping.value,
            attrs_mapping=settings.attrs_mapping.value,
            message_template=settings.template.value,
        )
        unresolved = {name: f"{settings.where}: {read.reason}" for name, read in reads.items() if read.reason}
        return _Wrapper(self.parsed.name, proxy, api_type(bool(settings.args_mapping.value)), config, unresolved)

    def _unrecognized(self) -> list[str]:
        """Return one gap detail per factory reference no recognized shape consumed.

        A reference is a pyDeprecate factory name, a name bound to a configured factory here or in an in-tree module, or
        a pyDeprecate name the source does not settle (imported inside a function, or rebound).

        """
        gaps = list(self.lookup_gaps)
        for node in ast.walk(self.parsed.tree):
            if not isinstance(node, (ast.Name, ast.Attribute)) or id(node) in self.consumed:
                continue
            if isinstance(node.ctx, ast.Load) and self._is_factory_reference(node):
                gaps.append(f"{self.where}:{node.lineno}: unrecognized use of `{ast.unparse(node)}`")
        return gaps

    def _is_factory_reference(self, node: Union[ast.Name, ast.Attribute]) -> bool:
        """Tell whether *node* refers to a factory, a configured factory, or a pyDeprecate name bound ambiguously."""
        if self.aliases.kind(node) not in (None, _Factory.TARGET_MODE):
            return True
        if not isinstance(node, ast.Name):
            return False
        if node.id in self.aliases.ambiguous:
            return self.aliases.ambiguous[node.id] is not _Factory.TARGET_MODE
        return node.id in self.ever_configured or self._imported_configured(node.id) is not None


def _callable_api_type(has_mapping: bool) -> str:
    """The ``api_type`` of a module-level ``@deprecated`` function."""
    return "args" if has_mapping else "callable"


def _class_api_type(is_dataclass: bool) -> Callable[[bool], str]:
    """The ``api_type`` rule of a proxied class: ``class``, ``dataclass`` or ``dataclass attributes``."""
    if not is_dataclass:
        return lambda _: "class"
    return lambda has_mapping: "dataclass attributes" if has_mapping else "dataclass"


def _member_api_type(attr: str, descriptor: Optional[str]) -> Callable[[bool], str]:
    """The ``api_type`` rule of a class member, shared with the import scan."""
    return lambda has_mapping: _classify_member_api_type(attr, descriptor, has_mapping)


def _store_target(read: _Read, route: _Route, mappings: tuple[Any, Any]) -> _Read:
    """Return the ``target`` the factory of *route* stores for a classified read, or why the source does not say."""
    if read.reason is not None:
        expr = read.value[1] if isinstance(read.value, tuple) and read.value[0] == "expr" else None
        # any expression given to ``deprecated_module(target=...)`` other than ``None`` names a replacement module
        return (
            _Read(types.ModuleType(expr)) if route is _Route.MODULE and expr is not None else _Read(None, read.reason)
        )
    classified = read.value
    if classified[0] == "default":
        default = {_Route.CALLABLE: TargetMode.AUTO, _Route.UNKNOWN_FRONT: TargetMode.AUTO}.get(route)
        classified = ("const", TargetMode.NOTIFY if route is _Route.CALLABLE_STRICT else default)
    if route is _Route.UNKNOWN_FRONT:
        return _unknown_front(classified, mappings)
    return _normalise_target(classified, route, mappings)


def _normalise_target(read: tuple[Any, ...], route: _Route, mappings: tuple[Any, Any]) -> _Read:
    """Turn a classified ``target`` into the value the factory of *route* stores, as the runtime normalises it."""
    args_mapping, attrs_mapping = mappings
    kind, value = read[0], read[1]
    if route is _Route.MODULE:
        return _Read(None if kind == "const" and value is None else types.ModuleType(str(value)))
    if kind == "replacement":
        if route is _Route.CLASS_FRONT and read[2] != "class":
            if read[2] == "def":
                return _Read(_auto_resolve(None, mappings))  # a non-class target is ignored on the class route
            return _Read(None, f"`@deprecated` on a class ignores a non-class `target`; `{value}` may be either")
        return _Read(_StaticTarget(value))
    if route in (_Route.CALLABLE, _Route.CALLABLE_STRICT):
        if value is TargetMode.AUTO:
            return _Read(TargetMode.ARGS_REMAP if args_mapping else TargetMode.NOTIFY)
        return _Read(_resolve_legacy_target(value, emit=False) if not isinstance(value, TargetMode) else value)
    if route is _Route.CLASS_FRONT:
        target = TargetMode.ARGS_REMAP if value is True else None if value in (None, False, TargetMode.AUTO) else value
        return _Read(_auto_resolve(target, mappings))
    if route is _Route.CLASS:
        if isinstance(value, bool) or value is None:
            value = _resolve_legacy_proxy_target(value, args_mapping=args_mapping, emit=False)
        return _Read(_auto_resolve(value, mappings))
    return _Read(value)  # deprecated_instance stores its target as given


def _auto_resolve(target: Any, mappings: tuple[Any, Any]) -> Any:  # noqa: ANN401
    """Resolve a proxy's unset target from its mappings, as ``_resolve_proxy_target`` does."""
    args_mapping, attrs_mapping = mappings
    if target is None and args_mapping:
        return TargetMode.ARGS_REMAP
    if target is None and attrs_mapping:
        return TargetMode.ATTRS_REMAP
    return target


def _unknown_front(read: tuple[Any, ...], mappings: tuple[Any, Any]) -> _Read:
    """Normalise ``deprecated(...)(obj)`` when *obj* may be a class: settled only when both routes agree on guidance."""
    as_callable = _normalise_target(read, _Route.CALLABLE, mappings)
    as_class = _normalise_target(read, _Route.CLASS_FRONT, mappings)
    if as_callable.reason is None and as_class.reason is None:
        guidance = {
            _has_migration_guidance(
                DeprecationWrapperInfo(deprecated_info=DeprecationConfig(target=target, args_mapping=mappings[0]))
            )
            for target in (as_callable.value, as_class.value)
        }
        if len(guidance) == 1:
            return as_callable
    return _Read(None, "`@deprecated` routes on whether the wrapped object is a class, which the source does not show")


# -- discovery ----------------------------------------------------------------------------------------------------


class _StaticSource(NamedTuple):
    """What a ``--static`` path names: a package, a plain directory or a single module file."""

    kind: str  # "package", "plain" or "module"
    name: str
    location: Path
    import_root: Optional[Path]


def _locate_static(path: str, package_dir: Optional[Path]) -> Union[_StaticSource, str]:
    """Return what *path* names for a ``--static`` read, or the usage error to print (the CLI exits 2).

    A module name is located with :func:`importlib.util.find_spec`, which runs the import system's finders but never the
    package itself; a dotted name is refused, since locating it would import its parent packages.

    """
    if package_dir is not None:
        return _StaticSource("package", package_dir.name, package_dir, package_dir.parent)
    pth = Path(path)
    if pth.is_dir():
        return _StaticSource("plain", path, pth.resolve(), None)
    if "." in path:
        return (
            f"`--static` takes a package directory, a plain directory or a top-level module name; locating {path!r}"
            " would import its parent packages. Pass its directory instead."
        )
    try:
        spec = importlib.util.find_spec(path)
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        return f"No such path or importable module: {path!r}"
    if spec.submodule_search_locations:
        location = Path(next(iter(spec.submodule_search_locations)))
        return _StaticSource("package", path, location, location.parent)
    if spec.origin and spec.origin.endswith(".py"):
        return _StaticSource("module", path, Path(spec.origin), Path(spec.origin).parent)
    return f"{path!r} has no Python source for `--static` to read."


def _package_entries(pkg_dir: Path) -> Iterator[tuple[str, bool]]:
    """Yield ``(name, is_package)`` for the submodules of a package directory, as :func:`pkgutil.iter_modules` does."""
    try:
        filenames = sorted(entry.name for entry in os.scandir(pkg_dir))
    except OSError:
        return
    yielded: set[str] = set()
    for filename in filenames:
        name = inspect.getmodulename(filename)
        if name == "__init__" or name in yielded:
            continue
        is_package = False
        if not name and (pkg_dir / filename).is_dir() and "." not in filename:
            try:
                is_package = any(inspect.getmodulename(sub) == "__init__" for sub in os.listdir(pkg_dir / filename))
            except OSError:
                is_package = False
            if not is_package:
                continue
            name = filename
        if name and "." not in name:
            yielded.add(name)
            yield name, is_package


def _walk_package(
    pkg_dir: Path, name: str, *, recursive: bool, exclude: Sequence[str], top: bool = True
) -> Iterator[tuple[str, Optional[Path]]]:
    """Yield ``(module name, source file or None)`` for a package and, when *recursive*, its submodules depth-first."""
    if top:
        yield name, pkg_dir / "__init__.py" if (pkg_dir / "__init__.py").is_file() else None
    if not recursive:
        return
    for sub, is_package in _package_entries(pkg_dir):
        modname = f"{name}.{sub}"
        if _is_excluded(modname, exclude):
            continue
        if is_package:
            init = pkg_dir / sub / "__init__.py"
            yield modname, init if init.is_file() else None
            yield from _walk_package(pkg_dir / sub, modname, recursive=True, exclude=exclude, top=False)
        else:
            source = pkg_dir / f"{sub}.py"
            yield modname, source if source.is_file() else None


def _plain_modules(directory: Path, exclude: Sequence[str]) -> Iterator[tuple[str, Optional[Path]]]:
    """Yield the top-level ``*.py`` modules of a plain directory, as the import scan of one does."""
    for entry in sorted(directory.iterdir(), key=lambda ent: ent.name):
        is_module = entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("__")
        if is_module and not _is_excluded(entry.stem, exclude):
            yield entry.stem, entry
    if any(not fpy.name.startswith("__") for fpy in directory.rglob("*.py") if fpy.parent != directory):
        _print_gap(
            _GapKind.NESTED_FILES,
            "Skipping nested Python files in plain directory scan. Use an importable package layout with '__init__.py'"
            " files, or scan an importable module/package path instead.",
        )


# -- reading a tree -------------------------------------------------------------------------------------------------


class _Reader:
    """Parse and analyze the modules of one scanned tree on demand, following in-tree imports between them.

    Two analyses run per module. The *table* is read with no cross-module lookups, so it settles what a module's names
    hold on their own (its configured factories and its re-exports) and can be asked for while another module is being
    analyzed, import cycles included. The *facts* are the full analysis, which asks other modules' tables.

    """

    def __init__(self, source: _StaticSource) -> None:
        self.source = source
        self.root_package = source.name if source.kind == "package" else None
        self._parsed: dict[str, Optional[_Parsed]] = {}
        #: Why a module's source could not be read or analyzed, printed once as a ``module not audited`` gap.
        self._errors: dict[str, str] = {}
        self._reported: set[str] = set()
        self._facts: dict[str, Optional[_ModuleFacts]] = {}
        self._tables: dict[str, Optional[_ModuleFacts]] = {}
        self._aliases: dict[str, _Aliases] = {}
        self._in_progress: set[str] = set()

    def in_tree(self, module: str) -> bool:
        """Whether *module* belongs to the scanned package, whose source the reader may follow imports into."""
        root = self.root_package
        return root is not None and (module == root or module.startswith(f"{root}."))

    def parse(self, module: str, path: Optional[Path] = None, *, report: bool = False) -> Optional[_Parsed]:
        """Parse *module* once; with *report*, a file that cannot be read is a ``module not audited`` gap."""
        if module not in self._parsed:
            roots = [self.source.import_root] if self.source.import_root is not None else []
            path = path if path is not None else _module_source_file(module, roots) if self.in_tree(module) else None
            if path is None:
                return None  # nothing to cache: a later call may name the file
            parsed: Optional[_Parsed] = None
            try:
                parsed = _Parsed(module, path, ast.parse(path.read_bytes(), filename=str(path)))
            except (OSError, SyntaxError, ValueError, RecursionError, MemoryError) as err:
                self._errors[module] = f"could not read its source: {err!r}"
            self._parsed[module] = parsed
        if report:
            self._report(module)
        return self._parsed[module]

    def _report(self, module: str) -> None:
        """Print the ``module not audited`` gap of a module that could not be read or analyzed, once."""
        if module in self._errors and module not in self._reported:
            self._reported.add(module)
            _print_gap(_GapKind.MODULE_NOT_AUDITED, f"{module}: {self._errors[module]}")

    def aliases(self, parsed: _Parsed) -> _Aliases:
        """Return the pyDeprecate names *parsed* binds, following in-tree modules that re-export them."""
        if parsed.name in self._aliases:
            return self._aliases[parsed.name]
        self._aliases[parsed.name] = _Aliases()  # an import cycle back here re-exports nothing
        aliases = _direct_aliases(parsed.tree)
        top = _module_level_nodes(parsed.tree)
        scope = _scope_events(parsed.tree.body)
        for node in ast.walk(parsed.tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            origin = _absolute_import(parsed.name, parsed.is_package, node)
            hop = self.parse(origin) if self.in_tree(origin) and origin != parsed.name else None
            if hop is None:
                continue
            exported = self.aliases(hop)
            exports = _literal_all(hop.tree)
            for alias in node.names:
                bound = _Aliases()
                if alias.name == "*":
                    public = (lambda name: name in exports) if isinstance(exports, set) else _is_public
                    bound.names = {name: kind for name, kind in exported.names.items() if public(name)}
                    unsettled = {name: kind for name, kind in exported.ambiguous.items() if public(name)}
                elif alias.name in exported.names:
                    bound.names = {alias.asname or alias.name: exported.names[alias.name]}
                    unsettled = {}
                else:
                    kind = exported.ambiguous.get(alias.name)
                    unsettled = {alias.asname or alias.name: kind} if alias.name in exported.ambiguous else {}
                aliases.merge(bound, settled=id(node) in top, scope=scope, star=alias.name == "*")
                aliases.ambiguous.update(unsettled)
        aliases.settle()
        self._aliases[parsed.name] = aliases
        return aliases

    def _analyze(self, parsed: _Parsed, *, isolated: bool) -> Optional[_ModuleFacts]:
        """Run the analyzer on *parsed*; isolated, it asks no other module, for the table."""
        name_kind = (lambda _module, _name: ("other", None)) if isolated else self.name_kind
        configured = (lambda _module, _name: None) if isolated else self.configured
        try:
            return _ModuleAnalyzer(parsed, self.aliases(parsed), self.in_tree, name_kind, configured).run()
        except (RecursionError, MemoryError) as err:
            self._errors.setdefault(parsed.name, f"its source is nested too deeply to analyze: {err!r}")
            return None

    def table(self, module: str) -> Optional[_ModuleFacts]:
        """Return *module*'s isolated analysis: what its names hold without following any other module."""
        if module not in self._tables:
            parsed = self.parse(module)
            self._tables[module] = self._analyze(parsed, isolated=True) if parsed is not None else None
        return self._tables[module]

    def facts(self, module: str, path: Optional[Path] = None, *, report: bool = False) -> Optional[_ModuleFacts]:
        """Analyze *module* once, or return ``None`` when its source cannot be read or analyzed."""
        parsed = self.parse(module, path)
        if module not in self._facts:
            self._in_progress.add(module)
            facts = self._analyze(parsed, isolated=False) if parsed is not None else None
            if facts is not None:
                self._expand_stars(module, facts)
            self._in_progress.discard(module)
            self._facts[module] = facts
        if report:
            self._report(module)
        return self._facts[module]

    def _expand_stars(self, module: str, facts: _ModuleFacts) -> None:
        """Bind the names each in-tree ``from X import *`` of *module* copies, as listed by ``X.__all__``."""
        for origin in facts.star_sources:
            star = self.facts(origin) if origin not in self._in_progress else None
            parsed = self.parse(origin)
            exports = _literal_all(parsed.tree) if parsed is not None else None
            if origin in self._in_progress or isinstance(exports, str):
                reason = "is part of an import cycle" if origin in self._in_progress else exports
                facts.gaps.append(f"{module}: `from {origin} import *`: `{origin}` {reason}; its names are not audited")
                continue
            for name in star.bindings if star is not None else ():
                listed = name in exports if exports is not None else _is_public(name)
                if listed and name not in facts.bindings:
                    facts.bindings[name] = _ImportRef(origin, name)

    def configured(self, module: str, name: str, depth: int = 0) -> _Lookup:
        """Return the configured factory an in-tree module binds *name* to, following its re-exports."""
        if depth >= _IMPORT_HOPS:
            return f"is re-exported through more than {_IMPORT_HOPS} modules; not followed"
        table = self.table(module)
        if table is None:
            return None
        if name in table.configured:
            return table.configured[name]
        value = table.bindings.get(name)
        if isinstance(value, _ImportRef):
            return self.configured(value.module, value.name, depth + 1)
        for origin in table.star_sources if name not in table.bindings else ():
            parsed = self.parse(origin)
            exports = _literal_all(parsed.tree) if parsed is not None else None
            if exports is None and _is_public(name) or isinstance(exports, set) and name in exports:
                found = self.configured(origin, name, depth + 1)
                if found is not None:
                    return found
        return None

    def name_kind(self, module: str, name: str, depth: int = 0) -> tuple[str, Optional[ast.AST]]:
        """Return ``def``, ``class`` or ``other`` for what *module* binds *name* to, following in-tree imports."""
        parsed = self.parse(module) if depth < _IMPORT_HOPS else None
        event = _single(_scope_events(parsed.tree.body), name) if parsed is not None else None
        if event is None:
            return "other", None
        if event.kind in ("def", "class"):
            return event.kind, event.value
        if parsed is not None and isinstance(event.value, ast.ImportFrom):
            origin = _absolute_import(module, parsed.is_package, event.value)
            source = next((alias.name for alias in event.value.names if (alias.asname or alias.name) == name), "")
            if self.in_tree(origin):
                return self.name_kind(origin, source, depth + 1)
        return "other", None

    def resolve(self, value: _Value) -> tuple[_Value, bool]:
        """Follow in-tree import references to the wrapper or class they name; also tell whether the hops ran out."""
        for _ in range(_IMPORT_HOPS):
            if not isinstance(value, _ImportRef):
                return value, False
            facts = self.facts(value.module)
            value = facts.bindings.get(value.name) if facts is not None else None
        return (None, True) if isinstance(value, _ImportRef) else (value, False)

    def class_members(self, info: _ClassInfo, unaudited: list[str]) -> dict[str, _Wrapper]:
        """Return a class's deprecated members, those inherited from bases in the scanned tree included.

        Each member resolves through the method resolution order, so a plain member of an earlier base hides a
        deprecated one of a later base, as at runtime. A base the source does not show to be a class of the tree is
        added to *unaudited* unless it comes from the standard library, which holds no deprecated member.

        """
        merged: dict[str, Optional[_Wrapper]] = {}
        for cls in self._mro(info, unaudited, depth=0):
            for attr, wrapper in cls.members.items():
                merged.setdefault(attr, wrapper)
        return {attr: wrapper for attr, wrapper in merged.items() if wrapper is not None}

    def _mro(self, info: _ClassInfo, unaudited: list[str], depth: int) -> list[_ClassInfo]:
        """Return the C3 linearization of *info* over the bases the reader resolves, noting the others."""
        bases: list[_ClassInfo] = []
        for base in info.bases:
            resolved = self._base(info.module, base)
            if isinstance(resolved, _ClassInfo) and depth < _BASE_DEPTH:
                bases.append(resolved)
            elif isinstance(resolved, _ClassInfo):
                unaudited.append(f"`{ast.unparse(base)}` (base classes nest more than {_BASE_DEPTH} levels)")
            elif resolved is not None:
                unaudited.append(f"`{ast.unparse(base)}` ({resolved})")
        return _c3_merge(info, [self._mro(base, unaudited, depth + 1) for base in bases] + [bases])

    def _base(self, module: str, base: ast.expr) -> Union[_ClassInfo, str, None]:
        """Return the in-tree class a base expression names, ``None`` for a memberless one, else why it is unread."""
        while isinstance(base, ast.Subscript):  # ``Base[T]`` subclasses ``Base``
            base = base.value
        root = base
        while isinstance(root, ast.Attribute):
            root = root.value
        facts = self.facts(module)
        parsed = self.parse(module)
        if not isinstance(root, ast.Name) or facts is None or parsed is None:
            return "not a name the source settles"
        if isinstance(base, ast.Name) and base.id in facts.bindings:
            resolved, _ = self.resolve(facts.bindings[base.id])
            if isinstance(resolved, _ClassInfo):
                return resolved
        event = _single(_scope_events(parsed.tree.body), root.id)
        if event is None:
            unbound = root.id not in _scope_events(parsed.tree.body)
            return None if unbound and hasattr(builtins, root.id) else "not a class the source defines"
        if event.kind != "import":
            return "not a class the scanned source defines"
        origin = (
            _absolute_import(module, parsed.is_package, event.value)
            if isinstance(event.value, ast.ImportFrom)
            else root.id
        )
        if self.in_tree(origin):
            return "not a class the scanned source defines"
        if _is_memberless_module(origin):
            return None
        return f"imported from `{origin}`, whose source is not read"


def _is_public(name: str) -> bool:
    """Whether a star import without ``__all__`` copies *name*."""
    return not name.startswith("_")


def _c3_merge(head: _ClassInfo, sequences: list[list[_ClassInfo]]) -> list[_ClassInfo]:
    """Merge base linearizations as Python's C3 does; an inconsistent order falls back to depth-first."""
    result = [head]
    pending = [list(seq) for seq in sequences if seq]
    while pending:
        candidate = next(
            (seq[0] for seq in pending if not any(seq[0] in other[1:] for other in pending)),
            None,
        )
        if candidate is None:  # Python refuses such a class; keep every base once rather than drop members
            result.extend(cls for seq in pending for cls in seq if cls not in result)
            break
        result.append(candidate)
        pending = [seq[1:] if seq[0] is candidate else seq for seq in pending]
        pending = [seq for seq in pending if seq]
    return result


def _row(module: str, function: str, wrapper: _Wrapper) -> _StaticWrapperInfo:
    """Build the synthetic wrapper info the checks read, named as the import scan names it."""
    config = replace(wrapper.config, name=function.rsplit(".", 1)[-1] if function else module)
    return _StaticWrapperInfo(
        module=module,
        function=function,
        deprecated_info=config,
        api_type=wrapper.api_type,
        unresolved=dict(wrapper.unresolved),
    )


def _module_rows(
    reader: _Reader, module: str, facts: _ModuleFacts, *, include_members: bool, attribute: bool, seen: set[int]
) -> list[DeprecationWrapperInfo]:
    """Return one module's rows in import-scan order: the module itself, then its public bindings by name."""
    rows: list[DeprecationWrapperInfo] = []
    if facts.module_wrapper is not None:
        rows.append(_row(module, "", facts.module_wrapper))
    for name in sorted(facts.bindings):
        if name.startswith("_"):
            continue
        value, exhausted = reader.resolve(facts.bindings[name])
        if exhausted:
            _print_gap(
                _GapKind.STATIC_UNRESOLVED,
                f"{module}.{name}: re-exported through more than {_IMPORT_HOPS} modules; not followed",
            )
        elif isinstance(value, _Wrapper):
            rows.extend(_wrapper_rows(module, name, value, attribute=attribute, seen=seen))
        elif isinstance(value, _ClassInfo) and include_members and value.module == module:
            unaudited: list[str] = []
            members = reader.class_members(value, unaudited)
            for base in dict.fromkeys(unaudited):
                _print_gap(
                    _GapKind.STATIC_UNRESOLVED, f"{module}.{name}: members inherited from {base} are not audited"
                )
            rows.extend(_row(module, f"{name}.{attr}", members[attr]) for attr in sorted(members))
    return rows


def _wrapper_rows(
    module: str, name: str, value: _Wrapper, *, attribute: bool, seen: set[int]
) -> list[DeprecationWrapperInfo]:
    """Return the row of one public binding of a wrapper, or none when another binding or module owns it."""
    foreign = value.module != module and value.module.split(".")[0] == module.split(".")[0]
    if attribute and foreign and value.proxy is None and id(value) not in seen:
        seen.add(id(value))
        _print_gap(
            _GapKind.STATIC_UNRESOLVED,
            f"{module}.{name}: wraps an object imported from `{value.module}` that the source does not show"
            " to be a class or a function, so its row cannot be attributed",
        )
        return []
    if (attribute and value.proxy is False and foreign) or id(value) in seen:
        return []
    seen.add(id(value))
    return [_row(module, name, value)]


def _read_static(
    source: _StaticSource, *, recursive: bool, include_members: bool, exclude: Sequence[str]
) -> list[DeprecationWrapperInfo]:
    """Read every wrapper of a located source, printing a gap for everything the source alone does not settle."""
    reader = _Reader(source)
    if source.kind == "package":
        modules = list(_walk_package(source.location, source.name, recursive=recursive, exclude=exclude))
    elif source.kind == "plain":
        modules = list(_plain_modules(source.location, exclude))
    else:
        modules = [(source.name, source.location)]
    # the import scan turns re-export attribution off only for a package scanned without its submodules
    attribute = not (source.kind == "package" and not recursive)
    rows: list[DeprecationWrapperInfo] = []
    seen: set[int] = set()
    for module, path in modules:
        if path is None:
            _print_gap(_GapKind.MODULE_NOT_AUDITED, f"{module}: no Python source to read")
            continue
        facts = reader.facts(module, path, report=True)
        if facts is None:
            continue
        for detail in facts.gaps:
            _print_gap(_GapKind.STATIC_UNRESOLVED, detail)
        if source.kind == "plain":
            seen = set()  # each top-level module of a plain directory is scanned on its own
        rows.extend(
            _module_rows(reader, module, facts, include_members=include_members, attribute=attribute, seen=seen)
        )
    return rows


# -- the checks' view ------------------------------------------------------------------------------------------------


def _unresolved(info: DeprecationWrapperInfo) -> Mapping[str, str]:
    """Return the settings of *info* the source did not settle (none for an imported wrapper)."""
    return info.unresolved if isinstance(info, _StaticWrapperInfo) else {}


def _report_unresolved(wrappers: Sequence[DeprecationWrapperInfo], keywords: Sequence[str]) -> None:
    """Print one ``not statically resolvable`` gap per wrapper and unsettled reason among *keywords*."""
    for info in wrappers:
        details = dict.fromkeys(_unresolved(info)[key] for key in keywords if key in _unresolved(info))
        for detail in details:
            _print_gap(_GapKind.STATIC_UNRESOLVED, f"{detail} ({_format_subject(info)} not checked)")


def _resolved(wrappers: Sequence[DeprecationWrapperInfo], keywords: Sequence[str]) -> list[DeprecationWrapperInfo]:
    """Return the wrappers whose settings among *keywords* the source settled."""
    return [info for info in wrappers if not any(key in _unresolved(info) for key in keywords)]


def _policy_keywords(spec: _PolicySpec) -> tuple[str, ...]:
    """Return the settings the enabled policy rules read."""
    return (_GRACE_KEYWORDS if spec.grace is not None else ()) + (_GUIDANCE_KEYWORDS if spec.message_required else ())


def _static_policy_findings(wrappers: list[DeprecationWrapperInfo], spec: _PolicySpec) -> list[_Finding]:
    """Run each policy rule on the wrappers whose settings for that rule resolved, in import-scan finding order.

    A version held in a variable leaves ``min-grace`` unable to judge a wrapper, but not ``message-required``, which
    still reads its target and message — so one unsettled setting never hides a violation another rule can prove.

    Raises:
        ImportError: As :func:`~deprecate.audit._policy._policy_findings` does, when ``min-grace`` runs without the
            ``packaging`` library.

    """
    findings: list[_Finding] = []
    if spec.grace is not None:
        findings += _policy_findings(_resolved(wrappers, _GRACE_KEYWORDS), replace(spec, message_required=False))
    if spec.message_required:
        findings += _policy_findings(_resolved(wrappers, _GUIDANCE_KEYWORDS), replace(spec, grace=None))
    order = {(info.module, info.function): index for index, info in enumerate(wrappers)}
    return sorted(findings, key=lambda f: (order.get((f.module, f.symbol), 0), f.rule is _Rule.MESSAGE_REQUIRED))
