"""The lineage graph as one JSON document, the shape the browser explorer loads."""

from __future__ import annotations

from typing import Any

import relate

from .store import from_store


def export_payload(conn: relate.Connection) -> dict[str, Any]:
    graph = from_store(conn)
    project = conn.execute("MATCH (p:Project) RETURN properties(p) AS p").single().p
    return {
        "project": project,
        "nodes": {label: list(rows.values()) for label, rows in graph.nodes.items()},
        "edges": {key: [{"src": s, "dst": d, "props": p} for (s, d), p in sorted(rows.items())] for key, rows in graph.edges.items()},
    }
