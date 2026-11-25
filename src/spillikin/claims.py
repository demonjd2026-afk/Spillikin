"""Claims: how agents working in parallel find out that their changes meet downstream.

Two agents can edit different files and never touch the same line, yet both alter
what one dashboard shows. A claim records the columns an agent intends to change
together with everything those columns reach. A second claim is checked against
that record inside the same transaction that would create it, so two agents can
never both be told the path is clear.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

import relate

from .analysis import CHANGES, blast_radius

DEFAULT_TTL_SECONDS = 2 * 60 * 60
_AGENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")

# Open claims held by anyone else whose reach meets ours, with the columns where they meet.
COLLISIONS = """
MATCH (holder:Agent)-[:HOLDS]->(claim:Claim {status: 'open'})-[:TARGETS|REACHES]->(c:Column)
WHERE holder.id <> $agent AND claim.expires_at > $now AND c.id IN $columns
OPTIONAL MATCH (c)-[:SURFACES_IN]->(e:Exposure)
RETURN claim.id AS claim, holder.id AS agent, claim.intent AS intent, claim.change AS change,
       collect(DISTINCT c.id) AS columns, collect(DISTINCT e.id) AS exposures
ORDER BY claim
"""

OPEN = """
MERGE (agent:Agent {id: $agent})
CREATE (agent)-[:HOLDS]->(claim:Claim {
    id: $id, intent: $intent, change: $change, status: 'open',
    opened_at: $now, expires_at: $expires_at, contested: $contested
})
WITH claim
CALL (claim) { UNWIND $targets AS id MATCH (c:Column {id: id}) CREATE (claim)-[:TARGETS]->(c) }
CALL (claim) { UNWIND $reach AS row MATCH (c:Column {id: row.id}) CREATE (claim)-[:REACHES {severity: row.severity}]->(c) }
"""

# A refused attempt is kept too: who was stopped, by whom, and where their changes meet.
REFUSE = """
MERGE (agent:Agent {id: $agent})
CREATE (agent)-[:HOLDS]->(claim:Claim {
    id: $id, intent: $intent, change: $change, status: 'refused', opened_at: $now, expires_at: $expires_at
})
WITH claim
CALL (claim) { UNWIND $targets AS id MATCH (c:Column {id: id}) CREATE (claim)-[:TARGETS]->(c) }
CALL (claim) {
    UNWIND $blockers AS b MATCH (other:Claim {id: b.claim})
    CREATE (claim)-[:BLOCKED_BY {columns: b.columns, exposures: b.exposures}]->(other)
}
"""

REFUSALS = """
MATCH (agent:Agent)-[:HOLDS]->(claim:Claim {status: 'refused'}) WHERE claim.opened_at > $since
RETURN claim.id AS id, agent.id AS agent, claim.intent AS intent, claim.change AS change, claim.opened_at AS opened_at,
       COLLECT { MATCH (claim)-[:TARGETS]->(c:Column) RETURN c.id ORDER BY c.id } AS targets,
       COLLECT {
           MATCH (claim)-[b:BLOCKED_BY]->(other:Claim)<-[:HOLDS]-(holder:Agent)
           RETURN {claim: other.id, agent: holder.id, intent: other.intent, columns: b.columns, exposures: b.exposures}
       } AS blocked_by
ORDER BY opened_at DESC, id
"""

RELEASE = """
MATCH (agent:Agent)-[:HOLDS]->(claim:Claim {id: $id})
WHERE claim.status = 'open'
SET claim.status = 'released', claim.released_at = $now
RETURN agent.id AS agent
"""

LIST = """
MATCH (agent:Agent)-[:HOLDS]->(claim:Claim {status: 'open'}) WHERE claim.expires_at > $now
RETURN claim.id AS id, agent.id AS agent, claim.intent AS intent, claim.change AS change,
       claim.opened_at AS opened_at, claim.expires_at AS expires_at, claim.contested AS contested,
       COLLECT { MATCH (claim)-[:TARGETS]->(c:Column) RETURN c.id ORDER BY c.id } AS targets,
       COUNT { (claim)-[:REACHES]->(:Column) } AS reach
