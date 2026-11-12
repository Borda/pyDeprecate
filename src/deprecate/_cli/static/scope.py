"""Which names a source binds and how: factory aliases, scope events, and the ways a module rebinds a name."""

import ast
import functools
import importlib
import importlib.util
import os
import sys
import sysconfig
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Callable, NamedTuple, Optional, Union

from deprecate import TargetMode
from deprecate._cli.static.consts import (
    _DATACLASS,
    _DEPRECATE_MODULES,
    _DICT_MUTATORS,
    _MEMBERLESS_MODULES,
    _Factory,
    _module_exports,
)


def _dotted(node: ast.expr) -> Optional[str]:
    """Return ``a.b.c`` for a name or attribute chain, else ``None``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base is not None else None
    return None


@dataclass
class _PyDeprecateNames:
    """The names a module binds to recognized pyDeprecate objects, and the pyDeprecate modules it binds.

    ``ambiguous`` holds the names a pyDeprecate import binds that the source does not settle: bound inside a function,
    or bound at module level more than once. What such a name holds depends on runtime, so each use of it is a gap.

    """

    names: dict[str, _Factory] = field(default_factory=dict)
    modules: dict[str, str] = field(default_factory=dict)
    ambiguous: dict[str, Optional[_Factory]] = field(default_factory=dict)

    def merge(self, bound: "_PyDeprecateNames", *, settled: bool, scope: Mapping[str, list], star: bool) -> None:
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

    def settle(self) -> "_PyDeprecateNames":
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


def _binds_package_only(events: Sequence["_NameBinding"], name: str) -> bool:
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


def _direct_aliases(tree: ast.Module) -> _PyDeprecateNames:
    """Collect the pyDeprecate names and modules a module's own import statements bind."""
    aliases = _PyDeprecateNames()
    top = _module_level_nodes(tree)
    scope = _scope_events(tree.body)
    for node in ast.walk(tree):
        bound = _PyDeprecateNames()
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


def _bind_from_deprecate(aliases: _PyDeprecateNames, module: str, alias: ast.alias) -> None:
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


class _NameBinding(NamedTuple):
    """One binding of a name in a scope: how it is bound, and the assigned value for a plain assignment."""

    kind: str  # "assign", "def", "class", "import" or "other"
    value: Optional[ast.AST] = None


