import wasmUrl from '@relate/relate-wasm/relate_bg.wasm?url';
import { useEffect, useMemo, useState } from 'react';
import { Board } from './Board.tsx';
import { Engine, type Change, type GraphFile, type Radius } from './engine.ts';

const STARTERS = [
  { id: 'raw.orders.status', note: 'a filter three models down' },
  { id: 'raw.payments.amount', note: 'a value that reaches the ledger export' },
  { id: 'raw.fx_rates.usd_rate', note: 'a rate every revenue figure multiplies by' },
];

type Loaded = { engine: Engine; graph: GraphFile };

export function App() {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const [change, setChange] = useState<Change>('logic');
  const [query, setQuery] = useState('');

  useEffect(() => {
    let live = true;
    (async () => {
      const response = await fetch(`${import.meta.env.BASE_URL}graph.json`);
      if (!response.ok) throw new Error(`graph.json returned ${response.status}`);
      const graph = (await response.json()) as GraphFile;
      const engine = await Engine.open({ module_or_path: wasmUrl }, graph);
      if (live) setLoaded({ engine, graph });
    })().catch((error: unknown) => live && setFailure(error instanceof Error ? error.message : String(error)));
    return () => { live = false; };
  }, []);

  const radius = useMemo<Radius | null>(
    () => (loaded && target ? loaded.engine.blastRadius(target, change) : null),
    [loaded, target, change],
  );
  const matches = useMemo(() => (loaded ? loaded.engine.search(query) : []), [loaded, query]);

  if (failure) {
    return <p className="p-8 max-w-xl">The lineage graph did not load: {failure}. Reload the page; if it keeps failing, the build is missing graph.json.</p>;
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-end gap-x-10 gap-y-4 px-6 pt-5 pb-4">
        <div>
          <h1 className="wordmark text-[2.6rem] leading-none">Spillikin</h1>
          <p className="mt-1 max-w-md text-[14px] opacity-80">
            In pick-up sticks you lift one stick without moving the pile. Pick a column and see what moves.
          </p>
        </div>
        <div className="seg" role="group" aria-label="What happens to the column">
          <button type="button" aria-pressed={change === 'logic'} onClick={() => setChange('logic')}>Its values change</button>
          <button type="button" aria-pressed={change === 'drop'} onClick={() => setChange('drop')}>It is dropped or renamed</button>
        </div>
        <div className="relative ml-auto w-full max-w-xs">
          <label className="sr-only" htmlFor="find">Find a column</label>
          <input id="find" className="field" placeholder="Find a column, e.g. paid revenue" value={query}
            onChange={(event) => setQuery(event.target.value)} autoComplete="off" />
          {matches.length > 0 && (
            <ul className="tile absolute z-20 mt-1 w-full overflow-hidden py-1">
              {matches.map((match) => (
                <li key={match.id}>
                  <button type="button" className="col" data-state="idle"
                    onClick={() => { setTarget(match.id); setQuery(''); }}>{match.id}</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <main className="min-h-[55vh] min-w-0 flex-1 overflow-auto px-6 pb-8">
          {loaded ? <Board graph={loaded.graph} radius={radius} onPick={setTarget} /> : <p className="opacity-70">Loading the graph into Relate…</p>}
        </main>

        <aside className="panel w-full shrink-0 overflow-y-auto px-6 py-6 lg:w-[23rem]" aria-live="polite">
          {radius ? <Result radius={radius} /> : (
            <>
              <h2 className="wordmark text-2xl">Pick a column on the table.</h2>
              <p className="mt-2 opacity-80">Or start with one of these:</p>
              <ul className="mt-4 space-y-3">
                {STARTERS.map((starter) => (
                  <li key={starter.id}>
                    <button type="button" className="starter" disabled={!loaded} onClick={() => setTarget(starter.id)}>{starter.id}</button>
                    <span className="ml-2 text-[13px] opacity-75">{starter.note}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {loaded && (
            <p className="mt-8 border-t border-white/10 pt-4 text-[12.5px] opacity-70">
              {loaded.graph.nodes.Column.length} columns across {loaded.graph.nodes.Relation.length} tables and models, loaded into
              Relate {loaded.engine.version} running as WebAssembly in this tab in {Math.round(loaded.engine.loadMs)} ms. Nothing is sent
              anywhere. <a href="https://github.com/demonjd2026-afk/Spillikin">Source on GitHub</a>
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}

function Result({ radius }: { radius: Radius }) {
  const byValue = radius.hits.filter((hit) => hit.via === 'value').length;
  const byRows = radius.hits.filter((hit) => hit.via === 'rows').length;
  const broken = radius.hits.filter((hit) => hit.severity === 'breaks').length;
  const untested = radius.hits.filter((hit) => !hit.guarded).length;
  const models = new Set(radius.hits.map((hit) => hit.relation)).size;
  const verb = radius.change === 'drop' ? 'stop working' : 'move';
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

  return (
    <>
      <p className="font-mono text-[12.5px] opacity-80">{radius.target}</p>
      <p className="count mt-2">{radius.hits.length}</p>
      <h2 className="wordmark text-2xl">{radius.hits.length === 1 ? 'column' : 'columns'} {verb}</h2>
      <p className="mt-1 text-[13.5px] opacity-80">in {plural(models, 'model')}, reaching {plural(radius.exposures.length, 'exposure')}</p>

      {radius.hits.length === 0 ? (
        <p className="mt-5">Nothing downstream reads this column. It lifts out clean.</p>
      ) : (
        <ul className="mt-5 space-y-1.5 text-[14px]">
          {broken > 0 && <li><span className="swatch bg-ink ring-1 ring-birch/60" />{broken} in {plural(radius.brokenModels.length, 'model')} that no longer compile</li>}
          <li><span className="swatch bg-band-red" />{byValue} because a value they are built from changes</li>
          <li><span className="swatch bg-band-blue" />{byRows} because the rows they are computed over change</li>
          <li><span className="swatch" style={{ width: 7, height: 7, borderRadius: '50%', border: '1.5px solid currentColor', marginLeft: 5, marginRight: 13 }} />{untested} of them have no test</li>
        </ul>
      )}

      {radius.exposures.length > 0 && (
        <>
          <h3 className="mt-6 text-[13px] font-semibold opacity-75">What people will see change</h3>
          <ul className="mt-2 space-y-2">
            {radius.exposures.map((exposure) => (
              <li key={exposure.id} className="text-[14px]">
                <span className="font-mono text-[12.5px]">{exposure.id}</span>
                <span className="opacity-75"> ({exposure.kind.replace('_', ' ')}), {plural(exposure.columns.length, 'column')}, owned by {exposure.owners.join(', ') || 'nobody'}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {radius.owners.length > 0 && (
        <p className="mt-5 text-[14px]"><span className="font-semibold">Tell before you change it:</span> {radius.owners.join(', ')}</p>
      )}

      <h3 className="mt-6 text-[13px] font-semibold opacity-75">The traversal Relate just ran ({radius.engineMs.toFixed(1)} ms)</h3>
      <pre className="cypher mt-2">{radius.statement}</pre>
    </>
  );
}
