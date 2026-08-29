from __future__ import annotations

import ast
import itertools
from collections.abc import Mapping
from dataclasses import dataclass

type EventContractPair = tuple[str, int]
type EnumFingerprint = tuple[tuple[str, str], ...]
type SourcePosition = tuple[int, int]


class StaticContractAnalysisError(AssertionError):
    """生产合同的有限域无法被确定性证明。"""


@dataclass(frozen=True, slots=True)
class EventProducerAnalysis:
    sink_pairs: dict[str, frozenset[EventContractPair]]
    helper_caller_pairs: dict[str, frozenset[EventContractPair]]
    enum_dependencies: dict[str, EnumFingerprint]

    @property
    def all_event_versions(self) -> set[EventContractPair]:
        return set().union(*self.sink_pairs.values()) if self.sink_pairs else set()


@dataclass(frozen=True, slots=True)
class _EnumAtom:
    enum_key: str
    member: str
    value: str


@dataclass(frozen=True, slots=True)
class _EnumDefinition:
    key: str
    name: str
    members: tuple[_EnumAtom, ...]


@dataclass(frozen=True, slots=True)
class _FunctionInfo:
    relative_path: str
    qualified_name: str
    class_name: str | None
    node: ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True, slots=True)
class _EventDraftBinding:
    relative_path: str
    local_name: str
    class_node: ast.ClassDef


@dataclass(frozen=True, slots=True)
class _ReachingDefinition:
    value: ast.expr
    position: SourcePosition
    controls: tuple[ast.expr, ...] = ()


@dataclass(frozen=True, slots=True)
class _DefinitionFlow:
    definitions: tuple[_ReachingDefinition, ...] = ()
    unbound: bool = True
    reachable: bool = True


@dataclass(slots=True)
class _AnalysisState:
    functions: tuple[_FunctionInfo, ...]
    enum_bindings: dict[str, dict[str, _EnumDefinition]]
    event_helper_names: dict[str, frozenset[str]]
    max_domain: int
    max_helper_depth: int
    used_enums: set[str]


def analyze_event_producers(
    sources: Mapping[str, str],
    *,
    max_domain: int = 64,
    max_helper_depth: int = 8,
) -> EventProducerAnalysis:
    if max_domain < 1:
        raise ValueError("max_domain must be positive")
    if max_helper_depth < 1:
        raise ValueError("max_helper_depth must be positive")
    trees = {
        relative_path: _parse_source(relative_path, source)
        for relative_path, source in sorted(sources.items())
    }
    event_draft = _resolve_event_draft_binding(trees)
    default_schema_version = _event_draft_default_version(event_draft)
    _reject_event_draft_mutations(trees, event_draft)
    enums = _collect_enums(trees)
    enum_bindings = {
        relative_path: _enum_import_bindings(tree, enums, relative_path)
        for relative_path, tree in trees.items()
    }
    functions = tuple(_collect_functions(trees))
    _reject_unscoped_event_draft_calls(trees, functions, event_draft)
    event_helper_names = _event_helper_names(functions, trees, event_draft)
    state = _AnalysisState(
        functions=functions,
        enum_bindings=enum_bindings,
        event_helper_names=event_helper_names,
        max_domain=max_domain,
        max_helper_depth=max_helper_depth,
        used_enums=set(),
    )

    sink_pairs: dict[str, frozenset[EventContractPair]] = {}
    helper_caller_pairs: dict[str, frozenset[EventContractPair]] = {}
    for function in functions:
        event_calls = _event_draft_calls(function, trees, event_draft)
        _reject_indirect_helper_references(function, state)
        for ordinal, call in enumerate(event_calls, start=1):
            sink_key = f"{function.relative_path}::{function.qualified_name}#EventDraft[{ordinal}]"
            name_node = _keyword(call, "name")
            if name_node is None:
                raise StaticContractAnalysisError(f"{sink_key}: EventDraft.name must be explicit")
            version_node = _keyword(call, "schema_version")
            version = (
                default_schema_version
                if version_node is None
                else _literal_schema_version(version_node, sink_key)
            )
            try:
                names = _evaluate_strings(
                    name_node,
                    function=function,
                    state=state,
                    bindings={},
                    before_position=_node_position(call),
                    trail=(sink_key,),
                )
            except StaticContractAnalysisError as direct_error:
                if not isinstance(name_node, ast.Name) or not _is_parameter(
                    function.node, name_node.id
                ):
                    raise direct_error
                names, callers = _resolve_helper_parameter(
                    helper=function,
                    parameter=name_node.id,
                    version=version,
                    state=state,
                    trail=(sink_key,),
                    helper_stack=((function.relative_path, function.qualified_name),),
                    helper_depth=1,
                )
                overlap = set(helper_caller_pairs).intersection(callers)
                if overlap:
                    raise StaticContractAnalysisError(
                        f"{sink_key}: duplicate reviewed helper callers: {sorted(overlap)}"
                    ) from direct_error
                helper_caller_pairs.update(callers)
            sink_pairs[sink_key] = _pairs(names, version, sink_key)

    enum_definitions = {definition.key: definition for definition in enums.values()}
    enum_dependencies = {
        key: tuple((member.member, member.value) for member in enum_definitions[key].members)
        for key in sorted(state.used_enums)
    }
    return EventProducerAnalysis(
        sink_pairs=sink_pairs,
        helper_caller_pairs=helper_caller_pairs,
        enum_dependencies=enum_dependencies,
    )


def _parse_source(relative_path: str, source: str) -> ast.Module:
    try:
        return ast.parse(source, filename=relative_path)
    except SyntaxError as exc:
        raise StaticContractAnalysisError(f"Cannot parse {relative_path}: {exc}") from exc


