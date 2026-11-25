"""Column-level lineage for one SELECT, resolved through CTEs, subqueries and set operations.

Two kinds of dependency come out of a query, and they behave differently when
something upstream changes:

* a column FEEDS an output column when its value flows into it, and
* a column SHAPES the whole model when it decides which rows exist
  (a join key, a filter, a grouping key).

Text search finds the first kind. The second kind is the one that takes down a
dashboard without the changed column appearing anywhere in the dashboard's query.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, build_scope

from .project import Project, Relation

# Ordered weakest to strongest; a chain of hops reports its strongest transformation.
KINDS = ("passthrough", "expression", "window", "aggregate")
_RANK = {kind: rank for rank, kind in enumerate(KINDS)}
_SHAPING_CLAUSES = {"joins": "join", "where": "filter", "having": "filter", "qualify": "filter", "group": "group"}

ColumnId = tuple[str, str]  # (relation id, column name)


class LineageError(Exception):
    def __init__(self, model: str, message: str):
        super().__init__(f"{model}: {message}")
        self.model = model
        self.reason = message


@dataclass
class OutputColumn:
    name: str
    ordinal: int
    expression: str
    feeds: dict[ColumnId, str] = field(default_factory=dict)


@dataclass
class ModelLineage:
    model: str
    columns: dict[str, OutputColumn]
    shaped_by: dict[ColumnId, tuple[str, ...]]
    reads: tuple[str, ...]


def dependency_order(project: Project) -> list[str]:
    """Models sorted so every model comes after the relations it reads."""
    by_ident = {m.ident: m.id for m in project.models.values()}
    sorter: TopologicalSorter[str] = TopologicalSorter()
    for model in project.models.values():
        try:
            tree = sqlglot.parse_one(model.sql, read=project.dialect)
        except SqlglotError as exc:
            raise LineageError(model.id, f"SQL does not parse: {exc}") from exc
        local = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        deps = {
            by_ident[t.name.lower()]
            for t in tree.find_all(exp.Table)
            if t.name.lower() in by_ident and t.name.lower() not in local
        }
        sorter.add(model.id, *sorted(deps))
    try:
        return list(sorter.static_order())
    except CycleError as exc:
        raise LineageError(str(exc.args[1][0]), f"models form a cycle: {' -> '.join(exc.args[1])}") from exc


class _Tracer:
    """Follows a column reference down through nested scopes to physical columns."""

    def __init__(self, model: str, relations: dict[str, str]):
        self.model = model
        self.relations = relations  # SQL identifier -> relation id
        self._clauses: dict[int, list[tuple[exp.Column, str, exp.Expression]]] = {}
        self._outputs: dict[tuple[int, int], dict[ColumnId, int]] = {}

    def clause_columns(self, scope: Scope) -> list[tuple[exp.Column, str, exp.Expression]]:
        """Each column of a scope with the clause it sits in and that clause's top node."""
        key = id(scope)
        if key not in self._clauses:
            found = []
            for column in scope.columns:
                node: exp.Expression = column
                while node.parent is not None and node.parent is not scope.expression:
                    node = node.parent
                if node.parent is scope.expression:
                    found.append((column, node.arg_key or "", node))
            self._clauses[key] = found
        return self._clauses[key]

    def resolve(self, scope: Scope, column: exp.Column) -> dict[ColumnId, int]:
        holder: Scope | None = scope
        while holder is not None and column.table not in holder.sources:
            holder = holder.parent  # correlated reference to an enclosing query
        if holder is None:
            return {}
        source = holder.sources[column.table]
        if isinstance(source, exp.Table):
            ident = source.name.lower()
            if ident not in self.relations:
                raise LineageError(self.model, f"reads {ident!r}, which is neither a model nor a declared source")
            return {(self.relations[ident], column.name.lower()): 0}
        if isinstance(source, Scope):
            names = [n.lower() for n in source.expression.named_selects]
            if column.name.lower() not in names:
                return {}
            return self.output(source, names.index(column.name.lower()))
        return {}  # table functions, UNNEST and the like carry no column lineage

    def output(self, scope: Scope, index: int) -> dict[ColumnId, int]:
        key = (id(scope), index)
        if key in self._outputs:
            return self._outputs[key]
        merged: dict[ColumnId, int] = {}
        if isinstance(scope.expression, exp.SetOperation):
            for branch in scope.set_operation_scopes:
                for ref, rank in self.output(branch, index).items():
                    merged[ref] = max(merged.get(ref, 0), rank)
        else:
            projection = scope.expression.selects[index]
            here = _RANK[classify(projection)]
            for column, clause, top in self.clause_columns(scope):
                if clause == "expressions" and top is projection:
                    for ref, rank in self.resolve(scope, column).items():
                        merged[ref] = max(merged.get(ref, 0), rank, here)
            # A scalar subquery inside the projection feeds it as a computed value.
            for sub in scope.subquery_scopes:
                if _is_within(sub.expression, projection):
                    for ref in self.everything_read(sub):
                        merged[ref] = max(merged.get(ref, 0), _RANK["expression"])
        self._outputs[key] = merged
        return merged

    def everything_read(self, scope: Scope) -> set[ColumnId]:
        refs: set[ColumnId] = set()
        for inner in scope.traverse():
            for column in inner.columns:
                refs.update(self.resolve(inner, column))
        return refs


