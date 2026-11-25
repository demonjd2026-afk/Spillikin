"""The Relate store: how it is opened, what it holds, and how a project is synced into it.

The store is a Relate *directory* store with the JSON snapshot codec, for two reasons:

* a directory store can be read while another process holds the writer lease, so
  agents keep querying while one of them records a claim; and
* the JSON snapshot is plain text that any tool can read without Relate, and a
  build that changes nothing leaves it byte-identical, so it is safe to commit.

Every edge points the way data flows, so "what does this column reach" is a
single outward traversal over three relationship types.
"""

from __future__ import annotations

import os
import random
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import relate

from .lineage import ModelLineage
from .project import Project

SCHEMA = (
    "CREATE CONSTRAINT relation_id IF NOT EXISTS FOR (n:Relation) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT column_id IF NOT EXISTS FOR (n:Column) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT exposure_id IF NOT EXISTS FOR (n:Exposure) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT owner_id IF NOT EXISTS FOR (n:Owner) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT agent_id IF NOT EXISTS FOR (n:Agent) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (n:Claim) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT project_id IF NOT EXISTS FOR (n:Project) REQUIRE n.id IS UNIQUE",
    "CREATE FULLTEXT INDEX column_text IF NOT EXISTS FOR (n:Column) ON EACH [n.search_text]",
)

# Labels and relationship types are fixed here and never come from user input,
# which is what makes it safe to place them in statement text.
NODE_LABELS = ("Relation", "Column", "Exposure", "Owner")
EDGE_SPECS: dict[str, tuple[str, str, str]] = {
    "HAS_COLUMN": ("Relation", "HAS_COLUMN", "Column"),
    "FEEDS": ("Column", "FEEDS", "Column"),
    "SHAPES": ("Column", "SHAPES", "Relation"),
    "UPSTREAM_OF": ("Relation", "UPSTREAM_OF", "Relation"),
    "SURFACES_IN": ("Column", "SURFACES_IN", "Exposure"),
    "OWNS_RELATION": ("Owner", "OWNS", "Relation"),
    "OWNS_EXPOSURE": ("Owner", "OWNS", "Exposure"),
}

_LOCK_WAIT_SECONDS = 15.0


class StoreError(Exception):
    pass


def _is_locked(exc: Exception) -> bool:
    return "storage locked" in str(exc)