def _resolve_event_draft_binding(trees: Mapping[str, ast.Module]) -> _EventDraftBinding:
    candidates: list[_EventDraftBinding] = []
    for relative_path, tree in trees.items():
        if not relative_path.endswith("persistence/events.py"):
            continue
        for statement in tree.body:
            if isinstance(statement, ast.ClassDef) and statement.name == "EventDraft":
                candidates.append(_EventDraftBinding(relative_path, statement.name, statement))
    if len(candidates) != 1:
        raise StaticContractAnalysisError(
            "Exactly one obsion.persistence.events.EventDraft definition must be present"
        )
    return candidates[0]


def _event_draft_default_version(binding: _EventDraftBinding) -> int:
    defaults: list[ast.expr | None] = []
    for statement in binding.class_node.body:
        if (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "schema_version"
        ):
            defaults.append(statement.value)
    if len(defaults) != 1 or defaults[0] is None:
        raise StaticContractAnalysisError(
            f"{binding.relative_path}: EventDraft.schema_version must have one literal default"
        )
    value = defaults[0]
    assert value is not None
    return _literal_schema_version(
        value,
        f"{binding.relative_path}::EventDraft.schema_version",
    )


def _reject_event_draft_mutations(
    trees: Mapping[str, ast.Module], binding: _EventDraftBinding
) -> None:
    canonical_module = _module_name(binding.relative_path)
    for relative_path, tree in trees.items():
        local_name = _event_draft_import_name(tree, binding, relative_path)
        if not local_name:
            continue
        for node in ast.walk(tree):
            target: ast.expr | None = None
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                target = next(
                    (
                        candidate
                        for candidate in targets
                        if isinstance(candidate, ast.Attribute)
                        and isinstance(candidate.value, ast.Name)
                        and candidate.value.id == local_name
                    ),
                    None,
                )
            elif isinstance(node, ast.Delete):
                target = next(
                    (
                        candidate
                        for candidate in node.targets
                        if isinstance(candidate, ast.Attribute)
                        and isinstance(candidate.value, ast.Name)
                        and candidate.value.id == local_name
                    ),
                    None,
                )
            if target is not None:
                raise StaticContractAnalysisError(
                    f"{relative_path}: {canonical_module}.EventDraft must not be mutated "
                    f"at line {getattr(node, 'lineno', '?')}"
                )


def _reject_unscoped_event_draft_calls(
    trees: Mapping[str, ast.Module],
    functions: tuple[_FunctionInfo, ...],
    binding: _EventDraftBinding,
) -> None:
    scoped_call_ids = {
        id(call) for function in functions for call in _calls_in_function(function.node)
    }
    canonical_module = _module_name(binding.relative_path)
    for relative_path, tree in trees.items():
        imported_name = _event_draft_import_name(tree, binding, relative_path)
        if not imported_name:
            continue
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            if (
                isinstance(call.func, ast.Name)
                and call.func.id == imported_name
                and id(call) not in scoped_call_ids
            ):
                raise StaticContractAnalysisError(
                    f"{relative_path}: {canonical_module}.EventDraft call at line "
                    f"{call.lineno} is outside a reviewed function scope"
                )


def _event_helper_names(
    functions: tuple[_FunctionInfo, ...],
    trees: Mapping[str, ast.Module],
    binding: _EventDraftBinding,
) -> dict[str, frozenset[str]]:
    result: dict[str, set[str]] = {}
    for function in functions:
        if function.class_name is None:
            continue
        if _event_draft_calls(function, trees, binding):
            result.setdefault(function.class_name, set()).add(function.node.name)
    changed = True
    while changed:
        changed = False
        for function in functions:
            if function.class_name is None or function.node.name in result.get(
                function.class_name, set()
            ):
                continue
            known = result.get(function.class_name, set())
            if any(
                _is_self_method_call(call, method)
                for method in known
                for call in _calls_in_function(function.node)
            ):
                result.setdefault(function.class_name, set()).add(function.node.name)
                changed = True
    return {class_name: frozenset(names) for class_name, names in result.items()}


def _event_draft_calls(
    function: _FunctionInfo,
    trees: Mapping[str, ast.Module],
    binding: _EventDraftBinding,
) -> list[ast.Call]:
    tree = trees[function.relative_path]
    imported_name = _event_draft_import_name(tree, binding, function.relative_path)
    if not imported_name:
        return []
    calls = _calls_in_function(function.node)
    direct_calls = [
        call for call in calls if isinstance(call.func, ast.Name) and call.func.id == imported_name
    ]
    references = [
        node
        for node in _runtime_nodes_in_function(function.node)
        if isinstance(node, ast.Name)
        and node.id == imported_name
        and isinstance(node.ctx, ast.Load)
    ]
    direct_references = {id(call.func) for call in direct_calls}
    escaped = [node for node in references if id(node) not in direct_references]
    if escaped:
        first = min(escaped, key=_node_position)
        raise StaticContractAnalysisError(
            f"{function.relative_path}::{function.qualified_name}: canonical EventDraft "
            f"reference escapes a direct constructor call at line {first.lineno}"
        )
    if not direct_calls:
        return []
    shadow = _function_binding(function.node, imported_name)
    if shadow is not None:
        raise StaticContractAnalysisError(
            f"{function.relative_path}::{function.qualified_name}: "
            f"EventDraft import is shadowed at line {getattr(shadow, 'lineno', '?')}"
        )
    return direct_calls


