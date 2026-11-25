import { motion, useMotionValue, useReducedMotion, useSpring, useTransform, type MotionValue } from 'motion/react';
import type { PointerEvent, ReactNode } from 'react';
import { Explorer } from './App.tsx';

const REPO = 'https://github.com/demonjd2026-afk/Spillikin';
const HACKATHON = 'https://relate.devpost.com/';

// A dropped pile: [x1, y1, x2, y2, band colour, depth]. Depth sets how far a stick drifts with the pointer.
const PILE: [number, number, number, number, string, number][] = [
  [40, 250, 470, 96, 'var(--color-band-blue)', 0.3], [96, 60, 420, 330, 'var(--color-band-red)', 0.5],
  [20, 150, 500, 210, 'var(--color-ink)', 0.2], [150, 20, 250, 370, 'var(--color-band-blue)', 0.7],
  [300, 30, 110, 360, 'var(--color-band-red)', 0.9], [60, 320, 480, 280, 'var(--color-band-blue)', 0.4],
  [350, 50, 470, 350, 'var(--color-ink)', 0.6], [30, 70, 330, 130, 'var(--color-band-red)', 0.8],
  [200, 330, 500, 120, 'var(--color-band-blue)', 1.0], [80, 200, 380, 40, 'var(--color-band-red)', 0.35],
];
const PICKED: [number, number, number, number] = [70, 110, 450, 300];

function Stick({ line, band, depth, index, x, y }: { line: number[]; band: string; depth: number; index: number; x: MotionValue<number>; y: MotionValue<number> }) {
  const still = useReducedMotion();
  const dx = useTransform(x, (v) => v * depth * 14);
  const dy = useTransform(y, (v) => v * depth * 14);
  const [x1, y1, x2, y2] = line as [number, number, number, number];
  const at = (t: number) => [x1 + (x2 - x1) * t, y1 + (y2 - y1) * t] as const;
  const [a, b, c, d] = [at(0.07), at(0.13), at(0.87), at(0.93)];
  return (
    <motion.g style={{ x: dx, y: dy }} initial={still ? false : { opacity: 0, rotate: (index % 2 ? 1 : -1) * 14, scale: 1.08 }}
      animate={{ opacity: 1, rotate: 0, scale: 1 }} transition={{ delay: 0.15 + index * 0.07, type: 'spring', stiffness: 120, damping: 13 }}>
      <line x1={x1} y1={y1 + 5} x2={x2} y2={y2 + 5} stroke="#0003" strokeWidth={8} strokeLinecap="round" />
      <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="var(--color-wood)" strokeWidth={8} strokeLinecap="round" />
      <line x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke={band} strokeWidth={8} />
      <line x1={c[0]} y1={c[1]} x2={d[0]} y2={d[1]} stroke={band} strokeWidth={8} />
    </motion.g>
  );
}

function Pile() {
  const still = useReducedMotion();
  const px = useSpring(useMotionValue(0), { stiffness: 60, damping: 14 });
  const py = useSpring(useMotionValue(0), { stiffness: 60, damping: 14 });
  const move = (event: PointerEvent<SVGSVGElement>) => {
    if (still) return;
    const box = event.currentTarget.getBoundingClientRect();
    px.set((event.clientX - box.left) / box.width - 0.5);
    py.set((event.clientY - box.top) / box.height - 0.5);
  };
  const [x1, y1, x2, y2] = PICKED;
  return (
    <svg viewBox="0 0 520 390" className="pile" onPointerMove={move} onPointerLeave={() => { px.set(0); py.set(0); }} role="img"
      aria-label="A pile of pick-up sticks. One stick with yellow bands is lifted clear of the others.">
      {PILE.map(([a, b, c, d, band, depth], index) => <Stick key={index} line={[a, b, c, d]} band={band} depth={depth} index={index} x={px} y={py} />)}
      {/* The picked stick: it settles with the pile, then lifts clear without the others moving. */}
      <motion.g initial={still ? false : { opacity: 0, y: 0 }} animate={{ opacity: 1, y: still ? -26 : [0, 0, -26] }}
        transition={{ delay: 1, duration: 1.6, times: [0, 0.45, 1], ease: 'easeInOut' }}>
        <line x1={x1} y1={y1 + 30} x2={x2} y2={y2 + 30} stroke="#00000040" strokeWidth={9} strokeLinecap="round" />
        <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="var(--color-wood)" strokeWidth={9} strokeLinecap="round" />
        <line x1={x1 + 27} y1={y1 + 13.5} x2={x1 + 49} y2={y1 + 24.5} stroke="var(--color-band-yellow)" strokeWidth={9} />
        <line x1={x2 - 49} y1={y2 - 24.5} x2={x2 - 27} y2={y2 - 13.5} stroke="var(--color-band-yellow)" strokeWidth={9} />
      </motion.g>
    </svg>
  );
}

/** The mark: two crossed pick-up sticks on a birch tile, one banded for values, one for rows. */
function Mark() {
  return (
    <svg viewBox="0 0 40 40" aria-hidden="true">
      <rect width="40" height="40" rx="9" fill="var(--color-birch)" />
      <path d="M8 31 32 9" stroke="#b8935a" strokeWidth="4.2" strokeLinecap="round" />
      <path d="M11.6 27.7 14.6 25M25.4 15 28.4 12.3" stroke="var(--color-band-red)" strokeWidth="4.2" />
      <path d="M9 11 31 30" stroke="#b8935a" strokeWidth="4.2" strokeLinecap="round" />
      <path d="M12.4 13.9 15.4 16.5M24.6 24.5 27.6 27.1" stroke="var(--color-band-blue)" strokeWidth="4.2" />
    </svg>
  );
}

