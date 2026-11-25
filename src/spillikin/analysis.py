"""The questions the graph answers: what a change reaches, where a value comes from, where a name lives."""

from __future__ import annotations

from typing import Any

import relate

CHANGES = ("drop", "rename", "retype", "logic")
_STRUCTURAL = {"drop", "rename"}  # the column's name stops existing, so readers stop compiling

# One shortest path per reached column. FEEDS carries values; SHAPES then HAS_COLUMN
# carries row membership: a join key or filter column reaches every column of the model it shapes.
REACH = """
MATCH (seed:Column) WHERE seed.id IN $seeds
MATCH p = SHORTEST 1 (seed)-[:FEEDS|SHAPES|HAS_COLUMN]->+(hit:Column)
RETURN seed.id AS seed, hit.id AS id, hit.relation AS relation, size(hit.tests) > 0 AS guarded,
       length(p) AS hops, [n IN nodes(p) | n.id] AS path, [r IN relationships(p) | type(r)] AS via
"""

REACH_BY_VALUE = """
MATCH (seed:Column) WHERE seed.id IN $seeds
MATCH p = SHORTEST 1 (seed)-[:FEEDS]->+(hit:Column)
RETURN hit.id AS id, min(length(p)) AS hops
"""

# Every model that names the column, whether it selects it or only joins or filters on it.
READERS = """
MATCH (seed:Column) WHERE seed.id IN $seeds
CALL (seed) {
    MATCH (seed)-[:FEEDS]->(:Column)<-[:HAS_COLUMN]-(m:Relation) RETURN m
    UNION
    MATCH (seed)-[:SHAPES]->(m:Relation) RETURN m
}
RETURN DISTINCT m.id AS id
"""

COLUMNS_OF = """
MATCH (m:Relation)-[:HAS_COLUMN]->(c:Column) WHERE m.id IN $relations
RETURN c.id AS id, c.relation AS relation, size(c.tests) > 0 AS guarded
"""

EXPOSURES = """
MATCH (c:Column)-[:SURFACES_IN]->(e:Exposure) WHERE c.id IN $columns
OPTIONAL MATCH (o:Owner)-[:OWNS]->(e)
RETURN e.id AS id, e.kind AS kind, e.description AS description,
       collect(DISTINCT c.id) AS columns, collect(DISTINCT o.id) AS owners
ORDER BY id
"""

RELATION_OWNERS = """
MATCH (o:Owner)-[:OWNS]->(m:Relation) WHERE m.id IN $relations
RETURN o.id AS owner, collect(m.id) AS relations ORDER BY owner
"""

KNOWN = "MATCH (c:Column) WHERE c.id IN $ids RETURN collect(c.id) AS ids"

PROVENANCE = """
MATCH (target:Column {id: $id})
MATCH p = SHORTEST 1 (origin:Column)-[:FEEDS]->+(target)
MATCH (rel:Relation)-[:HAS_COLUMN]->(origin)
RETURN origin.id AS id, rel.kind AS kind, length(p) AS hops,
       [r IN relationships(p) | r.kind] AS transformations, [n IN nodes(p) | n.id] AS path
ORDER BY hops, id
"""

SHAPED_BY = """
MATCH (c:Column)-[s:SHAPES]->(:Relation)-[:HAS_COLUMN]->(:Column {id: $id})
RETURN c.id AS id, s.roles AS roles ORDER BY id
"""

SEARCH = """
MATCH (c:Column) SEARCH c IN (FULLTEXT INDEX column_text FOR $query LIMIT $limit) SCORE AS score
RETURN c.id AS id, c.description AS description, c.expression AS expression, score
ORDER BY score DESC, id
"""


class UnknownColumn(Exception):
    pass


def require_columns(conn: relate.Connection, ids: list[str]) -> list[str]:
    ids = sorted({i.strip().lower() for i in ids if i.strip()})
    if not ids:
        raise UnknownColumn("no column given; use relation.column, for example raw.orders.status")
    known = set(conn.execute(KNOWN, {"ids": ids}).single().ids)
    missing = [i for i in ids if i not in known]
    if missing:
        raise UnknownColumn(f"not in the graph: {', '.join(missing)}")
    return ids


