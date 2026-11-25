import json
from pathlib import Path

import pytest

from spillikin import analysis, project, workflow
from spillikin.project import ProjectError
from spillikin.store import Store


def node(kind: str, name: str, **extra) -> dict:
    return {"resource_type": kind, "name": name, "relation_name": f'"db"."main"."{name}"', "original_file_path": f"models/{name}.sql",
            "depends_on": {"nodes": []}, "columns": {}, **extra}


@pytest.fixture
def dbt_project(tmp_path: Path) -> Path:
    """The smallest compiled dbt project: a seed and a model that share a table name pattern, a test, an exposure."""
    manifest = {
        "metadata": {"adapter_type": "duckdb", "project_name": "tiny"},
        "nodes": {
            "seed.tiny.orders": node("seed", "orders"),
            "model.tiny.stg_orders": node("model", "stg_orders", compiled_code='select id as order_id, amount, status from "db"."main"."orders" where status <> \'void\'',
                                          columns={"order_id": {"description": "Order key."}, "ghost": {"description": "Removed long ago."}}),
            "model.tiny.revenue": node("model", "revenue", compiled_code='with o as (select * from "db"."main"."stg_orders") select sum(amount) as total from o'),
            "test.tiny.unique_order": {"resource_type": "test", "name": "unique_stg_orders_order_id", "attached_node": "model.tiny.stg_orders",
                                       "column_name": "order_id", "test_metadata": {"name": "unique"}},
        },
        "sources": {},
        "exposures": {"exposure.tiny.board": {"name": "board", "type": "dashboard", "owner": {"name": "finance"}, "depends_on": {"nodes": ["model.tiny.revenue"]}}},
    }
    catalog = {"nodes": {"seed.tiny.orders": {"columns": {"id": {"type": "INTEGER"}, "amount": {"type": "INTEGER"}, "status": {"type": "VARCHAR"}}}}, "sources": {}}
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "target" / "catalog.json").write_text(json.dumps(catalog))
    return tmp_path


def test_a_compiled_dbt_project_builds_and_answers(dbt_project: Path):
    store = Store(dbt_project / "store")
    result = workflow.build(dbt_project, store)
    assert (result["project"], result["relations"], result["exposures"]) == ("tiny", 3, 1)
    assert result["stale_docs"] == ["stg_orders.ghost"], "documentation that lags the SQL is reported, not fatal"
    with store.read() as conn:
        radius = analysis.blast_radius(conn, ["seed.orders.status"])
    by_id = {row["id"]: row for row in radius["impacted"]}
    assert by_id["stg_orders.order_id"]["via"] == "rows of stg_orders" and by_id["stg_orders.order_id"]["guarded"]
    assert by_id["revenue.total"]["via"] == "rows of stg_orders"
    assert radius["exposures"][0]["id"] == "board" and radius["notify"] == {"finance": ["exposure:board"]}
    assert workflow.check(dbt_project, store)["clean"]


def test_a_missing_catalog_says_what_to_run(dbt_project: Path):
    (dbt_project / "target" / "catalog.json").unlink()
    with pytest.raises(ProjectError, match="dbt docs generate"):
        project.load(dbt_project)
