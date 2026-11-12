"""Read the wrappers one module builds: recognize factory calls and decorators in a parsed module body."""

import ast
import builtins
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Callable, Optional, Union

from deprecate._cli.static.consts import (
    _ACCESSOR_SLOTS,
    _ACCESSORS,
    _CONDITIONAL_STATEMENTS,
    _TRANSPARENT_DECORATORS,
    _TYPE_CHECKING_FLAGS,
    _Factory,
    _positional_params,
    _TargetRoute,
)
from deprecate._cli.static.model import (
    _display,
    _FactoryCallArgs,
    _FactorySettings,
    _ImportRef,
    _Lookup,
    _module_scope,
    _ModuleFacts,
    _ParsedModule,
    _PlainClass,
    _qualified_name,
    _SettingValue,
    _SourceWrapper,
    _Value,
)
from deprecate._cli.static.scope import (
    _dotted,
    _escaped_names,
    _is_dataclass,
    _is_memberless_module,
    _module_level_nodes,
    _NameBinding,
    _PyDeprecateNames,
    _scope_events,
    _single,
)
from deprecate._cli.static.targets import (
    _callable_api_type,
    _class_api_type,
    _member_api_type,
    _module_template,
    _store_target,
    _TargetClassifier,
)
from deprecate._types import DeprecationConfig
from deprecate.audit._version import _absolute_import


