"""Reads a SQL project from disk: the manifest plus one SELECT per model file.

The manifest (`spillikin.yml`) declares what SQL alone cannot say: the shape
of raw sources, who owns what, which columns carry tests, and which columns are
read by things outside the warehouse (dashboards, exports, models, syncs).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

MANIFEST_NAME = "spillikin.yml"

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_CONFIG = re.compile(r"\{\{\s*config\s*\(.*?\)\s*\}\}", re.S)
_REF = re.compile(r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
_SOURCE = re.compile(r"\{\{\s*source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
_LEFTOVER = re.compile(r"\{\{|\{%")


class ProjectError(Exception):
    """The project on disk is malformed. The message says where and why."""


@dataclass(frozen=True)
class ColumnMeta:
    description: str = ""
    tests: tuple[str, ...] = ()
    data_type: str = ""


@dataclass(frozen=True)
class Relation:
    """A source table or a model. `id` is what humans type; `ident` is what SQL sees."""

    id: str
    ident: str
    kind: str  # "source" | "model"
    layer: str
    path: str = ""
    owner: str = ""
    description: str = ""
    sql: str = ""
    columns: dict[str, ColumnMeta] = field(default_factory=dict)


@dataclass(frozen=True)
class Exposure:
    id: str
    kind: str
    owner: str
    description: str
    reads: tuple[str, ...]


@dataclass(frozen=True)
class Project:
    name: str
    dialect: str
    root: Path
    sources: dict[str, Relation]
    models: dict[str, Relation]
    exposures: dict[str, Exposure]
    strict_docs: bool = True  # a hand-written manifest must match the SQL; documentation inherited from dbt may lag it
    fingerprint_seed: str = ""  # set by loaders that have no manifest file of their own to hash

    @property
    def fingerprint(self) -> str:
        if self.fingerprint_seed:
            return self.fingerprint_seed
        digest = hashlib.sha256()
        digest.update((self.root / MANIFEST_NAME).read_bytes())
        for model in sorted(self.models.values(), key=lambda m: m.id):
            digest.update(model.id.encode())
            digest.update(model.sql.encode())
        return digest.hexdigest()[:16]


def _require_ident(value: object, where: str) -> str:
    text = str(value).lower()
    if not _IDENT.match(text):
        raise ProjectError(f"{where}: {value!r} is not a plain SQL identifier")
    return text


def _column_meta(raw: object, where: str) -> ColumnMeta:
    if raw is None:
        return ColumnMeta()
    if isinstance(raw, str):
        return ColumnMeta(data_type=raw)
    if not isinstance(raw, dict):
        raise ProjectError(f"{where}: expected a type name or a mapping, got {type(raw).__name__}")
    unknown = set(raw) - {"description", "tests", "type"}
    if unknown:
        raise ProjectError(f"{where}: unknown keys {sorted(unknown)}")
    return ColumnMeta(
        description=str(raw.get("description", "")),
        tests=tuple(sorted(str(t) for t in raw.get("tests", ()))),
        data_type=str(raw.get("type", "")),
    )


def render(sql: str, sources: dict[str, Relation], model_names: set[str], where: str) -> str:
    """Resolve the two macros lineage depends on and refuse anything else.

    Guessing at arbitrary Jinja would produce a graph that looks right and is not,
    so an unsupported tag is an error rather than something to skip.
    """
    sql = _CONFIG.sub("", _COMMENT.sub("", sql))

    def ref(match: re.Match[str]) -> str:
        name = match.group(1).lower()
        if name not in model_names:
            raise ProjectError(f"{where}: ref('{name}') does not match any model")
        return name

    def source(match: re.Match[str]) -> str:
        key = f"{match.group(1)}.{match.group(2)}".lower()
        if key not in sources:
            raise ProjectError(f"{where}: source {key!r} is not declared in {MANIFEST_NAME}")
        return sources[key].ident

    sql = _SOURCE.sub(source, _REF.sub(ref, sql))
    leftover = _LEFTOVER.search(sql)
    if leftover:
        line = sql.count("\n", 0, leftover.start()) + 1
        raise ProjectError(
            f"{where}: unsupported template tag near line {line}; "
            "only ref(), source() and config() are understood"
        )
    return sql.strip().rstrip(";")


def load(root: Path | str) -> Project:
    """A native project (spillikin.yml) or, failing that, a compiled dbt project (target/manifest.json)."""
    root = Path(root).resolve()
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        from . import dbt  # imported here: dbt.py builds on the types defined in this module

        if dbt.is_dbt_project(root):
            return dbt.load(root)
        raise ProjectError(f"{root} holds neither {MANIFEST_NAME} nor a compiled dbt project (target/manifest.json)")
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ProjectError(f"{MANIFEST_NAME}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ProjectError(f"{MANIFEST_NAME}: top level must be a mapping")

    sources: dict[str, Relation] = {}
    for schema, tables in (manifest.get("sources") or {}).items():
        schema = _require_ident(schema, "sources")
        for table, spec in (tables or {}).items():
            table = _require_ident(table, f"sources.{schema}")
            spec = spec or {}
            where = f"sources.{schema}.{table}"
            columns = {
                _require_ident(name, where): _column_meta(meta, f"{where}.{name}")
                for name, meta in (spec.get("columns") or {}).items()
            }
            if not columns:
                raise ProjectError(f"{where}: a source must declare its columns")
            sources[f"{schema}.{table}"] = Relation(
                id=f"{schema}.{table}",
                ident=f"{schema}__{table}",
                kind="source",
                layer="source",
                owner=str(spec.get("owner", "")),
                description=str(spec.get("description", "")),
                columns=columns,
            )

    models_dir = root / str(manifest.get("models_dir", "models"))
    if not models_dir.is_dir():
        raise ProjectError(f"models directory {models_dir} does not exist")
    files = sorted(models_dir.rglob("*.sql"))
    names: dict[str, Path] = {}
    for file in files:
        name = _require_ident(file.stem, str(file.relative_to(root)))
        if name in names:
            raise ProjectError(f"model {name!r} is defined twice: {names[name]} and {file}")
        names[name] = file

    declared = manifest.get("models") or {}
    stray = set(declared) - set(names)
    if stray:
        raise ProjectError(f"models declared without a .sql file: {sorted(stray)}")

    models: dict[str, Relation] = {}
    for name, file in names.items():
        spec = declared.get(name) or {}
        rel = file.relative_to(root).as_posix()
        parent = file.parent.relative_to(models_dir).as_posix()
        models[name] = Relation(
            id=name,
            ident=name,
            kind="model",
            layer=parent if parent != "." else "model",
            path=rel,
            owner=str(spec.get("owner", "")),
            description=str(spec.get("description", "")),
            sql=render(file.read_text(encoding="utf-8"), sources, set(names), rel),
            columns={
                _require_ident(col, f"models.{name}"): _column_meta(meta, f"models.{name}.{col}")
                for col, meta in (spec.get("columns") or {}).items()
            },
        )

    exposures: dict[str, Exposure] = {}
    for name, spec in (manifest.get("exposures") or {}).items():
        name = _require_ident(name, "exposures")
        spec = spec or {}
        reads = tuple(sorted(str(r).lower() for r in spec.get("reads") or ()))
        if not reads:
            raise ProjectError(f"exposures.{name}: `reads` must list at least one column")
        exposures[name] = Exposure(
            id=name,
            kind=str(spec.get("kind", "other")),
            owner=str(spec.get("owner", "")),
            description=str(spec.get("description", "")),
            reads=reads,
        )

    return Project(
        name=str(manifest.get("project", root.name)),
        dialect=str(manifest.get("dialect", "duckdb")),
        root=root,
        sources=sources,
        models=models,
        exposures=exposures,
    )
