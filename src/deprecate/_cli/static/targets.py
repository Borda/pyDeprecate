"""Turn a ``target`` expression into the value the factory of its route stores, as the runtime normalises it."""

import ast
import types
from collections.abc import Mapping
from typing import Any, Callable, Optional

from deprecate import TargetMode
from deprecate._cli.static.consts import _TargetRoute
from deprecate._cli.static.model import (
    _DATA_DISPLAYS,
    _FactorySettings,
    _may_be_callable_class,
    _ModuleScope,
    _ParsedModule,
    _SettingValue,
    _StaticDataTarget,
    _StaticTarget,
)
from deprecate._cli.static.scope import _NameBinding, _PyDeprecateNames, _single
from deprecate._types import DeprecationConfig
from deprecate._validate import _resolve_legacy_proxy_target, _resolve_legacy_target
from deprecate.audit import DeprecationWrapperInfo
from deprecate.audit._policy import _has_migration_guidance
from deprecate.audit._version import _absolute_import
from deprecate.audit._wrappers import _classify_member_api_type
from deprecate.module import _build_module_warn_msg


class _TargetClassifier:
    """Classify a ``target`` expression from how one module binds the names it uses.

    The module of a factory call reads its own ``target`` with one; for a ``target`` imported from another module of the
    scanned tree, the reader builds one for that module, so an imported name is judged by the same rules as a local one.

    """

    def __init__(
        self,
        parsed: _ParsedModule,
        aliases: _PyDeprecateNames,
        scope: _ModuleScope,
        in_tree: Callable[[str], bool],
        imported: Callable[[str, str], Optional[_SettingValue]],
    ) -> None:
        self.parsed = parsed
        self.aliases = aliases
        self.scope = scope
        self.in_tree = in_tree
        #: Classifies what an in-tree module binds a name to; ``None`` when that module's source cannot be read.
        self.imported = imported

    def classify(self, node: ast.expr, shadow: Mapping[str, list], depth: int) -> _SettingValue:
        """Classify a ``target`` expression as ``("const", value)`` or ``("replacement", text, kind)``."""
        if isinstance(node, ast.Constant) and (node.value is None or isinstance(node.value, bool)):
            return _SettingValue(("const", node.value))
        member = self.aliases.target_mode(node)
        if member is not None:
            return _SettingValue(("const", member))
        if isinstance(node, ast.Lambda):
            return _SettingValue(("replacement", ast.unparse(node), "def"))
        if isinstance(node, ast.Attribute):
            text = ast.unparse(node)
            return _SettingValue(("expr", text), f"`target` is `{text}`, whose attribute value is not settled")
        if isinstance(node, ast.Name):
            return self._classify_name(node, shadow, depth)
        text = ast.unparse(node)
        if isinstance(node, _DATA_DISPLAYS) or self._constructs_object(node, shadow):
            # plain data: no route but ``deprecated_instance`` takes it, so the reason stays for every other route
            return _SettingValue(("data", text), f"`target` is `{text}`, not a literal or a name")
        return _SettingValue(("expr", text), f"`target` is `{text}`, not a literal or a name")

    def _constructs_object(self, node: ast.expr, shadow: Mapping[str, list]) -> bool:
        """Tell whether *node* builds an object of a class the source binds, which is plain data without ``__call__``.

        A same-file class is read for ``__call__`` and bases; a name imported from outside the scanned tree is not read
        and is taken as a class, the same blind spot as a replacement name imported from there. A name imported from the
        tree counts only when its module binds a class. Any other callee may return a callable, so it is not settled.

        """
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            return False
        name = node.func.id
        event = _single(shadow if name in shadow else self.scope.events, name)
        if event is None:
            return False
        if event.kind == "class":
            return isinstance(event.value, ast.ClassDef) and not _may_be_callable_class(event.value)
        if event.kind != "import" or not isinstance(event.value, ast.ImportFrom):
            return False
        origin = _absolute_import(self.parsed.name, self.parsed.is_package, event.value)
        if not self.in_tree(origin):
            return True
        if origin == self.parsed.name:
            return False
        source = next((alias.name for alias in event.value.names if (alias.asname or alias.name) == name), name)
        read = self.imported(origin, source)
        return read is not None and read.reason is None and read.value[0] == "replacement" and read.value[2] == "class"

    def _classify_name(self, node: ast.Name, shadow: Mapping[str, list], depth: int) -> _SettingValue:
        """Classify a ``target`` given by name, from how the class body or the module binds it."""
        events = shadow if node.id in shadow else self.scope.events
        bindings = events.get(node.id, [])
        if not bindings and self.scope.foreign_star and events is self.scope.events:
            return _SettingValue(("expr", node.id), f"`target` is `{node.id}`, which a star import may bind")
        if not bindings:  # a builtin, or a name the module never binds: a replacement callable at runtime
            return _SettingValue(("replacement", node.id, "other"))
        event = _single(events, node.id)
        if event is None:
            return _SettingValue(("expr", node.id), f"`target` is `{node.id}`, which is bound more than once")
        if event.kind in ("def", "class"):
            return _SettingValue(("replacement", node.id, event.kind))
        if event.kind == "import":
            return self._imported(node, event)
        settled = events is not self.scope.events or not self.scope.unsettled
        if event.kind == "assign" and depth == 0 and isinstance(event.value, ast.expr) and settled:
            read = self.classify(event.value, {}, depth + 1)
            if read.reason is None:
                return read if read.value[0] == "const" else _SettingValue(("replacement", node.id, read.value[2]))
            unsettled = f"`target` is `{node.id}`, which is not bound to a literal or a definition"
            if isinstance(read.value, tuple) and read.value[0] == "data":
                return _SettingValue(("data", node.id), unsettled)
            return _SettingValue(("expr", node.id), unsettled)
        return _SettingValue(
            ("expr", node.id), f"`target` is `{node.id}`, which is not bound to a literal or a definition"
        )

    def _imported(self, node: ast.Name, event: _NameBinding) -> _SettingValue:
        """Classify a ``target`` imported by name: as the in-tree module it comes from binds it, else a replacement.

        A constant kept in another module of the tree (``REPL = None``, ``MODE = TargetMode.NOTIFY``) is read there, so
        it names no replacement, as at runtime. A name imported from outside the tree is not read and stays a
        replacement callable, a blind spot the CLI guide lists.

        """
        replacement = _SettingValue(("replacement", node.id, "other"))
        if not isinstance(event.value, ast.ImportFrom):  # ``import pkg`` binds a module
            return replacement
        origin = _absolute_import(self.parsed.name, self.parsed.is_package, event.value)
        if not self.in_tree(origin) or origin == self.parsed.name:
            return replacement
        source = next((alias.name for alias in event.value.names if (alias.asname or alias.name) == node.id), node.id)
        read = self.imported(origin, source)
        if read is None:  # the module's source cannot be read, which is its own ``module not audited`` gap
            return replacement
        if read.reason is not None:
            reason = f"`target` is `{node.id}`, imported from `{origin}`, which does not settle its value"
            return _SettingValue(("expr", node.id), reason)
        return read if read.value[0] == "const" else _SettingValue(("replacement", node.id, read.value[2]))


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


