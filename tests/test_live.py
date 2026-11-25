import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from spillikin import claims
from spillikin.live import Hub, make_handler


def test_the_hub_notices_claims_refusals_and_edits(meridian):
    root, store = meridian
    hub = Hub(root, store)
    assert hub.snapshot["claims"] == [] and hub.snapshot["drift"]["clean"]
    assert hub.refresh() is False, "nothing changed, nothing is sent"

    listener = hub.subscribe()
    listener.get_nowait()  # the snapshot every new page receives on connecting
    with store.write() as conn:
        claims.open_claim(conn, "agent-a", ["raw.payments.amount"], "minor units", "retype")
        claims.open_claim(conn, "agent-b", ["raw.fx_rates.usd_rate"], "mid-market rates")
    assert hub.refresh()
    event = json.loads(listener.get_nowait().removeprefix("data: "))
    assert [c["agent"] for c in event["claims"]] == ["agent-a"]
    assert event["refusals"][0]["blocked_by"][0]["exposures"] == ["month_end_close"]

    model = root / "models" / "staging" / "stg_payments.sql"
    model.write_text(model.read_text().replace("    amount,\n", ""))
    assert hub.refresh()
    assert hub.snapshot["drift"]["failures"][0]["model"] == "int_payments_by_order"

    model.write_text("select from where")  # a half-saved file
    assert hub.refresh() and hub.snapshot["drift"]["failures"], "a broken file is reported, not fatal"


def test_the_server_only_answers_locally_and_only_serves_the_explorer(meridian, tmp_path):
    root, store = meridian
    explorer = tmp_path / "dist"
    explorer.mkdir()
    (explorer / "index.html").write_text("<p>explorer</p>")
    (tmp_path / "secret.txt").write_text("outside the explorer")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Hub(root, store), explorer))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert urllib.request.urlopen(f"{base}/").read() == b"<p>explorer</p>"
        assert json.loads(urllib.request.urlopen(f"{base}/graph.json").read())["project"]["id"] == "meridian"
        for path, headers in (("/%2e%2e/secret.txt", {}), ("/", {"Host": "evil.example"})):
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(urllib.request.Request(base + path, headers=headers))
            assert refused.value.code in (403, 404)
    finally:
        server.shutdown()
        server.server_close()