def _node_events(node: ast.AST, events: dict[str, list[_NameBinding]]) -> list[ast.AST]:
    """Record the bindings *node* itself makes in its scope and return the child nodes still to visit.

    A ``def``, ``class`` or lambda opens a scope of its own, so none of its children is visited. A plain ``name =
    value`` records its value; a star import binds names the reader cannot list here; any other store or delete (loop
    target, ``with ... as``, walrus, augmented assignment) binds opaquely.

    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        events[node.name].append(_NameBinding("class" if isinstance(node, ast.ClassDef) else "def", node))
        return []
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        rest: list[ast.AST] = [node.value] if node.value is not None else []
        for target in targets:
            if not isinstance(target, ast.Name):
                rest.append(target)
            elif node.value is not None:
                events[target.id].append(_NameBinding("assign", node.value))
        return rest
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        _import_events(node, events)
    elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        events[node.id].append(_NameBinding("other"))
    elif not isinstance(node, ast.Lambda):
        return list(ast.iter_child_nodes(node))
    return []


def _import_events(node: Union[ast.Import, ast.ImportFrom], events: dict[str, list[_NameBinding]]) -> None:
    """Record what an import binds: ``import a.b`` binds ``a``; ``from x import a as b`` binds ``b``."""
    for alias in node.names:
        if isinstance(node, ast.Import):
            events[alias.asname or alias.name.split(".")[0]].append(_NameBinding("import", node))
        elif alias.name != "*":
            events[alias.asname or alias.name].append(_NameBinding("import", node))


def _scope_events(body: Sequence[ast.stmt]) -> dict[str, list[_NameBinding]]:
    """Return every binding of every name in a module or class body, compound statements included.

    The walk keeps its own stack, so a deeply nested expression (a long generated concatenation) cannot exhaust the
    interpreter's recursion limit.

    """
    events: dict[str, list[_NameBinding]] = defaultdict(list)
    stack: list[ast.AST] = list(body)
    while stack:
        stack.extend(_node_events(stack.pop(), events))
    return dict(events)


def _escaped_names(tree: ast.Module, is_factory: Callable[[ast.expr], bool]) -> set[str]:
    r"""Return the names whose object the module hands to code that may change it, so a dict they hold is not settled.

    A dict stays as its literal spells it only while every read of its name copies it — a ``**name`` splat into a call
    or a dict display — or passes it by keyword to a pyDeprecate factory, which stores it unchanged. Any other read
    may change it: ``name[key] = ...``, ``name.update(...)``, an alias (``other = name``, then ``other[key] = ...``) or
    a helper call (``bump(name)``).

    Examples:
        >>> tree = ast.parse("KW = {}\nprint(**KW)\nCOPY = {**KW}\nM = {}\nALIAS = M")
        >>> sorted(_escaped_names(tree, lambda func: False))
        ['M', 'print']

    """
    copied: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            factory = is_factory(node.func)
            copied.update(id(keyword.value) for keyword in node.keywords if keyword.arg is None or factory)
        elif isinstance(node, ast.Dict):
            copied.update(id(value) for key, value in zip(node.keys, node.values) if key is None)
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and id(node) not in copied
    }


def _globals_rebinds(tree: ast.Module) -> Optional[set[str]]:
    r"""Return the names the module rebinds through ``globals()``, ``vars()`` or ``setattr(sys.modules[...], ...)``.

    ``None`` when such a write names no literal key (``globals().update(...)``, ``globals()[key] = ...``), so it may
    rebind any name.

    Examples:
        >>> tree = ast.parse("import sys\nglobals()['A'] = 1\nsetattr(sys.modules[__name__], 'B', 2)")
        >>> sorted(_globals_rebinds(tree))
        ['A', 'B']
        >>> _globals_rebinds(ast.parse("globals().update(a=1)")) is None
        True

    """
    found: set[str] = set()
    for node in ast.walk(tree):
        key: Optional[ast.expr] = None
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)) and _is_namespace(node.value):
            key = node.slice
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if _is_namespace(node.value):
                found.add(node.attr)  # ``sys.modules[__name__].NAME = ...``
            continue
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _DICT_MUTATORS:
            if _is_namespace(node.func.value):
                return None
            continue
        elif isinstance(node, ast.Call) and _dotted(node.func) in ("setattr", "delattr") and len(node.args) >= 2:
            if not _is_namespace(node.args[0]):
                continue
            key = node.args[1]
        else:
            continue
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            return None
        found.add(key.value)
    return found


def _is_namespace(node: ast.expr) -> bool:
    """Tell whether *node* spells a module namespace: ``globals()``, ``vars()`` or ``sys.modules[...]``."""
    if isinstance(node, ast.Call):
        return _dotted(node.func) in ("globals", "vars") and not node.args and not node.keywords
    return isinstance(node, ast.Subscript) and _dotted(node.value) == "sys.modules"


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


def _single(events: Mapping[str, list[_NameBinding]], name: str) -> Optional[_NameBinding]:
    """Return the binding of *name* when the scope binds it exactly once, else ``None``."""
    found = events.get(name, [])
    return found[0] if len(found) == 1 else None


def _is_dataclass(node: ast.ClassDef, qualify: Callable[[ast.expr], Optional[str]]) -> bool:
    """Tell whether a class definition carries a ``dataclasses.dataclass`` decorator (called or not).

    *qualify* resolves a decorator's name through the imports of the module that defines the class, so an alias (``from
    dataclasses import dataclass as record``) counts, as does a ``dataclasses`` module of another distribution
    (``pydantic.dataclasses.dataclass`` builds a runtime dataclass too), while a local function named ``dataclass`` does
    not.

    """
    for decorator in node.decorator_list:
        name = qualify(decorator.func if isinstance(decorator, ast.Call) else decorator)
        if name is not None and (name == _DATACLASS or name.endswith(f".{_DATACLASS}")):
            return True
    return False