def _event_draft_import_name(
    tree: ast.Module,
    binding: _EventDraftBinding,
    relative_path: str,
) -> str:
    imports: list[str] = []
    canonical_module = _module_name(binding.relative_path)
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom):
            if any(alias.name == "*" for alias in statement.names):
                raise StaticContractAnalysisError(
                    f"{relative_path}: star imports are not allowed in Event producer analysis"
                )
            if (
                statement.level == 0
                and statement.module == canonical_module.rpartition(".")[0]
                and any(
                    alias.name == canonical_module.rpartition(".")[2] for alias in statement.names
                )
            ):
                raise StaticContractAnalysisError(
                    f"{relative_path}: package-qualified EventDraft calls are not supported"
                )
            if statement.level != 0 or statement.module != canonical_module:
                continue
            imports.extend(
                alias.asname or alias.name
                for alias in statement.names
                if alias.name == binding.local_name
            )
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.name == canonical_module:
                    raise StaticContractAnalysisError(
                        f"{relative_path}: module-qualified EventDraft calls are not supported"
                    )
    if relative_path == binding.relative_path:
        if imports:
            raise StaticContractAnalysisError(f"{relative_path}: EventDraft cannot import itself")
        return binding.local_name
    if not imports:
        if any(
            isinstance(call.func, ast.Name) and call.func.id == binding.local_name
            for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call))
        ):
            raise StaticContractAnalysisError(
                f"{relative_path}: EventDraft call is not bound to {canonical_module}"
            )
        return ""
    if len(imports) != 1:
        raise StaticContractAnalysisError(
            f"{relative_path}: EventDraft must have one unambiguous canonical import"
        )
    imported_name = imports[0]
    shadow = _module_binding_after_import(tree, imported_name, canonical_module)
    if shadow is not None:
        raise StaticContractAnalysisError(
            f"{relative_path}: EventDraft import is shadowed at line "
            f"{getattr(shadow, 'lineno', '?')}"
        )
    return imported_name


def _module_binding_after_import(
    tree: ast.Module,
    name: str,
    canonical_module: str,
) -> ast.AST | None:
    found_canonical = False
    for statement in tree.body:
        if (
            isinstance(statement, ast.ImportFrom)
            and statement.level == 0
            and statement.module == canonical_module
            and any((alias.asname or alias.name) == name for alias in statement.names)
        ):
            found_canonical = True
            continue
        if found_canonical and _statement_binds_name(statement, name):
            return statement
    return None


def _statement_binds_name(statement: ast.stmt, name: str) -> bool:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return statement.name == name
    if isinstance(statement, ast.Assign):
        return any(_target_binds_name(target, name) for target in statement.targets)
    if isinstance(statement, ast.AnnAssign):
        return _target_binds_name(statement.target, name)
    if isinstance(statement, ast.AugAssign):
        return _target_binds_name(statement.target, name)
    if isinstance(statement, (ast.Import, ast.ImportFrom)):
        return any((alias.asname or alias.name.split(".")[0]) == name for alias in statement.names)
    return False


def _target_binds_name(target: ast.expr, name: str) -> bool:
    if isinstance(target, ast.Name):
        return target.id == name
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_target_binds_name(element, name) for element in target.elts)
    return False


def _function_binding(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
) -> ast.AST | None:
    if not name:
        return None
    parameters = (
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    )
    parameter = next((argument for argument in parameters if argument.arg == name), None)
    if parameter is not None:
        return parameter
    collector = _NameBindingCollector(function, name)
    collector.visit(function)
    return collector.bindings[0] if collector.bindings else None


