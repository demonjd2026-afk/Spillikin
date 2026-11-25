import { motion, useReducedMotion } from 'motion/react';
import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { GraphFile, Radius } from './engine.ts';

interface Segment { key: string; x1: number; y1: number; x2: number; y2: number; band: string; delay: number }

const BAND = { value: 'var(--color-band-red)', rows: 'var(--color-band-blue)', surface: 'var(--color-band-yellow)' } as const;

/** The table: one lane per dependency depth, sources on the left, the things people look at on the right. */
export function Board({ graph, radius, chips, meet, onPick }: {
  graph: GraphFile; radius: Radius | null; chips: Map<string, string>; meet: string[]; onPick: (id: string) => void;
}) {
  const meeting = useMemo(() => new Set(meet), [meet]);
  const table = useRef<HTMLDivElement>(null);
  const [sticks, setSticks] = useState<Segment[]>([]);
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
  const surfaced = useMemo(() => new Set(radius?.exposures.map((exposure) => exposure.id)), [radius]);

  // Sticks lie on the table under the tiles, so they show in the gutters and never cover a column name.
  // Sticks are measured from the laid-out tiles, so they stay true to whatever the browser rendered.
  useLayoutEffect(() => {
    const root = table.current;
    if (!root || !radius) { setSticks([]); return; }
    const measure = () => {
      const origin = root.getBoundingClientRect();
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
          src, dst: exposure.id, kind: 'surface' as const, to: `[data-exp="${exposure.id}"]`, hops: (state.get(src)?.hops ?? 0) + 1,
        }))),
      ];
      setSticks(wanted.flatMap((stick) => {
        const from = box(`[data-col="${stick.src}"]`);
        const to = box(stick.to);
        if (!from || !to) return [];
        return [{
          key: `${stick.kind}:${stick.src}>${stick.dst}`,
          x1: from.right - origin.left, y1: from.top + from.height / 2 - origin.top,
          x2: to.left - origin.left, y2: to.top + Math.min(to.height / 2, 14) - origin.top,
          band: BAND[stick.kind], delay: still ? 0 : Math.min(stick.hops, 9) * 0.11,
        }];
      }));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    return () => observer.disconnect();
  }, [radius, state, still]);

  const along = (s: Segment, t: number) => ({ x: s.x1 + (s.x2 - s.x1) * t, y: s.y1 + (s.y2 - s.y1) * t });

  return (
    <div ref={table} className="relative flex w-max items-start gap-14 pt-2 pr-10">
      {lanes.map((lane) => (
        <section key={lane.depth} className="flex w-[15rem] flex-col gap-5" aria-label={lane.relations[0]?.layer}>
          {lane.relations.map((relation) => (
            <article key={relation.id} className="tile" data-rel={relation.id}>
              <header className="tile-head">
                <div className="tile-name">{relation.id}</div>
                <div className="tile-meta">{relation.kind === 'source' ? 'raw source' : relation.layer}{relation.owner ? `, ${relation.owner}` : ''}</div>
              </header>
              <ul className="py-1">
                {relation.columns.map((column) => {
                  const mark = state.get(column.id)?.state ?? 'idle';
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
            </article>
          ))}
        </section>
      ))}

      <section className="flex w-[15rem] flex-col gap-5" aria-label="exposures">
        {graph.nodes.Exposure.map((exposure) => (
          <article key={exposure.id} className="exposure" data-exp={exposure.id} data-hit={surfaced.has(exposure.id)}>
            <div className="tile-name">{exposure.id}</div>
            <div className="tile-meta">{exposure.kind.replace('_', ' ')}</div>
            <p className="mt-1 text-[12px] leading-snug opacity-80">{exposure.description}</p>
          </article>
        ))}
      </section>

      <svg className="pointer-events-none absolute inset-0 z-0 h-full w-full overflow-visible" aria-hidden="true">
        {sticks.map((s) => {
          const a = along(s, 0.1), b = along(s, 0.16), c = along(s, 0.84), d = along(s, 0.9);
          return (
            <motion.g key={`${radius?.target}:${radius?.change}:${s.key}`} initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              transition={{ delay: s.delay, duration: still ? 0 : 0.25 }}>
              <motion.line x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2} stroke="var(--stick)" strokeWidth={2.6} strokeLinecap="round"
                initial={{ pathLength: still ? 1 : 0 }} animate={{ pathLength: 1 }} transition={{ delay: s.delay, duration: still ? 0 : 0.45, ease: 'easeOut' }} />
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={s.band} strokeWidth={2.6} />
              <line x1={c.x} y1={c.y} x2={d.x} y2={d.y} stroke={s.band} strokeWidth={2.6} />
            </motion.g>
          );
        })}
      </svg>
    </div>
  );
}
