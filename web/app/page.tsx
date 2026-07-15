"use client";

import { useEffect, useMemo, useState } from "react";

type RunSummary = {
  run_id: string;
  generated_at: string | null;
  suite: string | null;
  repo: { sha: string | null; branch: string | null; dirty: boolean | null };
  summary: { total: number; pass: number; fail: number; skip: number };
  path: string;
};

type Catalog = { schema_version: number; generated_at: string; runs: RunSummary[] };
type Metrics = Record<string, number | null>;
type Identity = {
  model_id: string;
  engine: string;
  board: string;
  toolchain: string;
  transport: string;
  requested_memory: Record<string, unknown>;
  requested_power: Record<string, unknown>;
  attempt: number;
};
type Case = {
  case_id: string;
  identity_key: string;
  identity: Identity;
  status: string;
  health_issues: string[];
  provenance: Record<string, unknown>;
  metrics: Metrics;
  layer_path: string | null;
};
type Run = RunSummary & { cases: Case[]; hpx_version: string | null };
type Layer = {
  index: number;
  op: string;
  cycles: number | null;
  cycles_pct: number | null;
  overflow: boolean | null;
  macs: number | null;
  ops: number | null;
  counters: Record<string, number>;
};
type LayerDocument = { layers: Layer[] };
type Pair = { key: string; baseline?: Case; candidate?: Case };
type Filters = { model: string; board: string; engine: string; toolchain: string };

const emptyFilters: Filters = { model: "all", board: "all", engine: "all", toolchain: "all" };

function formatNumber(value: number | null | undefined): string {
  if (value == null) return "—";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
}

function formatDate(value: string | null): string {
  if (!value) return "Unknown date";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function deltaPct(baseline: number | null | undefined, candidate: number | null | undefined) {
  if (baseline == null || candidate == null || baseline === 0) return null;
  return ((candidate - baseline) / baseline) * 100;
}

function Delta({ value }: { value: number | null }) {
  if (value == null) return <span className="delta neutral">Not comparable</span>;
  const kind = Math.abs(value) < 0.01 ? "neutral" : value > 0 ? "regression" : "improvement";
  return (
    <span className={`delta ${kind}`}>
      {value > 0 ? "+" : ""}{value.toFixed(3)}%
    </span>
  );
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="filter-control">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="all">All</option>
        {options.map((option) => <option key={option}>{option}</option>)}
      </select>
    </label>
  );
}