class _NameBindingCollector(ast.NodeVisitor):
    def __init__(self, root: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> None:
        self.root = root
        self.name = name
        self.bindings: list[ast.AST] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is self.root:
            for statement in node.body:
                self.visit(statement)
        elif node.name == self.name:
            self.bindings.append(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node is self.root:
            for statement in node.body:
                self.visit(statement)
        elif node.name == self.name:
            self.bindings.append(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name == self.name:
            self.bindings.append(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == self.name and isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bindings.append(node)

    def visit_Import(self, node: ast.Import) -> None:
        if any((alias.asname or alias.name.split(".")[0]) == self.name for alias in node.names):
            self.bindings.append(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if any((alias.asname or alias.name) == self.name for alias in node.names):
            self.bindings.append(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name == self.name:
            self.bindings.append(node)
        for statement in node.body:
            self.visit(statement)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name == self.name:
            self.bindings.append(node)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name == self.name:
            self.bindings.append(node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest == self.name:
            self.bindings.append(node)
        self.generic_visit(node)


def _module_name(relative_path: str) -> str:
    path = relative_path.removesuffix(".py").replace("/", ".")
    return path if path.startswith("obsion.") else f"obsion.{path}"


def _literal_schema_version(node: ast.expr, key: str) -> int:
    if not isinstance(node, ast.Constant) or type(node.value) is not int or node.value <= 0:
        raise StaticContractAnalysisError(
            f"{key}: schema_version must be a positive literal integer"
        )
    return node.value


def _collect_enums(trees: Mapping[str, ast.Module]) -> dict[str, _EnumDefinition]:
    definitions: dict[str, _EnumDefinition] = {}
    for relative_path, tree in trees.items():
        module = _module_name(relative_path)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not any(
                _call_name(base) == "StrEnum" for base in node.bases
            ):
                continue
            atoms: list[_EnumAtom] = []
            enum_key = f"{relative_path}::{node.name}"
            for statement in node.body:
                target: ast.Name | None = None
                value: ast.expr | None = None
                if (
                    isinstance(statement, ast.Assign)
                    and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)
                ):
                    target = statement.targets[0]
                    value = statement.value
                elif isinstance(statement, ast.AnnAssign) and isinstance(
                    statement.target, ast.Name
                ):
                    target = statement.target
                    value = statement.value
                if target is None:
                    continue
                if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                    raise StaticContractAnalysisError(
                        f"{enum_key}.{target.id}: StrEnum members must use literal strings"
                    )
                atoms.append(_EnumAtom(enum_key, target.id, value.value))
            if not atoms:
                raise StaticContractAnalysisError(f"{enum_key}: StrEnum has no literal members")
            qualified_name = f"{module}.{node.name}"
            definitions[qualified_name] = _EnumDefinition(enum_key, node.name, tuple(atoms))
    return definitions


def _enum_import_bindings(
    tree: ast.Module,
    definitions: Mapping[str, _EnumDefinition],
    relative_path: str,
) -> dict[str, _EnumDefinition]:
    module_name = _module_name(relative_path)
    bindings = {
        definition.name: definition
        for qualified_name, definition in definitions.items()
        if qualified_name.rpartition(".")[0] == module_name
    }
    definitions_by_suffix = {
        qualified_name.removeprefix("obsion."): definition
        for qualified_name, definition in definitions.items()
    }
    for statement in tree.body:
        if not isinstance(statement, ast.ImportFrom) or statement.level != 0:
            continue
        module = statement.module or ""
        for alias in statement.names:
            qualified_name = f"{module}.{alias.name}"
            definition = definitions.get(qualified_name) or definitions_by_suffix.get(
                qualified_name
            )
            if definition is None:
                continue
            local_name = alias.asname or alias.name
            if local_name in bindings and bindings[local_name] != definition:
                raise StaticContractAnalysisError(
                    f"{relative_path}: ambiguous StrEnum binding {local_name!r}"
                )
            bindings[local_name] = definition
    return bindings


def _enum_definition(
    function: _FunctionInfo,
    local_name: str,
    state: _AnalysisState,
) -> _EnumDefinition | None:
    return state.enum_bindings.get(function.relative_path, {}).get(local_name)


def _use_enum(definition: _EnumDefinition, state: _AnalysisState) -> None:
    state.used_enums.add(definition.key)


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.class_stack: list[str] = []
        self.function_stack: list[str] = []
        self.functions: list[_FunctionInfo] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified = ".".join((*self.class_stack, *self.function_stack, node.name))
        class_name = ".".join(self.class_stack) or None
        self.functions.append(_FunctionInfo(self.relative_path, qualified, class_name, node))
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()


def _collect_functions(trees: Mapping[str, ast.Module]) -> list[_FunctionInfo]:
    functions: list[_FunctionInfo] = []
    for relative_path, tree in trees.items():
        collector = _FunctionCollector(relative_path)
        collector.visit(tree)
        functions.extend(collector.functions)
    return functions


class _FunctionCallCollector(ast.NodeVisitor):
    def __init__(self, root: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.root = root
        self.calls: list[ast.Call] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is self.root:
            for statement in node.body:
                self.visit(statement)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node is self.root:
            for statement in node.body:
                self.visit(statement)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        del node

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(node)
        self.generic_visit(node)


def _nodes_in_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.AST]:
    collector = _FunctionNodeCollector(node)
    collector.visit(node)
    return collector.nodes


def _runtime_nodes_in_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.AST]:
    collector = _FunctionNodeCollector(node)
    for statement in node.body:
        collector.visit(statement)
    return collector.nodes


class _FunctionNodeCollector(ast.NodeVisitor):
    def __init__(self, root: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.root = root
        self.nodes: list[ast.AST] = []

    def visit(self, node: ast.AST) -> None:
        if node is not self.root:
            self.nodes.append(node)
        super().visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is self.root:
            for statement in node.body:
                self.visit(statement)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node is self.root:
            for statement in node.body:
                self.visit(statement)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        del node


def _calls_in_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    collector = _FunctionCallCollector(node)
    collector.visit(node)
    return sorted(collector.calls, key=lambda call: (call.lineno, call.col_offset))


def _evaluate_strings(
    node: ast.expr,
    *,
    function: _FunctionInfo,
    state: _AnalysisState,
    bindings: Mapping[str, frozenset[object]],
    before_position: SourcePosition,
    trail: tuple[str, ...],
) -> frozenset[str]:
    values = _evaluate(
        node,
        function=function,
        state=state,
        bindings=bindings,
        before_position=before_position,
        trail=trail,
    )
    if not values or any(not isinstance(value, str) for value in values):
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: Event name domain is not a non-empty string set"
        )
    return frozenset(value for value in values if isinstance(value, str))


def _evaluate(
    node: ast.expr,
    *,
    function: _FunctionInfo,
    state: _AnalysisState,
    bindings: Mapping[str, frozenset[object]],
    before_position: SourcePosition,
    trail: tuple[str, ...],
) -> frozenset[object]:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool)):
        return frozenset({node.value})
    if isinstance(node, ast.Name):
        if node.id in bindings:
            return bindings[node.id]
        definition_flow = _reaching_definitions(function.node, node.id, before_position)
        if definition_flow.definitions:
            if definition_flow.unbound:
                raise StaticContractAnalysisError(
                    f"{' -> '.join(trail)}: {node.id!r} may be unbound at EventDraft"
                )
            reaching_domains: list[frozenset[object]] = []
            for reaching_definition in definition_flow.definitions:
                definition_trail = (*trail, node.id)
                _record_control_enums(
                    reaching_definition.controls,
                    function=function,
                    state=state,
                    trail=definition_trail,
                )
                reaching_domains.append(
                    _evaluate(
                        reaching_definition.value,
                        function=function,
                        state=state,
                        bindings=bindings,
                        before_position=reaching_definition.position,
                        trail=definition_trail,
                    )
                )
            return _bounded_union(tuple(reaching_domains), state, trail)
        if _is_parameter(function.node, node.id):
            enum_values = _parameter_enum_domain(function, node.id, before_position, state)
            if enum_values is not None:
                return frozenset(enum_values)
        raise StaticContractAnalysisError(f"{' -> '.join(trail)}: unresolved name {node.id!r}")
    if isinstance(node, ast.IfExp):
        _record_control_enums(
            (node.test,),
            function=function,
            state=state,
            trail=trail,
        )
        return _bounded_union(
            (
                _evaluate(
                    node.body,
                    function=function,
                    state=state,
                    bindings=bindings,
                    before_position=before_position,
                    trail=(*trail, "if-true"),
                ),
                _evaluate(
                    node.orelse,
                    function=function,
                    state=state,
                    bindings=bindings,
                    before_position=before_position,
                    trail=(*trail, "if-false"),
                ),
            ),
            state,
            trail,
        )
    if isinstance(node, ast.Attribute):
        if (
            isinstance(node.value, ast.Name)
            and (enum_definition := _enum_definition(function, node.value.id, state)) is not None
        ):
            _use_enum(enum_definition, state)
            for member in enum_definition.members:
                if member.member == node.attr:
                    return frozenset({member})
            raise StaticContractAnalysisError(
                f"{' -> '.join(trail)}: unknown enum member {node.value.id}.{node.attr}"
            )
        if node.attr == "value":
            values = _evaluate(
                node.value,
                function=function,
                state=state,
                bindings=bindings,
                before_position=before_position,
                trail=(*trail, ".value"),
            )
            if any(not isinstance(value, _EnumAtom) for value in values):
                raise StaticContractAnalysisError(
                    f"{' -> '.join(trail)}: .value is only allowed on a finite StrEnum"
                )
            return frozenset(value.value for value in values if isinstance(value, _EnumAtom))
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: dynamic attribute access is not allowed"
        )
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "lower"
            and not node.args
            and not node.keywords
        ):
            values = _evaluate(
                node.func.value,
                function=function,
                state=state,
                bindings=bindings,
                before_position=before_position,
                trail=(*trail, ".lower()"),
            )
            if any(not isinstance(value, str) for value in values):
                raise StaticContractAnalysisError(
                    f"{' -> '.join(trail)}: lower() requires a finite string domain"
                )
            return frozenset(value.lower() for value in values if isinstance(value, str))
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: function calls are not allowed in Event names"
        )
    if isinstance(node, ast.JoinedStr):
        domains: list[frozenset[str]] = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                domains.append(frozenset({part.value}))
                continue
            if isinstance(part, ast.FormattedValue):
                values = _evaluate(
                    part.value,
                    function=function,
                    state=state,
                    bindings=bindings,
                    before_position=before_position,
                    trail=(*trail, "f-string"),
                )
                rendered: set[str] = set()
                for value in values:
                    if isinstance(value, _EnumAtom):
                        rendered.add(value.value)
                    elif isinstance(value, str):
                        rendered.add(value)
                    else:
                        raise StaticContractAnalysisError(
                            f"{' -> '.join(trail)}: unsupported f-string value"
                        )
                domains.append(frozenset(rendered))
                continue
            raise StaticContractAnalysisError(
                f"{' -> '.join(trail)}: unsupported f-string component"
            )
        product_size = 1
        for domain in domains:
            product_size *= len(domain)
            if product_size > state.max_domain:
                raise StaticContractAnalysisError(
                    f"{' -> '.join(trail)}: Event name domain exceeds {state.max_domain}"
                )
        return frozenset("".join(part for part in parts) for parts in itertools.product(*domains))
    raise StaticContractAnalysisError(
        f"{' -> '.join(trail)}: unsupported Event name expression {ast.dump(node)}"
    )


