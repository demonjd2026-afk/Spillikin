// Holds the browser engine to the Python engine: same graph, same columns, same answer.
// Run from web/: `node scripts/parity.mjs` (needs the `spillikin` CLI on PATH).
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { Engine } from '../src/engine.ts';

const here = new URL('.', import.meta.url);
const graph = JSON.parse(readFileSync(new URL('../public/graph.json', here), 'utf8'));
const wasm = readFileSync(new URL('../node_modules/@relate/relate-wasm/relate_bg.wasm', here));
const engine = await Engine.open({ module_or_path: wasm }, graph);

const sign = (rows) => rows.map((r) => `${r.id}|${r.severity}|${r.via}|${r.hops}`).sort().join('\n');
const via = (text) => (text === 'value' ? 'value' : text.startsWith('rows of') ? 'rows' : 'reads');
let checked = 0;
for (const column of graph.nodes.Column) {
  for (const [mine, theirs] of [['logic', 'logic'], ['drop', 'drop']]) {
    const python = JSON.parse(execFileSync('spillikin', ['impact', column.id, '--change', theirs, '--json', '--project', '../examples/meridian'], { encoding: 'utf8' }));
    const browser = engine.blastRadius(column.id, mine);
    const expected = sign(python.impacted.map((r) => ({ ...r, via: via(r.via) })));
    if (sign(browser.hits) !== expected) throw new Error(`mismatch for ${mine} ${column.id}`);
    if (browser.exposures.map((e) => e.id).join() !== python.exposures.map((e) => e.id).join()) throw new Error(`exposures differ for ${column.id}`);
    if (browser.owners.join() !== Object.keys(python.notify).sort().join()) throw new Error(`owners differ for ${column.id}`);
    checked += 1;
  }
}
console.log(`parity holds for ${checked} blast radii across ${graph.nodes.Column.length} columns (Relate ${engine.version})`);
