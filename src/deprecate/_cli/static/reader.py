"""Read a tree of modules: parse each once, follow imports and star exports, and merge members across bases."""

import ast
import builtins
import functools
from collections.abc import Collection
from pathlib import Path
from typing import Optional, Union

from deprecate._cli.report import _GapKind, _print_gap
from deprecate._cli.static.analyzer import _ModuleAnalyzer
from deprecate._cli.static.consts import _BASE_DEPTH, _CYCLE
from deprecate._cli.static.discovery import _StaticSource
from deprecate._cli.static.model import (
    _ImportRef,
    _Lookup,
    _module_scope,
    _ModuleFacts,
    _ModuleScope,
    _ParsedModule,
    _PlainClass,
    _qualified_name,
    _SettingValue,
    _SourceWrapper,
    _Value,
)
from deprecate._cli.static.scope import (
    _direct_aliases,
    _globals_rebinds,
    _is_dataclass,
    _is_memberless_module,
    _module_level_nodes,
    _NameBinding,
    _PyDeprecateNames,
    _scope_events,
    _single,
)
from deprecate._cli.static.targets import _TargetClassifier
from deprecate.audit._version import _absolute_import, _literal_all, _module_source_file


class _TreeReader:
    """Parse and analyze the modules of one scanned tree on demand, following in-tree imports between them.

    Two analyses run per module. The *table* is read with no cross-module lookups, so it settles what a module's names
    hold on their own (its configured factories and its re-exports) and can be asked for while another module is being
    analyzed, import cycles included. The *facts* are the full analysis, which asks other modules' tables.

    """

    def __init__(self, source: _StaticSource) -> None:
        self.source = source
        self.root_package = source.name if source.kind == "package" else None
        self._parsed: dict[str, Optional[_ParsedModule]] = {}
        #: Why a module's source could not be read or analyzed, printed once as a ``module not audited`` gap.
        self._errors: dict[str, str] = {}
        self._reported: set[str] = set()
        self._facts: dict[str, Optional[_ModuleFacts]] = {}
        self._tables: dict[str, Optional[_ModuleFacts]] = {}
        self._aliases: dict[str, _PyDeprecateNames] = {}
        self._scopes: dict[str, _ModuleScope] = {}
        self._in_progress: set[str] = set()

    def in_tree(self, module: str) -> bool:
        """Whether *module* belongs to the scanned package, whose source the reader may follow imports into."""
        root = self.root_package
        return root is not None and (module == root or module.startswith(f"{root}."))

    def parse(self, module: str, path: Optional[Path] = None) -> Optional[_ParsedModule]:
        """Parse *module* once; a file that cannot be read is a ``module not audited`` gap, printed when first met.

        The gap is printed where the failure happens, so a module reached only through another module's import (never
        walked itself under ``--no-recursive`` or ``--exclude``) still reports why its names were not audited.

        """
        if module not in self._parsed:
            roots = [portion.parent for portion in self.source.portions]
            roots = roots or ([self.source.import_root] if self.source.import_root is not None else [])
            path = path if path is not None else _module_source_file(module, roots) if self.in_tree(module) else None
            if path is None:
                namespace_root = bool(self.source.portions) and module == self.source.name
                if self.in_tree(module) and not namespace_root:  # a namespace package has no source of its own
                    self.missing(module)
                return None  # nothing to cache: a later call may name the file
            parsed: Optional[_ParsedModule] = None
            try:
                parsed = _ParsedModule(module, path, ast.parse(path.read_bytes(), filename=str(path)))
            except (OSError, SyntaxError, ValueError, RecursionError, MemoryError) as err:
                self._errors[module] = f"could not read its source: {err!r}"
                self._report(module)
            self._parsed[module] = parsed
        return self._parsed[module]

    def missing(self, module: str) -> None:
        """Report a module of the scanned tree that has no Python source to read, once."""
        self._errors.setdefault(module, "no Python source to read")
        self._report(module)

    def _report(self, module: str) -> None:
        """Print the ``module not audited`` gap of a module that could not be read or analyzed, once."""
        if module in self._errors and module not in self._reported:
            self._reported.add(module)
            _print_gap(_GapKind.MODULE_NOT_AUDITED, f"{module}: {self._errors[module]}")

    def aliases(self, parsed: _ParsedModule) -> _PyDeprecateNames:
        """Return the pyDeprecate names *parsed* binds, following in-tree modules that re-export them."""
        if parsed.name in self._aliases:
            return self._aliases[parsed.name]
        self._aliases[parsed.name] = _PyDeprecateNames()  # an import cycle back here re-exports nothing
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
                bound = _PyDeprecateNames()
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
        # Namespace writes can replace an imported factory without a syntactic name assignment.
        rebinds = _globals_rebinds(parsed.tree)
        global_names = {name for node in ast.walk(parsed.tree) if isinstance(node, ast.Global) for name in node.names}
        for name in set(aliases.names) | set(aliases.modules):
            if rebinds is None or name in rebinds or name in global_names:
                kind = aliases.names.pop(name, None)
                aliases.modules.pop(name, None)
                aliases.ambiguous[name] = kind
        aliases.settle()
        self._aliases[parsed.name] = aliases
        return aliases

    def _analyze(self, parsed: _ParsedModule, *, isolated: bool) -> Optional[_ModuleFacts]:
        """Run the analyzer on *parsed*; isolated, it asks no other module, for the table."""
        name_kind = (lambda _module, _name: ("other", False)) if isolated else self.name_kind
        configured = (lambda _module, _name: None) if isolated else self.configured
        try:
            # a ``target`` read follows imports through syntax and aliases only, never another module's analysis, so
            # the isolated table may follow them as well
            aliases = self.aliases(parsed)
            return _ModuleAnalyzer(parsed, aliases, self.in_tree, name_kind, configured, self.imported_target).run()
        except (RecursionError, MemoryError) as err:
            self._errors.setdefault(parsed.name, f"its source is nested too deeply to analyze: {err!r}")
            self._report(parsed.name)
            return None

    def table(self, module: str) -> Optional[_ModuleFacts]:
        """Return *module*'s isolated analysis: what its names hold without following any other module."""
        if module not in self._tables:
            parsed = self.parse(module)
            self._tables[module] = self._analyze(parsed, isolated=True) if parsed is not None else None
        return self._tables[module]

    def facts(self, module: str, path: Optional[Path] = None) -> Optional[_ModuleFacts]:
        """Analyze *module* once, or return ``None`` when its source cannot be read or analyzed."""
        parsed = self.parse(module, path)
        if module not in self._facts:
            self._in_progress.add(module)
            facts = self._analyze(parsed, isolated=False) if parsed is not None else None
            if facts is not None:
                self._expand_stars(module, facts)
            self._in_progress.discard(module)
            self._facts[module] = facts
        return self._facts[module]

    def unwalked_gaps(self, walked: Collection[str]) -> list[str]:
        """Return the gap details of every analyzed module outside *walked*, in module order, each module once.

        A module the walk skips (``--no-recursive``, ``--exclude``) is still analyzed when a walked module re-exports a
        name from it or star-imports it, and what that analysis could not place is the only sign the importer's view is
        incomplete.

        """
        outside = (self._facts[module] for module in sorted(set(self._facts) - set(walked)))
        return [detail for facts in outside if facts is not None for detail in facts.gaps]

    def _expand_stars(self, module: str, facts: _ModuleFacts) -> None:
        """Bind the names each in-tree ``from X import *`` of *module* copies, as listed by ``X.__all__``.

        Bindings follow statement order, as at runtime: a star import replaces what an earlier statement bound, a later
        star import replaces it in turn, and a binding after the star import keeps its own value.

        """
        for origin, position in facts.star_sources:
            star = self.facts(origin) if origin not in self._in_progress else None
            parsed = self.parse(origin)
            exports = _literal_all(parsed.tree) if parsed is not None else None
            if origin in self._in_progress or isinstance(exports, str):
                reason = "is part of an import cycle" if origin in self._in_progress else exports
                facts.gaps.append(f"{module}: `from {origin} import *`: `{origin}` {reason}; its names are not audited")
                continue
            for name in star.bindings if star is not None else ():
                listed = name in exports if exports is not None else _is_public(name)
                if listed and facts.positions.get(name, -1) < position:
                    facts.bindings[name] = _ImportRef(origin, name)
                    facts.positions[name] = position

    def configured(self, module: str, name: str, seen: frozenset[tuple[str, str]] = frozenset()) -> _Lookup:
        """Return the configured factory an in-tree module binds *name* to, following its re-exports to the end.

        A star import after the module's own binding of *name* replaces it, the latest one first, as at runtime.

        """
        if (module, name) in seen:
            return _CYCLE
        seen |= {(module, name)}
        table = self.table(module)
        if table is None:
            return None
        local = table.positions.get(name, -1)
        for origin, position in reversed(table.star_sources):
            if position < local or not self._star_lists(origin, name):
                continue
            found = self.configured(origin, name, seen)
            star = self.table(origin)
            if found is not None or (star is not None and name in star.bindings):
                return found  # this star import binds the name, to a configured factory or to something else
        if name in table.configured:
            return table.configured[name]
        value = table.bindings.get(name)
        return self.configured(value.module, value.name, seen) if isinstance(value, _ImportRef) else None

    def _star_lists(self, origin: str, name: str) -> bool:
        """Tell whether ``from origin import *`` may copy *name*: listed by a literal ``__all__``, else public."""
        parsed = self.parse(origin)
        exports = _literal_all(parsed.tree) if parsed is not None else None
        return (exports is None and _is_public(name)) or (isinstance(exports, set) and name in exports)

    def name_kind(self, module: str, name: str, seen: frozenset[tuple[str, str]] = frozenset()) -> tuple[str, bool]:
        """Return ``def``, ``class`` or ``other`` for what *module* binds *name* to, and whether it is a dataclass.

        In-tree imports are followed to the end; a re-export cycle names nothing. A class's decorators are resolved in
        the module that defines it.

        """
        parsed = self.parse(module) if (module, name) not in seen else None
        if parsed is None:
            return "other", False
        scope = _scope_events(parsed.tree.body)
        event = _single(scope, name)
        if event is None:
            return "other", False
        if event.kind in ("def", "class"):
            qualify = functools.partial(_qualified_name, parsed, scope)
            return event.kind, isinstance(event.value, ast.ClassDef) and _is_dataclass(event.value, qualify)
        if isinstance(event.value, ast.ImportFrom):
            origin = _absolute_import(module, parsed.is_package, event.value)
            source = next((alias.name for alias in event.value.names if (alias.asname or alias.name) == name), "")
            if self.in_tree(origin) and origin != module:
                return self.name_kind(origin, source, seen | {(module, name)})
        return "other", False

    def imported_target(
        self, module: str, name: str, seen: frozenset[tuple[str, str]] = frozenset()
    ) -> Optional[_SettingValue]:
        """Classify what an in-tree module binds *name* to as a ``target``; ``None`` when its source is unread.

        Only the module's syntax and pyDeprecate aliases are read, never another module's analysis, so a module being
        analyzed (its isolated table included) may ask. In-tree re-exports are followed; a re-export cycle is a gap.

        """
        if (module, name) in seen:
            return _SettingValue(("expr", name), f"`target` is `{name}`, which {_CYCLE}")
        parsed = self.parse(module)
        if parsed is None:
            return None
        try:
            if module not in self._scopes:
                self._scopes[module] = _module_scope(parsed)
            imported = functools.partial(self.imported_target, seen=seen | {(module, name)})
            targets = _TargetClassifier(parsed, self.aliases(parsed), self._scopes[module], self.in_tree, imported)
            return targets.classify(ast.Name(id=name, ctx=ast.Load()), {}, depth=0)
        except (RecursionError, MemoryError):  # a value nested too deeply to unparse settles nothing
            return _SettingValue(("expr", name), f"`target` is `{name}`, whose source is nested too deeply to analyze")

    def resolve(self, value: _Value) -> tuple[_Value, bool]:
        """Follow in-tree import references to the wrapper or class they name; also tell whether they form a cycle."""
        seen: set[_ImportRef] = set()
        while isinstance(value, _ImportRef):
            if value in seen:
                return None, True
            seen.add(value)
            facts = self.facts(value.module)
            value = facts.bindings.get(value.name) if facts is not None else None
        return value, False

    def class_members(self, info: _PlainClass, unaudited: list[str]) -> dict[str, _SourceWrapper]:
        """Return a class's deprecated members, those inherited from bases in the scanned tree included.

        Each member resolves through the method resolution order, so a plain member of an earlier base hides a
        deprecated one of a later base, as at runtime. A base the source does not show to be a class of the tree is
        added to *unaudited* unless it comes from the standard library, which holds no deprecated member.

        """
        merged: dict[str, Optional[_SourceWrapper]] = {}
        for cls in self._mro(info, unaudited, depth=0):
            for attr, wrapper in cls.members.items():
                merged.setdefault(attr, wrapper)
        return {attr: wrapper for attr, wrapper in merged.items() if wrapper is not None}

    def _mro(self, info: _PlainClass, unaudited: list[str], depth: int) -> list[_PlainClass]:
        """Return the C3 linearization of *info* over the bases the reader resolves, noting the others."""
        bases: list[_PlainClass] = []
        for base in info.bases:
            resolved = self._base(info.module, base)
            if isinstance(resolved, _PlainClass) and depth < _BASE_DEPTH:
                bases.append(resolved)
            elif isinstance(resolved, _PlainClass):
                unaudited.append(f"`{ast.unparse(base)}` (base classes nest more than {_BASE_DEPTH} levels)")
            elif resolved is not None:
                unaudited.append(f"`{ast.unparse(base)}` ({resolved})")
        return _c3_merge(info, [self._mro(base, unaudited, depth + 1) for base in bases] + [bases])

    def _base(self, module: str, base: ast.expr) -> Union[_PlainClass, str, None]:
        """Return the in-tree class a base expression names, ``None`` for a memberless one, else why it is unread."""
        base, root = _base_expression(base)
        facts = self.facts(module)
        parsed = self.parse(module)
        if not isinstance(root, ast.Name) or facts is None or parsed is None:
            return "not a name the source settles"
        scope = _module_scope(parsed)
        if scope.unsettled or len(scope.events.get(root.id, [])) > 1:
            return "not a class the source settles after rebinding"
        if isinstance(base, ast.Name):
            resolved, _ = self.resolve(facts.bindings.get(base.id))
            if isinstance(resolved, _PlainClass):
                return resolved
        event = _single(scope.events, root.id)
        if event is None:
            unbound = root.id not in scope.events
            return None if unbound and hasattr(builtins, root.id) else "not a class the source defines"
        if event.kind != "import":
            return "not a class the scanned source defines"
        origin = (
            _absolute_import(module, parsed.is_package, event.value)
            if isinstance(event.value, ast.ImportFrom)
            else root.id
        )
        resolved = self._qualified_base(base, event, origin, root.id)
        if resolved is not None:
            return resolved
        if self.in_tree(origin):
            return "not a class the scanned source defines"
        if _is_memberless_module(origin):
            return None
        return f"imported from `{origin}`, whose source is not read"

    def _qualified_base(self, base: ast.expr, event: _NameBinding, origin: str, root: str) -> Optional[_PlainClass]:
        """Resolve a module-qualified base only when its imported module is in the scanned tree."""
        if not isinstance(base, ast.Attribute) or not isinstance(base.value, ast.Name):
            return None
        imported_module = None
        if isinstance(event.value, ast.ImportFrom):
            alias = next((a for a in event.value.names if (a.asname or a.name) == root), None)
            if alias is not None:
                imported_module = f"{origin}.{alias.name}"
        elif isinstance(event.value, ast.Import):
            alias = next((a for a in event.value.names if (a.asname or a.name.split(".")[0]) == root), None)
            if alias is not None:
                imported_module = alias.name if alias.asname else root
        if not imported_module or not self.in_tree(imported_module):
            return None
        facts = self.facts(imported_module)
        if facts is None:
            return None
        resolved, _ = self.resolve(facts.bindings.get(base.attr))
        return resolved if isinstance(resolved, _PlainClass) else None


def _base_expression(base: ast.expr) -> tuple[ast.expr, ast.expr]:
    """Unwrap generic subscripts and find the root name of a base expression."""
    while isinstance(base, ast.Subscript):  # ``Base[T]`` subclasses ``Base``
        base = base.value
    root = base
    while isinstance(root, ast.Attribute):
        root = root.value
    return base, root


def _is_public(name: str) -> bool:
    """Whether a star import without ``__all__`` copies *name*."""
    return not name.startswith("_")


def _c3_merge(head: _PlainClass, sequences: list[list[_PlainClass]]) -> list[_PlainClass]:
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
