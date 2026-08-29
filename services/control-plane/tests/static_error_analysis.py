from __future__ import annotations

import ast
import itertools
from collections.abc import Mapping
from dataclasses import dataclass

from static_contract_analysis import SourcePosition, StaticContractAnalysisError

type ErrorCodeDomain = frozenset[str]
type QualifiedTypeDomain = frozenset[str]


@dataclass(frozen=True, slots=True)
class ErrorProducerAnalysis:
    origin_sinks: dict[str, ErrorCodeDomain]
    forwarding_sinks: dict[str, str]
    helper_caller_codes: dict[str, ErrorCodeDomain]

    @property
    def active_origin_codes(self) -> set[str]:
        return set().union(*self.origin_sinks.values()) if self.origin_sinks else set()


@dataclass(frozen=True, slots=True)
class _CallSink:
    module: str
    symbol: str
    keyword: str
    positional_index: int
    implicit_code: str | None = None


@dataclass(frozen=True, slots=True)
class _ConstructorFieldSink:
    module: str
    symbol: str
    field: str
    positional_index: int
    nullable: bool = True


@dataclass(frozen=True, slots=True)
class _FieldDefinition:
    model: str
    field: str
    nullable: bool
    position: int


@dataclass(frozen=True, slots=True)
class _ClassDefinition:
    relative_path: str
    qualified_name: str
    node: ast.ClassDef


@dataclass(frozen=True, slots=True)
class _FunctionInfo:
    relative_path: str
    qualified_name: str
    class_name: str | None
    node: ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True, slots=True)
class _ReachingDefinition:
    value: ast.expr
    position: SourcePosition


@dataclass(frozen=True, slots=True)
class _DefinitionFlow:
    definitions: tuple[_ReachingDefinition, ...] = ()
    unbound: bool = True
    reachable: bool = True
    terminator: str | None = None


@dataclass(frozen=True, slots=True)
class _ValueDomain:
    origins: ErrorCodeDomain = frozenset()
    forwarding: frozenset[str] = frozenset()
    nullable: bool = False

    def union(self, other: _ValueDomain) -> _ValueDomain:
        return _ValueDomain(
            origins=self.origins | other.origins,
            forwarding=self.forwarding | other.forwarding,
            nullable=self.nullable or other.nullable,
        )


@dataclass(slots=True)
class _AnalysisState:
    functions: tuple[_FunctionInfo, ...]
    class_definitions: dict[str, _ClassDefinition]
    imported_symbols: dict[str, dict[str, str]]
    catalog_codes: frozenset[str]
    call_sinks: dict[str, _CallSink]
    result_sinks: dict[str, _ConstructorFieldSink]
    orm_fields: dict[tuple[str, str], _FieldDefinition]
    module_constants: dict[str, dict[str, ast.expr]]
    max_domain: int
    max_helper_depth: int
    helper_caller_codes: dict[str, ErrorCodeDomain]


_OBSION_ERROR = "obsion.common.errors.ObsionError"
_ERROR_BODY = "obsion.api.schemas.ErrorBody"
_FASTAPI_APPLICATION = "fastapi.FastAPI"
_FORWARDING_CARRIER_FIELDS = frozenset(
    {
        ("obsion.actions.gateway.ActionGatewayResult", "error_code"),
        ("obsion.capabilities.gateway.GatewayResult", "error_code"),
        ("obsion.evaluations.engine.CaseEvaluation", "error_code"),
    }
)


_CALL_SINKS = (
    _CallSink("obsion.common.errors", "ObsionError", "code", 0),
    _CallSink("obsion.common.errors", "ConflictError", "code", 0),
    _CallSink("obsion.common.errors", "AuthorizationError", "code", 0),
    _CallSink("obsion.common.errors", "ValidationError", "code", 0),
    _CallSink(
        "obsion.common.errors",
        "NotFoundError",
        "",
        -1,
        implicit_code="resource_not_found",
    ),
    _CallSink(
        "obsion.common.errors",
        "BudgetExceededError",
        "",
        -1,
        implicit_code="budget_exceeded",
    ),
    _CallSink(
        "obsion.model_gateway.gateway",
        "ModelUnavailableError",
        "",
        -1,
        implicit_code="model_unavailable",
    ),
)
_RESULT_SINKS = (
    _ConstructorFieldSink(
        "obsion.api.schemas",
        "ErrorBody",
        "code",
        0,
        nullable=False,
    ),
    _ConstructorFieldSink(
        "obsion.capabilities.gateway",
        "GatewayResult",
        "error_code",
        5,
    ),
    _ConstructorFieldSink(
        "obsion.actions.gateway",
        "ActionGatewayResult",
        "error_code",
        3,
    ),
    _ConstructorFieldSink(
        "obsion.evaluations.engine",
        "CaseEvaluation",
        "error_code",
        6,
    ),
)
_ORM_FIELD_NAMES = frozenset({"error_code", "last_error_code"})
_ERROR_FIELD_NAMES = frozenset({"code", *_ORM_FIELD_NAMES})
_CANONICAL_ORM_MODULE = "obsion.db.models"
_ERROR_CODE_TYPE = "obsion.db.types.ErrorCodeType"
_MAPPED_COLUMN = "sqlalchemy.orm.mapped_column"


def analyze_error_producers(
    sources: Mapping[str, str],
    *,
    catalog_codes: frozenset[str],
    max_domain: int = 64,
    max_helper_depth: int = 8,
) -> ErrorProducerAnalysis:
    if max_domain < 1:
        raise ValueError("max_domain must be positive")
    if max_helper_depth < 1:
        raise ValueError("max_helper_depth must be positive")
    trees = {
        relative_path: _parse_source(relative_path, source)
        for relative_path, source in sorted(sources.items())
    }
    classes, functions = _collect_definitions(trees)
    imported_symbols = _resolve_imported_symbols(
        trees,
        {
            relative_path: _imported_symbols(tree, relative_path)
            for relative_path, tree in trees.items()
        },
    )
    class_definitions = {
        f"{_module_name(definition.relative_path)}.{definition.qualified_name}": definition
        for definition in classes
    }
    call_sinks = {f"{sink.module}.{sink.symbol}": sink for sink in _CALL_SINKS}
    result_sinks = {f"{sink.module}.{sink.symbol}": sink for sink in _RESULT_SINKS}
    _discover_inherited_error_sinks(
        class_definitions,
        imported_symbols,
        call_sinks,
    )
    _inherit_result_sinks(
        class_definitions,
        imported_symbols,
        result_sinks,
    )
    _require_canonical_definitions(class_definitions, call_sinks, result_sinks)
    _validate_implicit_error_sinks(class_definitions, call_sinks)
    _reject_qualified_sink_imports(trees, call_sinks, result_sinks)
    _reject_local_sink_imports(trees, call_sinks, result_sinks)
    _reject_canonical_sink_aliases(trees, imported_symbols, call_sinks, result_sinks)
    _reject_shadowed_sink_calls(functions, imported_symbols, call_sinks, result_sinks)
    _reject_nested_scope_sink_calls(
        functions,
        imported_symbols,
        call_sinks,
        result_sinks,
    )
    _reject_error_code_type_reference_escapes(trees, imported_symbols)
    orm_fields = _discover_orm_fields(
        classes,
        imported_symbols,
    )
    _inherit_orm_fields(
        class_definitions,
        imported_symbols,
        orm_fields,
    )
    state = _AnalysisState(
        functions=functions,
        class_definitions=class_definitions,
        imported_symbols=imported_symbols,
        catalog_codes=catalog_codes,
        call_sinks=call_sinks,
        result_sinks=result_sinks,
        orm_fields=orm_fields,
        module_constants={
            relative_path: _module_constants(tree) for relative_path, tree in trees.items()
        },
        max_domain=max_domain,
        max_helper_depth=max_helper_depth,
        helper_caller_codes={},
    )
    _reject_nonlocal_and_reflective_module_writes(trees, state)
    _reject_error_field_mutations(trees, state)
    _reject_dynamic_persisted_writes(trees, state)
    origin_sinks: dict[str, ErrorCodeDomain] = {}
    forwarding_sinks: dict[str, str] = {}
    observed_calls: set[int] = set()
    observed_assignments: set[tuple[int, int]] = set()

    for function in functions:
        sink_ordinals: dict[str, int] = {}
        sink_nodes = sorted(
            (*_calls_in_function(function.node), *_assignments_in_function(function.node)),
            key=_node_position,
        )
        for node in sink_nodes:
            if isinstance(node, ast.Call):
                _analyze_constructor_sink(
                    node,
                    function=function,
                    state=state,
                    sink_ordinals=sink_ordinals,
                    origin_sinks=origin_sinks,
                    forwarding_sinks=forwarding_sinks,
                    observed_calls=observed_calls,
                )
                continue
            _analyze_persisted_assignment(
                node,
                function=function,
                state=state,
                sink_ordinals=sink_ordinals,
                origin_sinks=origin_sinks,
                forwarding_sinks=forwarding_sinks,
                observed_assignments=observed_assignments,
            )

    _reject_unreviewed_error_calls(trees, functions, state, observed_calls)
    _reject_unreviewed_persisted_assignments(
        trees,
        functions,
        state,
        observed_assignments,
    )
    return ErrorProducerAnalysis(
        origin_sinks=dict(sorted(origin_sinks.items())),
        forwarding_sinks=dict(sorted(forwarding_sinks.items())),
        helper_caller_codes=dict(sorted(state.helper_caller_codes.items())),
    )


def _analyze_constructor_sink(
    call: ast.Call,
    *,
    function: _FunctionInfo,
    state: _AnalysisState,
    sink_ordinals: dict[str, int],
    origin_sinks: dict[str, ErrorCodeDomain],
    forwarding_sinks: dict[str, str],
    observed_calls: set[int],
) -> None:
    qualified = _resolved_call_symbol(call, function, state)
    call_sink = state.call_sinks.get(qualified)
    result_sink = state.result_sinks.get(qualified)
    model_name = _local_model_name(qualified, state)
    if call_sink is not None:
        sink_ordinals[call_sink.symbol] = sink_ordinals.get(call_sink.symbol, 0) + 1
        key = _sink_key(function, call_sink.symbol, sink_ordinals[call_sink.symbol])
        observed_calls.add(id(call))
        domain = _analyze_call_sink(
            call,
            call_sink,
            function=function,
            state=state,
            sink_key=key,
        )
        _record_sink(key, domain, origin_sinks, forwarding_sinks, state)
        return
    if result_sink is not None:
        sink_ordinals[result_sink.symbol] = sink_ordinals.get(result_sink.symbol, 0) + 1
        key = _sink_key(function, result_sink.symbol, sink_ordinals[result_sink.symbol])
        observed_calls.add(id(call))
        constructor_domain = _analyze_constructor_field(
            call,
            result_sink,
            function=function,
            state=state,
            sink_key=key,
        )
        if constructor_domain is not None:
            _record_sink(
                key,
                constructor_domain,
                origin_sinks,
                forwarding_sinks,
                state,
            )
        return
    if model_name is None:
        return
    fields = {
        field: definition
        for (model, field), definition in state.orm_fields.items()
        if model == model_name
    }
    for field, definition in fields.items():
        value = _constructor_argument(
            call,
            keyword=field,
            positional_index=_class_field_position(model_name, field, state),
            key=f"{function.relative_path}:{call.lineno}:{model_name}.{field}",
            required=False,
        )
        if value is None:
            continue
        sink_name = f"{model_name}.{field}"
        sink_ordinals[sink_name] = sink_ordinals.get(sink_name, 0) + 1
        key = _sink_key(function, sink_name, sink_ordinals[sink_name])
        observed_calls.add(id(call))
        domain = _evaluate_code(
            value,
            function=function,
            state=state,
            before_position=_node_position(value),
            trail=(key,),
            helper_stack=(),
            helper_depth=0,
        )
        _check_nullable(domain, definition.nullable, key)
        _record_sink(key, domain, origin_sinks, forwarding_sinks, state)


def _analyze_persisted_assignment(
    assignment: ast.Assign | ast.AnnAssign,
    *,
    function: _FunctionInfo,
    state: _AnalysisState,
    sink_ordinals: dict[str, int],
    origin_sinks: dict[str, ErrorCodeDomain],
    forwarding_sinks: dict[str, str],
    observed_assignments: set[tuple[int, int]],
) -> None:
    value = assignment.value
    if value is None:
        return
    attributes = _assigned_attributes(assignment)
    for attribute in attributes:
        if attribute.attr not in _ORM_FIELD_NAMES:
            continue
        model = _infer_assignment_model(function, attribute, assignment, state)
        if model is None or (model, attribute.attr) not in state.orm_fields:
            raise StaticContractAnalysisError(
                f"{function.relative_path}::{function.qualified_name}: untyped persisted "
                f"{attribute.attr} assignment at line {assignment.lineno}"
            )
        observed_assignments.add((id(assignment), id(attribute)))
        sink_name = f"{model}.{attribute.attr}"
        sink_ordinals[sink_name] = sink_ordinals.get(sink_name, 0) + 1
        key = _sink_key(function, sink_name, sink_ordinals[sink_name])
        target_value = _assignment_target_value(assignment, attribute)
        domain = _evaluate_code(
            target_value,
            function=function,
            state=state,
            before_position=_node_position(assignment),
            trail=(key,),
            helper_stack=(),
            helper_depth=0,
        )
        _check_nullable(domain, state.orm_fields[(model, attribute.attr)].nullable, key)
        _record_sink(key, domain, origin_sinks, forwarding_sinks, state)


def _parse_source(relative_path: str, source: str) -> ast.Module:
    try:
        return ast.parse(source, filename=relative_path)
    except SyntaxError as exc:
        raise StaticContractAnalysisError(f"Cannot parse {relative_path}: {exc}") from exc


def _module_name(relative_path: str) -> str:
    path = relative_path.removesuffix(".py").replace("/", ".")
    return path if path.startswith("obsion.") else f"obsion.{path}"


