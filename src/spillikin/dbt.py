"""Reads a dbt project from what dbt itself wrote: target/manifest.json and target/catalog.json.

dbt has already resolved every macro, so the compiled SQL in the manifest is plain SQL
and the lineage engine needs no knowledge of Jinja. The catalog supplies the columns of
seeds and sources, which SQL alone cannot.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from .project import ColumnMeta, Exposure, Project, ProjectError, Relation

MANIFEST = Path("target") / "manifest.json"
CATALOG = Path("target") / "catalog.json"
_UPSTREAM = ("seed", "source")


def is_dbt_project(root: Path) -> bool:
    return (root / MANIFEST).is_file()


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError(f"cannot read {path}: {exc}") from exc


def _key(relation_name: str, dialect: str) -> tuple[str, str]:
    """(schema, table) of a fully qualified relation name, lower-cased, quotes removed."""
    table = sqlglot.parse_one(relation_name, into=exp.Table, read=dialect)
    return (table.db.lower(), table.name.lower())


def _ident(model: str) -> str:
    """What a model is called inside rewritten SQL. dbt projects routinely name a CTE after the model it wraps
    (`with orders as (select * from <orders model>)`), so the bare model name would be read as the CTE itself."""
    return f"dbt__{model.lower()}"


def _owner(node: dict[str, Any]) -> str:
    return str((node.get("meta") or {}).get("owner") or (node.get("config") or {}).get("group") or node.get("group") or "")


def load(root: Path | str) -> Project:
    root = Path(root).resolve()
    manifest = _read(root / MANIFEST)
    if not (root / CATALOG).is_file():
        raise ProjectError(f"{root / CATALOG} is missing; run `dbt docs generate` so seed and source columns are known")
    catalog = _read(root / CATALOG)
    dialect = str(manifest.get("metadata", {}).get("adapter_type") or "duckdb")
    listed = {**catalog.get("nodes", {}), **catalog.get("sources", {})}

    tests: dict[tuple[str, str], set[str]] = {}
    for node in manifest["nodes"].values():
        if node["resource_type"] == "test" and node.get("attached_node") and node.get("column_name"):
            name = (node.get("test_metadata") or {}).get("name") or node["name"]
            tests.setdefault((node["attached_node"], node["column_name"].lower()), set()).add(str(name))

    def columns(unique_id: str, node: dict[str, Any], from_catalog: bool) -> dict[str, ColumnMeta]:
        described = {name.lower(): spec for name, spec in (node.get("columns") or {}).items()}
        names = [c.lower() for c in listed.get(unique_id, {}).get("columns", {})] if from_catalog else list(described)
        types = {name.lower(): spec.get("type", "") for name, spec in listed.get(unique_id, {}).get("columns", {}).items()}
        return {
            name: ColumnMeta(
                description=str((described.get(name) or {}).get("description", "")),
                tests=tuple(sorted(tests.get((unique_id, name), ()))),
                data_type=str(types.get(name, "")),
            )
            for name in names
        }

    sources: dict[str, Relation] = {}
    models: dict[str, Relation] = {}
    by_key: dict[tuple[str, str], str] = {}  # (schema, table) -> the identifier lineage will see
    ids: dict[str, str] = {}  # dbt unique_id -> relation id

    upstream = [(uid, n) for uid, n in manifest["nodes"].items() if n["resource_type"] == "seed"]
    upstream += list(manifest.get("sources", {}).items())
    declared = {_key(n["relation_name"], dialect) for n in manifest.get("sources", {}).values() if n.get("relation_name")}
    for uid, node in sorted(upstream):
        # A seed loaded into a table that a source also declares is one table; the source is what models read.
        if node["resource_type"] == "seed" and _key(node["relation_name"], dialect) in declared:
            continue
        rid = f"{node.get('source_name', 'seed')}.{node['name']}".lower()
        cols = columns(uid, node, from_catalog=True) or columns(uid, node, from_catalog=False)
        if not cols:
            raise ProjectError(f"{rid}: no columns in the manifest or the catalog; run `dbt docs generate` after loading it")
        ident = rid.replace(".", "__")
        sources[rid] = Relation(id=rid, ident=ident, kind="source", layer="source", owner=_owner(node),
                                description=str(node.get("description", "")), columns=cols)
        by_key[_key(node["relation_name"], dialect)] = ident
        ids[uid] = rid

    compiled = {uid: n for uid, n in manifest["nodes"].items() if n["resource_type"] == "model"}
    for uid, node in compiled.items():
        ids[uid] = node["name"].lower()
        if node.get("relation_name"):
            by_key[_key(node["relation_name"], dialect)] = _ident(node["name"])

    for uid, node in sorted(compiled.items()):
        name = node["name"].lower()
        code = node.get("compiled_code")
        if not code:
            raise ProjectError(f"{name}: the manifest holds no compiled SQL; run `dbt compile` first")
        try:
            tree = sqlglot.parse_one(code, read=dialect)
        except SqlglotError as exc:
            raise ProjectError(f"{name}: compiled SQL does not parse: {str(exc).splitlines()[0]}") from exc
        # Point every reference at the one relation it names, so a seed and a model with the same table name stay apart.
        for table in tree.find_all(exp.Table):
            ident = by_key.get((table.db.lower(), table.name.lower()))
            if ident and table.db:
                table.replace(exp.to_table(ident, alias=table.alias or None))
        path = node.get("original_file_path", "")
        parts = Path(path).parts
        models[name] = Relation(
            id=name, ident=_ident(name), kind="model", layer="/".join(parts[1:-1]) or "model", path=path, owner=_owner(node),
            description=str(node.get("description", "")), sql=tree.sql(dialect=dialect),
            columns=columns(uid, node, from_catalog=False),
        )

    exposures: dict[str, Exposure] = {}
    for node in manifest.get("exposures", {}).values():
        reads = tuple(sorted(f"{ids[dep]}.*" for dep in node.get("depends_on", {}).get("nodes", []) if dep in ids))
        if reads:
            exposures[node["name"].lower()] = Exposure(
                id=node["name"].lower(), kind=str(node.get("type", "other")), owner=str((node.get("owner") or {}).get("name", "")),
                description=str(node.get("description", "")), reads=reads,
            )

    digest = hashlib.sha256()
    for model in sorted(models.values(), key=lambda m: m.id):
        digest.update(model.id.encode() + model.sql.encode())
    return Project(name=str(manifest.get("metadata", {}).get("project_name") or root.name), dialect=dialect, root=root,
                   sources=sources, models=models, exposures=exposures, strict_docs=False,
                   fingerprint_seed=digest.hexdigest()[:16])