def _bounded_union(
    domains: tuple[frozenset[object], ...],
    state: _AnalysisState,
    trail: tuple[str, ...],
) -> frozenset[object]:
    result = frozenset().union(*domains)
    if len(result) > state.max_domain:
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: Event name domain exceeds {state.max_domain}"
        )
    return result


def _node_position(node: ast.AST) -> SourcePosition:
    return getattr(node, "lineno", 0), getattr(node, "col_offset", 0)


def _position_before(left: ast.AST, right: SourcePosition) -> bool:
    return _node_position(left) < right


def _reaching_definitions(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    before_position: SourcePosition,
) -> _DefinitionFlow:
    initial = _DefinitionFlow(unbound=not _is_parameter(function, name))
    return _analyze_definition_block(
        function.body,
        name=name,
        before_position=before_position,
        incoming=initial,
        controls=(),
    )


def _analyze_definition_block(
    statements: list[ast.stmt],
    *,
    name: str,
    before_position: SourcePosition,
    incoming: _DefinitionFlow,
    controls: tuple[ast.expr, ...],
) -> _DefinitionFlow:
    flow = incoming
    for statement in statements:
        if not flow.reachable or not _position_before(statement, before_position):
            break
        flow = _transfer_definition_statement(
            statement,
            name=name,
            before_position=before_position,
            incoming=flow,
            controls=controls,
        )
    return flow


