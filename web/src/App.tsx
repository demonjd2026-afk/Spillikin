import wasmUrl from '@relate/relate-wasm/relate_bg.wasm?url';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Board } from './Board.tsx';
import { NumberTicker } from '@/components/ui/number-ticker';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Engine, type Change, type Claim, type GraphFile, type Radius, type Refusal } from './engine.ts';
import { useLive } from './live.ts';

const AGENTS = ['claude-code', 'codex'] as const;
const STARTERS = [
  { id: 'raw.orders.status', note: 'a filter three models down' },
  { id: 'raw.payments.amount', note: 'a value that reaches the ledger export' },
  { id: 'raw.fx_rates.usd_rate', note: 'a rate every revenue figure multiplies by' },
];
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

type Loaded = { engine: Engine; graph: GraphFile };

export function Explorer() {
  const live = useLive();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const [change, setChange] = useState<Change>('logic');
  const [query, setQuery] = useState('');
  const [meet, setMeet] = useState<string[]>([]);
  const [tick, setTick] = useState(0); // bumps after every claim so lists re-read the graph
  const [playing, setPlaying] = useState(false);
  const [focus, setFocus] = useState(true);
  const [zoom, setZoom] = useState<'fit' | number>('fit');
  const [full, setFull] = useState(false);
  const step = (by: number) => setZoom((z) => Math.min(1.4, Math.max(0.4, Math.round(((z === 'fit' ? 1 : z) + by) * 10) / 10)));
  const cancelled = useRef(false);

  useEffect(() => {
    let current = true;
    (async () => {
      const response = await fetch(`${import.meta.env.BASE_URL}graph.json`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`graph.json returned ${response.status}`);
      const graph = (await response.json()) as GraphFile;
      const engine = await Engine.open({ module_or_path: wasmUrl }, graph);
      if (current) setLoaded({ engine, graph });
    })().catch((error: unknown) => current && setFailure(error instanceof Error ? error.message : String(error)));
    return () => { current = false; };
  }, [live.graphVersion]);
  useEffect(() => () => { cancelled.current = true; }, []);
  useEffect(() => {
    if (!full) return;
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setFull(false); };
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', close);
    return () => { document.body.style.overflow = ''; window.removeEventListener('keydown', close); };
  }, [full]);

  const known = useMemo(() => new Set(loaded?.graph.nodes.Column.map((column) => column.id)), [loaded]);
  const radius = useMemo<Radius | null>(
    () => (loaded && target && known.has(target) ? loaded.engine.blastRadius(target, change) : null),
    [loaded, target, change, known],
  );
  const matches = useMemo(() => (loaded ? loaded.engine.search(query) : []), [loaded, query]);
  const claims = useMemo<Claim[]>(() => live.snapshot?.claims ?? loaded?.engine.claims() ?? [], [live.snapshot, loaded, tick]);
  const refusals = useMemo<Refusal[]>(() => live.snapshot?.refusals ?? loaded?.engine.refusals() ?? [], [live.snapshot, loaded, tick]);

  const pick = useCallback((id: string, kind?: Change) => { setTarget(id); setMeet([]); if (kind) setChange(kind); }, []);
  const claim = useCallback((agent: string, intent: string) => {
    if (!loaded || !target) return;
    const result = loaded.engine.claim(agent, target, change, intent);
    setMeet(result.blockers.flatMap((blocker) => blocker.columns));
    setTick((value) => value + 1);
  }, [loaded, target, change]);

  // The scripted collision runs the real claim logic in Relate; only the pacing is staged.
  const play = useCallback(async () => {
    if (!loaded) return;
    setPlaying(true);
    loaded.engine.clearClaims(); setTick((v) => v + 1); setChange('logic');
    const step = async (run: () => void) => { if (!cancelled.current) { run(); await pause(4200); } };
    await step(() => pick('raw.payments.amount'));
    await step(() => { loaded.engine.claim('claude-code', 'raw.payments.amount', 'logic', 'store payment amounts in minor units'); setTick((v) => v + 1); });
    await step(() => pick('raw.fx_rates.usd_rate'));
    await step(() => {
      const result = loaded.engine.claim('codex', 'raw.fx_rates.usd_rate', 'logic', 'switch to mid-market FX rates');
      setMeet(result.blockers.flatMap((blocker) => blocker.columns)); setTick((v) => v + 1);
    });
    setPlaying(false);
  }, [loaded, pick]);

  if (failure) {
    return <p className="max-w-xl p-8">The lineage graph did not load: {failure}. Reload the page; if it keeps failing, the build is missing graph.json.</p>;
  }
  const drift = live.snapshot?.drift;
  const chips = new Map(claims.flatMap((c) => c.targets.map((id) => [id, c.agent] as const)));

  return (
    <TooltipProvider>
      <div className={full ? 'explorer explorer-full' : 'explorer'}>
      <header className="toolbar">
        <div className="seg" role="group" aria-label="What happens to the column">
          <button type="button" aria-pressed={change === 'logic'} onClick={() => setChange('logic')}>Its values change</button>
          <button type="button" aria-pressed={change === 'drop'} onClick={() => setChange('drop')}>It is dropped or renamed</button>
        </div>
        {live.snapshot
          ? <p className="live" role="status"><span className="pulse" />Watching {live.snapshot.project} on this machine</p>
          : <button type="button" className="starter" disabled={!loaded || playing} onClick={play}>{playing ? 'Playing…' : 'Play two agents colliding'}</button>}
        <div className="view" role="group" aria-label="View">
          <button type="button" aria-pressed={focus} onClick={() => setFocus((on) => !on)} title="Hide tables and columns the change does not touch">Only what moves</button>
          <button type="button" onClick={() => step(-0.1)} aria-label="Zoom out">−</button>
          <button type="button" aria-pressed={zoom === 'fit'} onClick={() => setZoom('fit')} title="Scale the board to the space available">Fit</button>
          <button type="button" onClick={() => step(0.1)} aria-label="Zoom in">+</button>
          <button type="button" aria-pressed={full} onClick={() => setFull((on) => !on)}>{full ? 'Exit full screen' : 'Full screen'}</button>
        </div>
        <div className="relative w-full max-w-xs sm:ml-auto">
          <label className="sr-only" htmlFor="find">Find a column</label>
          <input id="find" className="field" placeholder="Find a column, e.g. paid revenue" value={query} autoComplete="off"
            onChange={(event) => setQuery(event.target.value)} />
          {matches.length > 0 && (
            <ul className="tile absolute z-20 mt-1 w-full overflow-hidden py-1">
              {matches.map((match) => (
                <li key={match.id}><button type="button" className="col" data-state="idle" onClick={() => { pick(match.id); setQuery(''); }}>{match.id}</button></li>
              ))}
            </ul>
          )}
        </div>
      </header>

      <div className="stage">
        <main className="board-area">
          <p className="key"><span className="swatch bg-band-yellow" />the column you picked<span className="swatch bg-band-red ml-5" />its value flows here<span className="swatch bg-band-blue ml-5" />its rows depend on it</p>
          {loaded
            ? <Board graph={loaded.graph} radius={radius} chips={chips} meet={meet} focus={focus} zoom={zoom} onPick={pick} />
            : <p className="opacity-70">Loading the graph into Relate…</p>}
        </main>

        <aside className="panel" aria-live="polite">
          {live.snapshot?.error && <p className="note">The last edit could not be read: {live.snapshot.error}</p>}
          {drift && (drift.findings.length > 0 || drift.failures.length > 0) && (
            <section className="mb-7">
              <h3 className="heading">Edits on disk, not yet in the graph</h3>
              <ul className="mt-2 space-y-2 text-[14px]">
                {drift.failures.map((f) => <li key={f.model}><span className="font-mono text-[12.5px]">{f.model}</span> no longer compiles: {f.reason}</li>)}
                {drift.findings.map((f) => (
                  <li key={f.column}>
                    <button type="button" className="link" onClick={() => pick(f.column, f.change)}>{f.column}</button>{' '}
                    {f.change === 'drop' ? 'was removed' : 'was redefined'}: {plural(f.summary.columns, 'column')}, {plural(f.exposures.length, 'exposure')}
                    {drift.unclaimed.includes(f.column) && <strong>. No claim covers it.</strong>}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {claims.length > 0 && (
            <section className="mb-7">
              <h3 className="heading">Changes in flight</h3>
              <ul className="mt-2 space-y-2.5 text-[14px]">
                {claims.map((c) => (
                  <li key={c.id}>
                    <span className="agent">{c.agent}</span> {c.intent}
                    <div className="mt-0.5 text-[13px] opacity-80">
                      <button type="button" className="link" onClick={() => pick(c.targets[0]!, c.change === 'drop' || c.change === 'logic' ? c.change : 'logic')}>{c.targets.join(', ')}</button> reaches {plural(c.reach, 'column')}
                      {!live.snapshot && <>, <button type="button" className="link" onClick={() => { loaded?.engine.release(c.id); setMeet([]); setTick((v) => v + 1); }}>release</button></>}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {refusals.length > 0 && (
            <section className="mb-7">
              <h3 className="heading">Stopped before editing</h3>
              <ul className="mt-2 space-y-2.5 text-[14px]">
                {refusals.map((r) => r.blocked_by.map((b) => (
                  <li key={`${r.id}:${b.claim}`}>
                    <span className="agent">{r.agent}</span> wanted to change <span className="font-mono text-[12.5px]">{r.targets.join(', ')}</span>.
                    It meets <span className="agent">{b.agent}</span>'s change at {plural(b.columns.length, 'column')}
                    {b.exposures.length > 0 && <> feeding <span className="font-mono text-[12.5px]">{b.exposures.join(', ')}</span></>}.{' '}
                    <button type="button" className="link" onClick={() => { setTarget(r.targets[0]!); setMeet(b.columns); }}>Show where they meet</button>
                  </li>
                )))}
              </ul>
            </section>
          )}

          {radius ? <Result radius={radius} canClaim={!live.snapshot && !playing} onClaim={claim} /> : (
            <>
              <h2 className="wordmark text-2xl">Pick a column on the table.</h2>
              <p className="mt-2 opacity-80">Or start with one of these:</p>
              <ul className="mt-4 space-y-3">
                {STARTERS.map((starter) => (
                  <li key={starter.id}>
                    <button type="button" className="starter" disabled={!loaded} onClick={() => pick(starter.id)}>{starter.id}</button>
                    <span className="ml-2 text-[13px] opacity-75">{starter.note}</span>
                  </li>
                ))}
              </ul>
            </>
          )}

          {loaded && (
            <p className="mt-8 border-t border-current/10 pt-4 text-[12.5px] opacity-70">
              {loaded.graph.nodes.Column.length} columns across {loaded.graph.nodes.Relation.length} tables and models, loaded into Relate {loaded.engine.version} running
              as WebAssembly in this tab in {Math.round(loaded.engine.loadMs)} ms. Nothing is sent anywhere.
            </p>
          )}
        </aside>
      </div>
      </div>

    </TooltipProvider>
  );
}

function Result({ radius, canClaim, onClaim }: { radius: Radius; canClaim: boolean; onClaim: (agent: string, intent: string) => void }) {
  const byValue = radius.hits.filter((hit) => hit.via === 'value').length;
  const byRows = radius.hits.filter((hit) => hit.via === 'rows').length;
  const broken = radius.hits.filter((hit) => hit.severity === 'breaks').length;
  const untested = radius.hits.filter((hit) => !hit.guarded).length;
  const models = new Set(radius.hits.map((hit) => hit.relation)).size;

  return (
    <>
      <p className="font-mono text-[12.5px] opacity-80">{radius.target}</p>
      <p className="count mt-2"><NumberTicker key={`${radius.target}:${radius.change}`} value={radius.hits.length} /></p>
      <h2 className="wordmark text-2xl">{radius.hits.length === 1 ? 'column' : 'columns'} {radius.change === 'drop' ? 'stop working' : 'move'}</h2>
      <p className="mt-1 text-[13.5px] opacity-80">in {plural(models, 'model')}, reaching {plural(radius.exposures.length, 'exposure')}</p>

      {radius.hits.length === 0 ? <p className="mt-5">Nothing downstream reads this column. It lifts out clean.</p> : (
        <ul className="mt-5 space-y-1.5 text-[14px]">
          {broken > 0 && <li><span className="swatch bg-ink ring-1 ring-current/50" />{broken} in {plural(radius.brokenModels.length, 'model')} that no longer compile</li>}
          <li><span className="swatch bg-band-red" />{byValue} because a value they are built from changes</li>
          <li><span className="swatch bg-band-blue" />{byRows} because the rows they are computed over change</li>
          <li><span className="dot" />{untested} of them have no test</li>
        </ul>
      )}

      {radius.exposures.length > 0 && (
        <>
          <h3 className="heading mt-6">What people will see change</h3>
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
      {radius.owners.length > 0 && <p className="mt-5 text-[14px]"><span className="font-semibold">Tell before you change it:</span> {radius.owners.join(', ')}</p>}

      {canClaim && (
        <div className="mt-5 flex flex-wrap items-center gap-2 text-[13.5px]">
          <span className="opacity-80">Claim this change as</span>
          {AGENTS.map((agent) => <button key={agent} type="button" className="starter" onClick={() => onClaim(agent, `change ${radius.target}`)}>{agent}</button>)}
        </div>
      )}

      <h3 className="heading mt-6">The traversal Relate just ran ({radius.engineMs.toFixed(1)} ms)</h3>
      <pre className="cypher mt-2">{radius.statement}</pre>
    </>
  );
}