def _module_template(name: str, settings: _FactorySettings, target: _SettingValue) -> _SettingValue:
    """Return the ``message_template`` ``deprecated_module`` stores: the template rendered up front, not as spelled.

    The other factories keep the raw template, but a module wrapper stores the notice it renders at decoration time, so
    what the guidance check compares is the rendered text. A template that renders a setting the source does not settle,
    or that the factory would reject, is unresolved rather than judged on its raw text.

    """
    template = settings.template
    if template.reason is not None or not template.value:
        return template
    for key, read in (
        ("deprecated_in", settings.deprecated_in),
        ("remove_in", settings.remove_in),
        ("target_name", target),
    ):
        if read.reason is not None and f"%({key})" in template.value:
            return _SettingValue(None, f"`message_template` renders `{key}`, but {read.reason}")
    try:
        rendered = _build_module_warn_msg(
            name, settings.deprecated_in.value, settings.remove_in.value, target.value, template.value
        )
    except (ValueError, KeyError, TypeError) as err:
        return _SettingValue(None, f"`message_template` cannot be rendered: {type(err).__name__}: {err}")
    return _SettingValue(rendered)


def _store_target(read: _SettingValue, route: _TargetRoute, mappings: tuple[Any, Any]) -> _SettingValue:
    """Return the ``target`` the factory of *route* stores for a classified read, or why the source does not say."""
    if read.reason is not None:
        if route is _TargetRoute.INSTANCE and isinstance(read.value, tuple) and read.value[0] == "data":
            return _SettingValue(
                _StaticDataTarget(read.value[1])
            )  # a replacement object that is data, as ``deprecated_instance`` stores
        return _SettingValue(None, read.reason)
    classified = read.value
    if classified[0] == "default":
        default = {_TargetRoute.CALLABLE: TargetMode.AUTO, _TargetRoute.UNKNOWN_FRONT: TargetMode.AUTO}.get(route)
        classified = ("const", TargetMode.NOTIFY if route is _TargetRoute.CALLABLE_STRICT else default)
    if route is _TargetRoute.UNKNOWN_FRONT:
        return _unknown_front(classified, mappings)
    return _normalise_target(classified, route, mappings)