class _DefinitionCollector(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.class_stack: list[str] = []
        self.function_stack: list[str] = []
        self.classes: list[_ClassDefinition] = []
        self.functions: list[_FunctionInfo] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = ".".join((*self.class_stack, node.name))
        self.classes.append(_ClassDefinition(self.relative_path, qualified, node))
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


def _collect_definitions(
    trees: Mapping[str, ast.Module],
) -> tuple[tuple[_ClassDefinition, ...], tuple[_FunctionInfo, ...]]:
    classes: list[_ClassDefinition] = []
    functions: list[_FunctionInfo] = []
    for relative_path, tree in trees.items():
        collector = _DefinitionCollector(relative_path)
        collector.visit(tree)
        classes.extend(collector.classes)
        functions.extend(collector.functions)
    return tuple(classes), tuple(functions)


def _imported_symbols(tree: ast.Module, relative_path: str) -> dict[str, str]:
    bindings: dict[str, str] = {}
    local_module = _module_name(relative_path)
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom):
            module = _resolved_import_from_module(statement, relative_path)
            if module is None:
                continue
            for alias in statement.names:
                if alias.name == "*":
                    raise StaticContractAnalysisError(
                        f"{relative_path}: star imports are not allowed in Error producer analysis"
                    )
                local = alias.asname or alias.name
                qualified = f"{module}.{alias.name}"
                if local in bindings and bindings[local] != qualified:
                    raise StaticContractAnalysisError(
                        f"{relative_path}: ambiguous imported symbol {local!r}"
                    )
                bindings[local] = qualified
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                local = alias.asname or alias.name.split(".")[0]
                bindings[local] = alias.name
        elif isinstance(statement, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            bindings.setdefault(statement.name, f"{local_module}.{statement.name}")
    return bindings


def _resolved_import_from_module(
    statement: ast.ImportFrom,
    relative_path: str,
) -> str | None:
    if statement.level == 0:
        return statement.module
    package = _module_name(relative_path)
    if not relative_path.endswith("/__init__.py"):
        package = package.rpartition(".")[0]
    parts = package.split(".") if package else []
    ascend = statement.level - 1
    if ascend >= len(parts):
        raise StaticContractAnalysisError(
            f"{relative_path}:{statement.lineno}: relative Error import escapes its package"
        )
    prefix = ".".join(parts[: len(parts) - ascend])
    return f"{prefix}.{statement.module}" if statement.module else prefix


def _resolve_imported_symbols(
    trees: Mapping[str, ast.Module],
    imported_symbols: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    exports = {
        _module_name(relative_path): bindings
        for relative_path, bindings in imported_symbols.items()
    }
    module_aliases = {
        module.removesuffix(".__init__"): module
        for module in exports
        if module.endswith(".__init__")
    }
    for _ in range(len(exports) + 1):
        changed = False
        for bindings in imported_symbols.values():
            for local, qualified in tuple(bindings.items()):
                resolved = _resolve_reexported_symbol(
                    qualified,
                    exports,
                    module_aliases,
                )
                if resolved != qualified:
                    bindings[local] = resolved
                    changed = True
        if not changed:
            return imported_symbols
    raise StaticContractAnalysisError("Error import/re-export graph did not converge")


def _resolve_reexported_symbol(
    qualified: str,
    exports: Mapping[str, Mapping[str, str]],
    module_aliases: Mapping[str, str],
) -> str:
    module, separator, symbol = qualified.rpartition(".")
    if not separator:
        return qualified
    export_module = module_aliases.get(module, module)
    target = exports.get(export_module, {}).get(symbol)
    return target if target is not None else qualified


def _module_constants(tree: ast.Module) -> dict[str, ast.expr]:
    candidates: dict[str, ast.expr] = {}
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            candidates[statement.targets[0].id] = statement.value
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.value is not None
        ):
            candidates[statement.target.id] = statement.value

    binding_counts = _module_binding_counts(tree)
    globally_mutable = {
        name for node in ast.walk(tree) if isinstance(node, ast.Global) for name in node.names
    }
    return {
        name: value
        for name, value in candidates.items()
        if binding_counts.get(name) == 1 and name not in globally_mutable
    }


def _module_binding_counts(tree: ast.Module) -> dict[str, int]:
    collector = _ModuleBindingCollector()
    for statement in tree.body:
        collector.visit(statement)
    return collector.counts


class _ModuleBindingCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def _bind(self, name: str) -> None:
        self.counts[name] = self.counts.get(name, 0) + 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._bind(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._bind(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._bind(node.name)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node)

    def _visit_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    ) -> None:
        for generator in node.generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._bind(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._bind(alias.asname or alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self._bind(alias.asname or alias.name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name is not None:
            self._bind(node.name)
        for statement in node.body:
            self.visit(statement)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name is not None:
            self._bind(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name is not None:
            self._bind(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest is not None:
            self._bind(node.rest)
        self.generic_visit(node)


def _discover_inherited_error_sinks(
    classes: Mapping[str, _ClassDefinition],
    imported_symbols: Mapping[str, Mapping[str, str]],
    call_sinks: dict[str, _CallSink],
) -> None:
    pending = set(classes) - set(call_sinks) - {_OBSION_ERROR}
    while pending:
        discovered = False
        for qualified in sorted(pending):
            definition = classes[qualified]
            parents = _resolved_class_bases(definition, classes, imported_symbols)
            inherited = [call_sinks[parent] for parent in parents if parent in call_sinks]
            if not inherited:
                continue
            if len(definition.node.bases) != 1:
                raise StaticContractAnalysisError(
                    f"{definition.relative_path}::{definition.qualified_name}: multiple "
                    "inheritance for ObsionError sinks is not supported"
                )
            if "." in definition.qualified_name:
                raise StaticContractAnalysisError(
                    f"{definition.relative_path}:{definition.node.lineno}: nested ObsionError "
                    "subclasses are not supported"
                )
            constructor = _class_constructor(definition)
            if constructor is None:
                signatures = {
                    (sink.keyword, sink.positional_index, sink.implicit_code) for sink in inherited
                }
                if len(signatures) != 1:
                    raise StaticContractAnalysisError(
                        f"{definition.relative_path}::{definition.qualified_name}: inherited "
                        "ObsionError constructor is ambiguous"
                    )
                keyword, positional_index, implicit_code = next(iter(signatures))
            else:
                keyword, positional_index, implicit_code = _derive_error_constructor(
                    definition,
                    constructor,
                )
            module, _, symbol = qualified.rpartition(".")
            call_sinks[qualified] = _CallSink(
                module,
                symbol,
                keyword,
                positional_index,
                implicit_code=implicit_code,
            )
            pending.remove(qualified)
            discovered = True
        if not discovered:
            break


def _resolved_class_bases(
    definition: _ClassDefinition,
    classes: Mapping[str, _ClassDefinition],
    imported_symbols: Mapping[str, Mapping[str, str]],
) -> set[str]:
    bindings = imported_symbols.get(definition.relative_path, {})
    result: set[str] = set()
    for base in definition.node.bases:
        if not isinstance(base, ast.Name):
            continue
        qualified = bindings.get(base.id, "")
        if qualified in classes:
            result.add(qualified)
    return result


def _class_constructor(
    definition: _ClassDefinition,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    constructors = [
        statement
        for statement in definition.node.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        and statement.name == "__init__"
    ]
    if len(constructors) > 1:
        raise StaticContractAnalysisError(
            f"{definition.relative_path}::{definition.qualified_name}: multiple __init__ "
            "definitions are not supported"
        )
    return constructors[0] if constructors else None


def _derive_error_constructor(
    definition: _ClassDefinition,
    constructor: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, int, str | None]:
    calls = [
        node
        for node in _runtime_nodes_in_function(constructor)
        if isinstance(node, ast.Call) and _is_super_init_call(node)
    ]
    if len(calls) != 1:
        raise StaticContractAnalysisError(
            f"{definition.relative_path}::{definition.qualified_name}: an ObsionError "
            "constructor must make exactly one direct super().__init__ call"
        )
    call = calls[0]
    code = _constructor_argument(
        call,
        keyword="code",
        positional_index=0,
        key=f"{definition.relative_path}::{definition.qualified_name}.__init__",
        required=True,
        allow_keyword_unpacking=False,
    )
    assert code is not None
    if isinstance(code, ast.Constant) and isinstance(code.value, str):
        return "", -1, code.value
    if not isinstance(code, ast.Name):
        raise StaticContractAnalysisError(
            f"{definition.relative_path}::{definition.qualified_name}: ObsionError constructor "
            "code must be a literal or a direct constructor parameter"
        )
    positional = [*constructor.args.posonlyargs, *constructor.args.args]
    if positional and positional[0].arg in {"self", "cls"}:
        positional = positional[1:]
    position = next(
        (index for index, parameter in enumerate(positional) if parameter.arg == code.id),
        None,
    )
    keyword_only = any(parameter.arg == code.id for parameter in constructor.args.kwonlyargs)
    if (position is None and not keyword_only) or _parameter_writes(constructor, code.id):
        raise StaticContractAnalysisError(
            f"{definition.relative_path}::{definition.qualified_name}: ObsionError constructor "
            f"code parameter {code.id!r} is not a direct immutable parameter"
        )
    if keyword_only:
        return code.id, -1, None
    assert position is not None
    return code.id, position, None


def _is_super_init_call(call: ast.Call) -> bool:
    return bool(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "__init__"
        and isinstance(call.func.value, ast.Call)
        and isinstance(call.func.value.func, ast.Name)
        and call.func.value.func.id == "super"
        and not call.func.value.args
        and not call.func.value.keywords
    )


def _validate_implicit_error_sinks(
    classes: Mapping[str, _ClassDefinition],
    call_sinks: Mapping[str, _CallSink],
) -> None:
    for qualified, sink in call_sinks.items():
        if sink.implicit_code is None:
            continue
        definition = classes[qualified]
        constructor = _class_constructor(definition)
        if constructor is None:
            continue
        _, _, derived = _derive_error_constructor(definition, constructor)
        if derived != sink.implicit_code:
            raise StaticContractAnalysisError(
                f"{definition.relative_path}::{definition.qualified_name}: implicit Error code "
                f"drifted from {sink.implicit_code!r} to {derived!r}"
            )


def _require_canonical_definitions(
    classes: Mapping[str, _ClassDefinition],
    call_sinks: Mapping[str, _CallSink],
    result_sinks: Mapping[str, _ConstructorFieldSink],
) -> None:
    required = {*call_sinks, *result_sinks}
    missing = sorted(required - classes.keys())
    if missing:
        raise StaticContractAnalysisError(
            f"Canonical Error sink definitions are missing: {missing}"
        )


def _reject_qualified_sink_imports(
    trees: Mapping[str, ast.Module],
    call_sinks: Mapping[str, _CallSink],
    result_sinks: Mapping[str, _ConstructorFieldSink],
) -> None:
    sink_modules = {qualified.rpartition(".")[0] for qualified in (*call_sinks, *result_sinks)}
    for relative_path, tree in trees.items():
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    if alias.name in sink_modules:
                        raise StaticContractAnalysisError(
                            f"{relative_path}:{statement.lineno}: module-qualified Error sink "
                            "imports are not supported"
                        )
                    if any(module.startswith(f"{alias.name}.") for module in sink_modules):
                        raise StaticContractAnalysisError(
                            f"{relative_path}:{statement.lineno}: package-qualified Error sink "
                            "imports are not supported"
                        )
                continue
            if not isinstance(statement, ast.ImportFrom) or statement.level != 0:
                continue
            module = statement.module or ""
            imported_modules = {f"{module}.{alias.name}" for alias in statement.names}
            if imported_modules & sink_modules or any(
                sink_module.startswith(f"{imported_module}.")
                for imported_module in imported_modules
                for sink_module in sink_modules
            ):
                raise StaticContractAnalysisError(
                    f"{relative_path}:{statement.lineno}: package-qualified Error sink "
                    "imports are not supported"
                )


def _reject_local_sink_imports(
    trees: Mapping[str, ast.Module],
    call_sinks: Mapping[str, _CallSink],
    result_sinks: Mapping[str, _ConstructorFieldSink],
) -> None:
    sink_symbols = {*call_sinks, *result_sinks}
    sink_modules = {qualified.rpartition(".")[0] for qualified in sink_symbols}
    for relative_path, tree in trees.items():
        for function in (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            for node in _runtime_nodes_in_function(function):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in sink_modules or any(
                            module.startswith(f"{alias.name}.") for module in sink_modules
                        ):
                            raise StaticContractAnalysisError(
                                f"{relative_path}:{node.lineno}: function-local qualified "
                                "Error sink imports are not supported"
                            )
                    continue
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.level != 0 or node.module is None:
                    raise StaticContractAnalysisError(
                        f"{relative_path}:{node.lineno}: function-local relative Error imports "
                        "are not supported"
                    )
                if any(alias.name == "*" for alias in node.names):
                    raise StaticContractAnalysisError(
                        f"{relative_path}:{node.lineno}: function-local star Error imports "
                        "are not supported"
                    )
                module = node.module
                imported = {f"{module}.{alias.name}" for alias in node.names}
                if (
                    imported & sink_symbols
                    or imported & sink_modules
                    or any(
                        sink_module.startswith(f"{candidate}.")
                        for candidate in imported
                        for sink_module in sink_modules
                    )
                ):
                    raise StaticContractAnalysisError(
                        f"{relative_path}:{node.lineno}: function-local Error sink imports "
                        "are not supported"
                    )


def _reject_nested_scope_sink_calls(
    functions: tuple[_FunctionInfo, ...],
    imported_symbols: Mapping[str, Mapping[str, str]],
    call_sinks: Mapping[str, _CallSink],
    result_sinks: Mapping[str, _ConstructorFieldSink],
) -> None:
    relevant = {*call_sinks, *result_sinks}
    for function in functions:
        bindings = imported_symbols.get(function.relative_path, {})
        for node in ast.walk(function.node):
            if not isinstance(
                node,
                (
                    ast.Lambda,
                    ast.ListComp,
                    ast.SetComp,
                    ast.DictComp,
                    ast.GeneratorExp,
                ),
            ):
                continue
            for call in (
                candidate for candidate in ast.walk(node) if isinstance(candidate, ast.Call)
            ):
                local = _call_name(call.func)
                if bindings.get(local, "") in relevant:
                    scope = "lambdas" if isinstance(node, ast.Lambda) else "comprehensions"
                    raise StaticContractAnalysisError(
                        f"{function.relative_path}:{call.lineno}: Error sinks inside {scope} "
                        "are not supported"
                    )


def _reject_error_field_mutations(
    trees: Mapping[str, ast.Module],
    state: _AnalysisState,
) -> None:
    functions_by_path = _functions_by_path(state.functions)
    for relative_path, tree in trees.items():
        bindings = state.imported_symbols.get(relative_path, {})
        path_functions = functions_by_path.get(relative_path, ())
        for statement in (
            node
            for node in ast.walk(tree)
            if isinstance(
                node,
                (
                    ast.Assign,
                    ast.AnnAssign,
                    ast.AugAssign,
                    ast.Delete,
                    ast.For,
                    ast.AsyncFor,
                    ast.With,
                    ast.AsyncWith,
                ),
            )
        ):
            owner = _containing_function_for_position(
                path_functions,
                _node_position(statement),
            )
            for target in _statement_targets(statement):
                if isinstance(target, ast.Attribute) and target.attr == "code":
                    fields = _receiver_error_fields(
                        target.value,
                        state,
                        owner,
                        _node_position(target),
                    )
                    if fields is None or target.attr in fields:
                        raise StaticContractAnalysisError(
                            f"{relative_path}:{statement.lineno}: Error code attributes must not "
                            "be mutated"
                        )
                if not isinstance(target, ast.Subscript):
                    continue
                receivers = _object_mapping_receivers(
                    target.value,
                    bindings,
                    owner,
                    _node_position(target),
                    state,
                )
                if receivers is None:
                    continue
                fields = _mapping_receiver_error_fields(
                    receivers,
                    state,
                    owner,
                    _node_position(target),
                )
                if not _mapping_key_may_mutate_fields(target.slice, fields):
                    continue
                kind = (
                    "dynamic persisted Error field mutation"
                    if fields is not None and fields & _ORM_FIELD_NAMES
                    else "dynamic Error field mutation"
                )
                raise StaticContractAnalysisError(
                    f"{relative_path}:{statement.lineno}: {kind} is not supported"
                )
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            owner = _containing_function_for_position(
                path_functions,
                _node_position(call),
            )
            mutated_fields = _dynamic_setattr_error_fields(
                call,
                bindings,
                state,
                owner,
            ) or _dynamic_mapping_setitem_error_fields(
                call,
                bindings,
                state,
                owner,
            )
            if mutated_fields is not None:
                kind = (
                    "dynamic persisted Error field mutation"
                    if mutated_fields & _ORM_FIELD_NAMES
                    else "dynamic Error field mutation"
                )
                raise StaticContractAnalysisError(
                    f"{relative_path}:{call.lineno}: {kind} is not supported"
                )


def _functions_by_path(
    functions: tuple[_FunctionInfo, ...],
) -> dict[str, tuple[_FunctionInfo, ...]]:
    result: dict[str, tuple[_FunctionInfo, ...]] = {}
    for function in functions:
        result.setdefault(function.relative_path, ())
        result[function.relative_path] += (function,)
    return result


def _reject_nonlocal_and_reflective_module_writes(
    trees: Mapping[str, ast.Module],
    state: _AnalysisState,
) -> None:
    functions_by_path = _functions_by_path(state.functions)
    for relative_path, tree in trees.items():
        path_functions = functions_by_path.get(relative_path, ())
        for nonlocal_statement in (
            node for node in ast.walk(tree) if isinstance(node, ast.Nonlocal)
        ):
            if _nonlocal_statement_may_mutate_error_value(
                nonlocal_statement,
                path_functions,
                state,
            ):
                raise StaticContractAnalysisError(
                    f"{relative_path}:{nonlocal_statement.lineno}: nonlocal Error producer "
                    "mutations are not supported"
                )
        module_constants = state.module_constants.get(relative_path, {})
        bindings = state.imported_symbols.get(relative_path, {})
        for statement in (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete))
        ):
            owner = _containing_function_for_position(
                path_functions,
                _node_position(statement),
            )
            for target in _root_targets(statement):
                key = _globals_mapping_key(
                    target,
                    bindings,
                    owner,
                    state,
                )
                if key is not None and _reflective_module_key_may_mutate_error_value(
                    key,
                    relative_path,
                    module_constants,
                    state,
                ):
                    raise StaticContractAnalysisError(
                        f"{relative_path}:{target.lineno}: reflective module Error producer "
                        "mutations are not supported"
                    )


def _nonlocal_statement_may_mutate_error_value(
    statement: ast.Nonlocal,
    functions: tuple[_FunctionInfo, ...],
    state: _AnalysisState,
) -> bool:
    nested = _containing_function_for_position(
        functions,
        _node_position(statement),
    )
    if nested is None:
        return True
    return any(
        (owner := _nonlocal_binding_owner(name, nested, functions)) is None
        or _name_reaches_error_sink(name, owner, state)
        or name.lower().endswith("error_code")
        or name.lower() == "code"
        for name in statement.names
    )


def _nonlocal_binding_owner(
    name: str,
    nested: _FunctionInfo,
    functions: tuple[_FunctionInfo, ...],
) -> _FunctionInfo | None:
    """Resolve the lexical function whose binding a nonlocal statement mutates."""
    visited = {nested}
    current = nested
    while True:
        outer = _containing_function_for_position(
            tuple(function for function in functions if function not in visited),
            _node_position(current.node),
        )
        if outer is None:
            return None
        if _function_binding(outer.node, name) is not None:
            return outer
        visited.add(outer)
        current = outer


def _name_reaches_error_sink(
    name: str,
    function: _FunctionInfo,
    state: _AnalysisState,
    *,
    helper_stack: tuple[tuple[str, str, str], ...] = (),
    helper_depth: int = 0,
) -> bool:
    for call in _calls_in_function(function.node):
        qualified = _resolved_call_symbol(call, function, state)
        sink_parameter = _sink_parameter_for_call(call, qualified, function, state)
        if sink_parameter is not None and _expression_reaches_name(
            sink_parameter,
            name,
            function,
            _node_position(sink_parameter),
        ):
            return True
        helper = _resolved_nonlocal_helper(call, function, state)
        if helper is None:
            continue
        for parameter in _parameters_bound_from_name(
            helper,
            call,
            name,
            function,
        ):
            if _helper_parameter_reaches_error_sink(
                helper,
                parameter,
                state,
                helper_stack=helper_stack,
                helper_depth=helper_depth + 1,
            ):
                return True
    return _name_reaches_persisted_assignment(name, function, state)


def _name_reaches_persisted_assignment(
    name: str,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> bool:
    return any(
        attribute.attr in _ORM_FIELD_NAMES
        and _infer_assignment_model(function, attribute, assignment, state) is not None
        and assignment.value is not None
        and _expression_reaches_name(
            _assignment_target_value(assignment, attribute),
            name,
            function,
            _node_position(assignment),
        )
        for assignment in _assignments_in_function(function.node)
        for attribute in _assigned_attributes(assignment)
    )


def _sink_parameter_for_call(
    call: ast.Call,
    qualified: str,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> ast.expr | None:
    sink = state.call_sinks.get(qualified)
    if sink is not None:
        if sink.implicit_code is not None:
            return None
        return _constructor_argument(
            call,
            keyword=sink.keyword,
            positional_index=sink.positional_index,
            key=f"{function.relative_path}:{call.lineno}:{sink.symbol}",
            required=True,
            allow_keyword_unpacking=True,
        )
    result_sink = state.result_sinks.get(qualified)
    if result_sink is not None:
        return _constructor_argument(
            call,
            keyword=result_sink.field,
            positional_index=result_sink.positional_index,
            key=f"{function.relative_path}:{call.lineno}:{result_sink.symbol}",
            required=False,
        )
    model_name = _local_model_name(qualified, state)
    if model_name is None:
        return None
    return next(
        (
            value
            for (model, field), _ in state.orm_fields.items()
            if model == model_name
            for value in (
                _constructor_argument(
                    call,
                    keyword=field,
                    positional_index=_class_field_position(model_name, field, state),
                    key=f"{function.relative_path}:{call.lineno}:{model_name}.{field}",
                    required=False,
                ),
            )
            if value is not None
        ),
        None,
    )


def _expression_reaches_name(
    node: ast.AST,
    name: str,
    function: _FunctionInfo,
    before_position: SourcePosition,
    trail: frozenset[str] = frozenset(),
) -> bool:
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        if node.id == name:
            return True
        if node.id in trail:
            return False
        try:
            flow = _reaching_definitions(function.node, node.id, before_position)
        except StaticContractAnalysisError:
            return False
        return any(
            _expression_reaches_name(
                definition.value,
                name,
                function,
                definition.position,
                trail | {node.id},
            )
            for definition in flow.definitions
        )
    return any(
        _expression_reaches_name(
            candidate,
            name,
            function,
            before_position,
            trail,
        )
        for candidate in ast.iter_child_nodes(node)
    )


def _resolved_nonlocal_helper(
    call: ast.Call,
    caller: _FunctionInfo,
    state: _AnalysisState,
) -> _FunctionInfo | None:
    matches = tuple(
        helper for helper in state.functions if _is_helper_call(call, caller, helper, state)
    )
    if len(matches) > 1:
        raise StaticContractAnalysisError(
            f"{caller.relative_path}:{call.lineno}: Error helper call is ambiguous"
        )
    return matches[0] if matches else None


def _parameters_bound_from_name(
    helper: _FunctionInfo,
    call: ast.Call,
    name: str,
    caller: _FunctionInfo,
) -> tuple[str, ...]:
    parameters = _callable_parameters(helper)
    if any(isinstance(argument, ast.Starred) for argument in call.args) or any(
        item.arg is None for item in call.keywords
    ):
        if _expression_reaches_name(
            call,
            name,
            caller,
            _node_position(call),
        ):
            raise StaticContractAnalysisError(
                f"{caller.relative_path}:{call.lineno}: nonlocal Error helper "
                "flow cannot use *args or **kwargs"
            )
        return ()
    result: list[str] = []
    for index, parameter in enumerate(parameters):
        positional = call.args[index] if index < len(call.args) else None
        named = next(
            (item.value for item in call.keywords if item.arg == parameter.arg),
            None,
        )
        if positional is not None and named is not None:
            raise StaticContractAnalysisError(
                f"{caller.relative_path}:{call.lineno}: duplicate Error helper argument"
            )
        argument = positional or named
        if argument is None or not _expression_reaches_name(
            argument,
            name,
            caller,
            _node_position(argument),
        ):
            continue
        result.append(parameter.arg)
    return tuple(result)


def _callable_parameters(helper: _FunctionInfo) -> list[ast.arg]:
    parameters = [*helper.node.args.posonlyargs, *helper.node.args.args]
    if (
        parameters
        and helper.class_name is not None
        and parameters[0].arg in {"self", "cls"}
        and not _function_has_decorator(helper.node, "staticmethod")
    ):
        parameters = parameters[1:]
    return [*parameters, *helper.node.args.kwonlyargs]


def _helper_parameter_reaches_error_sink(
    helper: _FunctionInfo,
    parameter: str,
    state: _AnalysisState,
    *,
    helper_stack: tuple[tuple[str, str, str], ...],
    helper_depth: int,
) -> bool:
    if helper_depth > state.max_helper_depth:
        raise StaticContractAnalysisError(
            f"{helper.relative_path}::{helper.qualified_name}: nonlocal Error helper depth "
            f"exceeds {state.max_helper_depth}"
        )
    identity = (helper.relative_path, helper.qualified_name, parameter)
    if identity in helper_stack:
        raise StaticContractAnalysisError(
            f"{helper.relative_path}::{helper.qualified_name}: nonlocal Error helper cycle detected"
        )
    if _parameter_writes(helper.node, parameter):
        raise StaticContractAnalysisError(
            f"{helper.relative_path}::{helper.qualified_name}: nonlocal Error helper "
            f"parameter {parameter!r} is reassigned"
        )
    return _name_reaches_error_sink(
        parameter,
        helper,
        state,
        helper_stack=(*helper_stack, identity),
        helper_depth=helper_depth,
    )


def _globals_mapping_key(
    target: ast.expr,
    bindings: Mapping[str, str],
    function: _FunctionInfo | None,
    state: _AnalysisState,
) -> ast.expr | None:
    if not (
        isinstance(target, ast.Subscript)
        and isinstance(target.value, ast.Call)
        and _resolved_builtin_call(target.value, bindings, function, state) == "builtins.globals"
        and not target.value.args
        and not target.value.keywords
    ):
        return None
    return target.slice


def _reflective_module_key_may_mutate_error_value(
    key: ast.expr,
    relative_path: str,
    module_constants: Mapping[str, ast.expr],
    state: _AnalysisState,
) -> bool:
    if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
        return True
    name = key.value
    if name.lower().endswith("error_code") or name.lower() == "code":
        return True
    return name in module_constants and _module_name_reaches_error_sink(
        name,
        relative_path,
        state,
    )


def _module_name_reaches_error_sink(
    name: str,
    relative_path: str,
    state: _AnalysisState,
) -> bool:
    return any(
        function.relative_path == relative_path and _name_reaches_error_sink(name, function, state)
        for function in state.functions
    )


def _reject_dynamic_persisted_writes(
    trees: Mapping[str, ast.Module],
    state: _AnalysisState,
) -> None:
    functions_by_path = _functions_by_path(state.functions)
    for relative_path, tree in trees.items():
        bindings = state.imported_symbols.get(relative_path, {})
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            owners = tuple(
                function
                for function in functions_by_path.get(relative_path, ())
                if _function_contains_position(function.node, _node_position(call))
            )
            owner = max(owners, key=lambda item: item.node.lineno, default=None)
            if _is_dynamic_error_mapping_method(call, bindings, state, owner):
                raise StaticContractAnalysisError(
                    f"{relative_path}:{call.lineno}: dynamic Error field mutation through an "
                    "object mapping method is not supported"
                )
            if _is_error_model_copy_update(call, state, owner):
                raise StaticContractAnalysisError(
                    f"{relative_path}:{call.lineno}: Error model_copy(update=...) writes are "
                    "not supported"
                )
            if _is_sqlalchemy_values_write(
                call,
                bindings,
                state,
                owner,
            ) or _is_sqlalchemy_bulk_write(
                call,
                bindings,
                state,
                owner,
            ):
                raise StaticContractAnalysisError(
                    f"{relative_path}:{call.lineno}: bulk persisted Error field writes are "
                    "not supported"
                )
        for statement in (
            node
            for node in ast.walk(tree)
            if isinstance(
                node,
                (ast.Delete, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith),
            )
        ):
            if any(
                isinstance(target, ast.Attribute) and target.attr in _ORM_FIELD_NAMES
                for target in _statement_targets(statement)
            ):
                raise StaticContractAnalysisError(
                    f"{relative_path}:{statement.lineno}: persisted Error field write through "
                    "a descriptor target is not supported"
                )


def _is_sqlalchemy_values_write(
    call: ast.Call,
    bindings: Mapping[str, str],
    state: _AnalysisState,
    function: _FunctionInfo | None,
) -> bool:
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "values":
        return False
    if not _is_sqlalchemy_write_statement(
        call.func.value,
        bindings,
        state,
        function,
        _node_position(call),
    ):
        return False
    return any(
        keyword.arg in _ORM_FIELD_NAMES
        or keyword.arg is None
        and _mapping_may_contain_error_field(
            keyword.value,
            bindings,
            state,
            function,
            _node_position(call),
        )
        for keyword in call.keywords
    ) or any(
        _mapping_may_contain_error_field(
            argument,
            bindings,
            state,
            function,
            _node_position(call),
        )
        for argument in call.args
    )


def _is_sqlalchemy_write_statement(
    node: ast.expr,
    bindings: Mapping[str, str],
    state: _AnalysisState,
    function: _FunctionInfo | None,
    before_position: SourcePosition,
    trail: frozenset[str] = frozenset(),
) -> bool:
    if isinstance(node, ast.Call):
        if _is_sqlalchemy_write_factory_call(node, bindings, state):
            return True
        if isinstance(node.func, ast.Attribute):
            return _is_sqlalchemy_write_statement(
                node.func.value,
                bindings,
                state,
                function,
                _node_position(node),
                trail,
            )
        return False
    if function is None or not isinstance(node, ast.Name) or node.id in trail:
        return False
    try:
        flow = _reaching_definitions(function.node, node.id, before_position)
    except StaticContractAnalysisError:
        return False
    return (
        bool(flow.definitions)
        and not flow.unbound
        and all(
            _is_sqlalchemy_write_statement(
                definition.value,
                bindings,
                state,
                function,
                definition.position,
                trail | {node.id},
            )
            for definition in flow.definitions
        )
    )


def _is_sqlalchemy_write_factory_call(
    call: ast.Call,
    bindings: Mapping[str, str],
    state: _AnalysisState,
) -> bool:
    return bool(
        _resolved_imported_expression(call.func, bindings)
        in {"sqlalchemy.update", "sqlalchemy.insert"}
        and call.args
        and _expression_references_orm_model(call.args[0], bindings, state)
    )


def _resolved_imported_expression(
    node: ast.expr,
    bindings: Mapping[str, str],
) -> str:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, "")
    if not isinstance(node, ast.Attribute):
        return ""
    parent = _resolved_imported_expression(node.value, bindings)
    return f"{parent}.{node.attr}" if parent else ""


def _is_sqlalchemy_bulk_write(
    call: ast.Call,
    bindings: Mapping[str, str],
    state: _AnalysisState,
    function: _FunctionInfo | None,
) -> bool:
    if not isinstance(call.func, ast.Attribute):
        return False
    if call.func.attr in {"bulk_update_mappings", "bulk_insert_mappings"}:
        return bool(
            call.args
            and _expression_references_orm_model(call.args[0], bindings, state)
            and any(
                _mapping_may_contain_error_field(
                    argument,
                    bindings,
                    state,
                    function,
                    _node_position(call),
                )
                for argument in call.args[1:]
            )
        )
    if call.func.attr == "update" and _is_sqlalchemy_query_call(
        call.func.value,
        bindings,
        state,
    ):
        return any(
            _mapping_may_contain_error_field(
                argument,
                bindings,
                state,
                function,
                _node_position(call),
            )
            for argument in call.args
        ) or any(
            keyword.arg in _ORM_FIELD_NAMES
            or keyword.arg is None
            and _mapping_may_contain_error_field(
                keyword.value,
                bindings,
                state,
                function,
                _node_position(call),
            )
            for keyword in call.keywords
        )
    if call.func.attr != "execute" or len(call.args) < 2:
        return False
    return _is_sqlalchemy_write_statement(
        call.args[0],
        bindings,
        state,
        function,
        _node_position(call),
    ) and any(
        _mapping_may_contain_error_field(
            argument,
            bindings,
            state,
            function,
            _node_position(call),
        )
        for argument in call.args[1:]
    )


def _is_sqlalchemy_query_call(
    node: ast.expr,
    bindings: Mapping[str, str],
    state: _AnalysisState,
) -> bool:
    return bool(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "query"
        and node.args
        and _expression_references_orm_model(node.args[0], bindings, state)
    )


def _mapping_may_contain_error_field(
    node: ast.expr,
    bindings: Mapping[str, str],
    state: _AnalysisState,
    function: _FunctionInfo | None,
    before_position: SourcePosition,
) -> bool:
    if isinstance(node, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
        return _mapping_contains_error_field(
            node,
            bindings,
            state,
            function,
            before_position,
        )
    if not isinstance(node, ast.Name) or function is None:
        return True
    try:
        flow = _reaching_definitions(function.node, node.id, before_position)
    except StaticContractAnalysisError:
        return True
    if not flow.definitions or flow.unbound:
        return True
    return any(
        _mapping_may_contain_error_field(
            definition.value,
            bindings,
            state,
            function,
            definition.position,
        )
        for definition in flow.definitions
    )


def _mapping_contains_error_field(
    node: ast.expr,
    bindings: Mapping[str, str],
    state: _AnalysisState,
    function: _FunctionInfo | None = None,
    before_position: SourcePosition = (0, 0),
    trail: frozenset[str] = frozenset(),
) -> bool:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(
            _mapping_contains_error_field(
                item,
                bindings,
                state,
                function,
                before_position,
                trail,
            )
            for item in node.elts
        )
    if isinstance(node, ast.Name):
        if function is None or node.id in trail:
            return False
        flow = _reaching_definitions(function.node, node.id, before_position)
        return (
            bool(flow.definitions)
            and not flow.unbound
            and any(
                _mapping_contains_error_field(
                    definition.value,
                    bindings,
                    state,
                    function,
                    definition.position,
                    trail | {node.id},
                )
                for definition in flow.definitions
            )
        )
    if not isinstance(node, ast.Dict):
        return False
    return any(
        key is None
        and _mapping_contains_error_field(
            value,
            bindings,
            state,
            function,
            before_position,
            trail,
        )
        or key is not None
        and _mapping_key_is_error_field(key, bindings, state)
        for key, value in zip(node.keys, node.values, strict=True)
    )


def _mapping_key_is_error_field(
    key: ast.expr,
    bindings: Mapping[str, str],
    state: _AnalysisState,
) -> bool:
    if isinstance(key, ast.Constant):
        return key.value in _ORM_FIELD_NAMES
    return bool(
        isinstance(key, ast.Attribute)
        and key.attr in _ORM_FIELD_NAMES
        and _expression_references_orm_model(key.value, bindings, state)
    )


def _expression_references_orm_model(
    node: ast.expr,
    bindings: Mapping[str, str],
    state: _AnalysisState,
) -> bool:
    return any(
        isinstance(candidate, ast.Name)
        and _local_model_name(bindings.get(candidate.id, ""), state) is not None
        for candidate in ast.walk(node)
    )


def _resolved_builtin_call(
    call: ast.Call,
    bindings: Mapping[str, str],
    function: _FunctionInfo | None,
    state: _AnalysisState,
) -> str:
    if isinstance(call.func, ast.Name):
        local = call.func.id
        if local in {"globals", "vars", "setattr"} and local not in bindings:
            if (
                function is not None and _function_binding(function.node, local) is not None
            ) or _module_builtin_binding_is_shadowed(
                function.relative_path if function is not None else "",
                local,
                state,
            ):
                return ""
            return f"builtins.{local}"
        return bindings.get(local, "")
    return _resolved_imported_expression(call.func, bindings)


def _module_builtin_binding_is_shadowed(
    relative_path: str,
    name: str,
    state: _AnalysisState,
) -> bool:
    local_symbol = f"{_module_name(relative_path)}.{name}" if relative_path else ""
    return bool(
        relative_path
        and (
            state.imported_symbols.get(relative_path, {}).get(name) == local_symbol
            or name in state.module_constants.get(relative_path, {})
        )
    )


def _receiver_error_fields(
    receiver: ast.expr,
    state: _AnalysisState,
    function: _FunctionInfo | None,
    before_position: SourcePosition,
) -> frozenset[str] | None:
    if function is None:
        return None
    if (
        isinstance(receiver, ast.Name)
        and receiver.id in {"self", "cls"}
        and function.class_name is not None
    ):
        receiver_types = frozenset(
            {f"{_module_name(function.relative_path)}.{function.class_name}"}
        )
    else:
        try:
            receiver_types = _expression_types(
                receiver,
                function=function,
                state=state,
                before_position=before_position,
                trail=("mutation-receiver",),
            )
        except StaticContractAnalysisError:
            return None
    if not receiver_types:
        return None
    fields: set[str] = set()
    trusted_carriers = 0
    for carrier in receiver_types:
        if _is_obsion_error_type(carrier, state):
            fields.add("code")
            trusted_carriers += 1
            continue
        result_sink = state.result_sinks.get(carrier)
        if result_sink is not None:
            fields.add(result_sink.field)
            trusted_carriers += 1
            continue
        model = _local_model_name(carrier, state)
        if model is not None:
            fields.update(field for candidate, field in state.orm_fields if candidate == model)
            trusted_carriers += 1
    if 0 < trusted_carriers < len(receiver_types):
        return None
    return frozenset(fields)


def _object_mapping_receivers(
    node: ast.expr,
    bindings: Mapping[str, str],
    function: _FunctionInfo | None,
    before_position: SourcePosition,
    state: _AnalysisState,
    trail: frozenset[str] = frozenset(),
) -> tuple[ast.expr, ...] | None:
    if isinstance(node, ast.Call):
        if (
            _resolved_builtin_call(node, bindings, function, state) == "builtins.vars"
            and len(node.args) == 1
            and not node.keywords
        ):
            return (node.args[0],)
        return None
    if isinstance(node, ast.Attribute) and node.attr == "__dict__":
        return (node.value,)
    branches: tuple[ast.expr, ...]
    if isinstance(node, ast.IfExp):
        branches = (node.body, node.orelse)
    elif isinstance(node, ast.BoolOp):
        branches = tuple(node.values)
    else:
        branches = ()
    if branches:
        receivers = tuple(
            receiver
            for branch in branches
            for receiver in (
                _object_mapping_receivers(
                    branch,
                    bindings,
                    function,
                    before_position,
                    state,
                    trail,
                )
                or ()
            )
        )
        return receivers or None
    if function is None or not isinstance(node, ast.Name) or node.id in trail:
        return None
    try:
        flow = _reaching_definitions(function.node, node.id, before_position)
    except StaticContractAnalysisError:
        return None
    receivers = tuple(
        receiver
        for definition in flow.definitions
        for receiver in (
            _object_mapping_receivers(
                definition.value,
                bindings,
                function,
                definition.position,
                state,
                trail | {node.id},
            )
            or ()
        )
    )
    return receivers or None


def _mapping_key_may_mutate_fields(
    key: ast.expr,
    fields: frozenset[str] | None,
) -> bool:
    candidates = _ERROR_FIELD_NAMES if fields is None else fields
    if not candidates:
        return False
    return not isinstance(key, ast.Constant) or key.value in candidates


def _mapping_receiver_error_fields(
    receivers: tuple[ast.expr, ...],
    state: _AnalysisState,
    function: _FunctionInfo | None,
    before_position: SourcePosition,
) -> frozenset[str] | None:
    fields: set[str] = set()
    for receiver in receivers:
        receiver_fields = _receiver_error_fields(
            receiver,
            state,
            function,
            before_position,
        )
        if receiver_fields is None:
            return None
        fields.update(receiver_fields)
    return frozenset(fields)


def _containing_function_for_position(
    functions: tuple[_FunctionInfo, ...],
    position: SourcePosition,
) -> _FunctionInfo | None:
    owners = tuple(
        function for function in functions if _function_contains_position(function.node, position)
    )
    return max(owners, key=lambda item: item.node.lineno, default=None)


_MAPPING_MUTATION_METHODS = frozenset({"clear", "pop", "popitem", "setdefault", "update"})


def _is_dynamic_error_mapping_method(
    call: ast.Call,
    bindings: Mapping[str, str],
    state: _AnalysisState,
    function: _FunctionInfo | None,
) -> bool:
    return any(
        _mapping_method_may_mutate_error_fields(
            call,
            method,
            receivers,
            state,
            function,
        )
        for method, receivers in _object_mapping_method_candidates(
            call.func,
            bindings,
            function,
            _node_position(call),
            state,
        )
    )


def _object_mapping_method_candidates(
    node: ast.expr,
    bindings: Mapping[str, str],
    function: _FunctionInfo | None,
    before_position: SourcePosition,
    state: _AnalysisState,
    trail: frozenset[str] = frozenset(),
) -> tuple[tuple[str, tuple[ast.expr, ...]], ...]:
    if isinstance(node, ast.Attribute) and node.attr in _MAPPING_MUTATION_METHODS:
        receivers = _object_mapping_receivers(
            node.value,
            bindings,
            function,
            before_position,
            state,
        )
        return ((node.attr, receivers),) if receivers is not None else ()
    branches: tuple[ast.expr, ...]
    if isinstance(node, ast.IfExp):
        branches = (node.body, node.orelse)
    elif isinstance(node, ast.BoolOp):
        branches = tuple(node.values)
    else:
        branches = ()
    if branches:
        return tuple(
            candidate
            for branch in branches
            for candidate in _object_mapping_method_candidates(
                branch,
                bindings,
                function,
                before_position,
                state,
                trail,
            )
        )
    if function is None or not isinstance(node, ast.Name) or node.id in trail:
        return ()
    try:
        flow = _reaching_definitions(function.node, node.id, before_position)
    except StaticContractAnalysisError:
        return ()
    return tuple(
        candidate
        for definition in flow.definitions
        for candidate in _object_mapping_method_candidates(
            definition.value,
            bindings,
            function,
            definition.position,
            state,
            trail | {node.id},
        )
    )


def _mapping_method_may_mutate_error_fields(
    call: ast.Call,
    method: str,
    receivers: tuple[ast.expr, ...],
    state: _AnalysisState,
    function: _FunctionInfo | None,
) -> bool:
    fields = _mapping_receiver_error_fields(
        receivers,
        state,
        function,
        _node_position(call),
    )
    if fields == frozenset():
        return False
    candidates = _ERROR_FIELD_NAMES if fields is None else fields
    if method in {"clear", "popitem"}:
        return True
    if method in {"pop", "setdefault"}:
        return bool(
            not call.args
            or not isinstance(call.args[0], ast.Constant)
            or call.args[0].value in candidates
        )
    return any(
        keyword.arg is None
        and _mapping_expression_may_mutate_fields(
            keyword.value,
            fields,
            function,
            _node_position(call),
        )
        or keyword.arg is not None
        and keyword.arg in candidates
        for keyword in call.keywords
    ) or any(
        _mapping_expression_may_mutate_fields(
            argument,
            fields,
            function,
            _node_position(call),
        )
        for argument in call.args
    )


def _mapping_expression_may_mutate_fields(
    node: ast.expr,
    fields: frozenset[str] | None,
    function: _FunctionInfo | None,
    before_position: SourcePosition,
    trail: frozenset[str] = frozenset(),
) -> bool:
    if fields == frozenset():
        return False
    candidates = _ERROR_FIELD_NAMES if fields is None else fields
    if isinstance(node, ast.Dict):
        return any(
            key is None
            and _mapping_expression_may_mutate_fields(
                value,
                fields,
                function,
                before_position,
                trail,
            )
            or key is not None
            and (not isinstance(key, ast.Constant) or key.value in candidates)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.Name) and function is not None and node.id not in trail:
        try:
            flow = _reaching_definitions(function.node, node.id, before_position)
        except StaticContractAnalysisError:
            return True
        if not flow.definitions or flow.unbound:
            return True
        return any(
            _mapping_expression_may_mutate_fields(
                definition.value,
                fields,
                function,
                definition.position,
                trail | {node.id},
            )
            for definition in flow.definitions
        )
    return True


def _dynamic_mapping_setitem_error_fields(
    call: ast.Call,
    bindings: Mapping[str, str],
    state: _AnalysisState,
    function: _FunctionInfo | None,
) -> frozenset[str] | None:
    receiver: ast.expr
    key: ast.expr
    if _expression_resolves_to_operator_setitem(
        call.func,
        bindings,
        function,
        _node_position(call),
    ) or _is_builtin_dict_setitem(call.func, bindings, function, state):
        if len(call.args) < 2:
            return None
        receiver, key = call.args[:2]
        receivers = _object_mapping_receivers(
            receiver,
            bindings,
            function,
            _node_position(call),
            state,
        )
    elif isinstance(call.func, ast.Attribute) and call.func.attr == "__setitem__":
        if not call.args:
            return None
        key = call.args[0]
        receivers = _object_mapping_receivers(
            call.func.value,
            bindings,
            function,
            _node_position(call),
            state,
        )
    else:
        if not call.args:
            return None
        key = call.args[0]
        receivers = _object_mapping_setitem_receivers(
            call.func,
            bindings,
            function,
            _node_position(call),
            state,
        )
    if receivers is None:
        return None
    fields = _mapping_receiver_error_fields(
        receivers,
        state,
        function,
        _node_position(call),
    )
    if fields == frozenset():
        return None
    candidates = _ERROR_FIELD_NAMES if fields is None else fields
    if isinstance(key, ast.Constant) and key.value not in candidates:
        return None
    return candidates


def _expression_resolves_to_operator_setitem(
    node: ast.expr,
    bindings: Mapping[str, str],
    function: _FunctionInfo | None,
    before_position: SourcePosition,
    trail: frozenset[str] = frozenset(),
) -> bool:
    if _resolved_imported_expression(node, bindings) == "operator.setitem":
        return True
    if function is None or not isinstance(node, ast.Name) or node.id in trail:
        return False
    try:
        flow = _reaching_definitions(function.node, node.id, before_position)
    except StaticContractAnalysisError:
        return False
    return (
        bool(flow.definitions)
        and not flow.unbound
        and all(
            _expression_resolves_to_operator_setitem(
                definition.value,
                bindings,
                function,
                definition.position,
                trail | {node.id},
            )
            for definition in flow.definitions
        )
    )


def _is_builtin_dict_setitem(
    node: ast.expr,
    bindings: Mapping[str, str],
    function: _FunctionInfo | None,
    state: _AnalysisState,
) -> bool:
    if not isinstance(node, ast.Attribute) or node.attr != "__setitem__":
        return False
    qualified = _resolved_imported_expression(node.value, bindings)
    if qualified == "builtins.dict":
        return True
    return bool(
        isinstance(node.value, ast.Name)
        and node.value.id == "dict"
        and "dict" not in bindings
        and (
            function is None
            or _function_binding(function.node, "dict") is None
            and not _module_builtin_binding_is_shadowed(
                function.relative_path,
                "dict",
                state,
            )
        )
    )


def _object_mapping_setitem_receivers(
    node: ast.expr,
    bindings: Mapping[str, str],
    function: _FunctionInfo | None,
    before_position: SourcePosition,
    state: _AnalysisState,
    trail: frozenset[str] = frozenset(),
) -> tuple[ast.expr, ...] | None:
    if isinstance(node, ast.Attribute) and node.attr == "__setitem__":
        return _object_mapping_receivers(
            node.value,
            bindings,
            function,
            before_position,
            state,
        )
    if function is None or not isinstance(node, ast.Name) or node.id in trail:
        return None
    try:
        flow = _reaching_definitions(function.node, node.id, before_position)
    except StaticContractAnalysisError:
        return None
    receivers = tuple(
        receiver
        for definition in flow.definitions
        for receiver in (
            _object_mapping_setitem_receivers(
                definition.value,
                bindings,
                function,
                definition.position,
                state,
                trail | {node.id},
            )
            or ()
        )
    )
    return receivers or None


def _dynamic_setattr_error_fields(
    call: ast.Call,
    bindings: Mapping[str, str],
    state: _AnalysisState,
    function: _FunctionInfo | None,
) -> frozenset[str] | None:
    receiver: ast.expr
    key: ast.expr
    if _resolved_builtin_call(call, bindings, function, state) == "builtins.setattr":
        if len(call.args) < 2:
            return None
        receiver, key = call.args[:2]
    elif isinstance(call.func, ast.Attribute) and call.func.attr == "__setattr__" and call.args:
        receiver, key = call.func.value, call.args[0]
    else:
        return None
    fields = _receiver_error_fields(
        receiver,
        state,
        function,
        _node_position(call),
    )
    if fields == frozenset():
        return None
    candidates = _ERROR_FIELD_NAMES if fields is None else fields
    if isinstance(key, ast.Constant) and key.value not in candidates:
        return None
    return candidates


def _is_error_model_copy_update(
    call: ast.Call,
    state: _AnalysisState,
    function: _FunctionInfo | None,
) -> bool:
    if (
        function is None
        or not isinstance(call.func, ast.Attribute)
        or call.func.attr != "model_copy"
    ):
        return False
    update = next(
        (keyword.value for keyword in call.keywords if keyword.arg == "update"),
        None,
    )
    if update is None or not _mapping_expression_may_mutate_fields(
        update,
        frozenset({"code"}),
        function,
        _node_position(call),
    ):
        return False
    try:
        receiver_types = _expression_types(
            call.func.value,
            function=function,
            state=state,
            before_position=_node_position(call),
            trail=("model_copy",),
        )
    except StaticContractAnalysisError:
        return False
    return bool(receiver_types) and any(
        carrier == _ERROR_BODY
        or _class_is_or_inherits_qualified(
            carrier,
            _ERROR_BODY,
            state,
        )
        for carrier in receiver_types
    )


def _class_is_or_inherits_qualified(
    candidate: str,
    ancestor: str,
    state: _AnalysisState,
    trail: frozenset[str] = frozenset(),
) -> bool:
    if candidate == ancestor:
        return True
    if candidate in trail:
        return False
    definition = state.class_definitions.get(candidate)
    if definition is None:
        return False
    return any(
        _class_is_or_inherits_qualified(
            parent,
            ancestor,
            state,
            trail | {candidate},
        )
        for parent in _resolved_class_bases(
            definition,
            state.class_definitions,
            state.imported_symbols,
        )
    )


def _root_targets(
    statement: ast.Assign | ast.AnnAssign | ast.AugAssign | ast.Delete,
) -> list[ast.expr]:
    if isinstance(statement, (ast.Assign, ast.Delete)):
        return list(statement.targets)
    return [statement.target]


def _statement_targets(
    statement: (
        ast.Assign
        | ast.AnnAssign
        | ast.AugAssign
        | ast.Delete
        | ast.For
        | ast.AsyncFor
        | ast.With
        | ast.AsyncWith
    ),
) -> list[ast.expr]:
    if isinstance(statement, (ast.For, ast.AsyncFor)):
        roots = [statement.target]
    elif isinstance(statement, (ast.With, ast.AsyncWith)):
        roots = [item.optional_vars for item in statement.items if item.optional_vars is not None]
    else:
        roots = _root_targets(statement)
    result: list[ast.expr] = []
    for root in roots:
        _collect_written_targets(root, result)
    return result


def _collect_written_targets(target: ast.expr, result: list[ast.expr]) -> None:
    if isinstance(target, (ast.Attribute, ast.Subscript)):
        result.append(target)
        return
    if isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            _collect_written_targets(item, result)
        return
    if isinstance(target, ast.Starred):
        _collect_written_targets(target.value, result)


def _reject_canonical_sink_aliases(
    trees: Mapping[str, ast.Module],
    imported_symbols: Mapping[str, Mapping[str, str]],
    call_sinks: Mapping[str, _CallSink],
    result_sinks: Mapping[str, _ConstructorFieldSink],
) -> None:
    relevant = {*call_sinks, *result_sinks}
    for relative_path, tree in trees.items():
        bindings = imported_symbols.get(relative_path, {})
        canonical_locals = {local for local, qualified in bindings.items() if qualified in relevant}
        if not canonical_locals:
            continue
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                escaped = _definition_time_sink_references(
                    statement,
                    canonical_locals,
                )
            elif isinstance(statement, ast.ClassDef):
                escaped = _class_scope_sink_references(
                    statement,
                    canonical_locals,
                )
            else:
                direct_references = {
                    id(call.func)
                    for call in ast.walk(statement)
                    if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id in canonical_locals
                }
                escaped = [
                    node
                    for node in ast.walk(statement)
                    if isinstance(node, ast.Name)
                    and node.id in canonical_locals
                    and isinstance(node.ctx, ast.Load)
                    and id(node) not in direct_references
                ]
            if escaped:
                first = min(escaped, key=_node_position)
                raise StaticContractAnalysisError(
                    f"{relative_path}:{first.lineno}: canonical Error sink reference escapes "
                    "a direct constructor call at module scope"
                )
        for function in (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            calls = _calls_in_function(function)
            direct_references = {
                id(call.func)
                for call in calls
                if isinstance(call.func, ast.Name) and call.func.id in canonical_locals
            }
            schema_references = {
                id(node)
                for node in _runtime_nodes_in_function(function)
                if isinstance(node, ast.Name)
                and _is_fastapi_error_response_model_reference(
                    node,
                    calls=calls,
                    bindings=bindings,
                    function=function,
                )
            }
            escaped = [
                node
                for node in _runtime_nodes_in_function(function)
                if isinstance(node, ast.Name)
                and node.id in canonical_locals
                and isinstance(node.ctx, ast.Load)
                and id(node) not in direct_references
                and id(node) not in schema_references
                and not _is_exception_type_reference(node, function)
                and not _is_isinstance_type_reference(node, function)
                and not _is_context_manager_type_reference(node, function)
                and not _is_annotation_reference(node, function)
            ]
            if escaped:
                first = min(escaped, key=_node_position)
                raise StaticContractAnalysisError(
                    f"{relative_path}:{first.lineno}: canonical Error sink reference "
                    "escapes a direct constructor call"
                )


def _definition_time_sink_references(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    canonical_locals: set[str],
) -> list[ast.Name]:
    expressions = (
        *function.decorator_list,
        *function.args.defaults,
        *(default for default in function.args.kw_defaults if default is not None),
    )
    return _sink_name_references(expressions, canonical_locals)


def _class_scope_sink_references(
    definition: ast.ClassDef,
    canonical_locals: set[str],
) -> list[ast.Name]:
    result = _sink_name_references(
        (
            *definition.decorator_list,
            *(keyword.value for keyword in definition.keywords),
        ),
        canonical_locals,
    )
    for statement in definition.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.extend(_definition_time_sink_references(statement, canonical_locals))
            continue
        if isinstance(statement, ast.ClassDef):
            result.extend(_class_scope_sink_references(statement, canonical_locals))
            continue
        result.extend(_sink_name_references((statement,), canonical_locals))
    return result


def _sink_name_references(
    nodes: tuple[ast.AST, ...],
    canonical_locals: set[str],
) -> list[ast.Name]:
    return [
        node
        for root in nodes
        for node in ast.walk(root)
        if isinstance(node, ast.Name)
        and node.id in canonical_locals
        and isinstance(node.ctx, ast.Load)
    ]


def _is_fastapi_error_response_model_reference(
    node: ast.Name,
    *,
    calls: list[ast.Call],
    bindings: Mapping[str, str],
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    if bindings.get(node.id, "") != _ERROR_BODY:
        return False
    for call in calls:
        if not isinstance(call.func, ast.Name):
            continue
        if bindings.get(call.func.id, "") != _FASTAPI_APPLICATION:
            continue
        if _function_binding(function, call.func.id) is not None:
            continue
        responses = next(
            (keyword.value for keyword in call.keywords if keyword.arg == "responses"),
            None,
        )
        if responses is not None and _is_error_response_model_entry(
            responses,
            node,
        ):
            return True
    return False


def _is_error_response_model_entry(container: ast.expr, target: ast.Name) -> bool:
    if not isinstance(container, ast.Dict):
        return False
    for value in container.values:
        if not isinstance(value, ast.Dict):
            continue
        for key, candidate in zip(value.keys, value.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "model" and candidate is target:
                return True
    return False


def _is_exception_type_reference(
    node: ast.Name,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    return any(
        handler.type is not None and any(candidate is node for candidate in ast.walk(handler.type))
        for handler in (
            candidate
            for candidate in _runtime_nodes_in_function(function)
            if isinstance(candidate, ast.ExceptHandler)
        )
    )


def _is_isinstance_type_reference(
    node: ast.Name,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    for call in _calls_in_function(function):
        if (
            not isinstance(call.func, ast.Name)
            or call.func.id != "isinstance"
            or len(call.args) != 2
        ):
            continue
        if any(candidate is node for candidate in ast.walk(call.args[1])):
            return True
    return False


def _is_context_manager_type_reference(
    node: ast.Name,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    for manager in (
        candidate
        for candidate in _runtime_nodes_in_function(function)
        if isinstance(candidate, (ast.With, ast.AsyncWith))
    ):
        for item in manager.items:
            if any(candidate is node for candidate in ast.walk(item.context_expr)):
                return True
    return False


def _is_annotation_reference(
    node: ast.Name,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    annotations = [
        argument.annotation
        for argument in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
        if argument.annotation is not None
    ]
    if function.returns is not None:
        annotations.append(function.returns)
    annotations.extend(
        candidate.annotation
        for candidate in _runtime_nodes_in_function(function)
        if isinstance(candidate, ast.AnnAssign)
    )
    return any(
        any(candidate is node for candidate in ast.walk(annotation)) for annotation in annotations
    )


def _reject_shadowed_sink_calls(
    functions: tuple[_FunctionInfo, ...],
    imported_symbols: Mapping[str, Mapping[str, str]],
    call_sinks: Mapping[str, _CallSink],
    result_sinks: Mapping[str, _ConstructorFieldSink],
) -> None:
    relevant = {*call_sinks, *result_sinks}
    relevant_names = {qualified.rpartition(".")[2] for qualified in relevant}
    for function in functions:
        bindings = imported_symbols.get(function.relative_path, {})
        for call in _calls_in_function(function.node):
            if not isinstance(call.func, ast.Name) or call.func.id not in relevant_names:
                continue
            qualified = bindings.get(call.func.id, "")
            if qualified not in relevant:
                continue
            shadow = _function_binding(function.node, call.func.id)
            if shadow is not None:
                raise StaticContractAnalysisError(
                    f"{function.relative_path}::{function.qualified_name}: Error sink "
                    f"{call.func.id!r} is shadowed at line "
                    f"{getattr(shadow, 'lineno', '?')}"
                )


def _reject_error_code_type_reference_escapes(
    trees: Mapping[str, ast.Module],
    imported_symbols: Mapping[str, Mapping[str, str]],
) -> None:
    structural_modules = frozenset({_ERROR_CODE_TYPE.rpartition(".")[0], "sqlalchemy.orm"})
    structural_symbols = frozenset({_ERROR_CODE_TYPE, _MAPPED_COLUMN})
    for relative_path, tree in trees.items():
        bindings = imported_symbols.get(relative_path, {})
        structural_locals = {
            local
            for local, qualified in bindings.items()
            if qualified in structural_modules or qualified in structural_symbols
        }
        binding_counts = _module_binding_counts(tree)
        shadowed = sorted(local for local in structural_locals if binding_counts.get(local, 0) != 1)
        if shadowed:
            raise StaticContractAnalysisError(
                f"{relative_path}: ErrorCodeType structural bindings are shadowed: {shadowed}"
            )
        for definition in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
            class_shadowed = sorted(
                local
                for local in structural_locals
                if any(_statement_binds_name(statement, local) for statement in definition.body)
            )
            if class_shadowed:
                raise StaticContractAnalysisError(
                    f"{relative_path}:{definition.lineno}: ErrorCodeType structural bindings "
                    f"are shadowed in class scope: {class_shadowed}"
                )
        top_level_imports = {
            id(statement)
            for statement in tree.body
            if isinstance(statement, (ast.Import, ast.ImportFrom))
        }
        for statement in (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in top_level_imports
        ):
            if _is_nested_error_code_type_import(statement, relative_path):
                raise StaticContractAnalysisError(
                    f"{relative_path}:{statement.lineno}: nested ErrorCodeType imports are "
                    "not supported"
                )
        allowed_references = {
            id(first_argument.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
            for mapped_column in (_mapped_column_call(node.value, bindings),)
            if mapped_column is not None and mapped_column.args
            for first_argument in (mapped_column.args[0],)
            if isinstance(first_argument, ast.Call)
            and _resolved_imported_expression(first_argument.func, bindings) == _ERROR_CODE_TYPE
        }
        escaped = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Name, ast.Attribute))
            and isinstance(node.ctx, ast.Load)
            and _resolved_imported_expression(node, bindings) == _ERROR_CODE_TYPE
            and id(node) not in allowed_references
        ]
        if escaped:
            first = min(escaped, key=_node_position)
            raise StaticContractAnalysisError(
                f"{relative_path}:{first.lineno}: ErrorCodeType reference escapes a direct "
                "mapped_column type declaration"
            )


def _is_nested_error_code_type_import(
    statement: ast.Import | ast.ImportFrom,
    relative_path: str,
) -> bool:
    if isinstance(statement, ast.Import):
        return any(alias.name == "obsion.db.types" for alias in statement.names)
    module = _resolved_import_from_module(statement, relative_path)
    if module == "obsion.db.types":
        return any(alias.name == "ErrorCodeType" for alias in statement.names)
    return bool(module == "obsion.db" and any(alias.name == "types" for alias in statement.names))


def _discover_orm_fields(
    classes: tuple[_ClassDefinition, ...],
    imported_symbols: Mapping[str, Mapping[str, str]],
) -> dict[tuple[str, str], _FieldDefinition]:
    result: dict[tuple[str, str], _FieldDefinition] = {}
    canonical_model_paths = {
        definition.relative_path
        for definition in classes
        if _module_name(definition.relative_path) == _CANONICAL_ORM_MODULE
    }
    if len(canonical_model_paths) != 1:
        raise StaticContractAnalysisError("Exactly one obsion.db.models module must be present")
    canonical_model_path = next(iter(canonical_model_paths))
    for definition in classes:
        bindings = imported_symbols.get(definition.relative_path, {})
        model = definition.qualified_name
        position = 0
        for statement in definition.node.body:
            if not isinstance(statement, ast.AnnAssign) or not isinstance(
                statement.target,
                ast.Name,
            ):
                continue
            field = statement.target.id
            mapped_column = _mapped_column_call(statement.value, bindings)
            uses_error_code_type = (
                mapped_column is not None
                and _mapped_column_uses_error_code_type(mapped_column, bindings)
            )
            if uses_error_code_type and definition.relative_path != canonical_model_path:
                raise StaticContractAnalysisError(
                    f"{definition.relative_path}::{model}.{field}: ErrorCodeType columns must "
                    "be declared in the canonical obsion.db.models module"
                )
            if uses_error_code_type and field not in _ORM_FIELD_NAMES:
                raise StaticContractAnalysisError(
                    f"{definition.relative_path}::{model}.{field}: ErrorCodeType is not a "
                    "reviewed ORM Error field"
                )
            if definition.relative_path == canonical_model_path and field in _ORM_FIELD_NAMES:
                if mapped_column is None:
                    raise StaticContractAnalysisError(
                        f"{definition.relative_path}::{model}.{field} is not a mapped_column"
                    )
                if not uses_error_code_type:
                    raise StaticContractAnalysisError(
                        f"{definition.relative_path}::{model}.{field} must use ErrorCodeType"
                    )
                result[(model, field)] = _FieldDefinition(
                    model,
                    field,
                    _annotation_allows_none(statement.annotation),
                    position,
                )
            position += 1
    return result


def _inherit_orm_fields(
    classes: Mapping[str, _ClassDefinition],
    imported_symbols: Mapping[str, Mapping[str, str]],
    fields: dict[tuple[str, str], _FieldDefinition],
) -> None:
    pending = set(classes)
    while pending:
        discovered = False
        for qualified in sorted(pending):
            local_fields = [
                definition
                for (model, _), definition in fields.items()
                if f"obsion.db.models.{model}" == qualified
            ]
            inherited_fields: dict[str, _FieldDefinition] = {}
            for parent in _resolved_class_bases(
                classes[qualified],
                classes,
                imported_symbols,
            ):
                for (model, field), definition in fields.items():
                    if f"obsion.db.models.{model}" == parent:
                        inherited_fields[field] = definition
            if local_fields or inherited_fields:
                class_definition = classes[qualified]
                if inherited_fields and _class_constructor(class_definition) is not None:
                    raise StaticContractAnalysisError(
                        f"{class_definition.relative_path}::"
                        f"{class_definition.qualified_name}: custom constructor for an "
                        "inherited ORM Error field sink is not supported"
                    )
                local_model = qualified.rpartition(".")[2]
                for field, definition in inherited_fields.items():
                    fields.setdefault(
                        (local_model, field),
                        _FieldDefinition(
                            local_model,
                            field,
                            definition.nullable,
                            definition.position,
                        ),
                    )
                pending.remove(qualified)
                discovered = True
        if not discovered:
            break


def _inherit_result_sinks(
    classes: Mapping[str, _ClassDefinition],
    imported_symbols: Mapping[str, Mapping[str, str]],
    result_sinks: dict[str, _ConstructorFieldSink],
) -> None:
    pending = set(classes) - set(result_sinks)
    while pending:
        discovered = False
        for qualified in sorted(pending):
            inherited = [
                result_sinks[parent]
                for parent in _resolved_class_bases(
                    classes[qualified],
                    classes,
                    imported_symbols,
                )
                if parent in result_sinks
            ]
            if not inherited:
                continue
            definition = classes[qualified]
            if _class_constructor(definition) is not None:
                raise StaticContractAnalysisError(
                    f"{definition.relative_path}::{definition.qualified_name}: custom constructor "
                    "for an inherited Error result sink is not supported"
                )
            signatures = {(sink.field, sink.positional_index, sink.nullable) for sink in inherited}
            if len(signatures) != 1:
                raise StaticContractAnalysisError(
                    f"{definition.relative_path}::{qualified}: inherited Error result "
                    "constructor is ambiguous"
                )
            field, positional_index, nullable = next(iter(signatures))
            module, _, symbol = qualified.rpartition(".")
            result_sinks[qualified] = _ConstructorFieldSink(
                module,
                symbol,
                field,
                positional_index,
                nullable,
            )
            pending.remove(qualified)
            discovered = True
        if not discovered:
            break


def _mapped_column_call(
    node: ast.expr | None,
    bindings: Mapping[str, str],
) -> ast.Call | None:
    if not isinstance(node, ast.Call):
        return None
    return node if _resolved_imported_expression(node.func, bindings) == _MAPPED_COLUMN else None


def _mapped_column_uses_error_code_type(
    call: ast.Call,
    bindings: Mapping[str, str],
) -> bool:
    if not call.args:
        return False
    argument = call.args[0]
    return bool(
        isinstance(argument, ast.Call)
        and _resolved_imported_expression(argument.func, bindings) == _ERROR_CODE_TYPE
    )


def _annotation_allows_none(annotation: ast.expr) -> bool:
    return any(
        isinstance(node, ast.Constant) and node.value is None for node in ast.walk(annotation)
    ) or any(isinstance(node, ast.Name) and node.id == "None" for node in ast.walk(annotation))


def _resolved_call_symbol(
    call: ast.Call,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> str:
    if not isinstance(call.func, ast.Name):
        return ""
    local = call.func.id
    binding = state.imported_symbols.get(function.relative_path, {}).get(local, "")
    if not binding:
        return ""
    shadow = _function_binding(function.node, local)
    if shadow is not None:
        relevant = {
            *state.call_sinks,
            *state.result_sinks,
            *(f"obsion.db.models.{model}" for model, _ in state.orm_fields),
        }
        if binding in relevant:
            raise StaticContractAnalysisError(
                f"{function.relative_path}::{function.qualified_name}: Error sink "
                f"{local!r} is shadowed at line {getattr(shadow, 'lineno', '?')}"
            )
        return ""
    return binding


def _local_model_name(qualified: str, state: _AnalysisState) -> str | None:
    prefix = "obsion.db.models."
    canonical = (
        qualified.removeprefix(prefix)
        if qualified.startswith(prefix)
        else qualified.rpartition(".")[2]
    )
    return canonical if any(candidate == canonical for candidate, _ in state.orm_fields) else None


def _analyze_call_sink(
    call: ast.Call,
    sink: _CallSink,
    *,
    function: _FunctionInfo,
    state: _AnalysisState,
    sink_key: str,
) -> _ValueDomain:
    if sink.implicit_code is not None:
        return _literal_domain(sink.implicit_code, state, sink_key)
    value = _constructor_argument(
        call,
        keyword=sink.keyword,
        positional_index=sink.positional_index,
        key=sink_key,
        required=True,
        allow_keyword_unpacking=True,
    )
    assert value is not None
    domain = _evaluate_code(
        value,
        function=function,
        state=state,
        before_position=_node_position(value),
        trail=(sink_key,),
        helper_stack=(),
        helper_depth=0,
    )
    _check_nullable(domain, False, sink_key)
    return domain


def _analyze_constructor_field(
    call: ast.Call,
    sink: _ConstructorFieldSink,
    *,
    function: _FunctionInfo,
    state: _AnalysisState,
    sink_key: str,
) -> _ValueDomain | None:
    value = _constructor_argument(
        call,
        keyword=sink.field,
        positional_index=sink.positional_index,
        key=sink_key,
        required=False,
    )
    if value is None:
        return None
    domain = _evaluate_code(
        value,
        function=function,
        state=state,
        before_position=_node_position(value),
        trail=(sink_key,),
        helper_stack=(),
        helper_depth=0,
    )
    _check_nullable(domain, sink.nullable, sink_key)
    return domain


def _constructor_argument(
    call: ast.Call,
    *,
    keyword: str,
    positional_index: int,
    key: str,
    required: bool,
    allow_keyword_unpacking: bool = False,
) -> ast.expr | None:
    if any(isinstance(argument, ast.Starred) for argument in call.args) or (
        not allow_keyword_unpacking and any(item.arg is None for item in call.keywords)
    ):
        raise StaticContractAnalysisError(f"{key}: Error sinks cannot use *args or **kwargs")
    positional = call.args[positional_index] if 0 <= positional_index < len(call.args) else None
    named = next((item.value for item in call.keywords if item.arg == keyword), None)
    if positional is not None and named is not None:
        raise StaticContractAnalysisError(f"{key}: duplicate Error code argument")
    value = positional or named
    if value is None and required:
        raise StaticContractAnalysisError(f"{key}: missing Error code argument")
    return value


def _record_sink(
    key: str,
    domain: _ValueDomain,
    origin_sinks: dict[str, ErrorCodeDomain],
    forwarding_sinks: dict[str, str],
    state: _AnalysisState,
) -> None:
    if not domain.origins and not domain.forwarding and not domain.nullable:
        raise StaticContractAnalysisError(f"{key}: Error sink has an empty value domain")
    if domain.origins:
        if len(domain.origins) > state.max_domain:
            raise StaticContractAnalysisError(
                f"{key}: Error code domain exceeds {state.max_domain}"
            )
        origin_sinks[key] = domain.origins
    if domain.forwarding:
        forwarding_sinks[key] = " | ".join(sorted(domain.forwarding))


def _check_nullable(domain: _ValueDomain, nullable: bool, key: str) -> None:
    if domain.nullable and not nullable:
        raise StaticContractAnalysisError(f"{key}: non-nullable Error sink may receive None")


def _literal_domain(value: str, state: _AnalysisState, key: str) -> _ValueDomain:
    if value not in state.catalog_codes:
        raise StaticContractAnalysisError(
            f"{key}: unregistered Error code {value!r} enters a typed sink"
        )
    return _ValueDomain(origins=frozenset({value}))


def _evaluate_code(
    node: ast.expr,
    *,
    function: _FunctionInfo,
    state: _AnalysisState,
    before_position: SourcePosition,
    trail: tuple[str, ...],
    helper_stack: tuple[tuple[str, str, str], ...],
    helper_depth: int,
) -> _ValueDomain:
    key = " -> ".join(trail)
    if isinstance(node, ast.Constant):
        if node.value is None:
            return _ValueDomain(nullable=True)
        if isinstance(node.value, str):
            return _literal_domain(node.value, state, key)
        raise StaticContractAnalysisError(
            f"{key}: Error code must be a string or None, not {node.value!r}"
        )
    if isinstance(node, ast.IfExp):
        return _evaluate_code(
            node.body,
            function=function,
            state=state,
            before_position=before_position,
            trail=(*trail, "if-true"),
            helper_stack=helper_stack,
            helper_depth=helper_depth,
        ).union(
            _evaluate_code(
                node.orelse,
                function=function,
                state=state,
                before_position=before_position,
                trail=(*trail, "if-false"),
                helper_stack=helper_stack,
                helper_depth=helper_depth,
            )
        )
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        domain = _ValueDomain()
        final_nullable = False
        for index, value in enumerate(node.values, start=1):
            value_domain = _evaluate_code(
                value,
                function=function,
                state=state,
                before_position=before_position,
                trail=(*trail, f"or[{index}]"),
                helper_stack=helper_stack,
                helper_depth=helper_depth,
            )
            domain = domain.union(value_domain)
            final_nullable = value_domain.nullable
        return _ValueDomain(
            origins=domain.origins,
            forwarding=domain.forwarding,
            nullable=final_nullable,
        )
    if isinstance(node, ast.Name):
        if _is_parameter(function.node, node.id) and _parameter_writes(
            function.node,
            node.id,
        ):
            raise StaticContractAnalysisError(
                f"{key}: Error helper parameter {node.id!r} is reassigned"
            )
        flow = _reaching_definitions(function.node, node.id, before_position)
        if flow.definitions:
            if flow.unbound:
                raise StaticContractAnalysisError(
                    f"{key}: {node.id!r} may be unbound at Error sink"
                )
            domain = _ValueDomain()
            for definition in flow.definitions:
                domain = domain.union(
                    _evaluate_code(
                        definition.value,
                        function=function,
                        state=state,
                        before_position=definition.position,
                        trail=(*trail, node.id),
                        helper_stack=helper_stack,
                        helper_depth=helper_depth,
                    )
                )
            return domain
        if _is_parameter(function.node, node.id):
            return _resolve_helper_parameter(
                helper=function,
                parameter=node.id,
                state=state,
                trail=trail,
                helper_stack=helper_stack,
                helper_depth=helper_depth + 1,
            )
        module_value = state.module_constants.get(function.relative_path, {}).get(node.id)
        if module_value is not None:
            return _evaluate_code(
                module_value,
                function=function,
                state=state,
                before_position=(0, 0),
                trail=(*trail, f"module:{node.id}"),
                helper_stack=helper_stack,
                helper_depth=helper_depth,
            )
        raise StaticContractAnalysisError(f"{key}: unresolved Error code name {node.id!r}")
    if isinstance(node, ast.Attribute):
        nullable = _require_typed_forwarding(
            node,
            function=function,
            state=state,
            before_position=before_position,
            key=key,
        )
        return _ValueDomain(
            forwarding=frozenset({_forwarding_label(node, function)}),
            nullable=nullable,
        )
    if isinstance(node, ast.JoinedStr):
        return _evaluate_fstring(
            node,
            function=function,
            state=state,
            before_position=before_position,
            trail=trail,
            helper_stack=helper_stack,
            helper_depth=helper_depth,
        )
    raise StaticContractAnalysisError(f"{key}: unsupported Error code expression {ast.dump(node)}")


def _evaluate_fstring(
    node: ast.JoinedStr,
    *,
    function: _FunctionInfo,
    state: _AnalysisState,
    before_position: SourcePosition,
    trail: tuple[str, ...],
    helper_stack: tuple[tuple[str, str, str], ...],
    helper_depth: int,
) -> _ValueDomain:
    domains: list[ErrorCodeDomain] = []
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            domains.append(frozenset({part.value}))
            continue
        if isinstance(part, ast.FormattedValue):
            if part.conversion != -1 or part.format_spec is not None:
                raise StaticContractAnalysisError(
                    f"{' -> '.join(trail)}: Error code f-strings cannot use format conversions "
                    "or format specifiers"
                )
            domains.append(
                _evaluate_string_fragment(
                    part.value,
                    function=function,
                    state=state,
                    before_position=before_position,
                    trail=(*trail, "f-string"),
                    helper_stack=helper_stack,
                    helper_depth=helper_depth,
                )
            )
            continue
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: unsupported Error code f-string component"
        )
    product_size = 1
    for domain in domains:
        product_size *= len(domain)
        if product_size > state.max_domain:
            raise StaticContractAnalysisError(
                f"{' -> '.join(trail)}: Error code domain exceeds {state.max_domain}"
            )
    result = frozenset("".join(parts) for parts in itertools.product(*domains))
    for code in result:
        _literal_domain(code, state, " -> ".join(trail))
    return _ValueDomain(origins=result)


def _evaluate_string_fragment(
    node: ast.expr,
    *,
    function: _FunctionInfo,
    state: _AnalysisState,
    before_position: SourcePosition,
    trail: tuple[str, ...],
    helper_stack: tuple[tuple[str, str, str], ...],
    helper_depth: int,
) -> ErrorCodeDomain:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return frozenset({node.value})
    if isinstance(node, ast.Name):
        flow = _reaching_definitions(function.node, node.id, before_position)
        if flow.definitions:
            if flow.unbound:
                raise StaticContractAnalysisError(
                    f"{' -> '.join(trail)}: {node.id!r} may be unbound in Error f-string"
                )
            result: set[str] = set()
            for definition in flow.definitions:
                result.update(
                    _evaluate_string_fragment(
                        definition.value,
                        function=function,
                        state=state,
                        before_position=definition.position,
                        trail=(*trail, node.id),
                        helper_stack=helper_stack,
                        helper_depth=helper_depth,
                    )
                )
            return frozenset(result)
        if _is_parameter(function.node, node.id):
            return _resolve_fragment_parameter(
                helper=function,
                parameter=node.id,
                state=state,
                trail=trail,
                helper_stack=helper_stack,
                helper_depth=helper_depth + 1,
            )
        module_value = state.module_constants.get(function.relative_path, {}).get(node.id)
        if module_value is not None:
            return _evaluate_string_fragment(
                module_value,
                function=function,
                state=state,
                before_position=(0, 0),
                trail=(*trail, f"module:{node.id}"),
                helper_stack=helper_stack,
                helper_depth=helper_depth,
            )
    if isinstance(node, ast.IfExp):
        return _evaluate_string_fragment(
            node.body,
            function=function,
            state=state,
            before_position=before_position,
            trail=(*trail, "if-true"),
            helper_stack=helper_stack,
            helper_depth=helper_depth,
        ) | _evaluate_string_fragment(
            node.orelse,
            function=function,
            state=state,
            before_position=before_position,
            trail=(*trail, "if-false"),
            helper_stack=helper_stack,
            helper_depth=helper_depth,
        )
    raise StaticContractAnalysisError(
        f"{' -> '.join(trail)}: unresolved Error f-string fragment {ast.unparse(node)!r}"
    )


def _resolve_helper_parameter(
    *,
    helper: _FunctionInfo,
    parameter: str,
    state: _AnalysisState,
    trail: tuple[str, ...],
    helper_stack: tuple[tuple[str, str, str], ...],
    helper_depth: int,
) -> _ValueDomain:
    if helper_depth > state.max_helper_depth:
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: Error helper depth exceeds {state.max_helper_depth}"
        )
    if _parameter_writes(helper.node, parameter):
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: Error helper parameter {parameter!r} is reassigned"
        )
    identity = (helper.relative_path, helper.qualified_name, parameter)
    if identity in helper_stack:
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: Error helper cycle detected at {helper.qualified_name}"
        )
    _reject_indirect_helper_references(helper, state)
    calls = _helper_calls(helper, state)
    if not calls:
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: dynamic Error helper has no finite reviewed callers"
        )
    domain = _ValueDomain()
    for caller, call, ordinal in calls:
        caller_key = (
            f"{caller.relative_path}::{caller.qualified_name}#{helper.node.name}[{ordinal}]"
        )
        argument, uses_default = _bound_argument(helper, call, parameter, caller_key)
        evaluation_function = helper if uses_default else caller
        evaluation_position = (
            _node_position(helper.node) if uses_default else _node_position(argument)
        )
        resolved = _evaluate_code(
            argument,
            function=evaluation_function,
            state=state,
            before_position=evaluation_position,
            trail=(*trail, caller_key),
            helper_stack=(*helper_stack, identity),
            helper_depth=helper_depth,
        )
        if resolved.origins:
            existing = state.helper_caller_codes.get(caller_key, frozenset())
            state.helper_caller_codes[caller_key] = existing | resolved.origins
        domain = domain.union(resolved)
    return domain


def _resolve_fragment_parameter(
    *,
    helper: _FunctionInfo,
    parameter: str,
    state: _AnalysisState,
    trail: tuple[str, ...],
    helper_stack: tuple[tuple[str, str, str], ...],
    helper_depth: int,
) -> ErrorCodeDomain:
    if helper_depth > state.max_helper_depth:
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: Error helper depth exceeds {state.max_helper_depth}"
        )
    if _parameter_writes(helper.node, parameter):
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: Error helper parameter {parameter!r} is reassigned"
        )
    identity = (helper.relative_path, helper.qualified_name, parameter)
    if identity in helper_stack:
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: Error helper cycle detected at {helper.qualified_name}"
        )
    calls = _helper_calls(helper, state)
    if not calls:
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: Error f-string helper has no finite reviewed callers"
        )
    result: set[str] = set()
    for caller, call, ordinal in calls:
        caller_key = (
            f"{caller.relative_path}::{caller.qualified_name}#{helper.node.name}[{ordinal}]"
        )
        argument, uses_default = _bound_argument(helper, call, parameter, caller_key)
        evaluation_function = helper if uses_default else caller
        evaluation_position = (
            _node_position(helper.node) if uses_default else _node_position(argument)
        )
        values = _evaluate_string_fragment(
            argument,
            function=evaluation_function,
            state=state,
            before_position=evaluation_position,
            trail=(*trail, caller_key),
            helper_stack=(*helper_stack, identity),
            helper_depth=helper_depth,
        )
        result.update(values)
    if len(result) > state.max_domain:
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: Error fragment domain exceeds {state.max_domain}"
        )
    return frozenset(result)


def _helper_calls(
    helper: _FunctionInfo,
    state: _AnalysisState,
) -> list[tuple[_FunctionInfo, ast.Call, int]]:
    result: list[tuple[_FunctionInfo, ast.Call, int]] = []
    for caller in state.functions:
        matches = [
            call
            for call in _calls_in_function(caller.node)
            if _is_helper_call(call, caller, helper, state)
        ]
        result.extend((caller, call, ordinal) for ordinal, call in enumerate(matches, start=1))
    return result


def _is_helper_call(
    call: ast.Call,
    caller: _FunctionInfo,
    helper: _FunctionInfo,
    state: _AnalysisState,
) -> bool:
    if _is_nested_function(helper):
        return _is_nested_helper_call(call, caller, helper)
    if helper.class_name is not None:
        if (
            not isinstance(call.func, ast.Attribute)
            or call.func.attr != helper.node.name
            or not _is_supported_helper_receiver(call.func.value)
        ):
            return False
        if caller.relative_path != helper.relative_path or caller.class_name is None:
            return False
        if _is_super_receiver(call.func.value):
            return _super_resolves_to_helper(caller, helper, state)
        return _class_is_or_inherits(
            caller.relative_path,
            caller.class_name,
            helper.relative_path,
            helper.class_name,
            state,
        ) and not _class_overrides_method_before_ancestor(
            caller.relative_path,
            caller.class_name,
            helper.relative_path,
            helper.class_name,
            helper.node.name,
            state,
        )
    if not isinstance(call.func, ast.Name):
        return False
    if _function_binding(caller.node, call.func.id) is not None:
        return False
    helper_symbol = f"{_module_name(helper.relative_path)}.{helper.node.name}"
    if caller.relative_path == helper.relative_path:
        return call.func.id == helper.node.name
    return (
        state.imported_symbols.get(caller.relative_path, {}).get(call.func.id, "") == helper_symbol
    )


def _is_nested_function(function: _FunctionInfo) -> bool:
    return len(function.qualified_name.split(".")) > (2 if function.class_name else 1)


def _is_nested_helper_call(
    call: ast.Call,
    caller: _FunctionInfo,
    helper: _FunctionInfo,
) -> bool:
    if (
        caller.relative_path != helper.relative_path
        or not isinstance(call.func, ast.Name)
        or call.func.id != helper.node.name
    ):
        return False
    parent = helper.qualified_name.rpartition(".")[0]
    caller_parts = caller.qualified_name.split(".")
    parent_parts = parent.split(".")
    if caller_parts[: len(parent_parts)] != parent_parts:
        return False
    binding = _function_binding(caller.node, call.func.id)
    return binding is None or binding is helper.node


def _is_supported_helper_receiver(node: ast.expr) -> bool:
    return bool(isinstance(node, ast.Name) and node.id in {"self", "cls"}) or _is_super_receiver(
        node
    )


def _is_super_receiver(node: ast.expr) -> bool:
    return bool(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "super"
        and not node.args
        and not node.keywords
    )


def _super_resolves_to_helper(
    caller: _FunctionInfo,
    helper: _FunctionInfo,
    state: _AnalysisState,
) -> bool:
    if caller.class_name is None or helper.class_name is None:
        return False
    caller_class = f"{_module_name(caller.relative_path)}.{caller.class_name}"
    helper_class = f"{_module_name(helper.relative_path)}.{helper.class_name}"
    definition = state.class_definitions.get(caller_class)
    if definition is None:
        return False
    resolved = [
        parent
        for parent in _resolved_class_bases(
            definition,
            state.class_definitions,
            state.imported_symbols,
        )
        if _class_defines_method(parent, helper.node.name, state)
    ]
    return resolved == [helper_class]


def _class_overrides_method_before_ancestor(
    candidate_path: str,
    candidate_name: str,
    ancestor_path: str,
    ancestor_name: str,
    method: str,
    state: _AnalysisState,
    trail: frozenset[str] = frozenset(),
) -> bool:
    candidate = f"{_module_name(candidate_path)}.{candidate_name}"
    ancestor = f"{_module_name(ancestor_path)}.{ancestor_name}"
    if candidate == ancestor or candidate in trail:
        return False
    if _class_defines_method(candidate, method, state):
        return True
    definition = state.class_definitions.get(candidate)
    if definition is None:
        return False
    return any(
        parent != ancestor
        and _class_overrides_method_before_ancestor(
            state.class_definitions[parent].relative_path,
            state.class_definitions[parent].qualified_name,
            ancestor_path,
            ancestor_name,
            method,
            state,
            trail | {candidate},
        )
        for parent in _resolved_class_bases(
            definition,
            state.class_definitions,
            state.imported_symbols,
        )
        if parent in state.class_definitions
    )


def _class_defines_method(
    qualified: str,
    method: str,
    state: _AnalysisState,
) -> bool:
    definition = state.class_definitions.get(qualified)
    return bool(
        definition is not None
        and any(
            isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
            and statement.name == method
            for statement in definition.node.body
        )
    )


def _call_resolves_to_different_method(
    call: ast.Call,
    caller: _FunctionInfo,
    helper: _FunctionInfo,
    state: _AnalysisState,
) -> bool:
    if not isinstance(call.func, ast.Attribute) or caller.class_name is None:
        return False
    if _is_super_receiver(call.func.value):
        return not _super_resolves_to_helper(caller, helper, state)
    if not isinstance(call.func.value, ast.Name) or call.func.value.id not in {
        "self",
        "cls",
    }:
        return False
    return _class_overrides_method_before_ancestor(
        caller.relative_path,
        caller.class_name,
        helper.relative_path,
        helper.class_name or "",
        helper.node.name,
        state,
    )


def _class_is_or_inherits(
    candidate_path: str,
    candidate_name: str,
    ancestor_path: str,
    ancestor_name: str,
    state: _AnalysisState,
    trail: frozenset[str] = frozenset(),
) -> bool:
    candidate = f"{_module_name(candidate_path)}.{candidate_name}"
    ancestor = f"{_module_name(ancestor_path)}.{ancestor_name}"
    if candidate == ancestor:
        return True
    if candidate in trail:
        return False
    definition = state.class_definitions.get(candidate)
    if definition is None:
        return False
    return any(
        _class_is_or_inherits(
            state.class_definitions[parent].relative_path,
            state.class_definitions[parent].qualified_name,
            ancestor_path,
            ancestor_name,
            state,
            trail | {candidate},
        )
        for parent in _resolved_class_bases(
            definition,
            state.class_definitions,
            state.imported_symbols,
        )
        if parent in state.class_definitions
    )


def _reject_indirect_helper_references(
    helper: _FunctionInfo,
    state: _AnalysisState,
) -> None:
    helper_symbol = f"{_module_name(helper.relative_path)}.{helper.node.name}"
    for function in state.functions:
        for node in _runtime_nodes_in_function(function.node):
            if helper.class_name is not None:
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == helper.node.name
                    and _is_supported_helper_receiver(node.value)
                    and isinstance(node.ctx, ast.Load)
                    and not any(call.func is node for call in _calls_in_function(function.node))
                ):
                    raise StaticContractAnalysisError(
                        f"{function.relative_path}::{function.qualified_name}: Error helper "
                        f"{helper.node.name!r} escapes a direct call at line {node.lineno}"
                    )
                if (
                    function.relative_path == helper.relative_path
                    and isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == helper.node.name
                    and not _is_helper_call(node, function, helper, state)
                    and not _call_resolves_to_different_method(
                        node,
                        function,
                        helper,
                        state,
                    )
                ):
                    raise StaticContractAnalysisError(
                        f"{function.relative_path}::{function.qualified_name}: Error helper "
                        f"{helper.node.name!r} uses an unsupported receiver at line {node.lineno}"
                    )
                continue
            if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
                continue
            bound = (
                helper_symbol
                if function.relative_path == helper.relative_path and node.id == helper.node.name
                else state.imported_symbols.get(function.relative_path, {}).get(node.id, "")
            )
            if bound != helper_symbol:
                continue
            if not any(call.func is node for call in _calls_in_function(function.node)):
                raise StaticContractAnalysisError(
                    f"{function.relative_path}::{function.qualified_name}: Error helper "
                    f"{helper.node.name!r} escapes a direct call at line {node.lineno}"
                )


def _bound_argument(
    helper: _FunctionInfo,
    call: ast.Call,
    parameter: str,
    key: str,
) -> tuple[ast.expr, bool]:
    if any(isinstance(argument, ast.Starred) for argument in call.args) or any(
        item.arg is None for item in call.keywords
    ):
        raise StaticContractAnalysisError(f"{key}: Error helpers cannot use *args or **kwargs")
    parameters = [*helper.node.args.posonlyargs, *helper.node.args.args]
    if (
        parameters
        and helper.class_name is not None
        and parameters[0].arg in {"self", "cls"}
        and not _function_has_decorator(helper.node, "staticmethod")
    ):
        parameters = parameters[1:]
    position = next(
        (index for index, item in enumerate(parameters) if item.arg == parameter),
        None,
    )
    positional = call.args[position] if position is not None and position < len(call.args) else None
    named = next((item.value for item in call.keywords if item.arg == parameter), None)
    if positional is not None and named is not None:
        raise StaticContractAnalysisError(f"{key}: duplicate Error helper argument")
    value = positional or named
    if value is not None:
        return value, False
    default = _parameter_default(helper.node, parameter)
    if default is None:
        raise StaticContractAnalysisError(f"{key}: missing Error helper argument {parameter!r}")
    return default, True


def _function_has_decorator(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
) -> bool:
    return any(
        isinstance(decorator, ast.Name) and decorator.id == name
        for decorator in function.decorator_list
    )


def _parameter_default(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    parameter: str,
) -> ast.expr | None:
    positional = [*function.args.posonlyargs, *function.args.args]
    defaults = [None] * (len(positional) - len(function.args.defaults)) + list(
        function.args.defaults
    )
    for item, default in zip(positional, defaults, strict=True):
        if item.arg == parameter:
            return default
    for item, default in zip(function.args.kwonlyargs, function.args.kw_defaults, strict=True):
        if item.arg == parameter:
            return default
    return None


def _require_typed_forwarding(
    node: ast.Attribute,
    *,
    function: _FunctionInfo,
    state: _AnalysisState,
    before_position: SourcePosition,
    key: str,
) -> bool:
    expression = ast.unparse(node)
    if not isinstance(node.value, ast.Name):
        raise StaticContractAnalysisError(
            f"{key}: unsupported untyped Error code forwarding {expression!r}"
        )
    carrier_types = _narrowed_attribute_carrier_types(node, function, state)
    if not carrier_types:
        carrier_types = _expression_types(
            node.value,
            function=function,
            state=state,
            before_position=before_position,
            trail=(expression,),
        )
    carrier_annotation = _parameter_annotation(function.node, node.value.id)
    if (
        carrier_annotation is not None
        and _annotation_allows_none(carrier_annotation)
        and not _name_is_guarded_non_none(
            function.node,
            node.value.id,
            _node_position(node),
        )
    ):
        raise StaticContractAnalysisError(
            f"{key}: non-nullable Error sink may receive None through {expression!r}"
        )
    allowed = (
        node.attr == "code"
        and bool(carrier_types)
        and all(_is_obsion_error_type(carrier, state) for carrier in carrier_types)
    ) or (
        node.attr in _ORM_FIELD_NAMES
        and bool(carrier_types)
        and all(
            (carrier, node.attr) in _FORWARDING_CARRIER_FIELDS
            or _orm_carrier_field(carrier, node.attr, state)
            for carrier in carrier_types
        )
    )
    if not allowed:
        raise StaticContractAnalysisError(
            f"{key}: unsupported untyped Error code forwarding {expression!r}; "
            f"inferred carriers={sorted(carrier_types)!r}"
        )
    return any(
        _forwarding_field_is_nullable(carrier, node.attr, state) for carrier in carrier_types
    )


def _forwarding_field_is_nullable(
    carrier: str,
    field: str,
    state: _AnalysisState,
) -> bool:
    if (carrier, field) in _FORWARDING_CARRIER_FIELDS:
        sink = state.result_sinks.get(carrier)
        return sink is None or sink.nullable
    prefix = "obsion.db.models."
    if not carrier.startswith(prefix):
        return False
    definition = state.orm_fields.get((carrier.removeprefix(prefix), field))
    return definition is None or definition.nullable


def _name_is_guarded_non_none(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    position: SourcePosition,
) -> bool:
    for statement in function.body:
        if not _position_before(statement, position):
            break
        if (
            isinstance(statement, ast.If)
            and _test_is_name_none(statement.test, name)
            and _block_always_exits(statement.body)
        ):
            return True
        if (
            isinstance(statement, ast.If)
            and _test_is_name_not_none(statement.test, name)
            and _block_contains_position(statement.body, position)
            and not _name_is_reassigned_in_block_before(statement.body, name, position)
        ):
            return True
    return False


def _test_is_name_none(test: ast.expr, name: str) -> bool:
    return bool(
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == name
        and len(test.ops) == len(test.comparators) == 1
        and isinstance(test.ops[0], ast.Is)
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
    )


def _test_is_name_not_none(test: ast.expr, name: str) -> bool:
    return bool(
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == name
        and len(test.ops) == len(test.comparators) == 1
        and isinstance(test.ops[0], ast.IsNot)
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
    )


def _block_always_exits(statements: list[ast.stmt]) -> bool:
    return bool(statements) and isinstance(statements[-1], (ast.Raise, ast.Return))


def _narrowed_attribute_carrier_types(
    node: ast.Attribute,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> QualifiedTypeDomain:
    if not isinstance(node.value, ast.Name):
        return frozenset()
    result: set[str] = set()
    for expression in (
        candidate
        for candidate in _runtime_nodes_in_function(function.node)
        if isinstance(candidate, ast.IfExp)
    ):
        if not any(item is node for item in ast.walk(expression.body)):
            continue
        result.update(
            _isinstance_guard_types(
                expression.test,
                node.value.id,
                function,
                state,
            )
        )
    for statement in (
        candidate
        for candidate in _runtime_nodes_in_function(function.node)
        if isinstance(candidate, ast.If)
    ):
        if not _block_contains_position(statement.body, _node_position(node)):
            continue
        if _name_is_reassigned_in_block_before(
            statement.body,
            node.value.id,
            _node_position(node),
        ):
            continue
        result.update(
            _isinstance_guard_types(
                statement.test,
                node.value.id,
                function,
                state,
            )
        )
    return frozenset(result)


def _name_is_reassigned_in_block_before(
    statements: list[ast.stmt],
    name: str,
    position: SourcePosition,
) -> bool:
    for statement in statements:
        if not _position_before(statement, position):
            continue
        end = (
            getattr(statement, "end_lineno", statement.lineno),
            getattr(statement, "end_col_offset", statement.col_offset),
        )
        if position <= end:
            if _statement_header_binds_name(statement, name):
                return True
            return any(
                _name_is_reassigned_in_block_before(block, name, position)
                for block in _containing_statement_blocks(statement, position)
            )
        if _statement_binds_name(statement, name):
            return True
    return False


def _containing_statement_blocks(
    statement: ast.stmt,
    position: SourcePosition,
) -> tuple[list[ast.stmt], ...]:
    blocks: list[list[ast.stmt]] = []
    if isinstance(statement, (ast.If, ast.For, ast.AsyncFor, ast.While)):
        blocks.extend((statement.body, statement.orelse))
    elif isinstance(statement, (ast.With, ast.AsyncWith)):
        blocks.append(statement.body)
    elif isinstance(statement, (ast.Try, ast.TryStar)):
        blocks.extend((statement.body, statement.orelse, statement.finalbody))
        blocks.extend(handler.body for handler in statement.handlers)
    elif isinstance(statement, ast.Match):
        blocks.extend(case.body for case in statement.cases)
    return tuple(block for block in blocks if _block_contains_position(block, position))


def _statement_header_binds_name(statement: ast.stmt, name: str) -> bool:
    if isinstance(statement, (ast.For, ast.AsyncFor)):
        return _target_binds_name(statement.target, name)
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        return any(
            item.optional_vars is not None and _target_binds_name(item.optional_vars, name)
            for item in statement.items
        )
    return False


def _statement_binds_name(statement: ast.stmt, name: str) -> bool:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return statement.name == name
    return (
        any(
            isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, (ast.Store, ast.Del))
            for node in ast.walk(statement)
        )
        or any(
            isinstance(node, ast.ExceptHandler) and node.name == name
            for node in ast.walk(statement)
        )
        or any(
            isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name
            for node in ast.walk(statement)
        )
        or any(
            isinstance(node, ast.MatchMapping) and node.rest == name for node in ast.walk(statement)
        )
    )


def _module_binding_is_shadowed(
    relative_path: str,
    name: str,
    state: _AnalysisState,
) -> bool:
    local_symbol = f"{_module_name(relative_path)}.{name}"
    return bool(
        state.imported_symbols.get(relative_path, {}).get(name) == local_symbol
        or name in state.module_constants.get(relative_path, {})
    )


def _isinstance_guard_types(
    test: ast.expr,
    variable: str,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> QualifiedTypeDomain:
    if (
        not isinstance(test, ast.Call)
        or not isinstance(test.func, ast.Name)
        or test.func.id != "isinstance"
        or _function_binding(function.node, "isinstance") is not None
        or _module_binding_is_shadowed(
            function.relative_path,
            "isinstance",
            state,
        )
        or len(test.args) != 2
        or not isinstance(test.args[0], ast.Name)
        or test.args[0].id != variable
    ):
        return frozenset()
    return _annotation_types(test.args[1], function, state)


def _expression_types(
    node: ast.expr,
    *,
    function: _FunctionInfo,
    state: _AnalysisState,
    before_position: SourcePosition,
    trail: tuple[str, ...],
) -> QualifiedTypeDomain:
    if isinstance(node, ast.Await):
        return _expression_types(
            node.value,
            function=function,
            state=state,
            before_position=before_position,
            trail=trail,
        )
    if isinstance(node, ast.Name):
        flow = _reaching_definitions(
            function.node,
            node.id,
            before_position,
            allow_iteration_binding=True,
        )
        if flow.definitions:
            if flow.unbound:
                raise StaticContractAnalysisError(
                    f"{' -> '.join(trail)}: forwarding carrier {node.id!r} may be unbound"
                )
            flow_types: set[str] = set()
            for definition in flow.definitions:
                flow_types.update(
                    _expression_types(
                        definition.value,
                        function=function,
                        state=state,
                        before_position=definition.position,
                        trail=(*trail, node.id),
                    )
                )
            return frozenset(flow_types)
        exception_types = _exception_binding_types(
            function,
            node.id,
            before_position,
            state,
        )
        if exception_types:
            return exception_types
        iteration_types = _iteration_variable_types(
            function,
            node.id,
            before_position,
            state,
        )
        if iteration_types:
            return iteration_types
        annotation = _parameter_annotation(function.node, node.id)
        if annotation is not None:
            return _annotation_types(annotation, function, state)
        if _is_parameter(function.node, node.id):
            raise StaticContractAnalysisError(
                f"{' -> '.join(trail)}: forwarding parameter {node.id!r} has no trusted type"
            )
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: unresolved forwarding carrier {node.id!r}"
        )
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in {"self", "cls"}
    ):
        attribute_types: set[str] = set()
        for owner in state.functions:
            if (
                owner.relative_path != function.relative_path
                or owner.class_name != function.class_name
            ):
                continue
            for assignment in _assignments_in_function(owner.node):
                target = _assigned_attribute(assignment)
                if (
                    target is None
                    or target.attr != node.attr
                    or not isinstance(target.value, ast.Name)
                    or target.value.id not in {"self", "cls"}
                    or assignment.value is None
                ):
                    continue
                attribute_types.update(
                    _expression_types(
                        assignment.value,
                        function=owner,
                        state=state,
                        before_position=_node_position(assignment),
                        trail=(*trail, node.attr),
                    )
                )
        if attribute_types:
            return frozenset(attribute_types)
        raise StaticContractAnalysisError(
            f"{' -> '.join(trail)}: unresolved self attribute type {ast.unparse(node)!r}"
        )
    if isinstance(node, ast.IfExp):
        return _expression_types(
            node.body,
            function=function,
            state=state,
            before_position=before_position,
            trail=(*trail, "if-true"),
        ) | _expression_types(
            node.orelse,
            function=function,
            state=state,
            before_position=before_position,
            trail=(*trail, "if-false"),
        )
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "next"
            and node.args
            and isinstance(node.args[0], ast.GeneratorExp)
        ):
            generator_types: set[str] = set()
            for generator in node.args[0].generators:
                generator_types.update(
                    _collection_element_types(
                        generator.iter,
                        function=function,
                        state=state,
                        before_position=before_position,
                        trail=(*trail, "next"),
                    )
                )
            if generator_types:
                return frozenset(generator_types)
        returned = _function_return_types(node, function, state)
        if returned:
            return returned
        model = _model_from_expression(node, function, state)
        if model is not None:
            return frozenset({f"obsion.db.models.{model}"})
        if isinstance(node.func, ast.Name):
            qualified = state.imported_symbols.get(function.relative_path, {}).get(
                node.func.id,
                "",
            )
            if qualified in state.class_definitions:
                return frozenset({qualified})
        if isinstance(node.func, ast.Attribute):
            receiver_types = _expression_types(
                node.func.value,
                function=function,
                state=state,
                before_position=before_position,
                trail=(*trail, node.func.attr),
            )
            method_returns: set[str] = set()
            for receiver_type in receiver_types:
                method_returns.update(
                    _qualified_method_return_types(
                        receiver_type,
                        node.func.attr,
                        function,
                        state,
                    )
                )
            if method_returns:
                return frozenset(method_returns)
    raise StaticContractAnalysisError(
        f"{' -> '.join(trail)}: unsupported forwarding carrier expression {ast.unparse(node)!r}"
    )


def _exception_binding_types(
    function: _FunctionInfo,
    name: str,
    position: SourcePosition,
    state: _AnalysisState,
) -> QualifiedTypeDomain:
    result: set[str] = set()
    for node in _runtime_nodes_in_function(function.node):
        if not isinstance(node, ast.ExceptHandler) or node.name != name or node.type is None:
            continue
        if not _block_contains_position(node.body, position):
            continue
        result.update(_annotation_types(node.type, function, state))
    return frozenset(result)


def _iteration_variable_types(
    function: _FunctionInfo,
    name: str,
    position: SourcePosition,
    state: _AnalysisState,
) -> QualifiedTypeDomain:
    result: set[str] = set()
    for node in _runtime_nodes_in_function(function.node):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        if not _target_binds_name(node.target, name) or not _block_contains_position(
            node.body,
            position,
        ):
            continue
        result.update(
            _collection_element_types(
                node.iter,
                function=function,
                state=state,
                before_position=_node_position(node),
                trail=(f"for:{name}",),
            )
        )
    return frozenset(result)


def _collection_element_types(
    node: ast.expr,
    *,
    function: _FunctionInfo,
    state: _AnalysisState,
    before_position: SourcePosition,
    trail: tuple[str, ...],
) -> QualifiedTypeDomain:
    if isinstance(node, ast.Name):
        annotation = _parameter_annotation(function.node, node.id)
        if annotation is not None:
            annotated_types = _annotation_types(annotation, function, state)
            if annotated_types:
                return annotated_types
        flow = _reaching_definitions(function.node, node.id, before_position)
        result: set[str] = set()
        for definition in flow.definitions:
            result.update(
                _collection_element_types(
                    definition.value,
                    function=function,
                    state=state,
                    before_position=definition.position,
                    trail=(*trail, node.id),
                )
            )
        return frozenset(result)
    if isinstance(node, ast.ListComp):
        element_types: set[str] = set()
        for generator in node.generators:
            element_types.update(
                _collection_element_types(
                    generator.iter,
                    function=function,
                    state=state,
                    before_position=before_position,
                    trail=(*trail, "comprehension"),
                )
            )
        return frozenset(element_types)
    if isinstance(node, ast.Call):
        model = _model_from_expression(node, function, state)
        if model is not None:
            return frozenset({f"obsion.db.models.{model}"})
        referenced_models = {
            f"obsion.db.models.{model_name}"
            for model_name in _models_referenced(node, function, state)
        }
        if referenced_models:
            return frozenset(referenced_models)
    raise StaticContractAnalysisError(
        f"{' -> '.join(trail)}: unsupported forwarding collection {ast.unparse(node)!r}"
    )


def _qualified_method_return_types(
    receiver_type: str,
    method: str,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> QualifiedTypeDomain:
    definition = state.class_definitions.get(receiver_type)
    if definition is None:
        return frozenset()
    methods = [
        candidate
        for candidate in state.functions
        if candidate.relative_path == definition.relative_path
        and candidate.class_name == definition.qualified_name
        and candidate.node.name == method
    ]
    if len(methods) != 1 or methods[0].node.returns is None:
        return frozenset()
    return _annotation_types(methods[0].node.returns, methods[0], state)


def _function_return_types(
    call: ast.Call,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> QualifiedTypeDomain:
    definitions: list[_FunctionInfo] = []
    if isinstance(call.func, ast.Name):
        local = call.func.id
        binding = state.imported_symbols.get(function.relative_path, {}).get(local, "")
        local_symbol = f"{_module_name(function.relative_path)}.{local}"
        if binding != local_symbol or _function_binding(function.node, local) is not None:
            return frozenset()
        definitions = [
            candidate
            for candidate in state.functions
            if candidate.relative_path == function.relative_path
            and candidate.class_name is None
            and candidate.qualified_name == local
        ]
    elif (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id in {"self", "cls"}
        and function.class_name is not None
    ):
        definitions = [
            candidate
            for candidate in state.functions
            if candidate.relative_path == function.relative_path
            and candidate.class_name == function.class_name
            and candidate.node.name == call.func.attr
        ]
    if len(definitions) != 1 or definitions[0].node.returns is None:
        return frozenset()
    return _annotation_types(definitions[0].node.returns, definitions[0], state)


def _annotation_types(
    annotation: ast.expr,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> QualifiedTypeDomain:
    result: set[str] = set()
    bindings = state.imported_symbols.get(function.relative_path, {})
    for node in (
        candidate for candidate in ast.walk(annotation) if isinstance(candidate, ast.Name)
    ):
        qualified = bindings.get(node.id, "")
        if qualified not in state.class_definitions:
            raise StaticContractAnalysisError(
                f"{function.relative_path}::{function.qualified_name}: forwarding annotation "
                f"contains an untrusted type {node.id!r}"
            )
        result.add(qualified)
    return frozenset(result)


def _is_obsion_error_type(carrier: str, state: _AnalysisState) -> bool:
    if carrier == _OBSION_ERROR:
        return True
    definition = state.class_definitions.get(carrier)
    if definition is None:
        return False
    bindings = state.imported_symbols.get(definition.relative_path, {})
    for base in definition.node.bases:
        if isinstance(base, ast.Name):
            parent = bindings.get(base.id, "")
            if parent and _is_obsion_error_type(parent, state):
                return True
    return False


def _orm_carrier_field(carrier: str, field: str, state: _AnalysisState) -> bool:
    prefix = "obsion.db.models."
    return (
        carrier.startswith(prefix)
        and (
            carrier.removeprefix(prefix),
            field,
        )
        in state.orm_fields
    )


def _forwarding_label(node: ast.Attribute, function: _FunctionInfo) -> str:
    return f"forward:{function.relative_path}:{node.lineno}:{ast.unparse(node)}"


def _infer_assignment_model(
    function: _FunctionInfo,
    target: ast.Attribute,
    assignment: ast.Assign | ast.AnnAssign,
    state: _AnalysisState,
) -> str | None:
    root = target.value
    if not isinstance(root, ast.Name):
        return None
    annotation = _parameter_annotation(function.node, root.id)
    if annotation is not None:
        models = _annotation_models(annotation, function, state)
        if len(models) == 1:
            return next(iter(models))
    iteration_models = _iteration_models(function.node, root.id, assignment, function, state)
    if len(iteration_models) == 1:
        return next(iter(iteration_models))
    for candidate in _reaching_definitions(
        function.node,
        root.id,
        _node_position(assignment),
        allow_iteration_binding=True,
    ).definitions:
        model = _model_from_expression(candidate.value, function, state)
        if model is not None:
            return model
    return _infer_model_from_query_use(function, root.id, state)


def _iteration_models(
    root: ast.FunctionDef | ast.AsyncFunctionDef,
    variable: str,
    assignment: ast.Assign | ast.AnnAssign,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> set[str]:
    result: set[str] = set()
    for node in _runtime_nodes_in_function(root):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        end = (
            getattr(node, "end_lineno", node.lineno),
            getattr(node, "end_col_offset", node.col_offset),
        )
        if not (_node_position(node) <= _node_position(assignment) <= end):
            continue
        if not _target_binds_name(node.target, variable):
            continue
        result.update(_models_for_comprehension_target(node.iter, variable, function, state))
        result.update(_models_referenced(node.iter, function, state))
        if isinstance(node.iter, ast.Name):
            annotation = _parameter_annotation(function.node, node.iter.id)
            if annotation is not None:
                result.update(_iterable_annotation_models(annotation, function, state))
            for definition in _reaching_definitions(
                root,
                node.iter.id,
                _node_position(node),
            ).definitions:
                result.update(
                    _models_in_collection_expression(
                        definition.value,
                        function,
                        state,
                        definition.position,
                    )
                )
    return result


def _models_in_collection_expression(
    node: ast.expr,
    function: _FunctionInfo,
    state: _AnalysisState,
    before_position: SourcePosition,
) -> set[str]:
    result = _models_referenced(node, function, state)
    for name in (candidate for candidate in ast.walk(node) if isinstance(candidate, ast.Name)):
        if name.id in state.imported_symbols.get(function.relative_path, {}):
            continue
        try:
            definitions = _reaching_definitions(
                function.node,
                name.id,
                before_position,
            ).definitions
        except StaticContractAnalysisError:
            continue
        for definition in definitions:
            result.update(_models_referenced(definition.value, function, state))
            for comprehension in (
                candidate
                for candidate in ast.walk(definition.value)
                if isinstance(candidate, ast.comprehension)
            ):
                result.update(_models_referenced(comprehension.iter, function, state))
    return result


def _models_for_comprehension_target(
    node: ast.expr,
    variable: str,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> set[str]:
    result: set[str] = set()
    for comprehension in (
        candidate for candidate in ast.walk(node) if isinstance(candidate, ast.comprehension)
    ):
        if not _target_binds_name(comprehension.target, variable):
            continue
        result.update(_models_referenced(comprehension.iter, function, state))
        if isinstance(comprehension.iter, ast.Name):
            for definition in _reaching_definitions(
                function.node,
                comprehension.iter.id,
                _node_position(comprehension),
            ).definitions:
                result.update(_models_referenced(definition.value, function, state))
    return result


def _annotation_model(
    annotation: ast.expr,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> str | None:
    models = _annotation_models(annotation, function, state)
    return next(iter(models)) if len(models) == 1 else None


def _annotation_models(
    annotation: ast.expr,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(annotation):
        if not isinstance(node, ast.Name):
            continue
        qualified = state.imported_symbols.get(function.relative_path, {}).get(node.id, "")
        model = _local_model_name(qualified, state)
        if model is not None:
            result.add(model)
    return result


def _iterable_annotation_models(
    annotation: ast.expr,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> set[str]:
    if not isinstance(annotation, ast.Subscript):
        return set()
    container = _annotation_root_name(annotation.value)
    if container not in {
        "AsyncIterable",
        "AsyncIterator",
        "Collection",
        "Iterable",
        "Iterator",
        "Sequence",
        "list",
        "set",
        "tuple",
    }:
        return set()
    return _annotation_models(annotation.slice, function, state)


def _annotation_root_name(annotation: ast.expr) -> str | None:
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    return None


def _model_from_expression(
    node: ast.expr,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> str | None:
    if isinstance(node, ast.Await):
        return _model_from_expression(node.value, function, state)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            qualified = state.imported_symbols.get(function.relative_path, {}).get(
                node.func.id,
                "",
            )
            model = _local_model_name(qualified, state)
            if model is not None:
                return model
        if isinstance(node.func, ast.Name):
            functions = [
                candidate
                for candidate in state.functions
                if candidate.class_name is None
                and candidate.node.name == node.func.id
                and (
                    candidate.relative_path == function.relative_path
                    or state.imported_symbols.get(function.relative_path, {}).get(
                        node.func.id,
                        "",
                    )
                    == f"{_module_name(candidate.relative_path)}.{candidate.node.name}"
                )
            ]
            if len(functions) == 1 and functions[0].node.returns is not None:
                model = _annotation_model(functions[0].node.returns, function, state)
                if model is not None:
                    return model
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"self", "cls"}
            and function.class_name is not None
        ):
            methods = [
                candidate
                for candidate in state.functions
                if candidate.relative_path == function.relative_path
                and candidate.class_name == function.class_name
                and candidate.node.name == node.func.attr
            ]
            if len(methods) == 1 and methods[0].node.returns is not None:
                model = _annotation_model(methods[0].node.returns, function, state)
                if model is not None:
                    return model
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"scalar", "scalars"}
            and node.args
        ):
            query_models = _models_referenced(node.args[0], function, state)
            if len(query_models) == 1:
                return next(iter(query_models))
    return None


def _infer_model_from_query_use(
    function: _FunctionInfo,
    variable: str,
    state: _AnalysisState,
) -> str | None:
    models: set[str] = set()
    for call in (
        node for node in _runtime_nodes_in_function(function.node) if isinstance(node, ast.Call)
    ):
        if not any(
            isinstance(candidate, ast.Name)
            and candidate.id == variable
            and isinstance(candidate.ctx, ast.Load)
            for candidate in ast.walk(call)
        ):
            continue
        if not _is_supported_orm_query_call(call):
            continue
        models.update(_models_referenced(call, function, state))
    return next(iter(models)) if len(models) == 1 else None


def _is_supported_orm_query_call(call: ast.Call) -> bool:
    return bool(
        isinstance(call.func, ast.Attribute)
        and call.func.attr in {"add", "add_all", "delete", "merge", "refresh"}
    )


def _models_referenced(
    node: ast.AST,
    function: _FunctionInfo,
    state: _AnalysisState,
) -> set[str]:
    result: set[str] = set()
    for candidate in ast.walk(node):
        if not isinstance(candidate, ast.Name):
            continue
        qualified = state.imported_symbols.get(function.relative_path, {}).get(candidate.id, "")
        model = _local_model_name(qualified, state)
        if model is not None:
            result.add(model)
    return result


def _class_field_position(model: str, field: str, state: _AnalysisState) -> int:
    definition = state.orm_fields.get((model, field))
    return definition.position if definition is not None else -1


def _reject_unreviewed_error_calls(
    trees: Mapping[str, ast.Module],
    functions: tuple[_FunctionInfo, ...],
    state: _AnalysisState,
    observed: set[int],
) -> None:
    scoped = {id(call) for function in functions for call in _calls_in_function(function.node)}
    relevant_symbols = {
        qualified.rpartition(".")[2] for qualified in (*state.call_sinks, *state.result_sinks)
    } | {model for model, _ in state.orm_fields}
    for relative_path, tree in trees.items():
        bindings = state.imported_symbols.get(relative_path, {})
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            if not isinstance(call.func, ast.Name):
                continue
            local = call.func.id
            qualified = bindings.get(local, "")
            symbol = qualified.rpartition(".")[2]
            if symbol not in relevant_symbols:
                continue
            if id(call) not in scoped:
                raise StaticContractAnalysisError(
                    f"{relative_path}:{call.lineno}: Error sink call is outside a reviewed function"
                )
            if (qualified in state.call_sinks or qualified in state.result_sinks) and id(
                call
            ) not in observed:
                raise StaticContractAnalysisError(
                    f"{relative_path}:{call.lineno}: canonical Error sink was not analyzed"
                )


def _reject_unreviewed_persisted_assignments(
    trees: Mapping[str, ast.Module],
    functions: tuple[_FunctionInfo, ...],
    state: _AnalysisState,
    observed: set[tuple[int, int]],
) -> None:
    scoped = {
        id(assignment)
        for function in functions
        for assignment in _assignments_in_function(function.node)
    }
    for relative_path, tree in trees.items():
        for augmented in (node for node in ast.walk(tree) if isinstance(node, ast.AugAssign)):
            if (
                isinstance(augmented.target, ast.Attribute)
                and augmented.target.attr in _ORM_FIELD_NAMES
            ):
                raise StaticContractAnalysisError(
                    f"{relative_path}:{augmented.lineno}: augmented persisted Error field "
                    "writes are not supported"
                )
        for assignment in (
            node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))
        ):
            for attribute in _assigned_attributes(assignment):
                if attribute.attr not in _ORM_FIELD_NAMES:
                    continue
                if relative_path.endswith("db/models.py") and isinstance(assignment, ast.AnnAssign):
                    continue
                if id(assignment) not in scoped:
                    raise StaticContractAnalysisError(
                        f"{relative_path}:{assignment.lineno}: persisted Error field write is "
                        "outside a reviewed function"
                    )
                if (id(assignment), id(attribute)) not in observed:
                    raise StaticContractAnalysisError(
                        f"{relative_path}:{assignment.lineno}: persisted Error field write was "
                        "not bound to a typed ORM model"
                    )


def _sink_key(function: _FunctionInfo, sink: str, ordinal: int) -> str:
    return f"{function.relative_path}::{function.qualified_name}#{sink}[{ordinal}]"


def _assignment_target_value(
    assignment: ast.Assign | ast.AnnAssign,
    attribute: ast.Attribute,
) -> ast.expr:
    value = assignment.value
    assert value is not None
    targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
    for target in targets:
        resolved = _destructured_target_value(target, value, attribute)
        if resolved is not None:
            return resolved
    return value


def _destructured_target_value(
    target: ast.expr,
    value: ast.expr,
    attribute: ast.Attribute,
) -> ast.expr | None:
    if target is attribute:
        return value
    if not isinstance(target, (ast.Tuple, ast.List)):
        return None
    if not isinstance(value, (ast.Tuple, ast.List)) or len(target.elts) != len(value.elts):
        raise StaticContractAnalysisError(
            f"persisted Error field destructuring at line {target.lineno} requires a "
            "literal tuple/list value with matching arity"
        )
    for target_item, value_item in zip(target.elts, value.elts, strict=True):
        if any(candidate is attribute for candidate in ast.walk(target_item)):
            return _destructured_target_value(target_item, value_item, attribute)
    return None


def _assigned_attributes(
    assignment: ast.Assign | ast.AnnAssign,
) -> list[ast.Attribute]:
    result: list[ast.Attribute] = []
    for target in _root_targets(assignment):
        _collect_assigned_attributes(target, result)
    return result


def _collect_assigned_attributes(
    target: ast.expr,
    result: list[ast.Attribute],
) -> None:
    if isinstance(target, ast.Attribute):
        result.append(target)
        return
    if isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            _collect_assigned_attributes(item, result)
        return
    if isinstance(target, ast.Starred):
        _collect_assigned_attributes(target.value, result)


def _assigned_attribute(
    assignment: ast.Assign | ast.AnnAssign,
) -> ast.Attribute | None:
    matches = _assigned_attributes(assignment)
    return matches[0] if len(matches) == 1 else None


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

    def visit_Lambda(self, node: ast.Lambda) -> None:
        if any(
            isinstance(candidate, ast.Call)
            and _call_name(candidate.func)
            in {
                *(sink.symbol for sink in _CALL_SINKS),
                *(sink.symbol for sink in _RESULT_SINKS),
            }
            for candidate in ast.walk(node.body)
        ):
            raise StaticContractAnalysisError(
                f"Error sinks inside lambdas are not supported at line {node.lineno}"
            )

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._reject_comprehension_calls(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._reject_comprehension_calls(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._reject_comprehension_calls(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._reject_comprehension_calls(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        del node

    def _reject_comprehension_calls(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    ) -> None:
        if any(
            isinstance(candidate, ast.Call)
            and _call_name(candidate.func)
            in {
                *(sink.symbol for sink in _CALL_SINKS),
                *(sink.symbol for sink in _RESULT_SINKS),
            }
            for candidate in ast.walk(node)
        ):
            raise StaticContractAnalysisError(
                f"Error sinks inside comprehensions are not supported at line {node.lineno}"
            )
        self.nodes.append(node)
        self.generic_visit(node)


def _runtime_nodes_in_function(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.AST]:
    collector = _FunctionNodeCollector(function)
    for statement in function.body:
        collector.visit(statement)
    return collector.nodes


def _calls_in_function(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    return sorted(
        (node for node in _runtime_nodes_in_function(function) if isinstance(node, ast.Call)),
        key=_node_position,
    )


def _assignments_in_function(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.Assign | ast.AnnAssign]:
    return sorted(
        (
            node
            for node in _runtime_nodes_in_function(function)
            if isinstance(node, ast.Assign)
            or (isinstance(node, ast.AnnAssign) and node.value is not None)
        ),
        key=_node_position,
    )


def _node_position(node: ast.AST) -> SourcePosition:
    return getattr(node, "lineno", 0), getattr(node, "col_offset", 0)


def _position_before(node: ast.AST, position: SourcePosition) -> bool:
    return _node_position(node) < position


def _reaching_definitions(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    before_position: SourcePosition,
    *,
    allow_iteration_binding: bool = False,
) -> _DefinitionFlow:
    initial = _DefinitionFlow(unbound=not _is_parameter(function, name))
    return _analyze_definition_block(
        function.body,
        name=name,
        before_position=before_position,
        incoming=initial,
        allow_iteration_binding=allow_iteration_binding,
    )


def _analyze_definition_block(
    statements: list[ast.stmt],
    *,
    name: str,
    before_position: SourcePosition,
    incoming: _DefinitionFlow,
    allow_iteration_binding: bool = False,
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
            allow_iteration_binding=allow_iteration_binding,
        )
    return flow


def _statement_expression_containing_position(
    statement: ast.stmt,
    position: SourcePosition,
) -> ast.expr | None:
    return next(
        (
            expression
            for expression in _statement_runtime_expressions(statement)
            if _node_contains_position(expression, position)
        ),
        None,
    )


def _statement_runtime_expressions(statement: ast.stmt) -> tuple[ast.expr, ...]:
    expressions: list[ast.expr] = []
    if isinstance(statement, (ast.Assign, ast.Expr)):
        expressions.append(statement.value)
    elif isinstance(statement, (ast.AnnAssign, ast.Return)):
        if statement.value is not None:
            expressions.append(statement.value)
    elif isinstance(statement, ast.Raise):
        if statement.exc is not None:
            expressions.append(statement.exc)
        if statement.cause is not None:
            expressions.append(statement.cause)
    elif isinstance(statement, ast.Assert):
        expressions.append(statement.test)
        if statement.msg is not None:
            expressions.append(statement.msg)
    elif isinstance(statement, (ast.If, ast.While)):
        expressions.append(statement.test)
    elif isinstance(statement, (ast.For, ast.AsyncFor)):
        expressions.append(statement.iter)
    elif isinstance(statement, (ast.With, ast.AsyncWith)):
        expressions.extend(item.context_expr for item in statement.items)
    elif isinstance(statement, ast.Match):
        expressions.append(statement.subject)
        expressions.extend(case.guard for case in statement.cases if case.guard is not None)
    elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        expressions.extend(statement.decorator_list)
        expressions.extend(statement.args.defaults)
        expressions.extend(default for default in statement.args.kw_defaults if default is not None)
    elif isinstance(statement, ast.ClassDef):
        expressions.extend(statement.decorator_list)
        expressions.extend(statement.bases)
        expressions.extend(keyword.value for keyword in statement.keywords)
    return tuple(expressions)


def _node_contains_position(node: ast.AST, position: SourcePosition) -> bool:
    return (
        _node_position(node)
        <= position
        <= (
            getattr(node, "end_lineno", getattr(node, "lineno", 0)),
            getattr(node, "end_col_offset", getattr(node, "col_offset", 0)),
        )
    )


def _transfer_definition_statement(
    statement: ast.stmt,
    *,
    name: str,
    before_position: SourcePosition,
    incoming: _DefinitionFlow,
    allow_iteration_binding: bool = False,
) -> _DefinitionFlow:
    containing_expression = _statement_expression_containing_position(
        statement,
        before_position,
    )
    if containing_expression is not None:
        return _transfer_named_expressions_before_position(
            containing_expression,
            position=before_position,
            name=name,
            incoming=incoming,
        )
    assigned = _simple_assignment(statement, name)
    if assigned is not None:
        return _DefinitionFlow(
            definitions=(_ReachingDefinition(assigned, _node_position(statement)),),
            unbound=False,
        )
    expression_flow = _transfer_statement_named_expressions(
        statement,
        name=name,
        incoming=incoming,
    )
    if expression_flow is not incoming:
        incoming = expression_flow
    if _unsupported_assignment(statement, name):
        raise StaticContractAnalysisError(
            f"{name} uses an unsupported Error reaching definition at line {statement.lineno}"
        )
    if isinstance(statement, ast.If):
        test_flow = _transfer_named_expressions(
            statement.test,
            name=name,
            incoming=incoming,
        )
        containing = _containing_block((statement.body, statement.orelse), before_position)
        if containing is not None:
            return _analyze_definition_block(
                containing,
                name=name,
                before_position=before_position,
                incoming=test_flow,
                allow_iteration_binding=allow_iteration_binding,
            )
        return _join_definition_flows(
            (
                _analyze_definition_block(
                    statement.body,
                    name=name,
                    before_position=before_position,
                    incoming=test_flow,
                    allow_iteration_binding=allow_iteration_binding,
                ),
                _analyze_definition_block(
                    statement.orelse,
                    name=name,
                    before_position=before_position,
                    incoming=test_flow,
                    allow_iteration_binding=allow_iteration_binding,
                ),
            )
        )
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        if any(
            item.optional_vars is not None and _target_binds_name(item.optional_vars, name)
            for item in statement.items
        ):
            raise StaticContractAnalysisError(
                f"{name} uses an unsupported with-as Error definition at line {statement.lineno}"
            )
        context_flow = incoming
        for item in statement.items:
            context_flow = _transfer_named_expressions(
                item.context_expr,
                name=name,
                incoming=context_flow,
            )
        return _analyze_definition_block(
            statement.body,
            name=name,
            before_position=before_position,
            incoming=context_flow,
            allow_iteration_binding=allow_iteration_binding,
        )
    if isinstance(statement, (ast.Try, ast.TryStar)):
        try_flow, exceptional_flows = _analyze_try_definition_flows(
            statement.body,
            name=name,
            before_position=before_position,
            incoming=incoming,
            allow_iteration_binding=allow_iteration_binding,
        )
        handler_flow = _join_definition_flows(exceptional_flows) if exceptional_flows else incoming
        preceding_handler_flow = handler_flow
        for handler in statement.handlers:
            if _block_contains_position(handler.body, before_position):
                handler_input = (
                    _DefinitionFlow() if handler.name == name else preceding_handler_flow
                )
                return _analyze_definition_block(
                    handler.body,
                    name=name,
                    before_position=before_position,
                    incoming=handler_input,
                    allow_iteration_binding=allow_iteration_binding,
                )
            if isinstance(statement, ast.TryStar):
                if handler.name == name:
                    raise StaticContractAnalysisError(
                        f"{name} uses an unsupported except-as Error definition at line "
                        f"{handler.lineno}"
                    )
                preceding_handler_flow = _analyze_definition_block(
                    handler.body,
                    name=name,
                    before_position=before_position,
                    incoming=preceding_handler_flow,
                    allow_iteration_binding=allow_iteration_binding,
                )
        if _block_contains_position(statement.body, before_position):
            return _analyze_definition_block(
                statement.body,
                name=name,
                before_position=before_position,
                incoming=incoming,
                allow_iteration_binding=allow_iteration_binding,
            )
        if _block_contains_position(statement.orelse, before_position):
            return _analyze_definition_block(
                statement.orelse,
                name=name,
                before_position=before_position,
                incoming=try_flow,
                allow_iteration_binding=allow_iteration_binding,
            )
        normal = _analyze_definition_block(
            statement.orelse,
            name=name,
            before_position=before_position,
            incoming=try_flow,
            allow_iteration_binding=allow_iteration_binding,
        )
        handlers: list[_DefinitionFlow] = []
        preceding_handler_flow = handler_flow
        for handler in statement.handlers:
            if handler.name == name:
                raise StaticContractAnalysisError(
                    f"{name} uses an unsupported except-as Error definition at line "
                    f"{handler.lineno}"
                )
            analyzed_handler = _analyze_definition_block(
                handler.body,
                name=name,
                before_position=before_position,
                incoming=preceding_handler_flow,
                allow_iteration_binding=allow_iteration_binding,
            )
            handlers.append(analyzed_handler)
            if isinstance(statement, ast.TryStar):
                preceding_handler_flow = analyzed_handler
        joined = _join_definition_flows((normal, *handlers))
        final_input = joined
        if statement.finalbody:
            exit_flows = _definition_exit_flows(
                statement.body,
                name=name,
                before_position=before_position,
                incoming=incoming,
                allow_iteration_binding=allow_iteration_binding,
            )
            exit_flows.extend(
                flow
                for handler in statement.handlers
                for flow in _definition_exit_flows(
                    handler.body,
                    name=name,
                    before_position=before_position,
                    incoming=handler_flow,
                    allow_iteration_binding=allow_iteration_binding,
                )
            )
            final_input = _join_definition_flows(
                (
                    joined,
                    *exceptional_flows,
                    *(
                        _DefinitionFlow(
                            definitions=flow.definitions,
                            unbound=flow.unbound,
                        )
                        for flow in exit_flows
                    ),
                )
            )
        return _analyze_definition_block(
            statement.finalbody,
            name=name,
            before_position=before_position,
            incoming=final_input,
            allow_iteration_binding=allow_iteration_binding,
        )
    if isinstance(statement, ast.Match):
        case_flows, fallthrough = _match_definition_flows(
            statement,
            name=name,
            incoming=incoming,
        )
        containing_case = next(
            (
                (case, body_input)
                for case, _, body_input in case_flows
                if _block_contains_position(case.body, before_position)
            ),
            None,
        )
        if containing_case is not None:
            case, body_input = containing_case
            return _analyze_definition_block(
                case.body,
                name=name,
                before_position=before_position,
                incoming=body_input,
                allow_iteration_binding=allow_iteration_binding,
            )
        return _join_definition_flows(
            (
                *(
                    _analyze_definition_block(
                        case.body,
                        name=name,
                        before_position=before_position,
                        incoming=body_input,
                        allow_iteration_binding=allow_iteration_binding,
                    )
                    for case, _, body_input in case_flows
                ),
                fallthrough,
            )
        )
    if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
        test_flow = incoming
        if isinstance(statement, ast.While):
            test_flow = _transfer_named_expressions(
                statement.test,
                name=name,
                incoming=incoming,
            )
        containing = _containing_block((statement.body, statement.orelse), before_position)
        if containing is not None:
            if (
                not allow_iteration_binding
                and isinstance(statement, (ast.For, ast.AsyncFor))
                and _target_binds_name(statement.target, name)
            ):
                raise StaticContractAnalysisError(
                    f"{name} uses an unsupported loop Error definition at line {statement.lineno}"
                )
            loop_input = test_flow
            if containing is statement.body:
                loop_input = _loop_entry_flow(
                    statement.body,
                    name=name,
                    before_position=before_position,
                    incoming=test_flow,
                    allow_iteration_binding=allow_iteration_binding,
                )
            return _analyze_definition_block(
                containing,
                name=name,
                before_position=before_position,
                incoming=loop_input,
                allow_iteration_binding=allow_iteration_binding,
            )
        if (
            not allow_iteration_binding
            and isinstance(statement, (ast.For, ast.AsyncFor))
            and _target_binds_name(statement.target, name)
        ):
            raise StaticContractAnalysisError(
                f"{name} uses an unsupported loop Error definition at line {statement.lineno}"
            )
        body = _analyze_definition_block(
            statement.body,
            name=name,
            before_position=before_position,
            incoming=test_flow,
            allow_iteration_binding=allow_iteration_binding,
        )
        exit_flows = _definition_exit_flows(
            statement.body,
            name=name,
            before_position=before_position,
            incoming=test_flow,
            allow_iteration_binding=allow_iteration_binding,
        )
        break_flows = [flow for flow in exit_flows if flow.terminator == "break"]
        iteration_flows = [flow for flow in exit_flows if flow.terminator == "continue"]
        if body.reachable:
            iteration_flows.append(body)
        iteration_flows = _loop_iteration_closure(
            statement.body,
            name=name,
            before_position=before_position,
            incoming=test_flow,
            initial=iteration_flows,
            allow_iteration_binding=allow_iteration_binding,
        )
        no_break = _analyze_definition_block(
            statement.orelse,
            name=name,
            before_position=before_position,
            incoming=_join_definition_flows((test_flow, *iteration_flows)),
            allow_iteration_binding=allow_iteration_binding,
        )
        return _join_definition_flows(
            (
                no_break,
                *(
                    _DefinitionFlow(
                        definitions=flow.definitions,
                        unbound=flow.unbound,
                    )
                    for flow in break_flows
                ),
            )
        )
    if isinstance(statement, (ast.Break, ast.Continue)):
        return _DefinitionFlow(
            definitions=incoming.definitions,
            unbound=incoming.unbound,
            reachable=False,
            terminator="break" if isinstance(statement, ast.Break) else "continue",
        )
    if isinstance(statement, (ast.Return, ast.Raise)):
        return _DefinitionFlow(
            definitions=incoming.definitions,
            unbound=incoming.unbound,
            reachable=False,
            terminator="raise" if isinstance(statement, ast.Raise) else "return",
        )
    return incoming


def _loop_entry_flow(
    statements: list[ast.stmt],
    *,
    name: str,
    before_position: SourcePosition,
    incoming: _DefinitionFlow,
    allow_iteration_binding: bool,
) -> _DefinitionFlow:
    current = incoming
    for _ in range(2):
        body = _analyze_definition_block(
            statements,
            name=name,
            before_position=(10**12, 10**12),
            incoming=current,
            allow_iteration_binding=allow_iteration_binding,
        )
        exits = _definition_exit_flows(
            statements,
            name=name,
            before_position=(10**12, 10**12),
            incoming=current,
            allow_iteration_binding=allow_iteration_binding,
        )
        candidates = [flow for flow in exits if flow.terminator == "continue"]
        if body.reachable:
            candidates.append(body)
        next_flow = _join_definition_flows((current, *candidates))
        if _definition_domain_key(next_flow) == _definition_domain_key(current):
            break
        current = next_flow
    return current


def _loop_iteration_closure(
    statements: list[ast.stmt],
    *,
    name: str,
    before_position: SourcePosition,
    incoming: _DefinitionFlow,
    initial: list[_DefinitionFlow],
    allow_iteration_binding: bool,
) -> list[_DefinitionFlow]:
    flows = list(initial)
    current = _join_definition_flows((incoming, *initial))
    for _ in range(2):
        body = _analyze_definition_block(
            statements,
            name=name,
            before_position=before_position,
            incoming=current,
            allow_iteration_binding=allow_iteration_binding,
        )
        exits = _definition_exit_flows(
            statements,
            name=name,
            before_position=before_position,
            incoming=current,
            allow_iteration_binding=allow_iteration_binding,
        )
        candidates = [flow for flow in exits if flow.terminator == "continue"]
        if body.reachable:
            candidates.append(body)
        next_flow = _join_definition_flows((current, *candidates))
        if _definition_domain_key(next_flow) == _definition_domain_key(current):
            break
        flows.extend(candidates)
        current = next_flow
    return flows


def _definition_domain_key(
    flow: _DefinitionFlow,
) -> tuple[frozenset[tuple[int, int, int]], bool]:
    return (
        frozenset((*definition.position, id(definition.value)) for definition in flow.definitions),
        flow.unbound,
    )


def _transfer_statement_named_expressions(
    statement: ast.stmt,
    *,
    name: str,
    incoming: _DefinitionFlow,
) -> _DefinitionFlow:
    expressions: list[ast.expr] = []
    if (
        isinstance(statement, (ast.Assign, ast.Expr))
        or isinstance(statement, (ast.AnnAssign, ast.Return))
        and statement.value is not None
    ):
        assert statement.value is not None
        expressions.append(statement.value)
    elif isinstance(statement, ast.Raise):
        if statement.exc is not None:
            expressions.append(statement.exc)
        if statement.cause is not None:
            expressions.append(statement.cause)
    elif isinstance(statement, ast.Assert):
        expressions.append(statement.test)
        if statement.msg is not None:
            expressions.append(statement.msg)
    elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        expressions.extend(statement.decorator_list)
        expressions.extend(statement.args.defaults)
        expressions.extend(default for default in statement.args.kw_defaults if default is not None)
    elif isinstance(statement, ast.ClassDef):
        expressions.extend(statement.decorator_list)
        expressions.extend(statement.bases)
        expressions.extend(keyword.value for keyword in statement.keywords)
    elif isinstance(statement, (ast.For, ast.AsyncFor)):
        expressions.append(statement.iter)
    if not expressions:
        return incoming
    flow = incoming
    for expression in expressions:
        flow = _transfer_named_expressions(
            expression,
            name=name,
            incoming=flow,
        )
    return flow


def _transfer_named_expressions_before_position(
    expression: ast.expr,
    *,
    position: SourcePosition,
    name: str,
    incoming: _DefinitionFlow,
) -> _DefinitionFlow:
    if not _node_contains_position(expression, position):
        return _transfer_named_expressions(
            expression,
            name=name,
            incoming=incoming,
        )
    if _node_position(expression) == position:
        return incoming
    if isinstance(expression, ast.NamedExpr):
        return _transfer_named_expressions_before_position(
            expression.value,
            position=position,
            name=name,
            incoming=incoming,
        )
    if isinstance(expression, ast.Call):
        children = (
            expression.func,
            *expression.args,
            *(keyword.value for keyword in expression.keywords),
        )
        return _transfer_expression_children_before_position(
            children,
            position=position,
            name=name,
            incoming=incoming,
        )
    if isinstance(expression, ast.Dict):
        children = tuple(
            child
            for key, value in zip(expression.keys, expression.values, strict=True)
            for child in ((key, value) if key is not None else (value,))
        )
        return _transfer_expression_children_before_position(
            children,
            position=position,
            name=name,
            incoming=incoming,
        )
    if isinstance(expression, ast.IfExp):
        if _node_contains_position(expression.test, position):
            return _transfer_named_expressions_before_position(
                expression.test,
                position=position,
                name=name,
                incoming=incoming,
            )
        test_flow = _transfer_named_expressions(
            expression.test,
            name=name,
            incoming=incoming,
        )
        branch = (
            expression.body
            if _node_contains_position(expression.body, position)
            else expression.orelse
        )
        return _transfer_named_expressions_before_position(
            branch,
            position=position,
            name=name,
            incoming=test_flow,
        )
    if isinstance(expression, ast.BoolOp):
        return _transfer_expression_children_before_position(
            tuple(expression.values),
            position=position,
            name=name,
            incoming=incoming,
        )
    return _transfer_expression_children_before_position(
        tuple(child for child in ast.iter_child_nodes(expression) if isinstance(child, ast.expr)),
        position=position,
        name=name,
        incoming=incoming,
    )


def _transfer_expression_children_before_position(
    children: tuple[ast.expr, ...],
    *,
    position: SourcePosition,
    name: str,
    incoming: _DefinitionFlow,
) -> _DefinitionFlow:
    flow = incoming
    for child in children:
        if _node_contains_position(child, position):
            return _transfer_named_expressions_before_position(
                child,
                position=position,
                name=name,
                incoming=flow,
            )
        flow = _transfer_named_expressions(
            child,
            name=name,
            incoming=flow,
        )
    return flow


def _transfer_named_expressions(
    expression: ast.expr,
    *,
    name: str,
    incoming: _DefinitionFlow,
) -> _DefinitionFlow:
    if isinstance(expression, ast.NamedExpr):
        flow = _transfer_named_expressions(
            expression.value,
            name=name,
            incoming=incoming,
        )
        if not _target_binds_name(expression.target, name):
            return flow
        return _DefinitionFlow(
            definitions=(_ReachingDefinition(expression.value, _node_position(expression)),),
            unbound=False,
        )
    if isinstance(expression, (ast.BoolOp, ast.IfExp)):
        return _join_definition_flows(
            _expression_control_flow_branches(
                expression,
                name=name,
                incoming=incoming,
            )
        )
    if isinstance(expression, ast.Compare):
        flow = _transfer_named_expressions(
            expression.left,
            name=name,
            incoming=incoming,
        )
        branches: list[_DefinitionFlow] = []
        for comparator in expression.comparators:
            flow = _transfer_named_expressions(
                comparator,
                name=name,
                incoming=flow,
            )
            branches.append(flow)
        return _join_definition_flows(tuple(branches))
    if isinstance(expression, ast.Call):
        flow = _transfer_named_expressions(
            expression.func,
            name=name,
            incoming=incoming,
        )
        for argument in expression.args:
            flow = _transfer_named_expressions(
                argument,
                name=name,
                incoming=flow,
            )
        for keyword in expression.keywords:
            flow = _transfer_named_expressions(
                keyword.value,
                name=name,
                incoming=flow,
            )
        return flow
    flow = incoming
    for child in ast.iter_child_nodes(expression):
        if isinstance(child, ast.expr):
            flow = _transfer_named_expressions(
                child,
                name=name,
                incoming=flow,
            )
    return flow


def _expression_exception_flows(
    expression: ast.expr,
    *,
    name: str,
    incoming: _DefinitionFlow,
) -> list[_DefinitionFlow]:
    if isinstance(expression, ast.NamedExpr):
        return _expression_exception_flows(
            expression.value,
            name=name,
            incoming=incoming,
        )
    if isinstance(expression, ast.BoolOp):
        result: list[_DefinitionFlow] = []
        flow = incoming
        for value in expression.values:
            result.extend(
                _expression_exception_flows(
                    value,
                    name=name,
                    incoming=flow,
                )
            )
            flow = _transfer_named_expressions(
                value,
                name=name,
                incoming=flow,
            )
        return result
    if isinstance(expression, ast.IfExp):
        result = _expression_exception_flows(
            expression.test,
            name=name,
            incoming=incoming,
        )
        test_flow = _transfer_named_expressions(
            expression.test,
            name=name,
            incoming=incoming,
        )
        result.extend(
            flow
            for branch in (expression.body, expression.orelse)
            for flow in _expression_exception_flows(
                branch,
                name=name,
                incoming=test_flow,
            )
        )
        return result
    if isinstance(expression, ast.Call):
        children = (
            expression.func,
            *expression.args,
            *(keyword.value for keyword in expression.keywords),
        )
        result, flow = _sequential_expression_exception_flows(
            children,
            name=name,
            incoming=incoming,
        )
        result.append(_normal_definition_flow(flow))
        return result
    if isinstance(expression, ast.Dict):
        result = []
        flow = incoming
        for key, value in zip(expression.keys, expression.values, strict=True):
            if key is not None:
                child_result, flow = _sequential_expression_exception_flows(
                    (key,),
                    name=name,
                    incoming=flow,
                )
                result.extend(child_result)
            child_result, flow = _sequential_expression_exception_flows(
                (value,),
                name=name,
                incoming=flow,
            )
            result.extend(child_result)
            if key is None:
                result.append(_normal_definition_flow(flow))
        return result
    if isinstance(expression, (ast.Constant, ast.Name, ast.Lambda)):
        return []
    children = tuple(
        child for child in ast.iter_child_nodes(expression) if isinstance(child, ast.expr)
    )
    result, flow = _sequential_expression_exception_flows(
        children,
        name=name,
        incoming=incoming,
    )
    if _expression_operation_may_raise(expression):
        result.append(_normal_definition_flow(flow))
    return result


def _sequential_expression_exception_flows(
    expressions: tuple[ast.expr, ...],
    *,
    name: str,
    incoming: _DefinitionFlow,
) -> tuple[list[_DefinitionFlow], _DefinitionFlow]:
    result: list[_DefinitionFlow] = []
    flow = incoming
    for expression in expressions:
        result.extend(
            _expression_exception_flows(
                expression,
                name=name,
                incoming=flow,
            )
        )
        flow = _transfer_named_expressions(
            expression,
            name=name,
            incoming=flow,
        )
    return result, flow


def _expression_operation_may_raise(expression: ast.expr) -> bool:
    return not isinstance(
        expression,
        (
            ast.BoolOp,
            ast.Dict,
            ast.IfExp,
            ast.List,
            ast.NamedExpr,
            ast.Set,
            ast.Tuple,
        ),
    )


def _expression_control_flow_branches(
    expression: ast.BoolOp | ast.IfExp,
    *,
    name: str,
    incoming: _DefinitionFlow,
) -> tuple[_DefinitionFlow, ...]:
    if isinstance(expression, ast.IfExp):
        test = _transfer_named_expressions(
            expression.test,
            name=name,
            incoming=incoming,
        )
        return (
            _transfer_named_expressions(expression.body, name=name, incoming=test),
            _transfer_named_expressions(expression.orelse, name=name, incoming=test),
        )
    flows: list[_DefinitionFlow] = [incoming]
    current = incoming
    for value in expression.values:
        current = _transfer_named_expressions(
            value,
            name=name,
            incoming=current,
        )
        flows.append(current)
    return tuple(flows)


def _match_definition_flows(
    statement: ast.Match,
    *,
    name: str,
    incoming: _DefinitionFlow,
) -> tuple[
    tuple[tuple[ast.match_case, _DefinitionFlow, _DefinitionFlow], ...],
    _DefinitionFlow,
]:
    subject_flow = _transfer_named_expressions(
        statement.subject,
        name=name,
        incoming=incoming,
    )
    if any(_pattern_binds_name(case.pattern, name) for case in statement.cases):
        raise StaticContractAnalysisError(
            f"{name} uses an unsupported match Error definition at line {statement.lineno}"
        )
    cases: list[tuple[ast.match_case, _DefinitionFlow, _DefinitionFlow]] = []
    fallthrough = subject_flow
    for case in statement.cases:
        case_input = fallthrough
        body_input = (
            _transfer_named_expressions(
                case.guard,
                name=name,
                incoming=case_input,
            )
            if case.guard is not None
            else case_input
        )
        cases.append((case, case_input, body_input))
        if case.guard is not None:
            fallthrough = _join_definition_flows((case_input, body_input))
    return tuple(cases), fallthrough


def _analyze_try_definition_flows(
    statements: list[ast.stmt],
    *,
    name: str,
    before_position: SourcePosition,
    incoming: _DefinitionFlow,
    allow_iteration_binding: bool,
) -> tuple[_DefinitionFlow, tuple[_DefinitionFlow, ...]]:
    flow = incoming
    exceptional: list[_DefinitionFlow] = []
    for statement in statements:
        if not flow.reachable or not _position_before(statement, before_position):
            break
        exceptional.extend(
            _definition_exception_flows(
                statement,
                name=name,
                before_position=before_position,
                incoming=flow,
                allow_iteration_binding=allow_iteration_binding,
            )
        )
        flow = _transfer_definition_statement(
            statement,
            name=name,
            before_position=before_position,
            incoming=flow,
            allow_iteration_binding=allow_iteration_binding,
        )
    return flow, tuple(exceptional)


def _definition_exception_flows(
    statement: ast.stmt,
    *,
    name: str,
    before_position: SourcePosition,
    incoming: _DefinitionFlow,
    allow_iteration_binding: bool,
) -> list[_DefinitionFlow]:
    evaluated = _transfer_statement_named_expressions(
        statement,
        name=name,
        incoming=incoming,
    )
    runtime_expressions = _statement_runtime_expressions(statement)
    expression_exception_flows: list[_DefinitionFlow] = []
    expression_flow = incoming
    for expression in runtime_expressions:
        expression_exception_flows.extend(
            _expression_exception_flows(
                expression,
                name=name,
                incoming=expression_flow,
            )
        )
        expression_flow = _transfer_named_expressions(
            expression,
            name=name,
            incoming=expression_flow,
        )
    if isinstance(statement, (ast.For, ast.AsyncFor)):
        result = _expression_exception_flows(
            statement.iter,
            name=name,
            incoming=incoming,
        )
        result.append(_normal_definition_flow(evaluated))
    elif isinstance(statement, ast.ClassDef):
        expressions = (
            *statement.decorator_list,
            *statement.bases,
            *(keyword.value for keyword in statement.keywords),
        )
        result, class_header_flow = _sequential_expression_exception_flows(
            expressions,
            name=name,
            incoming=incoming,
        )
        result.append(_normal_definition_flow(class_header_flow))
    else:
        result = expression_exception_flows
        if _statement_may_raise_implicitly(statement):
            result.append(_normal_definition_flow(evaluated))
    if isinstance(statement, ast.Raise):
        result.append(_normal_definition_flow(evaluated))
        return result
    if isinstance(statement, ast.If):
        test_flow = _transfer_named_expressions(
            statement.test,
            name=name,
            incoming=incoming,
        )
        result.extend(
            _normal_definition_flow(flow)
            for branch in (statement.body, statement.orelse)
            for flow in _definition_exit_flows(
                branch,
                name=name,
                before_position=before_position,
                incoming=test_flow,
                allow_iteration_binding=allow_iteration_binding,
            )
            if flow.terminator == "raise"
        )
        result.extend(
            flow
            for branch in (statement.body, statement.orelse)
            for child in branch
            for flow in _definition_exception_flows(
                child,
                name=name,
                before_position=before_position,
                incoming=_flow_before_statement(
                    branch,
                    child,
                    name=name,
                    before_position=before_position,
                    incoming=test_flow,
                    allow_iteration_binding=allow_iteration_binding,
                ),
                allow_iteration_binding=allow_iteration_binding,
            )
        )
        return result
    if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
        loop_input = (
            _transfer_named_expressions(
                statement.test,
                name=name,
                incoming=incoming,
            )
            if isinstance(statement, ast.While)
            else incoming
        )
        result.extend(
            _normal_definition_flow(flow)
            for flow in _definition_exit_flows(
                statement.body,
                name=name,
                before_position=before_position,
                incoming=loop_input,
                allow_iteration_binding=allow_iteration_binding,
            )
            if flow.terminator == "raise"
        )
        result.extend(
            flow
            for child in statement.body
            for flow in _definition_exception_flows(
                child,
                name=name,
                before_position=before_position,
                incoming=_flow_before_statement(
                    statement.body,
                    child,
                    name=name,
                    before_position=before_position,
                    incoming=loop_input,
                    allow_iteration_binding=allow_iteration_binding,
                ),
                allow_iteration_binding=allow_iteration_binding,
            )
        )
        return result
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        context_flow = incoming
        for item in statement.items:
            context_flow = _transfer_named_expressions(
                item.context_expr,
                name=name,
                incoming=context_flow,
            )
        result.extend(
            _normal_definition_flow(flow)
            for flow in _definition_exit_flows(
                statement.body,
                name=name,
                before_position=before_position,
                incoming=context_flow,
                allow_iteration_binding=allow_iteration_binding,
            )
            if flow.terminator == "raise"
        )
        result.extend(
            flow
            for child in statement.body
            for flow in _definition_exception_flows(
                child,
                name=name,
                before_position=before_position,
                incoming=_flow_before_statement(
                    statement.body,
                    child,
                    name=name,
                    before_position=before_position,
                    incoming=context_flow,
                    allow_iteration_binding=allow_iteration_binding,
                ),
                allow_iteration_binding=allow_iteration_binding,
            )
        )
        return result
    if isinstance(statement, ast.Match):
        result.extend(
            _expression_exception_flows(
                statement.subject,
                name=name,
                incoming=incoming,
            )
        )
        case_flows, _ = _match_definition_flows(
            statement,
            name=name,
            incoming=incoming,
        )
        for case, case_input, _ in case_flows:
            if case.guard is not None:
                result.extend(
                    _expression_exception_flows(
                        case.guard,
                        name=name,
                        incoming=case_input,
                    )
                )
        result.extend(
            _normal_definition_flow(flow)
            for case, _, body_input in case_flows
            for flow in _definition_exit_flows(
                case.body,
                name=name,
                before_position=before_position,
                incoming=body_input,
                allow_iteration_binding=allow_iteration_binding,
            )
            if flow.terminator == "raise"
        )
        result.extend(
            flow
            for case, _, body_input in case_flows
            for child in case.body
            for flow in _definition_exception_flows(
                child,
                name=name,
                before_position=before_position,
                incoming=_flow_before_statement(
                    case.body,
                    child,
                    name=name,
                    before_position=before_position,
                    incoming=body_input,
                    allow_iteration_binding=allow_iteration_binding,
                ),
                allow_iteration_binding=allow_iteration_binding,
            )
        )
        return result
    if isinstance(statement, (ast.Try, ast.TryStar)):
        try_flow, nested_exceptional = _analyze_try_definition_flows(
            statement.body,
            name=name,
            before_position=before_position,
            incoming=incoming,
            allow_iteration_binding=allow_iteration_binding,
        )
        result.extend(nested_exceptional)
        handler_input = (
            _join_definition_flows(nested_exceptional) if nested_exceptional else incoming
        )
        for handler in statement.handlers:
            result.extend(
                flow
                for child in handler.body
                for flow in _definition_exception_flows(
                    child,
                    name=name,
                    before_position=before_position,
                    incoming=_flow_before_statement(
                        handler.body,
                        child,
                        name=name,
                        before_position=before_position,
                        incoming=handler_input,
                        allow_iteration_binding=allow_iteration_binding,
                    ),
                    allow_iteration_binding=allow_iteration_binding,
                )
            )
        normal = _analyze_definition_block(
            statement.orelse,
            name=name,
            before_position=before_position,
            incoming=try_flow,
            allow_iteration_binding=allow_iteration_binding,
        )
        final_input = _join_definition_flows((normal, handler_input))
        if statement.finalbody:
            for child in statement.finalbody:
                result.extend(
                    _definition_exception_flows(
                        child,
                        name=name,
                        before_position=before_position,
                        incoming=_flow_before_statement(
                            statement.finalbody,
                            child,
                            name=name,
                            before_position=before_position,
                            incoming=final_input,
                            allow_iteration_binding=allow_iteration_binding,
                        ),
                        allow_iteration_binding=allow_iteration_binding,
                    )
                )
        return result
    return result


def _normal_definition_flow(flow: _DefinitionFlow) -> _DefinitionFlow:
    return _DefinitionFlow(
        definitions=flow.definitions,
        unbound=flow.unbound,
    )


def _flow_before_statement(
    statements: list[ast.stmt],
    target: ast.stmt,
    *,
    name: str,
    before_position: SourcePosition,
    incoming: _DefinitionFlow,
    allow_iteration_binding: bool,
) -> _DefinitionFlow:
    flow = incoming
    for statement in statements:
        if statement is target or not flow.reachable:
            break
        flow = _transfer_definition_statement(
            statement,
            name=name,
            before_position=before_position,
            incoming=flow,
            allow_iteration_binding=allow_iteration_binding,
        )
    return flow


def _statement_may_raise_implicitly(statement: ast.stmt) -> bool:
    if isinstance(statement, (ast.Pass, ast.Break, ast.Continue)):
        return False
    if isinstance(statement, ast.Raise):
        return False
    if isinstance(statement, ast.Assign):
        return _expression_may_raise(statement.value) or any(
            not isinstance(target, ast.Name) for target in statement.targets
        )
    if isinstance(statement, ast.AnnAssign):
        return bool(
            statement.value is not None and _expression_may_raise(statement.value)
        ) or not isinstance(statement.target, ast.Name)
    if isinstance(statement, ast.NamedExpr):
        return _expression_may_raise(statement.value)
    if isinstance(statement, ast.Expr):
        return _expression_may_raise(statement.value)
    if isinstance(statement, ast.Return):
        return bool(statement.value is not None and _expression_may_raise(statement.value))
    if isinstance(statement, ast.Assert):
        return True
    if isinstance(statement, ast.If):
        return _expression_may_raise(statement.test)
    if isinstance(statement, ast.While):
        return _expression_may_raise(statement.test)
    if isinstance(statement, (ast.For, ast.AsyncFor)):
        return True
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        return True
    return not isinstance(statement, (ast.Try, ast.TryStar, ast.Match))


def _expression_may_raise(expression: ast.expr) -> bool:
    if isinstance(expression, (ast.Constant, ast.Name)):
        return False
    if isinstance(expression, (ast.Tuple, ast.List, ast.Set)):
        return any(_expression_may_raise(item) for item in expression.elts)
    if isinstance(expression, ast.Dict):
        return any(
            key is not None and _expression_may_raise(key) for key in expression.keys
        ) or any(_expression_may_raise(value) for value in expression.values)
    if isinstance(expression, ast.NamedExpr):
        return _expression_may_raise(expression.value)
    return True


def _definition_exit_flows(
    statements: list[ast.stmt],
    *,
    name: str,
    before_position: SourcePosition,
    incoming: _DefinitionFlow,
    allow_iteration_binding: bool,
) -> list[_DefinitionFlow]:
    flow = incoming
    exits: list[_DefinitionFlow] = []
    for statement in statements:
        if not flow.reachable or not _position_before(statement, before_position):
            break
        if isinstance(statement, ast.If):
            test_flow = _transfer_named_expressions(
                statement.test,
                name=name,
                incoming=flow,
            )
            branch_flows: list[_DefinitionFlow] = []
            for branch in (statement.body, statement.orelse):
                exits.extend(
                    _definition_exit_flows(
                        branch,
                        name=name,
                        before_position=before_position,
                        incoming=test_flow,
                        allow_iteration_binding=allow_iteration_binding,
                    )
                )
                branch_flows.append(
                    _analyze_definition_block(
                        branch,
                        name=name,
                        before_position=before_position,
                        incoming=test_flow,
                        allow_iteration_binding=allow_iteration_binding,
                    )
                )
            flow = _join_definition_flows(tuple(branch_flows))
            continue
        if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            body_flow = _analyze_definition_block(
                statement.body,
                name=name,
                before_position=before_position,
                incoming=flow,
                allow_iteration_binding=allow_iteration_binding,
            )
            exits.extend(
                _definition_exit_flows(
                    statement.body,
                    name=name,
                    before_position=before_position,
                    incoming=flow,
                    allow_iteration_binding=allow_iteration_binding,
                )
            )
            flow = _join_definition_flows((flow, body_flow))
            continue
        if isinstance(statement, ast.Match):
            case_flows, fallthrough = _match_definition_flows(
                statement,
                name=name,
                incoming=flow,
            )
            match_flows: list[_DefinitionFlow] = []
            for case, _, body_input in case_flows:
                exits.extend(
                    _definition_exit_flows(
                        case.body,
                        name=name,
                        before_position=before_position,
                        incoming=body_input,
                        allow_iteration_binding=allow_iteration_binding,
                    )
                )
                match_flows.append(
                    _analyze_definition_block(
                        case.body,
                        name=name,
                        before_position=before_position,
                        incoming=body_input,
                        allow_iteration_binding=allow_iteration_binding,
                    )
                )
            flow = _join_definition_flows((*match_flows, fallthrough))
            continue
        if isinstance(statement, (ast.Try, ast.TryStar)):
            try_flow, exceptional_flows = _analyze_try_definition_flows(
                statement.body,
                name=name,
                before_position=before_position,
                incoming=flow,
                allow_iteration_binding=allow_iteration_binding,
            )
            handler_input = _join_definition_flows(exceptional_flows) if exceptional_flows else flow
            exits.extend(
                _definition_exit_flows(
                    statement.body,
                    name=name,
                    before_position=before_position,
                    incoming=flow,
                    allow_iteration_binding=allow_iteration_binding,
                )
            )
            handler_flows: list[_DefinitionFlow] = []
            preceding_handler_flow = handler_input
            for handler in statement.handlers:
                current_handler_input = (
                    _DefinitionFlow() if handler.name == name else preceding_handler_flow
                )
                exits.extend(
                    _definition_exit_flows(
                        handler.body,
                        name=name,
                        before_position=before_position,
                        incoming=current_handler_input,
                        allow_iteration_binding=allow_iteration_binding,
                    )
                )
                analyzed_handler = _analyze_definition_block(
                    handler.body,
                    name=name,
                    before_position=before_position,
                    incoming=current_handler_input,
                    allow_iteration_binding=allow_iteration_binding,
                )
                handler_flows.append(analyzed_handler)
                if isinstance(statement, ast.TryStar):
                    preceding_handler_flow = analyzed_handler
            normal = _analyze_definition_block(
                statement.orelse,
                name=name,
                before_position=before_position,
                incoming=try_flow,
                allow_iteration_binding=allow_iteration_binding,
            )
            flow = _join_definition_flows((normal, *handler_flows))
            if statement.finalbody:
                final_input = _join_definition_flows(
                    (
                        flow,
                        *exceptional_flows,
                        *(
                            _DefinitionFlow(
                                definitions=exit_flow.definitions,
                                unbound=exit_flow.unbound,
                            )
                            for exit_flow in exits
                        ),
                    )
                )
                exits.extend(
                    _definition_exit_flows(
                        statement.finalbody,
                        name=name,
                        before_position=before_position,
                        incoming=final_input,
                        allow_iteration_binding=allow_iteration_binding,
                    )
                )
                flow = _analyze_definition_block(
                    statement.finalbody,
                    name=name,
                    before_position=before_position,
                    incoming=final_input,
                    allow_iteration_binding=allow_iteration_binding,
                )
            continue
        flow = _transfer_definition_statement(
            statement,
            name=name,
            before_position=before_position,
            incoming=flow,
            allow_iteration_binding=allow_iteration_binding,
        )
        if not flow.reachable and flow.terminator is not None:
            exits.append(flow)
            break
    return exits


def _containing_block(
    blocks: tuple[list[ast.stmt], ...],
    position: SourcePosition,
) -> list[ast.stmt] | None:
    return next((block for block in blocks if _block_contains_position(block, position)), None)


def _block_contains_position(block: list[ast.stmt], position: SourcePosition) -> bool:
    return any(
        _node_position(statement)
        <= position
        <= (
            getattr(statement, "end_lineno", statement.lineno),
            getattr(statement, "end_col_offset", statement.col_offset),
        )
        for statement in block
    )


def _function_contains_position(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    position: SourcePosition,
) -> bool:
    return (
        _node_position(function)
        <= position
        <= (
            getattr(function, "end_lineno", function.lineno),
            getattr(function, "end_col_offset", function.col_offset),
        )
    )


def _simple_assignment(statement: ast.stmt, name: str) -> ast.expr | None:
    if isinstance(statement, ast.Assign) and any(
        _target_binds_name(target, name) for target in statement.targets
    ):
        if not all(isinstance(target, ast.Name) for target in statement.targets):
            raise StaticContractAnalysisError(
                f"{name} uses an unsupported destructuring Error assignment at line "
                f"{statement.lineno}"
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
        targets = (statement.target,) if isinstance(statement, ast.AugAssign) else statement.targets
        return any(_target_binds_name(target, name) for target in targets)
    if isinstance(statement, (ast.Import, ast.ImportFrom)):
        return any((alias.asname or alias.name.split(".")[0]) == name for alias in statement.names)
    return False


def _join_definition_flows(flows: tuple[_DefinitionFlow, ...]) -> _DefinitionFlow:
    reachable = tuple(
        flow for flow in flows if flow.reachable or flow.terminator in {"break", "continue"}
    )
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


def _parameter_writes(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    parameter: str,
) -> list[ast.AST]:
    return [
        node
        for node in _runtime_nodes_in_function(function)
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


def _function_binding(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
) -> ast.AST | None:
    if _is_parameter(function, name):
        return next(
            argument
            for argument in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            )
            if argument.arg == name
        )
    collector = _NameBindingCollector(function, name)
    collector.visit(function)
    return collector.bindings[0] if collector.bindings else None


class _NameBindingCollector(ast.NodeVisitor):
    def __init__(
        self,
        root: ast.FunctionDef | ast.AsyncFunctionDef,
        name: str,
    ) -> None:
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


def _parameter_annotation(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
) -> ast.expr | None:
    return next(
        (
            argument.annotation
            for argument in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            )
            if argument.arg == name
        ),
        None,
    )


def _is_parameter(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    return any(
        argument.arg == name
        for argument in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
    )


def _target_binds_name(target: ast.expr, name: str) -> bool:
    if isinstance(target, ast.Name):
        return target.id == name
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_target_binds_name(item, name) for item in target.elts)
    return False


def _pattern_binds_name(pattern: ast.pattern, name: str) -> bool:
    return any(
        (isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name)
        or (isinstance(node, ast.MatchMapping) and node.rest == name)
        for node in ast.walk(pattern)
    )


def _call_name(node: ast.expr | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _call_name(node.value)
    if isinstance(node, ast.BinOp):
        return _call_name(node.left)
    return ""