def _is_within(node: exp.Expression, ancestor: exp.Expression) -> bool:
    current: exp.Expression | None = node
    while current is not None:
        if current is ancestor:
            return True
        current = current.parent
    return False


def _readable(sql: str, relations: dict[str, str]) -> str:
    """Show sources by the name people use (raw.orders), not the identifier the parser saw (raw__orders)."""
    for ident, relation in relations.items():
        if ident != relation:
            sql = sql.replace(ident, relation)
    return sql


def classify(projection: exp.Expression) -> str:
    body = projection.unalias()
    if isinstance(body, exp.Column):
        return "passthrough"
    if body.find(exp.Window):
        return "window"
    if body.find(exp.AggFunc):
        return "aggregate"
    return "expression"


def extract(model: Relation, schema: dict[str, dict[str, str]], relations: dict[str, str], dialect: str) -> ModelLineage:
    """Lineage for one model. `schema` holds the columns of everything upstream."""
    try:
        tree = sqlglot.parse_one(model.sql, read=dialect)
        if not isinstance(tree, exp.Query):
            raise LineageError(model.id, "a model must be a single SELECT statement")
        tree = qualify(tree, schema=schema, dialect=dialect, quote_identifiers=False, identify=False)
    except LineageError:
        raise
    except SqlglotError as exc:
        raise LineageError(model.id, str(exc).splitlines()[0]) from exc

    root = build_scope(tree)
    if root is None:
        raise LineageError(model.id, "no query scope found")
    tracer = _Tracer(model.id, relations)

    names = [name.lower() for name in tree.named_selects]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise LineageError(model.id, f"output columns are not unique: {duplicates}")
    if any(not n for n in names):
        raise LineageError(model.id, "every output column needs a name")

    head = tree
    while isinstance(head, exp.SetOperation):
        head = head.left
    columns: dict[str, OutputColumn] = {}
    for index, name in enumerate(names):
        columns[name] = OutputColumn(
            name=name,
            ordinal=index,
            expression=_readable(head.selects[index].unalias().sql(dialect=dialect), relations),
            feeds={ref: KINDS[rank] for ref, rank in sorted(tracer.output(root, index).items())},
        )

    shaped: dict[ColumnId, set[str]] = {}
    for scope in root.traverse():
        for column, clause, _ in tracer.clause_columns(scope):
            role = _SHAPING_CLAUSES.get(clause)
            if role:
                for ref in tracer.resolve(scope, column):
                    shaped.setdefault(ref, set()).add(role)
        # Subqueries used as predicates (IN, EXISTS) decide which rows survive.
        if scope.is_subquery and not any(
            _is_within(scope.expression, sel) for sel in (scope.parent.expression.selects if scope.parent else [])
        ):
            for ref in tracer.everything_read(scope):
                shaped.setdefault(ref, set()).add("filter")

    reads = sorted({relations[t.name.lower()] for s in root.traverse() for t in s.tables if t.name.lower() in relations})
    return ModelLineage(
        model=model.id,
        columns=columns,
        shaped_by={ref: tuple(sorted(roles)) for ref, roles in sorted(shaped.items())},
        reads=tuple(reads),
    )


def extract_project(
    project: Project, fallback: dict[str, list[str]] | None = None
) -> tuple[dict[str, ModelLineage], dict[str, str]]:
    """Lineage for every model, plus the models that failed and why.

    When a model fails, `fallback` (its last known columns) lets the models below
    it still be analysed, so one broken file reports as one broken file.
    """
    relations = {r.ident: r.id for r in (*project.sources.values(), *project.models.values())}
    schema: dict[str, dict[str, str]] = {
        s.ident: {name: meta.data_type or "unknown" for name, meta in s.columns.items()}
        for s in project.sources.values()
    }
    lineage: dict[str, ModelLineage] = {}
    failures: dict[str, str] = {}
    for model_id in dependency_order(project):
        model = project.models[model_id]
        try:
            result = extract(model, schema, relations, project.dialect)
        except LineageError as exc:
            failures[model_id] = exc.reason
            known = (fallback or {}).get(model_id)
            if known:
                schema[model.ident] = {name: "unknown" for name in known}
            continue
        lineage[model_id] = result
        schema[model.ident] = {name: "unknown" for name in result.columns}
    return lineage, failures
