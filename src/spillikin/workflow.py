"""The two operations that involve both the files on disk and the store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import analysis, claims, lineage, project as projects, store as stores


class BuildError(Exception):
    pass


def build(root: Path | str, store: stores.Store) -> dict[str, Any]:
    """Sync the store to the project. Unchanged projects write nothing, so the snapshot diff stays quiet."""
    project = projects.load(root)
    extracted, failures = lineage.extract_project(project)
    if failures:
        raise BuildError("; ".join(f"{model}: {reason}" for model, reason in sorted(failures.items())))
    desired = stores.from_project(project, extracted)
    with store.write() as conn:
        delta = stores.diff(stores.from_store(conn), desired)
        if not delta.empty:
            stores.apply(conn, delta, project)
    return {
        "project": project.name,
        "fingerprint": project.fingerprint,
        "relations": len(desired.nodes["Relation"]),
        "columns": len(desired.nodes["Column"]),
        "value_edges": len(desired.edges["FEEDS"]),
        "row_edges": len(desired.edges["SHAPES"]),
        "exposures": len(desired.nodes["Exposure"]),
        "changed": not delta.empty,
        **delta.counts(),
    }


def check(root: Path | str, store: stores.Store, agent: str | None = None) -> dict[str, Any]:
    """Compare the working tree with the committed graph and report what the edits reach.

    The store is the baseline, so this needs no git plumbing: it answers
    "what did I change since the graph was last built, and who feels it".
    """
    project = projects.load(root)
    with store.read() as conn:
        baseline = stores.from_store(conn)
        known = {rel: [c.rpartition(".")[2] for c in baseline.columns_of(rel)] for rel in baseline.nodes["Relation"]}
        extracted, failures = lineage.extract_project(project, fallback=known)

        # Models that no longer compile keep their baseline shape for the comparison;
        # they are reported as failures, not as a mass deletion of their columns.
        desired = stores.from_project(project, extracted)
        for model in failures:
            if model in baseline.nodes["Relation"]:
                desired.nodes["Relation"][model] = baseline.nodes["Relation"][model]
                for cid in baseline.columns_of(model):
                    desired.nodes["Column"][cid] = baseline.nodes["Column"][cid]
                for (src, dst), props in baseline.edges["FEEDS"].items():
                    if dst.rpartition(".")[0] == model:
                        desired.edges["FEEDS"][(src, dst)] = props
        delta = stores.diff(baseline, desired)

        findings = []
        for change, columns in (("drop", delta.removed_columns), ("logic", delta.changed_columns)):
            for cid in columns:
                radius = analysis.blast_radius(conn, [cid], change)
                findings.append({
                    "column": cid, "change": change, "summary": radius["summary"],
                    "exposures": [e["id"] for e in radius["exposures"]], "notify": sorted(radius["notify"]),
                    "broken_models": radius["broken_models"],
                })
        held = claims.open_claims(conn)

    touched = {f["column"] for f in findings}
    covering = [c for c in held if touched & set(c["targets"])]
    uncovered = sorted(touched - {t for c in covering if agent is None or c["agent"] == agent for t in c["targets"]})
    exposures = sorted({e for f in findings for e in f["exposures"]})
    return {
        "clean": not findings and not failures,
        "added_columns": delta.added_columns,
        "findings": findings,
        "failures": [{"model": m, "reason": r} for m, r in sorted(failures.items())],
        "exposures": exposures,
        "unclaimed": uncovered,
        "blocking": bool(failures or (exposures and uncovered)),
    }
