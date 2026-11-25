import { animate, motion, useMotionValue, useReducedMotion } from 'motion/react';
import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { GraphFile, Radius } from './engine.ts';


const BAND = { value: 'var(--color-band-red)', rows: 'var(--color-band-blue)', surface: 'var(--accent)' } as const;
interface Segment { key: string; kind: keyof typeof BAND; x1: number; y1: number; x2: number; y2: number; band: string; delay: number }

/** The table: one lane per dependency depth, sources on the left, the things people look at on the right. */
const STEP = 0.75; // seconds the camera spends on each step when a change is played back
const CLOSE = 1.25; // how far the camera pushes in while it follows the change
const LANE = 200, GAP = 40, PAD = 40; // px at zoom 1: lane width (12.5rem), gap-10, right padding

export function Board({ graph, radius, chips, meet, focus, zoom, onPick }: {
  graph: GraphFile; radius: Radius | null; chips: Map<string, string>; meet: string[];
  /** Hide every table and column the selection does not touch. */ focus: boolean;
  /** 'fit' scales the board to the space it has; a number is an explicit zoom. */ zoom: 'fit' | number;
  onPick: (id: string) => void;
}) {
  const [room, setRoom] = useState(0);
  const [cine, setCine] = useState(0); // 0 = still view; otherwise the id of the playback in progress
  const camX = useMotionValue(0);
  const camS = useMotionValue(1);
  const runs = useRef(0);
  const cineRef = useRef(0);
  cineRef.current = cine;
  // Sticks remember which playback they were timed for, so none are drawn with the previous playback's timing.
  const [drawn, setDrawn] = useState<{ run: number; sticks: Segment[] }>({ run: 0, sticks: [] });
  const setSticks = (sticks: Segment[]) => setDrawn({ run: cineRef.current, sticks });
  const sticks = drawn.run === cine ? drawn.sticks : [];
  const meeting = useMemo(() => new Set(meet), [meet]);
  const table = useRef<HTMLDivElement>(null);
  const still = useReducedMotion();

  const lanes = useMemo(() => {
    const columns = new Map<string, GraphFile['nodes']['Column']>();
    for (const column of graph.nodes.Column) {
      columns.set(column.relation, [...(columns.get(column.relation) ?? []), column]);
    }
    const owners = new Map(graph.edges.OWNS_RELATION?.map((edge) => [edge.dst, edge.src]));
    const byDepth = new Map<number, GraphFile['nodes']['Relation']>();
    for (const relation of graph.nodes.Relation) {
      byDepth.set(relation.depth, [...(byDepth.get(relation.depth) ?? []), relation]);
    }
    return [...byDepth.entries()].sort(([a], [b]) => a - b).map(([depth, relations]) => ({
      depth,
      relations: relations.sort((a, b) => a.id.localeCompare(b.id)).map((relation) => ({
        ...relation,
        owner: owners.get(relation.id),
        columns: (columns.get(relation.id) ?? []).sort((a, b) => a.ordinal - b.ordinal),
      })),
    }));
  }, [graph]);

  const state = useMemo(() => {
    const map = new Map<string, { state: string; hops: number }>();
    if (radius) {
      map.set(radius.target, { state: 'target', hops: 0 });
      for (const hit of radius.hits) map.set(hit.id, { state: hit.via, hops: hit.hops });
    }
    return map;
  }, [radius]);
  // Hop counts skip numbers (a filter adds two hops at once), so playback counts beats: 1st wave, 2nd wave, ...
  const beats = useMemo(() => new Map([...new Set(radius?.hits.map((hit) => hit.hops) ?? [])].sort((a, b) => a - b).map((hops, index) => [hops, index + 1])), [radius]);
  const beat = (hops: number) => (hops === 0 ? 0 : beats.get(hops) ?? beats.size + 1);
  const surfaced = useMemo(() => new Set(radius?.exposures.map((exposure) => exposure.id)), [radius]);

  // Focus: keep only the tables the change touches, and inside them only the columns that move.
  const focused = focus && radius !== null;
  const touched = useMemo(() => new Set(radius ? [radius.target.slice(0, radius.target.lastIndexOf('.')), ...radius.hits.map((hit) => hit.relation)] : []), [radius]);
  const shown = useMemo(() => lanes
    .map((lane) => ({ ...lane, relations: focused ? lane.relations.filter((relation) => touched.has(relation.id)) : lane.relations }))
    .filter((lane) => lane.relations.length > 0), [lanes, focused, touched]);
  const exposures = focused ? graph.nodes.Exposure.filter((exposure) => surfaced.has(exposure.id)) : graph.nodes.Exposure;
  const columns = shown.length + (exposures.length ? 1 : 0);
  const natural = columns * LANE + Math.max(columns - 1, 0) * GAP + PAD;
  const fit = Math.max(0.6, Math.min(1.15, room ? room / natural : 1));
  const scale = zoom === 'fit' ? fit : zoom;

  // When the board is wider than its window, the change is told step by step: the view starts on the
  // picked column and travels right with each hop, so what has already moved slides off to the left.
  const overflowing = !cine && room > 0 && natural * scale > room + 4;
  const pace = cine ? STEP : overflowing ? 0.42 : 0.11;
  useLayoutEffect(() => {
    const holder = table.current?.parentElement;
    const root = table.current;
    if (!holder || !root || !radius || !overflowing) return;
    const left = (id: string) => {
      const box = root.querySelector<HTMLElement>(`[data-col="${id}"]`)?.getBoundingClientRect();
      return box ? box.left - root.getBoundingClientRect().left : null;
    };
    const byHop = new Map<number, number>();
    for (const hit of radius.hits) {
      const x = left(hit.id);
      if (x !== null) byHop.set(hit.hops, Math.min(byHop.get(hit.hops) ?? x, x));
    }
    const go = (x: number | null) => { if (x !== null) holder.scrollTo({ left: Math.max(0, x - holder.clientWidth * 0.3), behavior: still ? 'auto' : 'smooth' }); };
    go(left(radius.target));
    const timers = [...byHop.entries()].map(([hops, x]) => window.setTimeout(() => go(x), (still ? 0 : Math.min(hops, 9) * pace) * 1000));
    return () => timers.forEach(window.clearTimeout);
  }, [radius, overflowing, pace, still, focused, scale]);

  // Playback. A new selection that does not fit at full size is told as a tracking shot: open close on the
  // picked column at full size, travel right one hop at a time as tables arrive, then pull back to show it all.
  const selection = radius ? `${radius.target}:${radius.change}` : '';
  useLayoutEffect(() => {
    if (!selection || !focused || zoom !== 'fit' || still || room === 0) { setCine(0); return; }
    runs.current += 1;
    setCine(runs.current);
  }, [selection, focused, zoom, still]); // eslint-disable-line react-hooks/exhaustive-deps -- a resize must not restart the shot

  useLayoutEffect(() => {
    const root = table.current;
    const holder = root?.parentElement;
    if (!cine || !root || !holder || !radius) return;
    holder.dataset.cine = 'true';
    holder.scrollTo({ left: 0, top: 0 });
    camS.set(1); camX.set(0);
    const origin = root.getBoundingClientRect().left;
    const leftOf = (selector: string) => (root.querySelector<HTMLElement>(selector)?.getBoundingClientRect().left ?? origin) - origin;
    const view = holder.clientWidth - 48;
    const aim = (x: number) => -Math.max(0, Math.min(natural * CLOSE - view, x * CLOSE - view * 0.26));
    const byBeat = new Map<number, number>([[0, leftOf(`[data-col="${radius.target}"]`)]]);
    for (const hit of radius.hits) byBeat.set(beat(hit.hops), Math.min(byBeat.get(beat(hit.hops)) ?? Infinity, leftOf(`[data-col="${hit.id}"]`)));
    const last = Math.max(...byBeat.keys());
    const ease = [0.3, 0.1, 0.2, 1] as const;
    const moves: { stop: () => void }[] = [];
    camS.set(Math.max(CLOSE, fit)); // a small selection that already fits is simply revealed step by step
    camX.set(aim(byBeat.get(0) ?? 0));
    // One move per beat, each started when its beat begins: a queued move would cancel the one before it.
    const timers = [...byBeat.entries()].filter(([step]) => step > 0).map(([step, x]) =>
      window.setTimeout(() => moves.push(animate(camX, aim(x), { duration: STEP * 0.9, ease })), (step * STEP - 0.3) * 1000));
    const pullBack = window.setTimeout(() => {
      moves.push(animate(camX, 0, { duration: 1, ease }));
      moves.push(animate(camS, fit, { duration: 1, ease, onComplete: () => setCine(0) }));
    }, ((last + 1) * STEP + 0.7) * 1000);
    return () => {
      window.clearTimeout(pullBack);
      timers.forEach(window.clearTimeout);
      moves.forEach((move) => move.stop());
      camX.set(0); camS.set(1);
      delete holder.dataset.cine;
    };
  }, [cine]); // eslint-disable-line react-hooks/exhaustive-deps -- one shot per playback id

  useLayoutEffect(() => {
    const holder = table.current?.parentElement;
    if (!holder) return;
    const read = () => setRoom(holder.clientWidth - 48);
    read();
    const observer = new ResizeObserver(read);
    observer.observe(holder);
    return () => observer.disconnect();
  }, []);

  // Sticks lie on the table under the tiles, so they show in the gutters and never cover a column name.
  // Sticks are measured from the laid-out tiles, so they stay true to whatever the browser rendered.
  useLayoutEffect(() => {
    const root = table.current;
    if (!root || !radius) { setSticks([]); return; }
    const measure = () => {
      const origin = root.getBoundingClientRect();
      const zoomed = cine ? camS.get() : scale; // on-screen pixels per board pixel right now
      const box = (selector: string) => root.querySelector<HTMLElement>(selector)?.getBoundingClientRect();
      const relationHops = new Map<string, number>();
      for (const hit of radius.hits) relationHops.set(hit.relation, Math.min(relationHops.get(hit.relation) ?? hit.hops, hit.hops));
      const wanted = [
        ...radius.sticks.map((stick) => ({
          ...stick,
          to: stick.kind === 'value' ? `[data-col="${stick.dst}"]` : `[data-rel="${stick.dst}"]`,
          hops: stick.kind === 'value' ? (state.get(stick.dst)?.hops ?? 1) : (relationHops.get(stick.dst) ?? 1),
        })),
        ...radius.exposures.flatMap((exposure) => exposure.columns.map((src) => ({
          src, dst: exposure.id, kind: 'surface' as const, to: `[data-exp="${exposure.id}"]`, hops: (radius.hits.at(-1)?.hops ?? 0) + 1,
        }))),
      ];
      setSticks(wanted.flatMap((stick) => {
        const from = box(`[data-col="${stick.src}"]`);
        const to = box(stick.to);
        if (!from || !to) return [];
        return [{
          key: `${stick.kind}:${stick.src}>${stick.dst}`, kind: stick.kind,
          // Rectangles are measured on screen; the drawing lives inside the zoomed board, so undo the zoom.
          x1: (from.right - origin.left) / zoomed, y1: (from.top + from.height / 2 - origin.top) / zoomed,
          x2: (to.left - origin.left) / zoomed, y2: (to.top + Math.min(to.height / 2, 14 * zoomed) - origin.top) / zoomed,
          band: BAND[stick.kind], delay: still ? 0 : (cine ? beat(stick.hops) : Math.min(stick.hops, 9)) * pace,
        }];
      }));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    return () => observer.disconnect();
  }, [radius, state, still, scale, focused, pace, cine]);

  // The hop at which each table first receives the change; its tile arrives with that hop.
  const arrivals = useMemo(() => {
    const map = new Map<string, number>();
    for (const hit of radius?.hits ?? []) map.set(hit.relation, Math.min(map.get(hit.relation) ?? hit.hops, hit.hops));
    return map;
  }, [radius]);
  const arrival = (relation: string) => arrivals.get(relation) ?? 0;
  const deepest = Math.max(0, ...arrivals.values());


  return (
    <motion.div ref={table} className="relative flex w-max items-start gap-10 pt-2 pr-10"
      style={{ zoom: cine ? 1 : scale, x: camX, scale: camS, transformOrigin: '0 0' }}>
      {shown.map((lane) => (
        <section key={lane.depth} className="flex w-[12.5rem] flex-col gap-5" aria-label={lane.relations[0]?.layer}>
          {lane.relations.map((relation) => (
            <motion.article key={`${cine}:${relation.id}`} className="tile" data-rel={relation.id}
              initial={cine ? { opacity: 0, filter: 'blur(8px)' } : false} animate={{ opacity: 1, filter: 'blur(0px)' }}
              transition={{ delay: Math.max(0, beat(arrival(relation.id)) * STEP - 0.15), duration: 0.5 }}>
              <header className="tile-head">
                <div className="tile-name">{relation.id}</div>
                <div className="tile-meta">{relation.kind === 'source' ? 'raw source' : relation.layer}{relation.owner ? `, ${relation.owner}` : ''}</div>
              </header>
              <ul className="py-1">
                {relation.columns.map((column) => {
                  const mark = state.get(column.id)?.state ?? 'idle';
                  if (focused && mark === 'idle') return null;
                  return (
                    <li key={column.id}>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button type="button" className="col" data-col={column.id} data-state={mark} data-dim={radius !== null}
                            data-meet={meeting.has(column.id)} onClick={() => onPick(column.id)}>
                            <span className="truncate">{column.name}</span>
                            {chips.has(column.id) && <span className="chip">{chips.get(column.id)}</span>}
                            {column.tests.length === 0 && mark !== 'idle' && mark !== 'target' && <span className="untested" aria-label="no test" />}
                          </button>
                        </TooltipTrigger>
                        <TooltipContent side="right">
                          {column.expression || column.description || 'raw column'}
                          {column.tests.length > 0 && <div className="mt-1 opacity-70">tests: {column.tests.join(', ')}</div>}
                        </TooltipContent>
                      </Tooltip>
                    </li>
                  );
                })}
              </ul>
              {focused && relation.columns.some((column) => !state.has(column.id)) && (
                <p className="tile-rest">{relation.columns.filter((column) => !state.has(column.id)).length} columns unchanged</p>
              )}
            </motion.article>
          ))}
        </section>
      ))}

      {exposures.length > 0 && <section className="flex w-[12.5rem] flex-col gap-5" aria-label="exposures">
        {exposures.map((exposure) => (
          <motion.article key={`${cine}:${exposure.id}`} className="exposure" data-exp={exposure.id} data-hit={surfaced.has(exposure.id)}
            initial={cine ? { opacity: 0, filter: 'blur(8px)' } : false} animate={{ opacity: 1, filter: 'blur(0px)' }}
            transition={{ delay: (beat(deepest) + 1) * STEP - 0.15, duration: 0.5 }}>
            <div className="tile-name">{exposure.id}</div>
            <div className="tile-meta">{exposure.kind.replace('_', ' ')}</div>
            <p className="mt-1 text-[12px] leading-snug opacity-80">{exposure.description}</p>
          </motion.article>
        ))}
      </section>}

      <svg className="pointer-events-none absolute inset-0 z-0 h-full w-full overflow-visible" aria-hidden="true">
        <defs>
          {Object.entries(BAND).map(([kind, colour]) => (
            <marker key={kind} id={`head-${kind}`} viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" markerUnits="userSpaceOnUse" orient="auto">
              <path d="M1 1.5 9 5 1 8.5z" fill={colour} />
            </marker>
          ))}
        </defs>
        {sticks.map((s) => {
          // A relationship is one smooth curve that leaves its column level and arrives level, with an arrowhead.
          const bend = Math.max(22, (s.x2 - s.x1) * 0.45);
          const d = `M${s.x1} ${s.y1} C${s.x1 + bend} ${s.y1} ${s.x2 - bend} ${s.y2} ${s.x2} ${s.y2}`;
          return (
            <motion.g key={`${cine}:${radius?.target}:${radius?.change}:${s.key}`} data-edge={s.key} initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              transition={{ delay: s.delay, duration: still ? 0 : 0.3 }}>
              <motion.path d={d} fill="none" stroke={s.band} strokeWidth={1.7} strokeLinecap="round" markerEnd={`url(#head-${s.kind})`}
                initial={{ pathLength: still ? 1 : 0 }} animate={{ pathLength: 1 }} transition={{ delay: s.delay, duration: still ? 0 : 0.5, ease: 'easeOut' }} />
            </motion.g>
          );
        })}
      </svg>
    </motion.div>
  );
}