def _transfer_definition_statement(
    statement: ast.stmt,
    *,
    name: str,
    before_position: SourcePosition,
    incoming: _DefinitionFlow,
    controls: tuple[ast.expr, ...],
) -> _DefinitionFlow:
    assigned = _simple_assignment(statement, name)
    if assigned is not None:
        return _DefinitionFlow(
            definitions=(_ReachingDefinition(assigned, _node_position(statement), controls),),
            unbound=False,
        )
    if _unsupported_assignment(statement, name):
        raise StaticContractAnalysisError(
            f"{name} uses an unsupported reaching definition at line {statement.lineno}"
        )
    if isinstance(statement, ast.If):
        containing = _containing_block((statement.body, statement.orelse), before_position)
        if containing is not None:
            return _analyze_definition_block(
                containing,
                name=name,
                before_position=before_position,
                incoming=incoming,
                controls=(*controls, statement.test),
            )
        true_flow = _analyze_definition_block(
            statement.body,
            name=name,
            before_position=before_position,
            incoming=incoming,
            controls=(*controls, statement.test),
        )
        false_flow = _analyze_definition_block(
            statement.orelse,
            name=name,
            before_position=before_position,
            incoming=incoming,
            controls=(*controls, statement.test),
        )
        return _join_definition_flows((true_flow, false_flow))
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        if any(
            _target_binds_name(item.optional_vars, name)
            for item in statement.items
            if item.optional_vars
        ):
            raise StaticContractAnalysisError(
                f"{name} uses an unsupported with-as reaching definition at line {statement.lineno}"
            )
        return _analyze_definition_block(
            statement.body,
            name=name,
            before_position=before_position,
            incoming=incoming,
            controls=controls,
        )
    if isinstance(statement, ast.Try):
        for handler in statement.handlers:
            if _block_contains_position(handler.body, before_position):
                if handler.name == name:
                    raise StaticContractAnalysisError(
                        f"{name} uses an unsupported except-as reaching definition at line "
                        f"{handler.lineno}"
                    )
                return _analyze_definition_block(
                    handler.body,
                    name=name,
                    before_position=before_position,
                    incoming=incoming,
                    controls=controls,
                )
        containing = _containing_block(
            (statement.body, statement.orelse, statement.finalbody), before_position
        )
        if containing is not None:
            return _analyze_definition_block(
                containing,
                name=name,
                before_position=before_position,
                incoming=incoming,
                controls=controls,
            )
        try_flow = _analyze_definition_block(
            statement.body,
            name=name,
            before_position=before_position,
            incoming=incoming,
            controls=controls,
        )
        normal_flow = _analyze_definition_block(
            statement.orelse,
            name=name,
            before_position=before_position,
            incoming=try_flow,
            controls=controls,
        )
        handler_flows: list[_DefinitionFlow] = []
        for handler in statement.handlers:
            if handler.name == name:
                raise StaticContractAnalysisError(
                    f"{name} uses an unsupported except-as reaching definition at line "
                    f"{handler.lineno}"
                )
            handler_flows.append(
                _analyze_definition_block(
                    handler.body,
                    name=name,
                    before_position=before_position,
                    incoming=incoming,
                    controls=controls,
                )
            )
        joined = _join_definition_flows((normal_flow, *handler_flows))
        return _analyze_definition_block(
            statement.finalbody,
            name=name,
            before_position=before_position,
            incoming=joined,
            controls=controls,
        )
    if isinstance(statement, ast.Match):
        containing = _containing_block(
            tuple(case.body for case in statement.cases), before_position
        )
        if containing is not None:
            return _analyze_definition_block(
                containing,
                name=name,
                before_position=before_position,
                incoming=incoming,
                controls=controls,
            )
        if any(_pattern_binds_name(case.pattern, name) for case in statement.cases):
            raise StaticContractAnalysisError(
                f"{name} uses an unsupported match binding at line {statement.lineno}"
            )
        case_flows = tuple(
            _analyze_definition_block(
                case.body,
                name=name,
                before_position=before_position,
                incoming=incoming,
                controls=controls,
            )
            for case in statement.cases
        )
        return _join_definition_flows((*case_flows, incoming))
    if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
        containing = _containing_block((statement.body, statement.orelse), before_position)
        if containing is not None:
            return _analyze_definition_block(
                containing,
                name=name,
                before_position=before_position,
                incoming=incoming,
                controls=controls,
            )
        if isinstance(statement, (ast.For, ast.AsyncFor)) and _target_binds_name(
            statement.target, name
        ):
            raise StaticContractAnalysisError(
                f"{name} uses an unsupported loop reaching definition at line {statement.lineno}"
            )
        body = _analyze_definition_block(
            statement.body,
            name=name,
            before_position=before_position,
            incoming=incoming,
            controls=controls,
        )
        after_loop = _analyze_definition_block(
            statement.orelse,
            name=name,
            before_position=before_position,
            incoming=_join_definition_flows((incoming, body)),
            controls=controls,
        )
        return after_loop
    if isinstance(statement, (ast.Return, ast.Raise)):
        return _DefinitionFlow(
            definitions=incoming.definitions,
            unbound=incoming.unbound,
            reachable=False,
        )
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if statement.name == name:
            raise StaticContractAnalysisError(
                f"{name} is shadowed by a local definition at line {statement.lineno}"
            )
        return incoming
    if any(
        isinstance(node, ast.NamedExpr) and _target_binds_name(node.target, name)
        for node in ast.walk(statement)
    ):
        raise StaticContractAnalysisError(
            f"{name} uses an unsupported assignment expression at line {statement.lineno}"
        )
    return incoming


def _containing_block(
    blocks: tuple[list[ast.stmt], ...], before_position: SourcePosition
) -> list[ast.stmt] | None:
    return next(
        (block for block in blocks if _block_contains_position(block, before_position)),
        None,
    )


def _block_contains_position(block: list[ast.stmt], before_position: SourcePosition) -> bool:
    for statement in block:
        end_position = (
            getattr(statement, "end_lineno", statement.lineno),
            getattr(statement, "end_col_offset", statement.col_offset),
        )
        if _node_position(statement) <= before_position <= end_position:
            return True
    return False


def _simple_assignment(statement: ast.stmt, name: str) -> ast.expr | None:
    if isinstance(statement, ast.Assign) and any(
        _target_binds_name(target, name) for target in statement.targets
    ):
        if not all(isinstance(target, ast.Name) for target in statement.targets):
            raise StaticContractAnalysisError(
                f"{name} uses an unsupported destructuring assignment at line {statement.lineno}"
            )
        return statement.value
    if (
        isinstance(statement, ast.AnnAssign)
        and _target_binds_name(statement.target, name)
        and statement.value is not None
    ):
        return statement.value
    return None