def blast_radius(conn: relate.Connection, columns: list[str], change: str = "logic") -> dict[str, Any]:
    """Everything a change to `columns` reaches, and how badly.

    A structural change (drop, rename) makes every model that names the column fail
    to compile, so all of that model's columns break, not only the ones the column fed.
    Whatever sits below a broken model is blocked. A semantic change (retype, logic)
    compiles fine and lets different values drift downstream.
    """
    if change not in CHANGES:
        raise ValueError(f"change must be one of {', '.join(CHANGES)}")
    targets = require_columns(conn, columns)

    impacted: dict[str, dict[str, Any]] = {}
    broken: list[str] = []
    seeds = targets
    if change in _STRUCTURAL:
        broken = sorted(r.id for r in conn.execute(READERS, {"seeds": targets}).fetchall())
        for row in conn.execute(COLUMNS_OF, {"relations": broken}).fetchall():
            impacted[row.id] = {
                "id": row.id, "relation": row.relation, "severity": "breaks", "via": "reads the column",
                "hops": 1, "guarded": row.guarded, "path": [],
            }
        seeds = sorted(impacted) or targets

    by_value = {r.id: r.hops for r in conn.execute(REACH_BY_VALUE, {"seeds": seeds}).fetchall()}
    for row in conn.execute(REACH, {"seeds": seeds}).fetchall():
        if row.id in targets or (row.id in impacted and impacted[row.id]["severity"] == "breaks"):
            continue
        offset = 1 if change in _STRUCTURAL else 0
        hops = row.hops + offset
        if row.id in impacted and impacted[row.id]["hops"] <= hops:
            continue
        gate = row.path[row.via.index("SHAPES") + 1] if "SHAPES" in row.via else None
        impacted[row.id] = {
            "id": row.id,
            "relation": row.relation,
            "severity": "blocked" if change in _STRUCTURAL else "drifts",
            "via": "value" if row.id in by_value else f"rows of {gate}",
            "hops": hops,
            "guarded": row.guarded,
            "path": list(row.path),
        }

    rows = sorted(impacted.values(), key=lambda r: (r["hops"], r["id"]))
    relations = sorted({r["relation"] for r in rows})
    exposures = [
        {"id": e.id, "kind": e.kind, "description": e.description, "owners": sorted(e.owners), "columns": sorted(e.columns)}
        for e in conn.execute(EXPOSURES, {"columns": [*targets, *impacted]}).fetchall()
    ]
    notify: dict[str, list[str]] = {}
    for row in conn.execute(RELATION_OWNERS, {"relations": relations}).fetchall():
        notify.setdefault(row.owner, []).extend(sorted(row.relations))
    for exposure in exposures:
        for owner in exposure["owners"]:
            notify.setdefault(owner, []).append(f"exposure:{exposure['id']}")

    return {
        "targets": targets,
        "change": change,
        "summary": {
            "columns": len(rows),
            "models": len(relations),
            "exposures": len(exposures),
            "owners": len(notify),
            "breaks": sum(1 for r in rows if r["severity"] == "breaks"),
            "by_value": sum(1 for r in rows if r["via"] == "value"),
            "by_rows": sum(1 for r in rows if r["via"].startswith("rows of")),
            "unguarded": sum(1 for r in rows if not r["guarded"]),
        },
        "broken_models": broken,
        "impacted": rows,
        "exposures": exposures,
        "notify": {owner: sorted(set(items)) for owner, items in sorted(notify.items())},
    }


def provenance(conn: relate.Connection, column: str) -> dict[str, Any]:
    (target,) = require_columns(conn, [column])
    origins = [
        {"id": r.id, "is_source": r.kind == "source", "hops": r.hops,
         "transformations": list(r.transformations), "path": list(r.path)}
        for r in conn.execute(PROVENANCE, {"id": target}).fetchall()
    ]
    shaped = [{"id": r.id, "roles": list(r.roles)} for r in conn.execute(SHAPED_BY, {"id": target}).fetchall()]
    return {"column": target, "origins": origins, "shaped_by": shaped}


def search(conn: relate.Connection, query: str, limit: int = 10) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return []
    limit = max(1, min(int(limit), 50))
    # Column names are snake_case; index them so "net revenue" finds net_revenue_usd.
    terms = " ".join(part for part in query.replace("_", " ").replace(".", " ").split())
    rows = conn.execute(SEARCH.replace("$limit", str(limit)), {"query": terms}).fetchall()
    return [{"id": r.id, "description": r.description, "expression": r.expression, "score": round(r.score, 4)} for r in rows]
