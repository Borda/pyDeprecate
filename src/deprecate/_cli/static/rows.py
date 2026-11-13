"""Turn what the reader found into the rows a static run hands to the checks.

The rows are named and ordered as the import scan names and orders them.

"""

from collections.abc import Sequence
from dataclasses import replace

from deprecate._cli.report import _GapKind, _print_gap
from deprecate._cli.static.consts import _CYCLE
from deprecate._cli.static.discovery import _plain_modules, _StaticSource, _walk_namespace, _walk_package
from deprecate._cli.static.model import _ModuleFacts, _PlainClass, _SourceWrapper, _StaticWrapperInfo
from deprecate._cli.static.reader import _TreeReader
from deprecate.audit import DeprecationWrapperInfo
from deprecate.audit._scan import _is_excluded


def _row(module: str, function: str, wrapper: _SourceWrapper) -> _StaticWrapperInfo:
    """Build the synthetic wrapper info the checks read, named as the import scan names it."""
    config = replace(wrapper.config, name=function.rsplit(".", 1)[-1] if function else module)
    return _StaticWrapperInfo(
        module=module,
        function=function,
        deprecated_info=config,
        api_type=wrapper.api_type,
        unresolved=dict(wrapper.unresolved),
        source_file=wrapper.source_file,
        source_line=wrapper.source_line,
    )


def _module_rows(
    reader: _TreeReader, module: str, facts: _ModuleFacts, *, include_members: bool, attribute: bool, seen: set[int]
) -> list[DeprecationWrapperInfo]:
    """Return one module's rows in import-scan order: the module itself, then its public bindings by name."""
    rows: list[DeprecationWrapperInfo] = []
    if facts.module_wrapper is not None:
        rows.append(_row(module, "", facts.module_wrapper))
    resolved = {
        name: reader.resolve(facts.bindings[name]) for name in sorted(facts.bindings) if not name.startswith("_")
    }
    # a wrapper kept under a second public name is reported under the binding named like itself, as the import scan does
    canonical = {
        id(value)
        for name, (value, _) in resolved.items()
        if isinstance(value, _SourceWrapper) and value.proxy is False and name == value.own_name
    }
    for name, (value, cycle) in resolved.items():
        if cycle:
            _print_gap(_GapKind.STATIC_UNRESOLVED, f"{module}.{name}: {_CYCLE}", subject=(module, name))
        elif isinstance(value, _SourceWrapper):
            if id(value) in canonical and name != value.own_name:
                continue
            rows.extend(_wrapper_rows(module, name, value, attribute=attribute, seen=seen))
        elif isinstance(value, _PlainClass) and include_members and value.module == module:
            unaudited: list[str] = []
            members = reader.class_members(value, unaudited)
            for base in dict.fromkeys(unaudited):
                _print_gap(
                    _GapKind.STATIC_UNRESOLVED, f"{module}.{name}: members inherited from {base} are not audited"
                )
            rows.extend(_row(module, f"{name}.{attr}", members[attr]) for attr in sorted(members))
    return rows


def _wrapper_rows(
    module: str, name: str, value: _SourceWrapper, *, attribute: bool, seen: set[int]
) -> list[DeprecationWrapperInfo]:
    """Return the row of one public binding of a wrapper, or none when another binding or module owns it."""
    foreign = value.module != module and value.module.split(".")[0] == module.split(".")[0]
    if attribute and foreign and value.proxy is None and id(value) not in seen:
        seen.add(id(value))
        _print_gap(
            _GapKind.STATIC_UNRESOLVED,
            f"{module}.{name}: wraps an object imported from `{value.module}` that the source does not show"
            " to be a class or a function, so its row cannot be attributed",
            subject=(module, name),
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
    reader = _TreeReader(source)
    if source.portions:
        modules = list(_walk_namespace(source.portions, source.name, recursive=recursive, exclude=exclude))
    elif source.kind == "package":
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
            reader.missing(module)
            continue
        facts = reader.facts(module, path)
        if facts is None:
            continue
        for detail in facts.gaps:
            _print_gap(_GapKind.STATIC_UNRESOLVED, detail)
        if source.kind == "plain":
            seen = set()  # each top-level module of a plain directory is scanned on its own
        rows.extend(
            _module_rows(reader, module, facts, include_members=include_members, attribute=attribute, seen=seen)
        )
    for detail in reader.unwalked_gaps([module for module, _ in modules]):
        _print_gap(_GapKind.STATIC_UNRESOLVED, detail)
    return [row for row in rows if not _is_excluded(row.module, exclude)]