ORDER BY opened_at, id
"""

PRUNE = """
MATCH (claim:Claim) WHERE claim.status IN ['released', 'refused'] OR claim.expires_at <= $now
DETACH DELETE claim
RETURN count(*) AS removed
"""

PRUNE_AGENTS = "MATCH (agent:Agent) WHERE NOT EXISTS { (agent)-[:HOLDS]->(:Claim) } DELETE agent"


class ClaimError(Exception):
    pass


def open_claim(
    conn: relate.Connection,
    agent: str,
    columns: list[str],
    intent: str,
    change: str = "logic",
    *,
    force: bool = False,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: int | None = None,
) -> dict[str, Any]:
    """Record intent to change `columns`, unless it meets someone else's open claim.

    Must run inside `Store.write()`: the collision check and the insert share a transaction.
    """
    if not _AGENT.match(agent):
        raise ClaimError("agent name must be 1-64 characters of letters, digits, '.', '_', '@' or '-'")
    intent = " ".join(intent.split())
    if not intent or len(intent) > 500:
        raise ClaimError("intent must be a sentence of at most 500 characters")
    if change not in CHANGES:
        raise ClaimError(f"change must be one of {', '.join(CHANGES)}")
    if not 60 <= ttl_seconds <= 7 * 24 * 60 * 60:
        raise ClaimError("ttl must be between one minute and seven days")
    now = int(time.time()) if now is None else now

    radius = blast_radius(conn, columns, change)
    footprint = [*radius["targets"], *(row["id"] for row in radius["impacted"])]
    collisions = [
        {"claim": r.claim, "agent": r.agent, "intent": r.intent, "change": r.change,
         "columns": sorted(r.columns), "exposures": sorted(r.exposures)}
        for r in conn.execute(COLLISIONS, {"agent": agent, "now": now, "columns": footprint}).fetchall()
    ]
    claim_id = f"clm_{uuid.uuid4().hex[:12]}"
    if collisions and not force:
        conn.execute(REFUSE, {
            "agent": agent, "id": claim_id, "intent": intent, "change": change, "now": now,
            "expires_at": now + ttl_seconds, "targets": radius["targets"],
            "blockers": [{"claim": c["claim"], "columns": c["columns"], "exposures": c["exposures"]} for c in collisions],
        })
        return {"granted": False, "attempt": claim_id, "collisions": collisions, "radius": radius["summary"]}

    conn.execute(OPEN, {
        "agent": agent, "id": claim_id, "intent": intent, "change": change, "now": now,
        "expires_at": now + ttl_seconds, "contested": bool(collisions), "targets": radius["targets"],
        "reach": [{"id": row["id"], "severity": row["severity"]} for row in radius["impacted"]],
    })
    return {
        "granted": True, "claim": claim_id, "expires_at": now + ttl_seconds,
        "collisions": collisions, "radius": radius["summary"], "notify": radius["notify"],
    }


def release_claim(conn: relate.Connection, claim_id: str, now: int | None = None) -> dict[str, Any]:
    now = int(time.time()) if now is None else now
    row = conn.execute(RELEASE, {"id": claim_id, "now": now}).fetchall()
    if not row:
        raise ClaimError(f"no open claim {claim_id!r}")
    return {"released": claim_id, "agent": row[0].agent}


def open_claims(conn: relate.Connection, now: int | None = None) -> list[dict[str, Any]]:
    now = int(time.time()) if now is None else now
    return [dict(row) for row in conn.execute(LIST, {"now": now}).fetchall()]


def recent_refusals(conn: relate.Connection, now: int | None = None, window_seconds: int = 3600) -> list[dict[str, Any]]:
    now = int(time.time()) if now is None else now
    return [dict(row) for row in conn.execute(REFUSALS, {"since": now - window_seconds}).fetchall()]


def prune_claims(conn: relate.Connection, now: int | None = None) -> int:
    now = int(time.time()) if now is None else now
    removed = conn.execute(PRUNE, {"now": now}).single().removed
    conn.execute(PRUNE_AGENTS)
    return removed
