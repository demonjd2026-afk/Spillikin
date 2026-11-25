from pathlib import Path

import pytest

from spillikin import lineage, project
from spillikin.project import ProjectError, Relation

SCHEMA = {"raw__t": {"id": "int", "amount": "int", "region": "text", "flag": "bool"}, "raw__u": {"id": "int", "amount": "int"}}
RELATIONS = {"raw__t": "raw.t", "raw__u": "raw.u"}


def trace(sql: str) -> lineage.ModelLineage:
    model = Relation(id="m", ident="m", kind="model", layer="model", sql=sql)
    return lineage.extract(model, SCHEMA, RELATIONS, "duckdb")


def test_kinds_survive_a_chain_of_ctes():
    out = trace("""
        with a as (select id, amount * 2 as doubled, region from raw__t),
             b as (select region, sum(doubled) as total from a group by region)
        select region, total, total as renamed from b
    """)
    assert out.columns["region"].feeds == {("raw.t", "region"): "passthrough"}
    assert out.columns["total"].feeds == {("raw.t", "amount"): "aggregate"}
    assert out.columns["renamed"].feeds == {("raw.t", "amount"): "aggregate"}
    assert out.shaped_by == {("raw.t", "region"): ("group",)}


def test_star_expands_against_upstream_schema():
    out = trace("select * from raw__u")
    assert list(out.columns) == ["id", "amount"]
    assert out.columns["amount"].feeds == {("raw.u", "amount"): "passthrough"}


def test_union_merges_both_branches_by_position():
    out = trace("select id, amount from raw__t union all select id, amount + 1 from raw__u")
    assert out.columns["amount"].feeds == {("raw.t", "amount"): "passthrough", ("raw.u", "amount"): "expression"}


def test_join_filter_and_window_roles():
    out = trace("""
        select t.id, row_number() over (partition by t.region order by u.amount) as rn
        from raw__t as t join raw__u as u on u.id = t.id
        where t.flag
    """)
    assert out.columns["rn"].feeds == {("raw.t", "region"): "window", ("raw.u", "amount"): "window"}
    assert out.shaped_by == {("raw.t", "flag"): ("filter",), ("raw.t", "id"): ("join",), ("raw.u", "id"): ("join",)}


def test_predicate_subquery_shapes_rows_and_scalar_subquery_feeds_value():
    out = trace("""
        select id, (select max(amount) from raw__u) as ceiling
        from raw__t where id in (select id from raw__u where amount > 0)
    """)
    assert out.columns["ceiling"].feeds == {("raw.u", "amount"): "expression"}
    assert out.shaped_by[("raw.u", "id")] == ("filter",)
    assert out.shaped_by[("raw.u", "amount")] == ("filter",)


def test_unknown_column_is_an_error_not_a_guess():
    with pytest.raises(lineage.LineageError, match="nope"):
        trace("select nope from raw__t")


def test_unsupported_template_tags_are_refused():
    with pytest.raises(ProjectError, match="unsupported template tag near line 2"):
        project.render("select 1\n{% if x %} from t", {}, set(), "m.sql")


def test_cycles_are_reported(tmp_path: Path):
    (tmp_path / "models").mkdir()
    (tmp_path / "spillikin.yml").write_text("project: p\n")
    (tmp_path / "models" / "a.sql").write_text("select * from {{ ref('b') }}")
    (tmp_path / "models" / "b.sql").write_text("select * from {{ ref('a') }}")
    with pytest.raises(lineage.LineageError, match="cycle"):
        lineage.dependency_order(project.load(tmp_path))
