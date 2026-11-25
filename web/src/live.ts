// When the page is served by `spillikin live`, the server streams what agents are doing on disk.
// On static hosting there is no stream, the first connection fails, and the page stays self-contained.
import { useEffect, useState } from 'react';
import type { Claim, Refusal } from './engine.ts';

export interface Finding { column: string; change: 'drop' | 'logic'; summary: { columns: number; models: number; exposures: number }; exposures: string[] }
export interface Snapshot {
  project: string;
  claims: Claim[];
  refusals: Refusal[];
  drift: { findings: Finding[]; failures: { model: string; reason: string }[]; unclaimed: string[] };
  graph_changed: boolean;
  error: string | null;
}

export function useLive(): { snapshot: Snapshot | null; graphVersion: number } {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [graphVersion, setGraphVersion] = useState(0);
  useEffect(() => {
    // Only `spillikin live` serves the stream, and it only ever binds to this machine.
    if (!['127.0.0.1', 'localhost'].includes(location.hostname)) return;
    const source = new EventSource(`${import.meta.env.BASE_URL}events`);
    let opened = false;
    source.onmessage = (event) => {
      opened = true;
      const next = JSON.parse(event.data as string) as Snapshot;
      setSnapshot(next);
      if (next.graph_changed) setGraphVersion((version) => version + 1);
    };
    source.onerror = () => { if (!opened) source.close(); };
    return () => source.close();
  }, []);
  return { snapshot, graphVersion };
}