def _unsupported_assignment(statement: ast.stmt, name: str) -> bool:
    if isinstance(statement, (ast.AugAssign, ast.Delete)):
        targets = (
            (statement.target,)
            if isinstance(statement, ast.AugAssign)
            else tuple(statement.targets)
        )
        return any(_target_binds_name(target, name) for target in targets)
    if isinstance(statement, (ast.Import, ast.ImportFrom)):
        return any((alias.asname or alias.name.split(".")[0]) == name for alias in statement.names)
    return False


def _join_definition_flows(flows: tuple[_DefinitionFlow, ...]) -> _DefinitionFlow:
    reachable = tuple(flow for flow in flows if flow.reachable)
    if not reachable:
        return _DefinitionFlow(reachable=False)
    seen: set[tuple[int, int, int]] = set()
    definitions: list[_ReachingDefinition] = []
    for flow in reachable:
        for definition in flow.definitions:
            key = (*definition.position, id(definition.value))
            if key not in seen:
                seen.add(key)
                definitions.append(definition)
    return _DefinitionFlow(
        definitions=tuple(definitions),
        unbound=any(flow.unbound for flow in reachable),
    )


def _pattern_binds_name(pattern: ast.pattern, name: str) -> bool:
    return any(isinstance(node, ast.MatchAs) and node.name == name for node in ast.walk(pattern))


def _record_control_enums(
    controls: tuple[ast.expr, ...],
    *,
    function: _FunctionInfo,
    state: _AnalysisState,
    trail: tuple[str, ...],
) -> None:
    for control in controls:
        for node in ast.walk(control):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                definition = _enum_definition(function, node.value.id, state)
                if definition is not None:
                    if not any(member.member == node.attr for member in definition.members):
                        raise StaticContractAnalysisError(
                            f"{' -> '.join(trail)}: unknown enum member "
                            f"{node.value.id}.{node.attr} in Event control"
                        )
                    _use_enum(definition, state)
            elif isinstance(node, ast.Name) and _is_parameter(function.node, node.id):
                annotation = _parameter_annotation(function.node, node.id)
                enum_name = _call_name(annotation) if annotation is not None else ""
                definition = _enum_definition(function, enum_name, state)
                if definition is not None:
                    _use_enum(definition, state)


def _parameter_enum_domain(
    function: _FunctionInfo,
    parameter: str,
    before_position: SourcePosition,
    state: _AnalysisState,
) -> tuple[_EnumAtom, ...] | None:
    annotation = _parameter_annotation(function.node, parameter)
    enum_name = _call_name(annotation) if annotation is not None else ""
    definition = _enum_definition(function, enum_name, state)
    if definition is None:
        return None
    _use_enum(definition, state)
    domain = set(definition.members)
    for statement in function.node.body:
        if not _position_before(statement, before_position) or not isinstance(statement, ast.If):
            continue
        narrowed = _not_in_raise_guard(statement, parameter, function, state)
        if narrowed is not None:
            domain.intersection_update(narrowed)
    if not domain:
        raise StaticContractAnalysisError(
            f"{function.relative_path}::{function.qualified_name}: "
            f"empty enum domain for {parameter}"
        )
    return tuple(member for member in definition.members if member in domain)


def _not_in_raise_guard(
    statement: ast.If,
    parameter: str,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> set[_EnumAtom] | None:
    test = statement.test
    if (
        not isinstance(test, ast.Compare)
        or len(test.ops) != 1
        or not isinstance(test.ops[0], ast.NotIn)
        or len(test.comparators) != 1
        or not isinstance(test.left, ast.Name)
        or test.left.id != parameter
        or not any(isinstance(node, ast.Raise) for node in statement.body)
    ):
        return None
    comparator = test.comparators[0]
    if not isinstance(comparator, (ast.Set, ast.Tuple, ast.List)):
        raise StaticContractAnalysisError(
            f"{function.relative_path}::{function.qualified_name}: enum guard must use literals"
        )
    allowed: set[_EnumAtom] = set()
    for element in comparator.elts:
        values = _evaluate(
            element,
            function=function,
            state=state,
            bindings={},
            before_position=_node_position(statement),
            trail=(f"{function.relative_path}::{function.qualified_name}", "enum-guard"),
        )
        if any(not isinstance(value, _EnumAtom) for value in values):
            raise StaticContractAnalysisError(
                f"{function.relative_path}::{function.qualified_name}: invalid enum guard member"
            )
        allowed.update(value for value in values if isinstance(value, _EnumAtom))
    return allowed


def _resolve_helper_parameter(
    *,
    helper: _FunctionInfo,
    parameter: str,
    version: int,
    state: _AnalysisState,
    trail: tuple[str, ...],
    helper_stack: tuple[tuple[str, str], ...],
    helper_depth: int,
) -> tuple[frozenset[str], dict[str, frozenset[EventContractPair]]]:
    parameter_stores = _parameter_writes(helper.node, parameter)
    if parameter_stores:
        first = min(parameter_stores, key=_node_position)
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: helper parameter {parameter!r} is reassigned "
            f"at line {getattr(first, 'lineno', '?')}"
        )
    if helper.class_name is None:
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: dynamic module helper parameters are not supported"
        )
    if helper_depth > state.max_helper_depth:
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: helper depth exceeds {state.max_helper_depth}"
        )
    callers: dict[str, frozenset[EventContractPair]] = {}
    all_names: set[str] = set()
    for caller in state.functions:
        if caller.class_name != helper.class_name:
            continue
        matching_calls: list[ast.Call] = []
        for call in _calls_in_function(caller.node):
            if _is_self_method_call(call, helper.node.name):
                matching_calls.append(call)
                continue
            if _is_possible_indirect_method_call(call, helper.node.name):
                raise StaticContractAnalysisError(
                    f"{caller.relative_path}::{caller.qualified_name}: Event helper "
                    f"{helper.node.name!r} uses an unsupported receiver at line {call.lineno}"
                )
        for ordinal, call in enumerate(matching_calls, start=1):
            caller_key = (
                f"{caller.relative_path}::{caller.qualified_name}#{helper.node.name}[{ordinal}]"
            )
            argument = _bound_argument(helper.node, call, parameter, caller_key)
            try:
                names = _evaluate_strings(
                    argument,
                    function=caller,
                    state=state,
                    bindings={},
                    before_position=_node_position(call),
                    trail=(*trail, caller_key),
                )
            except StaticContractAnalysisError as caller_error:
                if not isinstance(argument, ast.Name) or not _is_parameter(
                    caller.node, argument.id
                ):
                    raise caller_error
                caller_identity = (caller.relative_path, caller.qualified_name)
                if caller_identity in helper_stack:
                    cycle = " -> ".join(
                        qualified_name for _, qualified_name in (*helper_stack, caller_identity)
                    )
                    raise StaticContractAnalysisError(
                        f"{' -> '.join(trail)}: helper cycle detected: {cycle}"
                    ) from caller_error
                names, nested_callers = _resolve_helper_parameter(
                    helper=caller,
                    parameter=argument.id,
                    version=version,
                    state=state,
                    trail=(*trail, caller_key),
                    helper_stack=(*helper_stack, caller_identity),
                    helper_depth=helper_depth + 1,
                )
                overlap = set(callers).intersection(nested_callers)
                if overlap:
                    raise StaticContractAnalysisError(
                        f"{caller_key}: duplicate transitive helper callers: {sorted(overlap)}"
                    ) from caller_error
                callers.update(nested_callers)
            pairs = _pairs(names, version, caller_key)
            callers[caller_key] = pairs
            all_names.update(names)
    if not callers:
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: dynamic helper has no finite reviewed callers"
        )
    return frozenset(all_names), callers


