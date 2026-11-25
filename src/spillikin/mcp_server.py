"""MCP server: the same graph questions, shaped for a coding agent.

Each tool opens the store for the length of one call. Reads never take the writer
lease; a claim takes it for one transaction and gives it back, so any number of
agent sessions can run their own copy of this server against one store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from . import __version__, analysis, claims, workflow
from .store import Store

Change = Literal["drop", "rename", "retype", "logic"]

INSTRUCTIONS = """\
This server knows the column-level lineage of a SQL project.
Before editing a model: call blast_radius for every column you will drop, rename, retype or redefine.
If the result lists exposures or more than a handful of columns, call claim_change before you edit.
A refused claim means another agent's work reaches the same columns: stop and report the collision.
After editing, call check_working_tree; release_claim when the change is merged or abandoned.
Columns are written relation.column, for example raw.orders.status or fct_orders.net_revenue_usd."""


def create_server(root: Path, store: Store) -> MCPServer:
    server = MCPServer("spillikin", instructions=INSTRUCTIONS, version=__version__)

    def guarded(call: Any) -> dict[str, Any]:
        """Errors an agent can act on come back as data, not as a transport failure."""
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - the boundary between the engine and the agent
            return {"error": type(exc).__name__, "message": str(exc)}

    @server.tool()
    def blast_radius(columns: list[str], change: Change = "logic") -> dict[str, Any]:
        """Everything a change to these columns reaches: downstream columns, the models that stop compiling,
        the dashboards, exports and models that read them, and the owners to tell."""
        def run() -> dict[str, Any]:
            with store.read() as conn:
                return analysis.blast_radius(conn, columns, change)
        return guarded(run)

    @server.tool()
    def provenance(column: str) -> dict[str, Any]:
        """Where a column's values come from, back to raw sources, and which columns decide its rows."""
        def run() -> dict[str, Any]:
            with store.read() as conn:
                return analysis.provenance(conn, column)
        return guarded(run)

    @server.tool()
    def find_columns(query: str, limit: int = 10) -> dict[str, Any]:
        """Full-text search over column names and descriptions. Use it to turn a phrase into column ids."""
        def run() -> dict[str, Any]:
            with store.read() as conn:
                return {"matches": analysis.search(conn, query, limit)}
        return guarded(run)

    @server.tool()
    def claim_change(agent: str, columns: list[str], intent: str, change: Change = "logic", force: bool = False) -> dict[str, Any]:
        """Record that you are about to change these columns. Refused, with the collision, if another
        agent's open claim reaches any of the same columns."""
        def run() -> dict[str, Any]:
            with store.write() as conn:
                return claims.open_claim(conn, agent, columns, intent, change, force=force)
        return guarded(run)

    @server.tool()
    def release_claim(claim: str) -> dict[str, Any]:
        """Close a claim once the change is merged or abandoned."""
        def run() -> dict[str, Any]:
            with store.write() as conn:
                return claims.release_claim(conn, claim)
        return guarded(run)

    @server.tool()
    def open_claims() -> dict[str, Any]:
        """Changes other agents currently have in flight."""
        def run() -> dict[str, Any]:
            with store.read() as conn:
                return {"claims": claims.open_claims(conn)}
        return guarded(run)

    @server.tool()
    def check_working_tree(agent: str | None = None) -> dict[str, Any]:
        """Compare the SQL on disk with the graph: which columns you removed or redefined, which models
        no longer compile, what that reaches, and whether a claim covers it."""
        return guarded(lambda: workflow.check(root, store, agent))

    return server


def serve(root: Path, store: Store) -> None:
    create_server(root, store).run("stdio")
