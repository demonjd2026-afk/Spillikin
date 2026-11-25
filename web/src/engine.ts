// The browser side of Spillikin: Relate's WebAssembly build holding the lineage graph in memory.
// Every number on screen comes from a Cypher statement run here, in the tab, against that graph.
import init, { RelateDb } from '@relate/relate-wasm';

export type Change = 'logic' | 'drop';
export type Severity = 'breaks' | 'blocked' | 'drifts';

export interface ColumnNode { id: string; name: string; relation: string; ordinal: number; description: string; tests: string[]; expression: string }
export interface RelationNode { id: string; kind: 'source' | 'model'; layer: string; depth: number; description: string }
export interface ExposureNode { id: string; kind: string; description: string }
export interface GraphFile {
  project: { id: string };
  nodes: { Relation: RelationNode[]; Column: ColumnNode[]; Exposure: ExposureNode[]; Owner: { id: string }[] };
  edges: Record<string, { src: string; dst: string; props: Record<string, unknown> }[]>;
}

export interface Hit { id: string; relation: string; severity: Severity; via: 'value' | 'rows' | 'reads'; gate: string | null; hops: number; guarded: boolean }
export interface Stick { src: string; dst: string; kind: 'value' | 'rows' }
export interface Radius {
  target: string;
  change: Change;
  hits: Hit[];
  brokenModels: string[];
  exposures: { id: string; kind: string; owners: string[]; columns: string[] }[];
  owners: string[];
  sticks: Stick[];
  statement: string;
  engineMs: number;
}

// Labels and relationship types are fixed here; values from the graph file travel as parameters only.
const EDGES: Record<string, [string, string, string]> = {
  HAS_COLUMN: ['Relation', 'HAS_COLUMN', 'Column'],
  FEEDS: ['Column', 'FEEDS', 'Column'],
  SHAPES: ['Column', 'SHAPES', 'Relation'],
  UPSTREAM_OF: ['Relation', 'UPSTREAM_OF', 'Relation'],
  SURFACES_IN: ['Column', 'SURFACES_IN', 'Exposure'],
  OWNS_RELATION: ['Owner', 'OWNS', 'Relation'],
  OWNS_EXPOSURE: ['Owner', 'OWNS', 'Exposure'],
};

export const REACH = `MATCH (seed:Column) WHERE seed.id IN $seeds
MATCH p = SHORTEST 1 (seed)-[:FEEDS|SHAPES|HAS_COLUMN]->+(hit:Column)
RETURN hit.id AS id, hit.relation AS relation, size(hit.tests) > 0 AS guarded,
       length(p) AS hops, [n IN nodes(p) | n.id] AS path, [r IN relationships(p) | type(r)] AS via`;

const REACH_BY_VALUE = `MATCH (seed:Column) WHERE seed.id IN $seeds
MATCH p = SHORTEST 1 (seed)-[:FEEDS]->+(hit:Column) RETURN DISTINCT hit.id AS id`;

const READERS = `MATCH (seed:Column) WHERE seed.id IN $seeds
CALL (seed) {
  MATCH (seed)-[:FEEDS]->(:Column)<-[:HAS_COLUMN]-(m:Relation) RETURN m
  UNION
  MATCH (seed)-[:SHAPES]->(m:Relation) RETURN m
}
RETURN DISTINCT m.id AS id`;

const COLUMNS_OF = `MATCH (m:Relation)-[:HAS_COLUMN]->(c:Column) WHERE m.id IN $relations
RETURN c.id AS id, c.relation AS relation, size(c.tests) > 0 AS guarded`;

const EXPOSURES = `MATCH (c:Column)-[:SURFACES_IN]->(e:Exposure) WHERE c.id IN $columns
OPTIONAL MATCH (o:Owner)-[:OWNS]->(e)
RETURN e.id AS id, e.kind AS kind, collect(DISTINCT c.id) AS columns, collect(DISTINCT o.id) AS owners ORDER BY id`;

const RELATION_OWNERS = `MATCH (o:Owner)-[:OWNS]->(m:Relation) WHERE m.id IN $relations RETURN DISTINCT o.id AS owner`;

const VALUE_STICKS = `MATCH (a:Column)-[:FEEDS]->(b:Column) WHERE a.id IN $ids AND b.id IN $ids RETURN a.id AS src, b.id AS dst`;

const ROW_STICKS = `MATCH (a:Column)-[:SHAPES]->(m:Relation) WHERE a.id IN $ids AND m.id IN $relations RETURN a.id AS src, m.id AS dst`;