function Reveal({ children, className }: { children: ReactNode; className?: string }) {
  const still = useReducedMotion();
  return (
    <motion.section className={className} initial={still ? false : { opacity: 0, y: 28 }} whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-12% 0px' }} transition={{ duration: 0.6, ease: [0.2, 0.7, 0.2, 1] }}>
      {children}
    </motion.section>
  );
}

const QUESTIONS = [
  { title: 'What does this change reach?', body: 'Every downstream column, the models that stop compiling, the dashboards and exports that read them, and who owns each one.', code: 'spillikin impact raw.orders.status' },
  { title: 'Is someone already changing it?', body: 'An agent records what it is about to change. A second agent whose change reaches the same columns is refused, and told where the two meet.', code: 'spillikin claim raw.payments.amount \\\n  --agent claude-code --intent "minor units"' },
  { title: 'What did my edits do?', body: 'The SQL on disk is compared with the graph in the repository. Removed columns, redefined columns and models that no longer compile are reported with their reach.', code: 'spillikin check' },
];

const PIPELINE = [
  { name: 'SQL models', note: 'one SELECT per file, plus a manifest of sources, owners, tests and exposures' },
  { name: 'Lineage', note: 'each query is parsed and traced through CTEs, subqueries and unions, per column' },
  { name: 'Relate graph', note: 'a folder in the repository; any number of readers beside one writer' },
  { name: 'Agents and people', note: 'command line, MCP tools, a live local page, and this page in WebAssembly' },
];

export function Site() {
  return (
    <>
      <nav className="nav">
        <a href="#top" className="brand" aria-label="Spillikin, column lineage for coding agents">
          <Mark />
          <span>
            <span className="wordmark brand-name">Spillikin</span>
            <span className="brand-tag">column lineage for coding agents</span>
          </span>
        </a>
        <div className="nav-links">
          <a href="#explorer">Explorer</a>
          <a href="#how">How it works</a>
          <a href="#run">Run it</a>
          <a className="button button-quiet" href={REPO}>GitHub</a>
        </div>
      </nav>

      <header id="top" className="hero">
        <div>
          <p className="kicker">Built on Relate, the embedded graph database from Neo4j</p>
          <h1 className="wordmark headline">Pull one column without moving the rest.</h1>
          <p className="lede">
            Spillikin reads a SQL project into a graph of columns. Before an agent edits a model, it asks the graph what the change reaches,
            and whether another agent's change reaches the same place.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <a className="button" href="#explorer">Open the explorer</a>
            <a className="button button-quiet" href={REPO}>Read the source</a>
          </div>
        </div>
        <Pile />
      </header>

      <Reveal className="hook">
        <p className="hook-figures"><span>1</span><span className="hook-vs">against</span><span>48</span></p>
        <div>
          <h2 className="wordmark text-[2rem] leading-tight">Searching the repository for one column finds one model. The graph finds forty-eight columns.</h2>
          <p className="mt-4 max-w-xl opacity-85">
            <code>raw.orders.status</code> is selected once. It is also a filter three models down, so every row of every model below that filter
            depends on it: the revenue dashboard, the month-end export, the churn features. None of those queries mention it.
          </p>
        </div>
      </Reveal>

      <section id="explorer" className="frame-wrap">
        <div className="frame">
          <div className="frame-bar"><span /><span /><span /><p>meridian, a made-up retailer: 15 models, 135 columns, queried in this tab</p></div>
          <Explorer />
        </div>
      </section>

      <Reveal className="band">
        <h2 className="wordmark section-title">Three questions before an edit</h2>
        <div className="questions">
          {QUESTIONS.map((q) => (
            <article key={q.title} className="question">
              <h3>{q.title}</h3>
              <p>{q.body}</p>
              <pre className="cypher">{q.code}</pre>
            </article>
          ))}
        </div>
      </Reveal>

      <Reveal className="band">
        <div id="how" className="anchor" />
        <h2 className="wordmark section-title">How it works</h2>
        <ol className="pipeline">
          {PIPELINE.map((step) => (
            <li key={step.name}><strong>{step.name}</strong><span>{step.note}</span></li>
          ))}
        </ol>
        <div className="legend">
          <p><span className="swatch bg-band-red" /><strong>A value flows.</strong> One column is computed from another. Search can find these.</p>
          <p><span className="swatch bg-band-blue" /><strong>Rows are decided.</strong> A join key, a filter or a grouping decides which rows a model has, so it reaches every column of that model. Search cannot find these.</p>
        </div>
      </Reveal>

      <Reveal className="band">
        <div id="run" className="anchor" />
        <h2 className="wordmark section-title">Run it</h2>
        <div className="run">
          <pre className="cypher">{`git clone ${REPO}.git
cd Spillikin && pip install -e .
export SPILLIKIN_PROJECT=examples/meridian

spillikin impact raw.orders.status
spillikin serve     # the same answers over MCP
spillikin live      # this explorer, updating as files change`}</pre>
          <p>
            No account, API key or server. The graph is a Relate directory store committed next to the SQL, and continuous integration fails when the two disagree.
            The full walkthrough, the graph model and the design notes are in the README.
          </p>
        </div>
      </Reveal>

      <footer className="footer">
        <p className="brand"><Mark /><span className="wordmark brand-name">Spillikin</span></p>
        <p>Built by Jayanth Dolai for the <a href={HACKATHON}>Relate by Neo4j Hackathon</a>. Apache-2.0.</p>
        <p><a href={REPO}>GitHub</a><a href="https://www.linkedin.com/in/jayanth-dolai-7b115213a/">LinkedIn</a><a href="https://relatedb.ai">Relate</a></p>
      </footer>
    </>
  );
}