def _normalise_target(read: tuple[Any, ...], route: _TargetRoute, mappings: tuple[Any, Any]) -> _SettingValue:
    """Turn a classified ``target`` into the value the factory of *route* stores, as the runtime normalises it."""
    args_mapping, attrs_mapping = mappings
    kind, value = read[0], read[1]
    if route is _TargetRoute.MODULE:
        # a named replacement (module, function or class) is named by the warning the factory renders, which counts as
        # guidance, so it is stored as a module; a constant (``True``, a ``TargetMode`` member) has no name and gives
        # none, as at runtime
        return _SettingValue(types.ModuleType(str(value)) if kind == "replacement" else None)
    if kind == "replacement":
        if route is _TargetRoute.CLASS_FRONT and read[2] != "class":
            if read[2] == "def":
                return _SettingValue(_auto_resolve(None, mappings))  # a non-class target is ignored on the class route
            return _SettingValue(
                None, f"`@deprecated` on a class ignores a non-class `target`; `{value}` may be either"
            )
        return _SettingValue(_StaticTarget(value))
    if route in (_TargetRoute.CALLABLE, _TargetRoute.CALLABLE_STRICT):
        if value is TargetMode.AUTO:
            return _SettingValue(TargetMode.ARGS_REMAP if args_mapping else TargetMode.NOTIFY)
        return _SettingValue(_resolve_legacy_target(value, emit=False) if not isinstance(value, TargetMode) else value)
    if route is _TargetRoute.CLASS_FRONT:
        target = TargetMode.ARGS_REMAP if value is True else None if value in (None, False, TargetMode.AUTO) else value
        return _SettingValue(_auto_resolve(target, mappings))
    if route is _TargetRoute.CLASS:
        if isinstance(value, bool) or value is None:
            value = _resolve_legacy_proxy_target(value, args_mapping=args_mapping, emit=False)
        return _SettingValue(_auto_resolve(value, mappings))
    return _SettingValue(value)  # deprecated_instance stores its target as given


def _auto_resolve(target: Any, mappings: tuple[Any, Any]) -> Any:  # noqa: ANN401
    """Resolve a proxy's unset target from its mappings, as ``_resolve_proxy_target`` does."""
    args_mapping, attrs_mapping = mappings
    if target is None and args_mapping:
        return TargetMode.ARGS_REMAP
    if target is None and attrs_mapping:
        return TargetMode.ATTRS_REMAP
    return target


def _unknown_front(read: tuple[Any, ...], mappings: tuple[Any, Any]) -> _SettingValue:
    """Normalise ``deprecated(...)(obj)`` when *obj* may be a class: settled only when both routes agree on guidance."""
    as_callable = _normalise_target(read, _TargetRoute.CALLABLE, mappings)
    as_class = _normalise_target(read, _TargetRoute.CLASS_FRONT, mappings)
    if as_callable.reason is None and as_class.reason is None:
        guidance = {
            _has_migration_guidance(
                DeprecationWrapperInfo(deprecated_info=DeprecationConfig(target=target, args_mapping=mappings[0]))
            )
            for target in (as_callable.value, as_class.value)
        }
        if len(guidance) == 1:
            return as_callable
    return _SettingValue(
        None, "`@deprecated` routes on whether the wrapped object is a class, which the source does not show"
    )