def _parameter_writes(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    parameter: str,
) -> list[ast.AST]:
    return [
        node
        for node in _nodes_in_function(function)
        if (
            isinstance(node, ast.Name)
            and node.id == parameter
            and isinstance(node.ctx, (ast.Store, ast.Del))
        )
        or (isinstance(node, ast.ExceptHandler) and node.name == parameter)
        or (isinstance(node, ast.alias) and (node.asname or node.name.split(".")[0]) == parameter)
        or (isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == parameter)
        or (isinstance(node, ast.MatchMapping) and node.rest == parameter)
    ]


def _reject_indirect_helper_references(
    function: _FunctionInfo,
    state: _AnalysisState,
) -> None:
    if function.class_name is None:
        return
    helper_names = state.event_helper_names.get(function.class_name, frozenset())
    if not helper_names:
        return
    for node in _runtime_nodes_in_function(function.node):
        if (
            not isinstance(node, ast.Attribute)
            or node.attr not in helper_names
            or not isinstance(node.value, ast.Name)
            or node.value.id not in {"self", "cls"}
            or not isinstance(node.ctx, ast.Load)
        ):
            continue
        parent_calls = [call for call in _calls_in_function(function.node) if call.func is node]
        if not parent_calls:
            raise StaticContractAnalysisError(
                f"{function.relative_path}::{function.qualified_name}: Event helper "
                f"{node.attr!r} escapes a direct call at line {node.lineno}"
            )


def _is_self_method_call(call: ast.Call, method: str) -> bool:
    return bool(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == method
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id in {"self", "cls"}
    )


def _is_possible_indirect_method_call(call: ast.Call, method: str) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr == method


def _bound_argument(
    helper: ast.FunctionDef | ast.AsyncFunctionDef,
    call: ast.Call,
    parameter: str,
    caller_key: str,
) -> ast.expr:
    if any(isinstance(argument, ast.Starred) for argument in call.args) or any(
        keyword.arg is None for keyword in call.keywords
    ):
        raise StaticContractAnalysisError(
            f"{caller_key}: helper callers cannot use *args or **kwargs"
        )
    positional_parameters = [
        *helper.args.posonlyargs,
        *helper.args.args,
    ]
    if positional_parameters and positional_parameters[0].arg in {"self", "cls"}:
        positional_parameters = positional_parameters[1:]
    position = next(
        (index for index, item in enumerate(positional_parameters) if item.arg == parameter),
        None,
    )
    positional = call.args[position] if position is not None and position < len(call.args) else None
    keyword = next((item.value for item in call.keywords if item.arg == parameter), None)
    if positional is not None and keyword is not None:
        raise StaticContractAnalysisError(f"{caller_key}: duplicate helper argument {parameter}")
    argument = positional or keyword
    if argument is None:
        raise StaticContractAnalysisError(f"{caller_key}: missing helper argument {parameter}")
    return argument


def _parameter_annotation(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    parameter: str,
) -> ast.expr | None:
    for argument in (
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    ):
        if argument.arg == parameter:
            return argument.annotation
    return None


def _is_parameter(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    return _parameter_annotation(function, name) is not None or any(
        argument.arg == name
        for argument in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs)
    )


def _pairs(names: frozenset[str], version: int, key: str) -> frozenset[EventContractPair]:
    if not names:
        raise StaticContractAnalysisError(f"{key}: empty Event contract domain")
    return frozenset((name, version) for name in names)


def _call_name(node: ast.expr | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    if any(keyword.arg is None for keyword in call.keywords):
        raise StaticContractAnalysisError(f"EventDraft cannot use **kwargs at line {call.lineno}")
    return next((item.value for item in call.keywords if item.arg == name), None)