class Store:
    """Opens short-lived connections so the single writer lease is never held idle."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def exists(self) -> bool:
        return (self.path / "manifest.json").is_file()

    def _connect(self, read_only: bool) -> relate.Connection:
        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        delay = 0.02
        while True:
            try:
                return relate.connect(str(self.path), read_only=read_only)
            except relate.OperationalError as exc:
                if not _is_locked(exc) or time.monotonic() >= deadline:
                    raise StoreError(f"cannot open {self.path}: {exc}") from exc
                time.sleep(delay + random.uniform(0, delay))
                delay = min(delay * 2, 0.5)

    def _hydrate(self) -> None:
        """A fresh clone holds only the snapshot; one writable open rebuilds the WAL beside it."""
        self._connect(read_only=False).close()

    @contextmanager
    def read(self) -> Iterator[relate.Connection]:
        if not self.exists():
            raise StoreError(f"no store at {self.path}; run `spillikin build` first")
        if not (self.path / "wal").is_dir():
            self._hydrate()
        conn = self._connect(read_only=True)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[relate.Connection]:
        """One transaction: everything inside commits together or not at all."""
        if not self.exists():
            self.path.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("RELATE_SNAPSHOT_FORMAT", "json")
        conn = self._connect(read_only=False)
        try:
            for statement in SCHEMA:
                conn.execute(statement)
            conn.autocommit = False
            yield conn
            conn.commit()
        except BaseException:
            if not conn.closed:
                conn.rollback()
            raise
        finally:
            if not conn.closed:
                conn.close()


@dataclass
class Graph:
    """The lineage part of the store as plain data, so two versions can be compared."""

    nodes: dict[str, dict[str, dict[str, Any]]] = field(default_factory=lambda: {label: {} for label in NODE_LABELS})
    edges: dict[str, dict[tuple[str, str], dict[str, Any]]] = field(default_factory=lambda: {k: {} for k in EDGE_SPECS})
    stale_docs: list[str] = field(default_factory=list)  # documented columns the SQL no longer outputs

    def columns_of(self, relation: str) -> list[str]:
        owned = [(props["ordinal"], cid) for cid, props in self.nodes["Column"].items() if props["relation"] == relation]
        return [cid for _, cid in sorted(owned)]


def column_id(relation: str, column: str) -> str:
    return f"{relation}.{column}"


def from_project(project: Project, lineage: dict[str, ModelLineage]) -> Graph:
    graph = Graph()
    relations = {**project.sources, **{name: project.models[name] for name in lineage}}

    depth: dict[str, int] = {name: 0 for name in project.sources}
    for name, model in lineage.items():  # already in dependency order
        depth[name] = 1 + max((depth.get(parent, 0) for parent in model.reads), default=0)

    def add_owner(owner: str, edge: str, target: str) -> None:
        if owner:
            graph.nodes["Owner"][owner] = {"id": owner}
            graph.edges[edge][(owner, target)] = {}

    def add_column(relation: str, name: str, ordinal: int, expression: str) -> None:
        meta = relations[relation].columns.get(name)
        cid = column_id(relation, name)
        graph.nodes["Column"][cid] = {
            "id": cid,
            "name": name,
            "relation": relation,
            "ordinal": ordinal,
            "description": meta.description if meta else "",
            "data_type": meta.data_type if meta else "",
            "tests": list(meta.tests) if meta else [],
            "expression": expression,
            # snake_case names split into words so full-text search matches "net revenue".
            "search_text": " ".join([*name.split("_"), *relation.replace(".", "_").split("_"), meta.description if meta else ""]).strip(),
        }
        graph.edges["HAS_COLUMN"][(relation, cid)] = {}

    for rel in relations.values():
        graph.nodes["Relation"][rel.id] = {
            "id": rel.id,
            "kind": rel.kind,
            "layer": rel.layer,
            "depth": depth[rel.id],
            "path": rel.path,
            "description": rel.description,
        }
        add_owner(rel.owner, "OWNS_RELATION", rel.id)

    for source in project.sources.values():
        for ordinal, name in enumerate(source.columns):
            add_column(source.id, name, ordinal, "")

    for name, model in lineage.items():
        declared = set(relations[name].columns) - set(model.columns)
        if declared and project.strict_docs:
            raise StoreError(f"{name}: manifest describes columns the model does not output: {sorted(declared)}")
        graph.stale_docs += [column_id(name, column) for column in sorted(declared)]
        for parent in model.reads:
            graph.edges["UPSTREAM_OF"][(parent, name)] = {}
        for col in model.columns.values():
            add_column(name, col.name, col.ordinal, col.expression)
            for (src_rel, src_col), kind in col.feeds.items():
                graph.edges["FEEDS"][(column_id(src_rel, src_col), column_id(name, col.name))] = {"kind": kind}
        for (src_rel, src_col), roles in model.shaped_by.items():
            graph.edges["SHAPES"][(column_id(src_rel, src_col), name)] = {"roles": list(roles)}

    for exposure in project.exposures.values():
        graph.nodes["Exposure"][exposure.id] = {
            "id": exposure.id,
            "kind": exposure.kind,
            "description": exposure.description,
        }
        add_owner(exposure.owner, "OWNS_EXPOSURE", exposure.id)
        reads = [c for cid in exposure.reads for c in (graph.columns_of(cid[:-2]) if cid.endswith(".*") else [cid])]
        for cid in reads:
            if cid not in graph.nodes["Column"]:
                # A model that failed to extract cannot vouch for its columns either way.
                if cid.rpartition(".")[0] in project.models and cid.rpartition(".")[0] not in lineage:
                    continue
                raise StoreError(f"exposure {exposure.id!r} reads {cid!r}, which does not exist")
            graph.edges["SURFACES_IN"][(cid, exposure.id)] = {}
    return graph


def from_store(conn: relate.Connection) -> Graph:
    graph = Graph()
    for label in NODE_LABELS:
        for row in conn.execute(f"MATCH (n:{label}) RETURN properties(n) AS props").fetchall():
            graph.nodes[label][row.props["id"]] = dict(row.props)
    for key, (src, rel, dst) in EDGE_SPECS.items():
        statement = f"MATCH (a:{src})-[e:{rel}]->(b:{dst}) RETURN a.id AS src, b.id AS dst, properties(e) AS props"
        for row in conn.execute(statement).fetchall():
            graph.edges[key][(row.src, row.dst)] = dict(row.props)
    return graph


@dataclass
class Delta:
    upsert_nodes: dict[str, list[dict[str, Any]]]
    drop_nodes: dict[str, list[str]]
    upsert_edges: dict[str, list[dict[str, Any]]]
    drop_edges: dict[str, list[dict[str, str]]]
    added_columns: list[str]
    removed_columns: list[str]
    changed_columns: list[str]

    @property
    def empty(self) -> bool:
        groups = (self.upsert_nodes, self.drop_nodes, self.upsert_edges, self.drop_edges)
        return not any(rows for group in groups for rows in group.values())

    def counts(self) -> dict[str, int]:
        size = lambda group: sum(len(rows) for rows in group.values())  # noqa: E731
        return {
            "nodes_written": size(self.upsert_nodes),
            "nodes_removed": size(self.drop_nodes),
            "edges_written": size(self.upsert_edges),
            "edges_removed": size(self.drop_edges),
        }


def diff(current: Graph, desired: Graph) -> Delta:
    """What it takes to turn `current` into `desired`, and what that means for columns."""
    upsert_nodes = {
        label: [props for nid, props in sorted(desired.nodes[label].items()) if current.nodes[label].get(nid) != props]
        for label in NODE_LABELS
    }
    drop_nodes = {label: sorted(set(current.nodes[label]) - set(desired.nodes[label])) for label in NODE_LABELS}
    upsert_edges = {
        key: [
            {"src": src, "dst": dst, "props": props}
            for (src, dst), props in sorted(desired.edges[key].items())
            if current.edges[key].get((src, dst)) != props
        ]
        for key in EDGE_SPECS
    }
    drop_edges = {
        key: [{"src": s, "dst": d} for s, d in sorted(set(current.edges[key]) - set(desired.edges[key]))]
        for key in EDGE_SPECS
    }

    def inputs(graph: Graph, cid: str) -> tuple[Any, ...]:
        feeds = sorted((src, props["kind"]) for (src, dst), props in graph.edges["FEEDS"].items() if dst == cid)
        return (graph.nodes["Column"][cid]["expression"], tuple(feeds))

    before, after = set(current.nodes["Column"]), set(desired.nodes["Column"])
    return Delta(
        upsert_nodes=upsert_nodes,
        drop_nodes=drop_nodes,
        upsert_edges=upsert_edges,
        drop_edges=drop_edges,
        added_columns=sorted(after - before),
        removed_columns=sorted(before - after),
        changed_columns=sorted(c for c in before & after if inputs(current, c) != inputs(desired, c)),
    )


def apply(conn: relate.Connection, delta: Delta, project: Project) -> None:
    """Write a delta. Called inside `Store.write()`, so it lands as one commit."""
    for key, rows in delta.drop_edges.items():
        if rows:
            src, rel, dst = EDGE_SPECS[key]
            conn.execute(f"UNWIND $rows AS r MATCH (:{src} {{id: r.src}})-[e:{rel}]->(:{dst} {{id: r.dst}}) DELETE e", {"rows": rows})
    for label, ids in delta.drop_nodes.items():
        if ids:
            conn.execute(f"UNWIND $ids AS id MATCH (n:{label} {{id: id}}) DETACH DELETE n", {"ids": ids})
    for label, rows in delta.upsert_nodes.items():
        if rows:
            conn.execute(f"UNWIND $rows AS r MERGE (n:{label} {{id: r.id}}) SET n = r", {"rows": rows})
    for key, rows in delta.upsert_edges.items():
        if rows:
            src, rel, dst = EDGE_SPECS[key]
            conn.execute(
                f"UNWIND $rows AS r MATCH (a:{src} {{id: r.src}}), (b:{dst} {{id: r.dst}}) "
                f"MERGE (a)-[e:{rel}]->(b) SET e = r.props",
                {"rows": rows},
            )
    conn.execute(
        "MERGE (p:Project {id: $id}) SET p.dialect = $dialect, p.fingerprint = $fingerprint",
        {"id": project.name, "dialect": project.dialect, "fingerprint": project.fingerprint},
    )
