import multiprocessing
import shutil
from pathlib import Path

import pytest

from spillikin import analysis, claims, workflow
from spillikin.store import Store


def test_build_is_idempotent_and_leaves_the_snapshot_untouched(meridian):
    root, store = meridian
    before = (store.path / "graph.json").read_bytes()
    result = workflow.build(root, store)
    assert result["changed"] is False
    assert (store.path / "graph.json").read_bytes() == before


def test_a_filter_column_reaches_far_more_than_its_values_do(meridian):
    _, store = meridian
    with store.read() as conn:
        radius = analysis.blast_radius(conn, ["raw.orders.status"], "logic")
    by_id = {row["id"]: row for row in radius["impacted"]}
    assert radius["summary"]["by_value"] == 1
    assert by_id["stg_orders.status"]["via"] == "value"
    assert by_id["fct_revenue_daily.net_revenue_usd"]["via"] == "rows of int_order_totals"
    assert {e["id"] for e in radius["exposures"]} == {"churn_model", "crm_sync", "month_end_close", "revenue_dashboard"}
    assert "stg_customers.email" not in by_id  # customers are not filtered by order status


def test_dropping_a_column_breaks_every_column_of_its_readers(meridian):
    _, store = meridian
    with store.read() as conn:
        radius = analysis.blast_radius(conn, ["stg_payments.amount"], "drop")
        drift = analysis.blast_radius(conn, ["stg_payments.amount"], "logic")
    by_id = {row["id"]: row for row in radius["impacted"]}
    assert radius["broken_models"] == ["int_payments_by_order"]
    # last_paid_at never read `amount`, but its model no longer compiles.
    assert by_id["int_payments_by_order.last_paid_at"]["severity"] == "breaks"
    assert by_id["fct_orders.paid_usd"]["severity"] == "blocked"
    assert "int_payments_by_order.last_paid_at" not in {r["id"] for r in drift["impacted"]}
    assert all(row["severity"] == "drifts" for row in drift["impacted"])


def test_provenance_walks_back_to_raw_sources(meridian):
    _, store = meridian
    with store.read() as conn:
        result = analysis.provenance(conn, "fct_revenue_daily.net_revenue_usd")
    sources = {o["id"] for o in result["origins"] if o["is_source"]}
    assert sources == {"raw.order_items.quantity", "raw.order_items.unit_price", "raw.orders.discount_amount", "raw.fx_rates.usd_rate"}
    assert {"id": "fct_orders.order_date", "roles": ["group"]} in result["shaped_by"]


def test_search_matches_words_inside_snake_case_names(meridian):
    _, store = meridian
    with store.read() as conn:
        hits = [h["id"] for h in analysis.search(conn, "paid revenue")]
        assert analysis.search(conn, "   ") == []
    assert hits[0] == "fct_revenue_daily.paid_revenue_usd"


def test_unknown_columns_are_rejected(meridian):
    _, store = meridian
    with store.read() as conn, pytest.raises(analysis.UnknownColumn, match="raw.orders.nope"):
        analysis.blast_radius(conn, ["raw.orders.nope"])


def test_claims_collide_when_their_reach_meets_downstream(meridian):
    _, store = meridian
    with store.write() as conn:
        first = claims.open_claim(conn, "agent-a", ["raw.payments.amount"], "store payments in minor units", "retype", now=1000)
    assert first["granted"]
    with store.write() as conn:
        # A different file, a different source table, the same dashboard.
        second = claims.open_claim(conn, "agent-b", ["raw.fx_rates.usd_rate"], "switch to mid-market rates", "logic", now=1001)
        own = claims.open_claim(conn, "agent-a", ["raw.fx_rates.usd_rate"], "follow-up to my own change", "logic", now=1001)
    assert second["granted"] is False
    assert second["collisions"][0]["agent"] == "agent-a"
    assert "month_end_close" in second["collisions"][0]["exposures"]
    assert own["granted"], "an agent does not collide with itself"
    with store.read() as conn:
        (refusal,) = claims.recent_refusals(conn, now=1002)
    assert refusal["agent"] == "agent-b" and refusal["blocked_by"][0]["agent"] == "agent-a"
    assert "fct_revenue_daily.paid_revenue_usd" in refusal["blocked_by"][0]["columns"]

    with store.write() as conn:
        claims.release_claim(conn, first["claim"], now=1002)
        claims.release_claim(conn, own["claim"], now=1002)
        retry = claims.open_claim(conn, "agent-b", ["raw.fx_rates.usd_rate"], "switch to mid-market rates", "logic", now=1003)
        assert claims.prune_claims(conn, now=1003) == 3  # two released, one refused attempt
    assert retry["granted"]