class _ModuleAnalyzer:
    """Read one module's top-level statements into :class:`_ModuleFacts`, recognizing every wrapper shape."""

    def __init__(
        self,
        parsed: _ParsedModule,
        aliases: _PyDeprecateNames,
        in_tree: Callable[[str], bool],
        name_kind: Callable[[str, str], tuple[str, bool]],
        imported_configured: Callable[[str, str], _Lookup],
        imported_target: Callable[[str, str], Optional[_SettingValue]],
    ) -> None:
        self.parsed = parsed
        self.aliases = aliases
        self.in_tree = in_tree
        self.imported_kind = name_kind
        self.imported_configured = imported_configured
        module_scope = _module_scope(parsed)
        self.scope = module_scope.events
        #: Whether the module writes its globals under a computed key, so no module-level name is settled.
        self.globals_unsettled = module_scope.unsettled
        #: Names read other than by a copy or a factory keyword (``bump(KW)``), so a dict they hold is not settled.
        self.escaped = _escaped_names(parsed.tree, lambda func: aliases.kind(func) not in (None, _Factory.TARGET_MODE))
        #: Whether a star import may bind names the reader cannot list (pyDeprecate's own star imports aside).
        self.foreign_star = module_scope.foreign_star
        #: Reads a ``target`` from how this module binds the names it uses, following in-tree imports.
        self.targets = _TargetClassifier(parsed, aliases, module_scope, in_tree, imported_target)
        self.consumed: set[int] = set()
        self.bindings: dict[str, _Value] = {}
        #: Names bound to a configured factory call (``_dep = deprecated(...)``), later applied as ``@_dep``.
        self.configured: dict[str, tuple[_Factory, _FactorySettings]] = {}
        #: Every name the module ever bound to a configured factory, whose unconsumed uses are gaps.
        self.ever_configured: set[str] = set()
        self.lookup_gaps: list[str] = []
        #: Names whose import cycle is already a gap, so each is reported once rather than at every use.
        self.unfollowed: set[str] = set()
        self.module_wrapper: Optional[_SourceWrapper] = None
        self.star_sources: list[tuple[str, int]] = []
        #: The position of the statement being read, counted over block bodies too, so bindings can be ordered.
        self.position = 0
        self.positions: dict[str, int] = {}
        self.where = _display(parsed.path)

    def run(self) -> _ModuleFacts:
        """Walk the module body in order, so the last binding of a name wins as at runtime."""
        for stmt in self.parsed.tree.body:
            self._statement(stmt)
        unrecognized = self._unrecognized()
        return _ModuleFacts(
            self.bindings, self.module_wrapper, unrecognized, self.star_sources, dict(self.configured), self.positions
        )

    def _set(self, name: str, value: _Value) -> None:
        """Bind a module-level name, dropping any configured factory it held before."""
        self.bindings[name] = value
        self.positions[name] = self.position
        self.configured.pop(name, None)

    def _statement(self, stmt: ast.stmt) -> None:
        """Record what one module-level statement binds."""
        self.position += 1
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
        elif isinstance(stmt, (ast.With, ast.AsyncWith, ast.If)):
            self._block(stmt)
        else:
            self._rebind_opaque(stmt)

    def _block(self, stmt: Union[ast.With, ast.AsyncWith, ast.If]) -> None:
        """Read the part of a block that always runs as module-level code; any other ``if`` binds opaque values."""
        if isinstance(stmt, ast.If):
            if self._qualified(stmt.test) not in _TYPE_CHECKING_FLAGS:
                self._rebind_opaque(stmt)
                return
            body = stmt.orelse  # an ``if TYPE_CHECKING:`` body is read by type checkers only; its ``else`` always runs
        else:
            # A ``with`` body starts as module-level code; its ``as`` targets bind opaque values.
            for name in _scope_events([ast.Expr(item.optional_vars) for item in stmt.items if item.optional_vars]):
                self._set(name, None)
            body = stmt.body
        may_interrupt = False
        non_suppressing = isinstance(stmt, (ast.With, ast.AsyncWith)) and all(
            isinstance(item.context_expr, ast.Call)
            and self._qualified(item.context_expr.func) == "warnings.catch_warnings"
            for item in stmt.items
        )
        for inner in body:
            if not isinstance(stmt, ast.If) and not non_suppressing:
                # A context manager may suppress an exception before a binding executes.
                for name in _scope_events([inner]):
                    if may_interrupt or isinstance(self.bindings.get(name), (_SourceWrapper, _PlainClass)):
                        self.lookup_gaps.append(self._rebound_gap(stmt, name))
                if isinstance(inner, ast.Raise):
                    break
                if may_interrupt:
                    self._rebind_opaque(inner)
                    continue
                module_nodes = _module_level_nodes(ast.Module(body=[inner], type_ignores=[]))
                may_interrupt = any(
                    isinstance(node, ast.Call)
                    or isinstance(node, (ast.Import, ast.ImportFrom))
                    and id(node) in module_nodes
                    for node in ast.walk(inner)
                )
            self._statement(inner)

    def _rebind_opaque(self, stmt: ast.stmt) -> None:
        """Bind every name *stmt* assigns to an opaque value, reporting a wrapper a conditional statement may replace.

        Whether an ``if``, ``try`` or loop body runs is not settled by the source, so a deprecated name it rebinds may
        still hold its wrapper at runtime, or may not; the wrapper is left out of the checks and the line says so.

        """
        conditional = isinstance(stmt, _CONDITIONAL_STATEMENTS)
        for name in _scope_events([stmt]):
            if conditional and isinstance(self.bindings.get(name), (_SourceWrapper, _PlainClass)):
                self.lookup_gaps.append(self._rebound_gap(stmt, name))
            self._set(name, None)

    def _rebound_gap(self, stmt: ast.stmt, name: str) -> str:
        """Return the gap line for a deprecated *name* the conditional statement *stmt* may rebind."""
        return (
            f"{self.where}:{stmt.lineno}: `{name}` may be rebound by this `{type(stmt).__name__.lower()}` statement, so"
            " whether it still holds its deprecation wrapper is not settled; it is not audited"
        )

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

    def _configured_factory(self, value: ast.expr) -> Optional[tuple[_Factory, _FactorySettings]]:
        """Recognize ``deprecated(...)`` or ``deprecated_class(...)`` assigned to a name to be applied later."""
        if not isinstance(value, ast.Call):
            return None
        kind = self.aliases.kind(value.func)
        if kind not in (_Factory.DEPRECATED, _Factory.CALLABLE, _Factory.CLASS):
            return None
        self.consumed.add(id(value.func))
        return kind, self._read_settings(self._bind(value, kind, {}), value, {})

    def _assigned_value(self, value: ast.expr) -> _Value:
        """Return what an assigned value binds: an alias shares the object; ``typing.cast(T, x)`` aliases ``x``."""
        if isinstance(value, ast.Call) and _dotted(value.func) in ("cast", "typing.cast") and len(value.args) == 2:
            value = value.args[1]
        if isinstance(value, ast.Name):
            return self.bindings.get(value.id)
        return self._wrapper_expr(value, attr=None, shadow={})

    def _factory_call(
        self, node: ast.expr, kinds: tuple[_Factory, ...], shadow: Mapping[str, list]
    ) -> Optional[tuple[_Factory, _FactorySettings]]:
        """Return the factory and settings of ``factory(...)``, or of a name bound to one here or in-tree."""
        if self._shadowed_factory(node, shadow):
            return None
        configured: _Lookup = None
        if isinstance(node, ast.Name) and node.id in self.configured:
            configured = self.configured[node.id]
        elif isinstance(node, ast.Name):
            configured = self._imported_configured(node.id)
        if isinstance(configured, str):
            self.consumed.add(id(node))
            self._unfollowed(node, configured)
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
        return kind, self._read_settings(self._bind(node, kind, shadow), node, shadow)

    def _shadowed_factory(self, node: ast.expr, shadow: Mapping[str, list]) -> bool:
        """Tell whether *node* names a factory through a name the class body rebinds, recording the gap when it does.

        Which binding a class-body decorator or call sees depends on statement order, as for a setting
        (:meth:`_string`), so the factory is left unread rather than taken from the module scope.

        """
        callee = node.func if isinstance(node, ast.Call) else node
        root = callee
        while isinstance(root, ast.Attribute):
            root = root.value
        if not (isinstance(root, ast.Name) and root.id in shadow):
            return False
        names_factory = self.aliases.kind(callee) not in (None, _Factory.TARGET_MODE) or (
            isinstance(callee, ast.Name)
            and (callee.id in self.configured or self._imported_configured(callee.id) is not None)
        )
        if names_factory:
            self.consumed.add(id(callee))
            self.lookup_gaps.append(
                f"{self.where}:{node.lineno}: `{root.id}` is rebound in the class body, so the factory"
                f" `{ast.unparse(callee)}` names there is not settled; its wrapper is not audited"
            )
        return names_factory

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
                    self.star_sources.append((origin, self.position))
                continue
            local = alias.asname or alias.name
            if origin == self.parsed.name:
                # ``from . import sub`` in a package's ``__init__`` takes the attribute already bound here, else the
                # submodule; a reference back to this module would chase itself in a cycle
                self._set(local, self.bindings.get(alias.name))
                continue
            self._set(local, _ImportRef(origin, alias.name) if in_tree else None)

    def _module_call(self, call: ast.Call) -> None:
        """Recognize ``deprecated_module(...)`` deprecating this module; the first call wins, as at runtime."""
        if self.aliases.kind(call.func) is not _Factory.MODULE:
            return
        args = self._bind(call, _Factory.MODULE, {})
        name = args.given.get("name")
        own = (
            name is None
            or (isinstance(name, ast.Name) and name.id == "__name__")
            or (isinstance(name, ast.Constant) and name.value in (None, self.parsed.name))
        )
        if not own or args.splat is not None and "name" not in args.given:
            return
        self.consumed.add(id(call.func))
        if self.module_wrapper is None:
            settings = self._read_settings(args, call, {})
            self.module_wrapper = self._finish(settings, _TargetRoute.MODULE, lambda _: "module", proxy=False)

    def _decorated_def(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef], *, attr: Optional[str], shadow: Mapping[str, list]
    ) -> Optional[_SourceWrapper]:
        """Return the wrapper the outermost ``@deprecated``/``@deprecated_callable`` of a ``def`` builds, if any."""
        found: Optional[_SourceWrapper] = None
        replaced = False
        descriptor = next(
            (name for d in node.decorator_list if (name := _dotted(d)) in ("classmethod", "staticmethod")), None
        )
        for index, decorator in enumerate(node.decorator_list):  # outermost first; the scan reads its configuration
            factory = self._factory_call(decorator, (_Factory.DEPRECATED, _Factory.CALLABLE), shadow)
            if factory is None or found is not None or replaced:
                continue
            replaced = self._replaced_by_outer(node.decorator_list[:index], decorator)
            if replaced:
                continue
            kind, settings = factory
            route = _TargetRoute.CALLABLE if kind is _Factory.DEPRECATED else _TargetRoute.CALLABLE_STRICT
            api_type = _member_api_type(attr, descriptor) if attr is not None else _callable_api_type
            found = self._finish(settings, route, api_type, proxy=False)
        return found

    def _replaced_by_outer(self, outer: Sequence[ast.expr], factory: ast.expr) -> bool:
        """Tell whether a decorator stacked over *factory* may replace its wrapper, recording the gap when it may.

        The import scan sees only the object the outermost decorator returns; a decorator outside
        :data:`_TRANSPARENT_DECORATORS` may return something that is no wrapper, so the wrapper is left out of the
        checks rather than reported as a finding the import scan would not make.

        """
        for decorator in outer:
            name = self._qualified(decorator.func if isinstance(decorator, ast.Call) else decorator)
            accessor = name is not None and "." in name and name.rpartition(".")[2] in _ACCESSORS
            if name in _TRANSPARENT_DECORATORS or accessor:
                continue
            self.lookup_gaps.append(
                f"{self.where}:{factory.lineno}: `@{ast.unparse(decorator)}` is applied over the deprecation wrapper"
                " and may replace it; the wrapper is not audited"
            )
            return True
        return False

    def _qualified(self, node: ast.expr) -> Optional[str]:
        """Return the dotted name *node* spells, its root resolved through ``from module import name``."""
        return _qualified_name(self.parsed, self.scope, node)

    def _class(
        self, node: ast.ClassDef, *, attr: Optional[str], shadow: Mapping[str, list]
    ) -> Union[_SourceWrapper, _PlainClass, None]:
        """Return the proxy a class decorator builds, else the class with its own member bindings."""
        for index, decorator in enumerate(node.decorator_list):
            factory = self._factory_call(decorator, (_Factory.DEPRECATED, _Factory.CLASS), shadow)
            if factory is not None:
                if self._replaced_by_outer(node.decorator_list[:index], decorator):
                    return None
                kind, settings = factory
                route = _TargetRoute.CLASS_FRONT if kind is _Factory.DEPRECATED else _TargetRoute.CLASS
                is_dataclass = _is_dataclass(node, self._qualified)
                api_type = _member_api_type(attr, None) if attr is not None else _class_api_type(is_dataclass)
                return self._finish(settings, route, api_type, proxy=True)
        info = _PlainClass(self.parsed.name, {}, list(node.bases))
        body_scope = _scope_events(node.body)
        accessors: dict[str, dict[str, Optional[_SourceWrapper]]] = {}
        for stmt in node.body:
            self._class_statement(info, stmt, body_scope, accessors)
        return info

    def _class_statement(
        self,
        info: _PlainClass,
        stmt: ast.stmt,
        body_scope: Mapping[str, list],
        accessors: dict[str, dict[str, Optional[_SourceWrapper]]],
    ) -> None:
        """Record what one class-body statement binds as a member; *accessors* holds each property's accessor slots."""
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._member_def(info, stmt, body_scope, accessors)
            return
        if isinstance(stmt, ast.If) and self._qualified(stmt.test) in _TYPE_CHECKING_FLAGS:
            for inner in stmt.orelse:  # as in ``_block``: the ``if TYPE_CHECKING:`` body never runs, its ``else`` does
                self._class_statement(info, inner, body_scope, accessors)
            return
        for name in _scope_events([stmt]):
            accessors.pop(name, None)
        if isinstance(stmt, ast.ClassDef):
            nested = self._class(stmt, attr=stmt.name, shadow=body_scope)
            info.members[stmt.name] = nested if isinstance(nested, _SourceWrapper) else None
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None:
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    if isinstance(stmt.value, ast.Name):
                        info.members[target.id] = self._class_alias(info, stmt.value.id, target.id)
                    else:
                        info.members[target.id] = self._wrapper_expr(stmt.value, attr=target.id, shadow=body_scope)
        else:
            self._opaque_members(info, stmt)

    def _class_alias(self, info: _PlainClass, source: str, target: str) -> Optional[_SourceWrapper]:
        """Resolve a class or module binding and classify it under the exposed member name."""
        member = info.members.get(source) if source in info.members else self.bindings.get(source)
        if not isinstance(member, _SourceWrapper):
            return None
        descriptor = (
            "classmethod"
            if member.api_type.startswith("classmethod")
            else "staticmethod"
            if member.api_type.startswith("staticmethod")
            else None
        )
        return replace(member, api_type=_member_api_type(target, descriptor)(bool(member.config.args_mapping)))

    def _opaque_members(self, info: _PlainClass, stmt: ast.stmt) -> None:
        """Bind each member *stmt* assigns to no wrapper, reporting a deprecated one an ``if`` or ``try`` may replace.

        As for module-level names (:meth:`_rebind_opaque`): whether an ``if``, ``try`` or loop body runs is not settled
        by the source, so a deprecated member it rebinds may still hold its wrapper at runtime, where the import scan
        reports it. A multi-statement ``with`` body may stop before a rebinding if its manager suppresses an error.

        """
        conditional = isinstance(stmt, _CONDITIONAL_STATEMENTS) or (
            isinstance(stmt, (ast.With, ast.AsyncWith))
            and any(isinstance(node, (ast.Call, ast.Raise)) for inner in stmt.body for node in ast.walk(inner))
        )
        for name in _scope_events([stmt]):
            if conditional and isinstance(info.members.get(name), _SourceWrapper):
                self.lookup_gaps.append(self._rebound_gap(stmt, name))
            info.members[name] = None

    def _member_def(
        self,
        info: _PlainClass,
        stmt: Union[ast.FunctionDef, ast.AsyncFunctionDef],
        body_scope: Mapping[str, list],
        accessors: dict[str, dict[str, Optional[_SourceWrapper]]],
    ) -> None:
        """Record a method, or one accessor of a property, as the member the import scan reads.

        ``@old.setter`` rebuilds the property with one accessor replaced, and the import scan reports its first
        deprecated accessor in getter, setter, deleter order, whatever order the source declares them in.

        """
        wrapper = self._decorated_def(stmt, attr=stmt.name, shadow=body_scope)
        slot = next(
            (
                _ACCESSOR_SLOTS[d.attr]
                for d in stmt.decorator_list
                if isinstance(d, ast.Attribute) and d.attr in _ACCESSOR_SLOTS and _dotted(d) == f"{stmt.name}.{d.attr}"
            ),
            None,
        )
        if slot is None:
            slots: dict[str, Optional[_SourceWrapper]] = {"fget": wrapper}
        else:
            slots = {**accessors.get(stmt.name, {"fget": info.members.get(stmt.name)}), slot: wrapper}
        accessors[stmt.name] = slots
        info.members[stmt.name] = next((slots[s] for s in _ACCESSOR_SLOTS.values() if slots.get(s) is not None), None)

    def _wrapper_expr(
        self, value: ast.expr, *, attr: Optional[str], shadow: Mapping[str, list]
    ) -> Optional[_SourceWrapper]:
        """Recognize ``factory(...)(obj)`` and ``deprecated_instance(obj, ...)``; any other value is no wrapper."""
        if not isinstance(value, ast.Call) or self._shadowed_factory(value, shadow):
            return None
        if self.aliases.kind(value.func) is _Factory.INSTANCE:
            self.consumed.add(id(value.func))
            args = self._bind(value, _Factory.INSTANCE, shadow)
            api_type = _member_api_type(attr, None) if attr is not None else self._instance_api_type(args)
            wrapper = self._finish(
                self._read_settings(args, value, shadow), _TargetRoute.INSTANCE, api_type, proxy=True
            )
            obj = args.given.get("obj")
            if obj is not None and wrapper.config.args_mapping and not callable(wrapper.config.target):
                classification = self.targets.classify(obj, shadow, 0)
                if classification.value[0] == "data":
                    wrapper = replace(wrapper, config=replace(wrapper.config, call_options_inert=True))
                elif classification.value[0] != "replacement" or classification.value[2] != "def":
                    wrapper.unresolved["args_mapping"] = (
                        f"{self.where}:{value.lineno}: callability of the proxied object is not settled"
                    )
            return wrapper
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
    ) -> tuple[_TargetRoute, Callable[[bool], str], Optional[bool], str]:
        """Return the route, api_type rule, proxy flag and module of ``factory(...)(obj)`` from what *obj* names.

        A function wrapper copies its source's ``__module__`` (``functools.wraps``), so wrapping an imported function
        gives a wrapper the import scan attributes to the module the function came from.

        """
        obj_kind, is_dataclass, origin = self._name_kind(obj, shadow)
        here = self.parsed.name
        if kind is _Factory.CLASS:
            return _TargetRoute.CLASS, _class_api_type(is_dataclass), True, here
        if kind is _Factory.CALLABLE:
            return _TargetRoute.CALLABLE_STRICT, _callable_api_type, False, origin
        if obj_kind == "class":
            return _TargetRoute.CLASS_FRONT, _class_api_type(is_dataclass), True, here
        if obj_kind == "def":
            return _TargetRoute.CALLABLE, _callable_api_type, False, origin
        return _TargetRoute.UNKNOWN_FRONT, _callable_api_type, None, origin

    def _name_kind(self, node: ast.expr, shadow: Mapping[str, list]) -> tuple[str, bool, str]:
        """Return ``def``, ``class`` or ``other`` for what *node* names, whether it is a dataclass, and its module."""
        here = self.parsed.name
        if isinstance(node, ast.Lambda):
            return "def", False, here
        if not isinstance(node, ast.Name):
            return "other", False, here
        event = _single(shadow, node.id) if node.id in shadow else _single(self.scope, node.id)
        if event is not None and event.kind in ("def", "class"):
            is_dataclass = isinstance(event.value, ast.ClassDef) and _is_dataclass(event.value, self._qualified)
            return event.kind, is_dataclass, here
        if event is not None and isinstance(event.value, ast.ImportFrom):
            origin = _absolute_import(here, self.parsed.is_package, event.value)
            source = next((alias.name for alias in event.value.names if (alias.asname or alias.name) == node.id), "")
            kind, is_dataclass = self.imported_kind(origin, source) if self.in_tree(origin) else ("other", False)
            return kind, is_dataclass, origin
        return "other", False, here

    def _instance_api_type(self, args: _FactoryCallArgs) -> Callable[[bool], str]:
        """``deprecated_instance`` of a same-file class proxies the class; of anything else, a data object."""
        obj = args.given.get("obj")
        kind, is_dataclass, _ = self._name_kind(obj, {}) if obj is not None else ("other", False, "")
        if kind == "class":
            return _class_api_type(is_dataclass)
        return lambda _: "data"

    # -- argument reading -----------------------------------------------------------------------------------------

    def _bind(self, call: ast.Call, factory: _Factory, shadow: Mapping[str, list]) -> _FactoryCallArgs:
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
            entries = self._literal_dict(keyword.value, shadow)
            if entries is None:
                splat = f"**{ast.unparse(keyword.value)}"
            else:
                for key, value in entries.items():
                    given.setdefault(key, value)
        return _FactoryCallArgs(given, splat)

    def _literal_dict(self, node: ast.expr, shadow: Mapping[str, list]) -> Optional[dict[str, ast.expr]]:
        """Return the entries of a dict literal, inline or a same-file name bound once to one, with string keys.

        A name the enclosing class body binds is left unread: which binding a decorator sees depends on statement
        order, as for a string setting (:meth:`_string`).

        """
        if isinstance(node, ast.Name) and node.id in shadow:
            return None
        if isinstance(node, ast.Name):
            event = self._settled_binding(node.id) if node.id not in self.escaped else None
            node = event.value if event is not None and event.kind == "assign" else node
        if not isinstance(node, ast.Dict):
            return None
        entries: dict[str, ast.expr] = {}
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                return None
            entries[key.value] = value
        return entries

    def _settled_binding(self, name: str) -> Optional[_NameBinding]:
        """Return the one module-level binding of *name*, or ``None`` when the source does not settle what it holds."""
        return None if self.globals_unsettled else _single(self.scope, name)

    def _setting(self, args: _FactoryCallArgs, name: str) -> Optional[ast.expr]:
        """Return the expression given for *name*, raising :class:`LookupError` when only a splat could carry it."""
        if name in args.given:
            return args.given[name]
        if args.splat is not None:
            raise LookupError(f"arguments come from `{args.splat}`")
        return None

    def _string(
        self, args: _FactoryCallArgs, name: str, shadow: Mapping[str, list], default: Optional[str]
    ) -> _SettingValue:
        """Read a string setting: a literal, or a same-file module-level name bound once to one."""
        try:
            node = self._setting(args, name)
        except LookupError as err:
            return _SettingValue(default, str(err))
        if node is None:
            return _SettingValue(default)
        literal = node
        if isinstance(node, ast.Name) and node.id not in shadow:
            event = self._settled_binding(node.id)
            literal = event.value if event is not None and event.kind == "assign" else node
        if isinstance(literal, ast.Constant) and literal.value is None:
            return _SettingValue(default)  # ``None`` leaves the setting unset, as the runtime reads a falsy version
        if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
            return _SettingValue(literal.value)
        return _SettingValue(default, f"`{name}` is `{ast.unparse(node)}`, not a literal")

    def _mapping(self, args: _FactoryCallArgs, name: str, shadow: Mapping[str, list]) -> _SettingValue:
        """Read a mapping setting: ``None`` or a dict literal (inline or a same-file name bound once to one)."""
        try:
            node = self._setting(args, name)
        except LookupError as err:
            return _SettingValue(None, str(err))
        if node is None or (isinstance(node, ast.Constant) and node.value is None):
            return _SettingValue(None)
        entries = self._literal_dict(node, shadow)
        if entries is None:
            return _SettingValue(None, f"`{name}` is `{ast.unparse(node)}`, not a literal")
        return _SettingValue(dict.fromkeys(entries) or None)

    def _target(self, args: _FactoryCallArgs, shadow: Mapping[str, list]) -> _SettingValue:
        """Read and classify the ``target`` setting; :func:`_store_target` stores it per factory route."""
        try:
            node = self._setting(args, "target")
        except LookupError as err:
            return _SettingValue(None, str(err))
        return _SettingValue(("default",)) if node is None else self.targets.classify(node, shadow, depth=0)

    def _read_settings(self, args: _FactoryCallArgs, call: ast.Call, shadow: Mapping[str, list]) -> _FactorySettings:
        """Read every setting the checks use from one factory call, in the scope it is spelled in."""
        template = self._string(args, "message_template", shadow, None)
        if template.reason is None and template.value is None:
            template = self._string(args, "template_mgs", shadow, None)
        return _FactorySettings(
            where=f"{self.where}:{call.lineno}",
            deprecated_in=self._string(args, "deprecated_in", shadow, ""),
            remove_in=self._string(args, "remove_in", shadow, ""),
            template=template,
            args_mapping=self._mapping(args, "args_mapping", shadow),
            attrs_mapping=self._mapping(args, "attrs_mapping", shadow),
            target=self._target(args, shadow),
        )

    def _finish(
        self, settings: _FactorySettings, route: _TargetRoute, api_type: Callable[[bool], str], *, proxy: Optional[bool]
    ) -> _SourceWrapper:
        """Turn read settings into the configuration the factory of *route* stores, recording what stays unsettled."""
        mappings = (settings.args_mapping.value, settings.attrs_mapping.value)
        target = _store_target(settings.target, route, mappings)
        template = (
            _module_template(self.parsed.name, settings, target) if route is _TargetRoute.MODULE else settings.template
        )
        reads = {
            "deprecated_in": settings.deprecated_in,
            "remove_in": settings.remove_in,
            "message_template": template,
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
            message_template=template.value,
        )
        unresolved = {name: f"{settings.where}: {read.reason}" for name, read in reads.items() if read.reason}
        return _SourceWrapper(self.parsed.name, proxy, api_type(bool(settings.args_mapping.value)), config, unresolved)

    def _unrecognized(self) -> list[str]:
        """Return one gap detail per factory reference no recognized shape consumed.

        A reference is a pyDeprecate factory name, a name bound to a configured factory here or in an in-tree module, or
        a pyDeprecate name the source does not settle (imported inside a function, or rebound).

        """
        gaps: list[str] = []
        for node in ast.walk(self.parsed.tree):
            if isinstance(node, ast.Call) and self._is_foreign_factory_call(node):
                gaps.append(
                    f"{self.where}:{node.lineno}: `{ast.unparse(node.func)}` is called with `remove_in` but comes from"
                    " a module static mode does not read; if it is a pyDeprecate factory, its wrapper is not audited"
                )
            if not isinstance(node, (ast.Name, ast.Attribute)) or id(node) in self.consumed:
                continue
            if isinstance(node.ctx, ast.Load) and self._is_factory_reference(node):
                gaps.append(f"{self.where}:{node.lineno}: unrecognized use of `{ast.unparse(node)}`")
        return [*self.lookup_gaps, *gaps]  # the walk adds the import cycles it meets to ``lookup_gaps``

    def _is_foreign_factory_call(self, call: ast.Call) -> bool:
        """Tell whether *call* passes ``remove_in`` to a name that may hold a factory the reader cannot see.

        A factory re-exported by a module outside the scanned tree (``from compat import deprecated``, or a star import
        of one) is not recognized, so its wrappers would pass unseen. ``remove_in`` is pyDeprecate's own spelling, so a
        call passing it by keyword marks such a use; a call with positional settings stays out of reach.

        """
        if id(call.func) in self.consumed or not any(keyword.arg == "remove_in" for keyword in call.keywords):
            return False
        if isinstance(call.func, (ast.Name, ast.Attribute)) and self._is_factory_reference(call.func):
            return False  # already reported as an unrecognized use
        root = call.func
        while isinstance(root, ast.Attribute):
            root = root.value
        return isinstance(root, ast.Name) and self._is_foreign_name(root.id)

    def _is_foreign_name(self, name: str) -> bool:
        """Tell whether a module-level *name* comes from a module the reader does not follow, so it may be a factory."""
        if name not in self.scope:
            return self.foreign_star and not hasattr(builtins, name)
        event = _single(self.scope, name)
        if event is None or event.kind != "import":
            return False
        if isinstance(event.value, ast.Import):
            origin = next(a.name for a in event.value.names if (a.asname or a.name.split(".")[0]) == name)
        else:
            origin, source = self._import_origin(name) or ("", "")
            if self.in_tree(origin):
                return self.imported_kind(origin, source)[0] == "other"
        return origin.split(".")[0] != "deprecate" and not _is_memberless_module(origin)

    def _is_factory_reference(self, node: Union[ast.Name, ast.Attribute]) -> bool:
        """Tell whether *node* refers to a factory, a configured factory, or a pyDeprecate name bound ambiguously."""
        if self.aliases.kind(node) not in (None, _Factory.TARGET_MODE):
            return True
        if not isinstance(node, ast.Name):
            return False
        if node.id in self.aliases.ambiguous:
            return self.aliases.ambiguous[node.id] is not _Factory.TARGET_MODE
        lookup = self._imported_configured(node.id)
        if isinstance(lookup, str):
            self._unfollowed(node, lookup)
            return False
        return node.id in self.ever_configured or lookup is not None

    def _unfollowed(self, node: ast.expr, reason: str) -> None:
        """Record once per name why an imported name that may hold a configured factory was not followed."""
        name = ast.unparse(node)
        if name not in self.unfollowed:
            self.unfollowed.add(name)
            self.lookup_gaps.append(f"{self.where}:{node.lineno}: `{name}` {reason}")