const SEARCH = `MATCH (c:Column) SEARCH c IN (FULLTEXT INDEX column_text FOR $query LIMIT 8) SCORE AS score
RETURN c.id AS id, c.description AS description ORDER BY score DESC, id`;

export interface Claim { id: string; agent: string; intent: string; change: Change; targets: string[]; reach: number }
export interface Blocker { claim: string; agent: string; intent: string; columns: string[]; exposures: string[] }
export interface Refusal { id: string; agent: string; intent: string; targets: string[]; blocked_by: Blocker[] }

const COLLISIONS = `MATCH (holder:Agent)-[:HOLDS]->(claim:Claim {status: 'open'})-[:TARGETS|REACHES]->(c:Column)
WHERE holder.id <> $agent AND c.id IN $columns
OPTIONAL MATCH (c)-[:SURFACES_IN]->(e:Exposure)
RETURN claim.id AS claim, holder.id AS agent, claim.intent AS intent,
       collect(DISTINCT c.id) AS columns, collect(DISTINCT e.id) AS exposures ORDER BY claim`;

const OPEN_CLAIM = `MERGE (agent:Agent {id: $agent})
CREATE (agent)-[:HOLDS]->(claim:Claim {id: $id, intent: $intent, change: $change, status: $status, opened_at: $now})
WITH claim
CALL (claim) { UNWIND $targets AS id MATCH (c:Column {id: id}) CREATE (claim)-[:TARGETS]->(c) }
CALL (claim) { UNWIND $reach AS id MATCH (c:Column {id: id}) CREATE (claim)-[:REACHES]->(c) }
CALL (claim) {
  UNWIND $blockers AS b MATCH (other:Claim {id: b.claim})
  CREATE (claim)-[:BLOCKED_BY {columns: b.columns, exposures: b.exposures}]->(other)
}`;

const OPEN_CLAIMS = `MATCH (agent:Agent)-[:HOLDS]->(claim:Claim {status: 'open'})
RETURN claim.id AS id, agent.id AS agent, claim.intent AS intent, claim.change AS change,
       COLLECT { MATCH (claim)-[:TARGETS]->(c:Column) RETURN c.id ORDER BY c.id } AS targets,
       COUNT { (claim)-[:REACHES]->(:Column) } AS reach
ORDER BY claim.opened_at, id`;

const REFUSALS = `MATCH (agent:Agent)-[:HOLDS]->(claim:Claim {status: 'refused'})
RETURN claim.id AS id, agent.id AS agent, claim.intent AS intent,
       COLLECT { MATCH (claim)-[:TARGETS]->(c:Column) RETURN c.id ORDER BY c.id } AS targets,
       COLLECT {
         MATCH (claim)-[b:BLOCKED_BY]->(other:Claim)<-[:HOLDS]-(holder:Agent)
         RETURN {claim: other.id, agent: holder.id, intent: other.intent, columns: b.columns, exposures: b.exposures}
       } AS blocked_by
ORDER BY claim.opened_at DESC, id`;

interface Envelope<T> { rows: T[]; error: string | null; elapsed_ms: number }

export class Engine {
  private readonly db: RelateDb;
  readonly version: string;
  readonly loadMs: number;
  private sequence = 0;

  private constructor(db: RelateDb, version: string, loadMs: number) {
    this.db = db;
    this.version = version;
    this.loadMs = loadMs;
  }

  static async open(wasm: Parameters<typeof init>[0], graph: GraphFile): Promise<Engine> {
    await init(wasm);
    const started = performance.now();
    const db = new RelateDb();
    const engine = new Engine(db, '', 0);
    engine.run('CREATE FULLTEXT INDEX column_text IF NOT EXISTS FOR (n:Column) ON EACH [n.search_text]');
    for (const label of ['Relation', 'Column', 'Exposure', 'Owner'] as const) {
      engine.run(`UNWIND $rows AS r CREATE (n:${label}) SET n = r`, { rows: graph.nodes[label] });
    }
    for (const [key, [src, type, dst]] of Object.entries(EDGES)) {
      engine.run(
        `UNWIND $rows AS r MATCH (a:${src} {id: r.src}), (b:${dst} {id: r.dst}) CREATE (a)-[e:${type}]->(b) SET e = r.props`,
        { rows: graph.edges[key] ?? [] },
      );
    }
    const version = engine.run<{ v: string }>(
      "CALL dbms.components() YIELD name, versions WHERE name = 'Relate' RETURN versions[0] AS v",
    ).rows[0]?.v ?? 'unknown';
    return new Engine(db, version, performance.now() - started);
  }

  private run<T = Record<string, unknown>>(statement: string, params?: Record<string, unknown>): Envelope<T> {
    const envelope = JSON.parse(this.db.execute(statement, params ? JSON.stringify(params) : null)) as Envelope<T>;
    if (envelope.error) throw new Error(envelope.error);
    return envelope;
  }