def test_expired_claims_do_not_block(meridian):
    _, store = meridian
    with store.write() as conn:
        claims.open_claim(conn, "agent-a", ["raw.payments.amount"], "abandoned work", ttl_seconds=60, now=1000)
        later = claims.open_claim(conn, "agent-b", ["raw.payments.amount"], "picked up later", now=1061)
    assert later["granted"]


def test_a_failed_claim_writes_nothing(meridian):
    _, store = meridian
    with pytest.raises(claims.ClaimError):
        with store.write() as conn:
            claims.open_claim(conn, "agent-a", ["raw.payments.amount"], "first", now=1000)
            claims.open_claim(conn, "bad agent name!", ["raw.payments.amount"], "second", now=1000)
    with store.read() as conn:
        assert claims.open_claims(conn, now=1000) == []


def _race(path: str, agent: str, queue) -> None:
    store = Store(path)
    with store.write() as conn:
        queue.put(claims.open_claim(conn, agent, ["raw.orders.status"], f"edit by {agent}")["granted"])


def test_two_processes_racing_for_one_column_get_one_grant(meridian):
    _, store = meridian
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    workers = [ctx.Process(target=_race, args=(str(store.path), f"agent-{i}", queue)) for i in range(6)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(60)
    assert sorted(queue.get(timeout=5) for _ in workers) == [False] * 5 + [True]


def test_check_reports_a_removed_column_and_the_model_it_breaks(meridian):
    root, store = meridian
    assert workflow.check(root, store)["clean"]

    staging = root / "models" / "staging" / "stg_payments.sql"
    staging.write_text(staging.read_text().replace("    amount,\n", ""))
    result = workflow.check(root, store)
    assert [f["model"] for f in result["failures"]] == ["int_payments_by_order"]
    drop = next(f for f in result["findings"] if f["column"] == "stg_payments.amount")
    assert drop["change"] == "drop" and "month_end_close" in drop["exposures"]
    assert result["blocking"] and result["unclaimed"] == ["stg_payments.amount"]


def test_check_sees_a_redefinition_and_a_claim_covers_it(meridian):
    root, store = meridian
    model = root / "models" / "staging" / "stg_orders.sql"
    model.write_text(model.read_text().replace("coalesce(discount_amount, 0)", "coalesce(discount_amount, 0) * 100"))
    result = workflow.check(root, store, agent="agent-a")
    assert [f["column"] for f in result["findings"]] == ["stg_orders.discount_amount"]
    assert result["blocking"]

    with store.write() as conn:
        claims.open_claim(conn, "agent-a", ["stg_orders.discount_amount"], "discounts in minor units", "logic")
    assert workflow.check(root, store, agent="agent-a")["blocking"] is False
    assert workflow.check(root, store, agent="agent-b")["blocking"] is True


def test_a_fresh_clone_holding_only_the_snapshot_opens(meridian, tmp_path: Path):
    _, store = meridian
    clone = tmp_path / "clone"
    (clone / ".relate" / "schema").mkdir(parents=True)
    for name in ("graph.json", "manifest.json", ".relate/schema/schema.cypher"):
        shutil.copy(store.path / name, clone / name)
    with Store(clone).read() as conn:
        assert analysis.blast_radius(conn, ["raw.orders.status"])["summary"]["columns"] == 48
