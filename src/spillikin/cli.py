"""Command line entry point. Every command prints text for people or, with --json, one JSON document."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__, analysis, claims, workflow
from .analysis import UnknownColumn
from .claims import ClaimError
from .lineage import LineageError
from .project import ProjectError
from .cli_export import export_payload
from .store import Store, StoreError
from .workflow import BuildError

_MARK = {"breaks": "x", "blocked": "!", "drifts": "~"}


def _paths(args: argparse.Namespace) -> tuple[Path, Store]:
    root = Path(args.project or os.environ.get("SPILLIKIN_PROJECT") or ".").resolve()
    store = Path(args.store or os.environ.get("SPILLIKIN_STORE") or root / "store")
    return root, Store(store)


def _emit(args: argparse.Namespace, payload: Any, text: str) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True) if args.json else text)


def _radius_text(radius: dict[str, Any]) -> str:
    s = radius["summary"]
    lines = [
        f"{radius['change']} {', '.join(radius['targets'])}",
        f"  reaches {s['columns']} columns in {s['models']} models and {s['exposures']} exposures"
        f"  ({s['breaks']} break outright, {s['by_value']} by value, {s['by_rows']} by row membership, {s['unguarded']} with no test)",
    ]
    if radius["broken_models"]:
        lines.append(f"  stops compiling: {', '.join(radius['broken_models'])}")
    relation = None
    nearest: dict[str, int] = {}
    for row in radius["impacted"]:
        nearest[row["relation"]] = min(nearest.get(row["relation"], row["hops"]), row["hops"])
    for row in sorted(radius["impacted"], key=lambda r: (nearest[r["relation"]], r["relation"], r["hops"], r["id"])):
        if row["relation"] != relation:
            relation = row["relation"]
            lines.append(f"\n  {relation}")
        guard = "" if row["guarded"] else "  untested"
        lines.append(f"    {_MARK[row['severity']]} {row['id'].rpartition('.')[2]:<28} {row['severity']:<8} {row['hops']} hops  via {row['via']}{guard}")
    if radius["exposures"]:
        lines.append("\n  exposures")
        for e in radius["exposures"]:
            lines.append(f"    {e['id']} ({e['kind']}, owner {', '.join(e['owners']) or 'unowned'}): {len(e['columns'])} columns")
    if radius["notify"]:
        lines.append("\n  notify: " + ", ".join(radius["notify"]))
    return "\n".join(lines)


def cmd_build(args: argparse.Namespace) -> int:
    root, store = _paths(args)
    result = workflow.build(root, store)
    state = "updated" if result["changed"] else "already current"
    _emit(args, result, (
        f"{result['project']}: {result['relations']} relations, {result['columns']} columns, "
        f"{result['value_edges']} value edges, {result['row_edges']} row edges, {result['exposures']} exposures\n"
        f"store {state} at {store.path}"
        + "".join(f"\nnote: {column} is documented but the model no longer outputs it" for column in result["stale_docs"])
    ))
    return 0


def cmd_impact(args: argparse.Namespace) -> int:
    _, store = _paths(args)
    with store.read() as conn:
        radius = analysis.blast_radius(conn, args.column, args.change)
    _emit(args, radius, _radius_text(radius))
    return 0


def cmd_why(args: argparse.Namespace) -> int:
    _, store = _paths(args)
    with store.read() as conn:
        result = analysis.provenance(conn, args.column)
    lines = [result["column"]]
    for o in result["origins"]:
        tag = "source" if o["is_source"] else "model "
        lines.append(f"  {tag} {o['id']:<40} {o['hops']} hops  {' > '.join(o['transformations'])}")
    for s in result["shaped_by"]:
        lines.append(f"  rows   {s['id']:<40} {', '.join(s['roles'])}")
    _emit(args, result, "\n".join(lines))
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    _, store = _paths(args)
    with store.read() as conn:
        hits = analysis.search(conn, " ".join(args.query), args.limit)
    _emit(args, hits, "\n".join(f"{h['score']:<7} {h['id']:<44} {h['description']}" for h in hits) or "no match")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root, store = _paths(args)
    result = workflow.check(root, store, args.agent)
    lines = []
    if result["clean"]:
        lines.append("working tree matches the graph")
    for f in result["failures"]:
        lines.append(f"x {f['model']} no longer compiles: {f['reason']}")
    for f in result["findings"]:
        s = f["summary"]
        lines.append(f"{'x' if f['change'] == 'drop' else '~'} {f['change']} {f['column']}: {s['columns']} columns, "
                     f"{s['models']} models, exposures [{', '.join(f['exposures']) or 'none'}]")
    if result["unclaimed"]:
        lines.append(f"no open claim covers: {', '.join(result['unclaimed'])}")
    if result["blocking"]:
        lines.append("blocking: fix the failures or claim the columns before merging")
    _emit(args, result, "\n".join(lines))
    return 1 if result["blocking"] else 0


def cmd_claim(args: argparse.Namespace) -> int:
    _, store = _paths(args)
    with store.write() as conn:
        result = claims.open_claim(conn, args.agent, args.column, args.intent, args.change,
                                   force=args.force, ttl_seconds=args.ttl)
    if result["granted"]:
        text = f"granted {result['claim']} to {args.agent}: reaches {result['radius']['columns']} columns"
    else:
        text = f"refused ({result['attempt']}): this change meets work already claimed"
    for c in result["collisions"]:
        text += (f"\n  {c['agent']} holds {c['claim']} ({c['intent']})\n"
                 f"    shared columns: {', '.join(c['columns'][:6])}{' ...' if len(c['columns']) > 6 else ''}\n"
                 f"    shared exposures: {', '.join(c['exposures']) or 'none'}")
    _emit(args, result, text)
    return 0 if result["granted"] else 3


def cmd_release(args: argparse.Namespace) -> int:
    _, store = _paths(args)
    with store.write() as conn:
        result = claims.release_claim(conn, args.claim)
    _emit(args, result, f"released {result['released']} ({result['agent']})")
    return 0


def cmd_claims(args: argparse.Namespace) -> int:
    _, store = _paths(args)
    if args.prune:
        with store.write() as conn:
            removed = claims.prune_claims(conn)
        _emit(args, {"removed": removed}, f"removed {removed} finished claims")
        return 0
    with store.read() as conn:
        held = claims.open_claims(conn)
    _emit(args, held, "\n".join(
        f"{c['id']}  {c['agent']:<16} {c['change']:<6} {', '.join(c['targets'])}  reaches {c['reach']}  {c['intent']}" for c in held
    ) or "no open claims")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """The lineage graph as one JSON document, for the browser explorer to load into Relate's WebAssembly build."""
    _, store = _paths(args)
    with store.read() as conn:
        payload = export_payload(conn)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def cmd_live(args: argparse.Namespace) -> int:
    from .live import serve

    root, store = _paths(args)
    serve(root, store, args.port, Path(args.explorer) if args.explorer else None)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .mcp_server import serve

    root, store = _paths(args)
    serve(root, store)
    return 0


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", help="project root holding spillikin.yml (default: current directory)")
    common.add_argument("--store", help="Relate store directory (default: <project>/store)")
    common.add_argument("--json", action="store_true", help="print one JSON document")

    top = argparse.ArgumentParser(prog="spillikin", description="Pull one column without moving the rest: what a SQL change reaches, before you make it.")
    top.add_argument("--version", action="version", version=__version__)
    sub = top.add_subparsers(dest="command", required=True)

    def add(name: str, handler: Any, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, parents=[common], help=help_text)
        p.set_defaults(handler=handler)
        return p

    add("build", cmd_build, "sync the graph to the SQL on disk")
    p = add("impact", cmd_impact, "what a change to a column reaches")
    p.add_argument("column", nargs="+", help="relation.column, e.g. raw.orders.status")
    p.add_argument("--change", choices=analysis.CHANGES, default="logic")
    p = add("why", cmd_why, "where a column's values come from")
    p.add_argument("column")
    p = add("find", cmd_find, "full-text search over column names and descriptions")
    p.add_argument("query", nargs="+")
    p.add_argument("--limit", type=int, default=10)
    p = add("check", cmd_check, "compare the working tree with the graph; exits 1 if an edit needs attention")
    p.add_argument("--agent", help="only count claims held by this agent as covering the edits")
    p = add("claim", cmd_claim, "record intent to change columns; exits 3 if it meets another agent's claim")
    p.add_argument("column", nargs="+")
    p.add_argument("--agent", required=True)
    p.add_argument("--intent", required=True)
    p.add_argument("--change", choices=analysis.CHANGES, default="logic")
    p.add_argument("--ttl", type=int, default=claims.DEFAULT_TTL_SECONDS, help="seconds until the claim lapses")
    p.add_argument("--force", action="store_true", help="open the claim even if it is contested")
    p = add("release", cmd_release, "close a claim")
    p.add_argument("claim")
    p = add("claims", cmd_claims, "list open claims")
    p.add_argument("--prune", action="store_true", help="delete released and expired claims")
    p = add("export", cmd_export, "write the lineage graph as JSON")
    p.add_argument("--out")
    add("serve", cmd_serve, "run the MCP server on stdio")
    p = add("live", cmd_live, "serve the explorer locally and update it as SQL and claims change")
    p.add_argument("--port", type=int, default=4321)
    p.add_argument("--explorer", help="path to the built explorer (default: web/dist)")
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.handler(args)
    except (ProjectError, LineageError, BuildError, StoreError, UnknownColumn, ClaimError, ValueError, OSError) as exc:
        print(f"spillikin: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