  /** The same rules as the Python implementation in src/spillikin/analysis.py; scripts/parity.mjs holds them equal. */
  blastRadius(target: string, change: Change): Radius {
    const hits = new Map<string, Hit>();
    let brokenModels: string[] = [];
    let seeds = [target];
    if (change === 'drop') {
      brokenModels = this.run<{ id: string }>(READERS, { seeds }).rows.map((r) => r.id).sort();
      for (const row of this.run<Hit>(COLUMNS_OF, { relations: brokenModels }).rows) {
        hits.set(row.id, { id: row.id, relation: row.relation, severity: 'breaks', via: 'reads', gate: null, hops: 1, guarded: row.guarded });
      }
      if (hits.size) seeds = [...hits.keys()].sort();
    }
    const byValue = new Set(this.run<{ id: string }>(REACH_BY_VALUE, { seeds }).rows.map((r) => r.id));
    const reach = this.run<{ id: string; relation: string; guarded: boolean; hops: number; path: string[]; via: string[] }>(REACH, { seeds });
    for (const row of reach.rows) {
      const known = hits.get(row.id);
      const hops = row.hops + (change === 'drop' ? 1 : 0);
      if (row.id === target || known?.severity === 'breaks' || (known && known.hops <= hops)) continue;
      const shaped = row.via.indexOf('SHAPES');
      hits.set(row.id, {
        id: row.id,
        relation: row.relation,
        severity: change === 'drop' ? 'blocked' : 'drifts',
        via: byValue.has(row.id) ? 'value' : 'rows',
        gate: byValue.has(row.id) || shaped < 0 ? null : (row.path[shaped + 1] ?? null),
        hops,
        guarded: row.guarded,
      });
    }
    const list = [...hits.values()].sort((a, b) => a.hops - b.hops || a.id.localeCompare(b.id));
    const ids = [target, ...list.map((h) => h.id)];
    const relations = [...new Set(list.map((h) => h.relation))].sort();
    const exposures = this.run<Radius['exposures'][number]>(EXPOSURES, { columns: ids }).rows
      .map((e) => ({ ...e, owners: [...e.owners].sort(), columns: [...e.columns].sort() }));
    const owners = new Set(this.run<{ owner: string }>(RELATION_OWNERS, { relations }).rows.map((r) => r.owner));
    for (const exposure of exposures) for (const owner of exposure.owners) owners.add(owner);
    const sticks: Stick[] = [
      ...this.run<{ src: string; dst: string }>(VALUE_STICKS, { ids }).rows.map((s) => ({ ...s, kind: 'value' as const })),
      ...this.run<{ src: string; dst: string }>(ROW_STICKS, { ids, relations }).rows.map((s) => ({ ...s, kind: 'rows' as const })),
    ];
    return { target, change, hits: list, brokenModels, exposures, owners: [...owners].sort(), sticks, statement: REACH, engineMs: reach.elapsed_ms };
  }

  /** Records intent to change a column, or records the refusal when it meets someone else's open claim. */
  claim(agent: string, target: string, change: Change, intent: string): { granted: boolean; blockers: Blocker[] } {
    const radius = this.blastRadius(target, change);
    const reach = radius.hits.map((hit) => hit.id);
    const blockers = this.run<Blocker>(COLLISIONS, { agent, columns: [target, ...reach] }).rows
      .map((b) => ({ ...b, columns: [...b.columns].sort(), exposures: [...b.exposures].sort() }));
    this.sequence += 1;
    this.run(OPEN_CLAIM, {
      agent, intent, change, targets: [target], now: this.sequence,
      id: `clm_${this.sequence.toString().padStart(4, '0')}`,
      status: blockers.length ? 'refused' : 'open',
      reach: blockers.length ? [] : reach,
      blockers,
    });
    return { granted: blockers.length === 0, blockers };
  }

  release(id: string): void {
    this.run("MATCH (claim:Claim {id: $id}) SET claim.status = 'released'", { id });
  }

  clearClaims(): void {
    this.run('MATCH (claim:Claim) DETACH DELETE claim');
  }

  claims(): Claim[] {
    return this.run<Claim>(OPEN_CLAIMS).rows;
  }

  refusals(): Refusal[] {
    return this.run<Refusal>(REFUSALS).rows;
  }

  search(text: string): { id: string; description: string }[] {
    const query = text.replace(/[^A-Za-z0-9 ]+/g, ' ').trim();
    return query ? this.run<{ id: string; description: string }>(SEARCH, { query }).rows : [];
  }
}