export default function Dashboard() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [runs, setRuns] = useState<Record<string, Run>>({});
  const [baselineId, setBaselineId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [layers, setLayers] = useState<{ baseline: Layer[]; candidate: Layer[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/data/catalog.json")
      .then((response) => {
        if (!response.ok) throw new Error("The regression catalog could not be loaded.");
        return response.json() as Promise<Catalog>;
      })
      .then(async (nextCatalog) => {
        const loaded = await Promise.all(
          nextCatalog.runs.map(async (entry) => {
            const response = await fetch(`/data/${entry.path}`);
            if (!response.ok) throw new Error(`Run ${entry.run_id} could not be loaded.`);
            return [entry.run_id, await response.json() as Run] as const;
          }),
        );
        setCatalog(nextCatalog);
        setRuns(Object.fromEntries(loaded));
        const ordered = [...nextCatalog.runs].sort((a, b) =>
          (a.generated_at || "").localeCompare(b.generated_at || ""),
        );
        setCandidateId(ordered.at(-1)?.run_id || "");
        setBaselineId(ordered.at(-2)?.run_id || ordered.at(-1)?.run_id || "");
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const baseline = runs[baselineId];
  const candidate = runs[candidateId];
  const pairs = useMemo<Pair[]>(() => {
    const map = new Map<string, Pair>();
    for (const item of baseline?.cases || []) map.set(item.identity_key, { key: item.identity_key, baseline: item });
    for (const item of candidate?.cases || []) {
      const pair = map.get(item.identity_key) || { key: item.identity_key };
      pair.candidate = item;
      map.set(item.identity_key, pair);
    }
    return [...map.values()].sort((a, b) => {
      const left = a.candidate?.identity || a.baseline?.identity;
      const right = b.candidate?.identity || b.baseline?.identity;
      return `${left?.board}-${left?.model_id}-${left?.toolchain}`.localeCompare(
        `${right?.board}-${right?.model_id}-${right?.toolchain}`,
      );
    });
  }, [baseline, candidate]);

  const dimensions = useMemo(() => {
    const identities = pairs.map((pair) => pair.candidate?.identity || pair.baseline?.identity).filter(Boolean) as Identity[];
    const unique = (pick: (item: Identity) => string) => [...new Set(identities.map(pick))].sort();
    return {
      model: unique((item) => item.model_id),
      board: unique((item) => item.board),
      engine: unique((item) => item.engine),
      toolchain: unique((item) => item.toolchain),
    };
  }, [pairs]);

  const visiblePairs = pairs.filter((pair) => {
    const identity = pair.candidate?.identity || pair.baseline?.identity;
    if (!identity) return false;
    return (filters.model === "all" || filters.model === identity.model_id)
      && (filters.board === "all" || filters.board === identity.board)
      && (filters.engine === "all" || filters.engine === identity.engine)
      && (filters.toolchain === "all" || filters.toolchain === identity.toolchain);
  });

  const selected = pairs.find((pair) => pair.key === selectedKey) || null;
  useEffect(() => {
    if (!selected) {
      setLayers(null);
      return;
    }
    const load = async (item?: Case) => {
      if (!item?.layer_path) return [];
      const response = await fetch(`/data/${item.layer_path}`);
      if (!response.ok) return [];
      return ((await response.json()) as LayerDocument).layers;
    };
    Promise.all([load(selected.baseline), load(selected.candidate)])
      .then(([baselineLayers, candidateLayers]) => setLayers({ baseline: baselineLayers, candidate: candidateLayers }));
  }, [selected, baselineId, candidateId]);

  const compared = visiblePairs.filter((pair) => pair.baseline && pair.candidate).length;
  const changes = visiblePairs
    .map((pair) => deltaPct(pair.baseline?.metrics.total_cycles, pair.candidate?.metrics.total_cycles))
    .filter((value): value is number => value != null);
  const largestRegression = changes.length ? Math.max(...changes) : null;
  const largestImprovement = changes.length ? Math.min(...changes) : null;

  if (error) return <main className="center-state"><h1>Regression data unavailable</h1><p>{error}</p></main>;
  if (!catalog || !baseline || !candidate) return <main className="center-state"><div className="loader" /><p>Loading hardware history…</p></main>;

  return (
    <main>
      <header className="topbar">
        <div className="brand-mark">h<span>PX</span></div>
        <div>
          <p className="eyebrow">heliaPROFILER</p>
          <h1>Regression Lab</h1>
        </div>
        <div className="suite-health">
          <span className="status-dot" />
          <div><strong>{candidate.summary.pass}/{candidate.summary.total} passing</strong><small>Latest complete suite</small></div>
        </div>
      </header>

      <section className="hero-grid">
        <div className="intro-panel">
          <p className="eyebrow">Hardware performance history</p>
          <h2>See the change.<br /><em>Find the layer.</em></h2>
          <p>Compare every model across Apollo hardware, engines, compilers, and memory plans—then trace a regression to the operator that caused it.</p>
        </div>
        <div className="run-panel">
          <label><span>Baseline</span><select value={baselineId} onChange={(event) => { setBaselineId(event.target.value); setSelectedKey(null); }}>
            {catalog.runs.map((run) => <option value={run.run_id} key={run.run_id}>{formatDate(run.generated_at)} · {run.repo.sha?.slice(0, 8)}</option>)}
          </select></label>
          <div className="compare-arrow">→</div>
          <label><span>Candidate</span><select value={candidateId} onChange={(event) => { setCandidateId(event.target.value); setSelectedKey(null); }}>
            {catalog.runs.map((run) => <option value={run.run_id} key={run.run_id}>{formatDate(run.generated_at)} · {run.repo.sha?.slice(0, 8)}</option>)}
          </select></label>
          <div className="run-meta"><span>{candidate.suite}</span><span>{candidate.repo.branch}</span><span>{candidate.cases.length} cases</span></div>
        </div>
      </section>

      <section className="kpi-grid">
        <article><p>Compared cases</p><strong>{compared}</strong><span>of {visiblePairs.length} visible</span></article>
        <article><p>Largest regression</p><strong className="bad">{largestRegression == null ? "—" : `+${largestRegression.toFixed(3)}%`}</strong><span>total cycles</span></article>
        <article><p>Largest improvement</p><strong className="good">{largestImprovement == null ? "—" : `${largestImprovement.toFixed(3)}%`}</strong><span>total cycles</span></article>
        <article><p>Repository</p><strong className="mono">{candidate.repo.sha?.slice(0, 8)}</strong><span>{formatDate(candidate.generated_at)}</span></article>
      </section>

      <section className="workspace">
        <div className="section-heading">
          <div><p className="eyebrow">Comparison matrix</p><h2>Suite results</h2></div>
          <button className="reset-button" onClick={() => setFilters(emptyFilters)}>Reset filters</button>
        </div>
        <div className="filters">
          {(Object.keys(emptyFilters) as (keyof Filters)[]).map((key) => (
            <FilterSelect key={key} label={key} value={filters[key]} options={dimensions[key]}
              onChange={(value) => setFilters((current) => ({ ...current, [key]: value }))} />
          ))}
        </div>
        <div className="table-shell">
          <table>
            <thead><tr><th>Model</th><th>Board</th><th>Engine</th><th>Toolchain</th><th>Baseline cycles</th><th>Candidate cycles</th><th>Change</th><th /></tr></thead>
            <tbody>
              {visiblePairs.map((pair) => {
                const identity = pair.candidate?.identity || pair.baseline!.identity;
                const change = deltaPct(pair.baseline?.metrics.total_cycles, pair.candidate?.metrics.total_cycles);
                return (
                  <tr key={pair.key} className={selectedKey === pair.key ? "selected-row" : ""}>
                    <td><strong>{identity.model_id.toUpperCase()}</strong></td><td>{identity.board}</td><td>{identity.engine}</td><td><span className="toolchain-pill">{identity.toolchain}</span></td>
                    <td className="mono">{formatNumber(pair.baseline?.metrics.total_cycles)}</td><td className="mono">{formatNumber(pair.candidate?.metrics.total_cycles)}</td><td><Delta value={change} /></td>
                    <td><button className="inspect-button" onClick={() => setSelectedKey(pair.key)}>Inspect layers</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {selected && <LayerInspector pair={selected} layers={layers} onClose={() => setSelectedKey(null)} />}
    </main>
  );
}

function LayerInspector({ pair, layers, onClose }: { pair: Pair; layers: { baseline: Layer[]; candidate: Layer[] } | null; onClose: () => void }) {
  const identity = pair.candidate?.identity || pair.baseline!.identity;
  const aligned = layers && layers.baseline.length === layers.candidate.length
    && layers.baseline.every((layer, index) => layer.op === layers.candidate[index]?.op);
  const rows = aligned ? layers!.baseline.map((baseline, index) => {
    const candidate = layers!.candidate[index];
    return { baseline, candidate, delta: (candidate.cycles || 0) - (baseline.cycles || 0), pct: deltaPct(baseline.cycles, candidate.cycles) };
  }).sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta)) : [];
  const maxCycles = Math.max(1, ...rows.flatMap((row) => [row.baseline.cycles || 0, row.candidate.cycles || 0]));
  return (
    <section className="drawer" aria-label="Layer comparison">
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="drawer-panel">
        <div className="drawer-header"><div><p className="eyebrow">Layer explorer</p><h2>{identity.model_id.toUpperCase()} · {identity.board}</h2><p>{identity.engine} / {identity.toolchain}</p></div><button onClick={onClose} aria-label="Close layer explorer">×</button></div>
        {!layers && <div className="drawer-state">Loading layer measurements…</div>}
        {layers && !aligned && <div className="drawer-state"><strong>Layer sequences differ.</strong><p>Run-level metrics remain comparable, but positional layer alignment would be misleading.</p></div>}
        {aligned && <>
          <div className="layer-summary"><div><span>Total cycle change</span><Delta value={deltaPct(pair.baseline?.metrics.total_cycles, pair.candidate?.metrics.total_cycles)} /></div><div><span>Aligned layers</span><strong>{rows.length}</strong></div></div>
          <div className="layer-list">
            {rows.map(({ baseline, candidate, delta, pct }) => (
              <article className="layer-row" key={`${baseline.index}-${baseline.op}`}>
                <div className="layer-title"><span>#{baseline.index}</span><strong>{baseline.op}</strong><Delta value={pct} /></div>
                <div className="bar-pair"><div><span style={{ width: `${((baseline.cycles || 0) / maxCycles) * 100}%` }} /><em>{formatNumber(baseline.cycles)}</em></div><div className="candidate-bar"><span style={{ width: `${((candidate.cycles || 0) / maxCycles) * 100}%` }} /><em>{formatNumber(candidate.cycles)}</em></div></div>
                <small>{delta > 0 ? "+" : ""}{formatNumber(delta)} cycles · {(candidate.cycles_pct || 0).toFixed(1)}% of candidate</small>
              </article>
            ))}
          </div>
        </>}
      </div>
    </section>
  );
}
