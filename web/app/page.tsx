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
type CompareMode = "previous" | "toolchain";
type SelectedCase = { modelId: string; identityKey: string };

function formatNumber(value: number | null | undefined): string {
  if (value == null) return "—";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
}

function formatDate(value: string | null): string {
  if (!value) return "Unknown date";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
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
  return <span className={`delta ${kind}`}>{value > 0 ? "+" : ""}{value.toFixed(3)}%</span>;
}

function unique(values: string[]) {
  return [...new Set(values)].sort();
}

function sameConfiguration(left: Case, right: Case, ignoreToolchain = false) {
  return left.identity.model_id === right.identity.model_id
    && left.identity.board === right.identity.board
    && left.identity.engine === right.identity.engine
    && (ignoreToolchain || left.identity.toolchain === right.identity.toolchain)
    && left.identity.transport === right.identity.transport
    && JSON.stringify(left.identity.requested_memory) === JSON.stringify(right.identity.requested_memory)
    && JSON.stringify(left.identity.requested_power) === JSON.stringify(right.identity.requested_power)
    && left.identity.attempt === right.identity.attempt;
}

export default function Dashboard() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [runs, setRuns] = useState<Record<string, Run>>({});
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedCase, setSelectedCase] = useState<SelectedCase | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/data/catalog.json")
      .then((response) => {
        if (!response.ok) throw new Error("The regression catalog could not be loaded.");
        return response.json() as Promise<Catalog>;
      })
      .then(async (nextCatalog) => {
        const loaded = await Promise.all(nextCatalog.runs.map(async (entry) => {
          const response = await fetch(`/data/${entry.path}`);
          if (!response.ok) throw new Error(`Run ${entry.run_id} could not be loaded.`);
          return [entry.run_id, await response.json() as Run] as const;
        }));
        setCatalog(nextCatalog);
        setRuns(Object.fromEntries(loaded));
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const orderedRuns = useMemo(() => [...(catalog?.runs || [])].sort((a, b) =>
    (a.generated_at || "").localeCompare(b.generated_at || ""),
  ), [catalog]);
  const selectedIndex = selectedRunId
    ? orderedRuns.findIndex((run) => run.run_id === selectedRunId)
    : orderedRuns.length - 1;
  const selectedSummary = selectedIndex >= 0 ? orderedRuns[selectedIndex] : undefined;
  const latest = selectedSummary ? runs[selectedSummary.run_id] : undefined;
  const previousSummary = selectedIndex > 0 ? orderedRuns[selectedIndex - 1] : undefined;
  const previous = previousSummary ? runs[previousSummary.run_id] : undefined;
  const modelIds = unique((latest?.cases || []).map((item) => item.identity.model_id));

  if (error) return <main className="center-state"><h1>Regression data unavailable</h1><p>{error}</p></main>;
  if (!catalog || !latest) return <main className="center-state"><div className="loader" /><p>Loading hardware history…</p></main>;

  return (
    <main>
      {!selectedCase ? (
        <LatestModels
          latest={latest}
          previous={previous}
          modelIds={modelIds}
          orderedRuns={orderedRuns}
          selectedRunId={selectedSummary.run_id}
          onRunSelect={(runId) => { setSelectedRunId(runId); setSelectedCase(null); }}
          onSelect={setSelectedCase}
        />
      ) : (
        <ModelComparison
          modelId={selectedCase.modelId}
          initialCaseKey={selectedCase.identityKey}
          latest={latest}
          orderedRuns={orderedRuns}
          runs={runs}
          onBack={() => setSelectedCase(null)}
        />
      )}
    </main>
  );
}

function LatestModels({
  latest,
  previous,
  modelIds,
  orderedRuns,
  selectedRunId,
  onRunSelect,
  onSelect,
}: {
  latest: Run;
  previous?: Run;
  modelIds: string[];
  orderedRuns: RunSummary[];
  selectedRunId: string;
  onRunSelect: (runId: string) => void;
  onSelect: (selection: SelectedCase) => void;
}) {
  const [modelQuery, setModelQuery] = useState("");
  const [boardFilter, setBoardFilter] = useState("all");
  const [engineFilter, setEngineFilter] = useState("all");
  const [toolchainFilter, setToolchainFilter] = useState("all");
  const [maxCycles, setMaxCycles] = useState("");
  const [chartModel, setChartModel] = useState(modelIds[0] || "");
  const [chartBoard, setChartBoard] = useState("all");
  const [chartMetric, setChartMetric] = useState("total_cycles");
  const effectiveChartModel = modelIds.includes(chartModel) ? chartModel : modelIds[0] || "";
  const chartBoards = unique(latest.cases.map((item) => item.identity.board));
  const effectiveChartBoard = chartBoard === "all" || chartBoards.includes(chartBoard) ? chartBoard : "all";
  const rows = latest.cases.filter((item) => {
    const cycleLimit = maxCycles ? Number(maxCycles.replaceAll(",", "")) : null;
    return item.identity.model_id.toLowerCase().includes(modelQuery.toLowerCase())
      && (boardFilter === "all" || item.identity.board === boardFilter)
      && (engineFilter === "all" || item.identity.engine === engineFilter)
      && (toolchainFilter === "all" || item.identity.toolchain === toolchainFilter)
      && (cycleLimit == null || (item.metrics.total_cycles ?? Infinity) <= cycleLimit);
  });
  const clearFilters = () => {
    setModelQuery("");
    setBoardFilter("all");
    setEngineFilter("all");
    setToolchainFilter("all");
    setMaxCycles("");
  };

  return (
    <>
      <section className="page-intro">
        <div><p className="eyebrow">Performance dashboard</p><h2>Validation runs</h2></div>
      </section>

      <section className="run-history-section">
        <div className="table-shell run-table-shell">
          <table className="run-table">
            <thead><tr><th>Run</th><th>Suite</th><th>Commit</th><th>Cases</th><th>Status</th><th /></tr></thead>
            <tbody>{[...orderedRuns].reverse().map((run, index) => <tr key={run.run_id} className={run.run_id === selectedRunId ? "selected-run" : ""}>
              <td><strong>{formatDate(run.generated_at)}</strong>{index === 0 && <span className="latest-pill">Latest</span>}</td>
              <td>{run.suite || "—"}</td>
              <td className="mono">{run.repo.sha?.slice(0, 8) || "—"}</td>
              <td className="mono">{run.summary.pass}/{run.summary.total}</td>
              <td><span className={`run-status ${run.summary.fail ? "failed" : "passing"}`}>{run.summary.fail ? `${run.summary.fail} failed` : "Passing"}</span></td>
              <td><button className="select-run-button" onClick={() => onRunSelect(run.run_id)}>{run.run_id === selectedRunId ? "Selected" : "View run"}</button></td>
            </tr>)}</tbody>
          </table>
        </div>
      </section>

      <section className="summary-strip">
        <div><span>Models</span><strong>{modelIds.length}</strong><small>in this run</small></div>
        <div><span>Boards</span><strong>{unique(latest.cases.map((item) => item.identity.board)).length}</strong><small>hardware targets</small></div>
        <div><span>Toolchains</span><strong>{unique(latest.cases.map((item) => item.identity.toolchain)).length}</strong><small>compiler variants</small></div>
        <div><span>Status</span><strong className={latest.summary.fail ? "bad" : "good"}>{latest.summary.fail ? `${latest.summary.fail} failed` : "Passing"}</strong><small>{latest.summary.pass} of {latest.summary.total} cases</small></div>
      </section>

      <ConfigurationChart
        cases={latest.cases}
        modelIds={modelIds}
        selectedModel={effectiveChartModel}
        selectedBoard={effectiveChartBoard}
        metric={chartMetric}
        onModelChange={setChartModel}
        onBoardChange={setChartBoard}
        onMetricChange={setChartMetric}
        onSelect={onSelect}
      />

      <section className="model-section">
        <div className="section-heading"><div><p className="eyebrow">Selected run</p><h2>Configurations</h2></div><span>{rows.length} of {latest.cases.length} configurations</span></div>
        <div className="table-shell latest-table-shell">
          <table className="results-table">
            <thead>
              <tr><th>Model</th><th>Board</th><th>Engine</th><th>Toolchain</th><th>Total cycles</th><th>Latency</th><th>Arena</th><th>Change</th></tr>
              <tr className="column-filters">
                <th><input aria-label="Search models" placeholder="Search model…" value={modelQuery} onChange={(event) => setModelQuery(event.target.value)} /></th>
                <th><select aria-label="Filter board" value={boardFilter} onChange={(event) => setBoardFilter(event.target.value)}><option value="all">All boards</option>{unique(latest.cases.map((item) => item.identity.board)).map((item) => <option key={item}>{item}</option>)}</select></th>
                <th><select aria-label="Filter engine" value={engineFilter} onChange={(event) => setEngineFilter(event.target.value)}><option value="all">All engines</option>{unique(latest.cases.map((item) => item.identity.engine)).map((item) => <option key={item}>{item}</option>)}</select></th>
                <th><select aria-label="Filter toolchain" value={toolchainFilter} onChange={(event) => setToolchainFilter(event.target.value)}><option value="all">All toolchains</option>{unique(latest.cases.map((item) => item.identity.toolchain)).map((item) => <option key={item}>{item}</option>)}</select></th>
                <th><input aria-label="Maximum cycles" inputMode="numeric" placeholder="Maximum…" value={maxCycles} onChange={(event) => setMaxCycles(event.target.value)} /></th>
                <th colSpan={2}><span className="filter-hint">Filters apply to the selected run</span></th>
                <th><button className="clear-filters" onClick={clearFilters}>Clear</button></th>
              </tr>
            </thead>
            <tbody>{rows.map((item) => {
              const old = previous?.cases.find((candidate) => candidate.identity_key === item.identity_key);
              return <tr key={item.identity_key}>
                <td><button className="model-link" onClick={() => onSelect({ modelId: item.identity.model_id, identityKey: item.identity_key })}>{item.identity.model_id.toUpperCase()}</button></td>
                <td>{item.identity.board}</td>
                <td>{item.identity.engine}</td>
                <td><span className="toolchain-pill">{item.identity.toolchain}</span></td>
                <td className="mono metric-cell">{formatNumber(item.metrics.total_cycles)}</td>
                <td className="mono">{formatNumber(item.metrics.profiled_infer_avg_us)} µs</td>
                <td className="mono">{formatNumber(item.metrics.arena_allocated_bytes)}</td>
                <td><Delta value={deltaPct(old?.metrics.total_cycles, item.metrics.total_cycles)} /></td>
              </tr>;
            })}</tbody>
          </table>
          {!rows.length && <div className="no-results">No configurations match these filters. <button onClick={clearFilters}>Clear filters</button></div>}
        </div>
      </section>
    </>
  );
}

function ConfigurationChart({
  cases,
  modelIds,
  selectedModel,
  selectedBoard,
  metric,
  onModelChange,
  onBoardChange,
  onMetricChange,
  onSelect,
}: {
  cases: Case[];
  modelIds: string[];
  selectedModel: string;
  selectedBoard: string;
  metric: string;
  onModelChange: (model: string) => void;
  onBoardChange: (board: string) => void;
  onMetricChange: (metric: string) => void;
  onSelect: (selection: SelectedCase) => void;
}) {
  const metricOptions = [
    { value: "total_cycles", label: "Cycles", unit: "cycles" },
    { value: "clean_infer_avg_us", label: "Inference latency", unit: "µs" },
    { value: "profiled_infer_avg_us", label: "Instrumented latency", unit: "µs" },
  ];
  const boards = unique(cases.map((item) => item.identity.board));
  const metricInfo = metricOptions.find((item) => item.value === metric) || metricOptions[0];
  const ranked = cases
    .filter((item) => item.identity.model_id === selectedModel
      && (selectedBoard === "all" || item.identity.board === selectedBoard)
      && item.metrics[metric] != null)
    .map((item) => ({ item, value: item.metrics[metric] as number }))
    .sort((left, right) => left.value - right.value);
  const best = ranked[0]?.value || 1;

  return <section className="configuration-chart-section">
    <div className="chart-section-heading">
      <div><p className="eyebrow">Configuration comparison</p><h2>Best performance</h2></div>
      <div className="chart-controls">
        <label><span>Model</span><select value={selectedModel} onChange={(event) => onModelChange(event.target.value)}>{modelIds.map((model) => <option key={model} value={model}>{model.toUpperCase()}</option>)}</select></label>
        <label><span>Board</span><select aria-label="Compare board" value={selectedBoard} onChange={(event) => onBoardChange(event.target.value)}><option value="all">All boards</option>{boards.map((board) => <option key={board} value={board}>{board}</option>)}</select></label>
        <label><span>Metric</span><select value={metric} onChange={(event) => onMetricChange(event.target.value)}>{metricOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
      </div>
    </div>
    <div className="configuration-ranking">
      <div className="ranking-header"><span>Configuration</span><span>Relative performance</span><span>{metricInfo.label}</span></div>
      {ranked.map(({ item, value }, index) => {
        const score = Math.min(100, (best / value) * 100);
        const slower = ((value - best) / best) * 100;
        return <button className="configuration-rank-row" key={item.identity_key} onClick={() => onSelect({ modelId: item.identity.model_id, identityKey: item.identity_key })}>
          <span className="rank-number">{index + 1}</span>
          <span className="rank-identity"><strong>{item.identity.board}</strong><small>{item.identity.engine} · {item.identity.toolchain}</small></span>
          <span className="performance-track"><i style={{ width: `${score}%` }} />{index === 0 ? <em>Best</em> : <em>+{slower.toFixed(1)}%</em>}</span>
          <span className="rank-value"><strong>{formatNumber(value)}</strong><small>{metricInfo.unit}</small></span>
        </button>;
      })}
    </div>
  </section>;
}

function ModelComparison({
  modelId,
  initialCaseKey,
  latest,
  orderedRuns,
  runs,
  onBack,
}: {
  modelId: string;
  initialCaseKey: string;
  latest: Run;
  orderedRuns: RunSummary[];
  runs: Record<string, Run>;
  onBack: () => void;
}) {
  const modelCases = latest.cases.filter((item) => item.identity.model_id === modelId);
  const initialCase = modelCases.find((item) => item.identity_key === initialCaseKey) || modelCases[0];
  const boards = unique(modelCases.map((item) => item.identity.board));
  const engines = unique(modelCases.map((item) => item.identity.engine));
  const [board, setBoard] = useState(initialCase?.identity.board || boards[0] || "");
  const [engine, setEngine] = useState(initialCase?.identity.engine || engines[0] || "");
  const toolchains = unique(modelCases.filter((item) => item.identity.board === board && item.identity.engine === engine).map((item) => item.identity.toolchain));
  const [toolchain, setToolchain] = useState(initialCase?.identity.toolchain || toolchains[0] || "");
  const [mode, setMode] = useState<CompareMode>("previous");
  const historical = orderedRuns.filter((run) =>
    run.run_id !== latest.run_id && (run.generated_at || "") < (latest.generated_at || ""),
  ).reverse();
  const [referenceRunId, setReferenceRunId] = useState(historical[0]?.run_id || "");
  const alternateToolchains = toolchains.filter((item) => item !== toolchain);
  const [referenceToolchain, setReferenceToolchain] = useState(alternateToolchains[0] || "");

  useEffect(() => {
    if (!toolchains.includes(toolchain)) setToolchain(toolchains[0] || "");
  }, [toolchains, toolchain]);
  useEffect(() => {
    const alternatives = toolchains.filter((item) => item !== toolchain);
    if (!alternatives.includes(referenceToolchain)) setReferenceToolchain(alternatives[0] || "");
  }, [toolchains, toolchain, referenceToolchain]);

  const candidate = modelCases.find((item) => item.identity.board === board && item.identity.engine === engine && item.identity.toolchain === toolchain);
  const reference = candidate && mode === "previous"
    ? runs[referenceRunId]?.cases.find((item) => item.identity_key === candidate.identity_key)
    : candidate && latest.cases.find((item) => sameConfiguration(candidate, item, true) && item.identity.toolchain === referenceToolchain);
  const referenceLabel = mode === "previous"
    ? formatDate(runs[referenceRunId]?.generated_at || null)
    : referenceToolchain;
  const history = candidate ? orderedRuns.filter((summary) =>
    (summary.generated_at || "") <= (latest.generated_at || ""),
  ).flatMap((summary) => {
    const historicalCase = runs[summary.run_id]?.cases.find((item) => item.identity_key === candidate.identity_key);
    const cycles = historicalCase?.metrics.total_cycles;
    return cycles == null ? [] : [{ runId: summary.run_id, date: summary.generated_at, cycles }];
  }) : [];
  const [layers, setLayers] = useState<{ reference: Layer[]; candidate: Layer[] } | null>(null);

  useEffect(() => {
    const load = async (item?: Case) => {
      if (!item?.layer_path) return [];
      const response = await fetch(`/data/${item.layer_path}`);
      return response.ok ? ((await response.json()) as LayerDocument).layers : [];
    };
    setLayers(null);
    Promise.all([load(reference), load(candidate)]).then(([referenceLayers, candidateLayers]) => setLayers({ reference: referenceLayers, candidate: candidateLayers }));
  }, [reference, candidate]);

  return (
    <section className="model-detail">
      <button className="back-button" onClick={onBack}>← All models</button>
      <div className="detail-heading"><div><p className="eyebrow">Model analysis</p><h2>{modelId.toUpperCase()}</h2><p>Choose a configuration, then compare it with an earlier run or another toolchain.</p></div><div className="latest-badge"><span>Selected run</span><strong>{formatDate(latest.generated_at)}</strong></div></div>

      <div className="comparison-builder">
        <label><span>Board</span><select value={board} onChange={(event) => setBoard(event.target.value)}>{boards.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label><span>Engine</span><select value={engine} onChange={(event) => setEngine(event.target.value)}>{engines.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label><span>Toolchain</span><select value={toolchain} onChange={(event) => setToolchain(event.target.value)}>{toolchains.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label><span>Compare against</span><select value={mode} onChange={(event) => setMode(event.target.value as CompareMode)}><option value="previous">Previous validation run</option><option value="toolchain">Different toolchain</option></select></label>
        {mode === "previous" ? <label><span>Reference run</span><select value={referenceRunId} onChange={(event) => setReferenceRunId(event.target.value)}>{historical.map((run) => <option value={run.run_id} key={run.run_id}>{formatDate(run.generated_at)} · {run.repo.sha?.slice(0, 8)}</option>)}</select></label>
          : <label><span>Reference toolchain</span><select value={referenceToolchain} onChange={(event) => setReferenceToolchain(event.target.value)}>{alternateToolchains.map((item) => <option key={item}>{item}</option>)}</select></label>}
      </div>

      <CycleHistory points={history} />

      {candidate && reference ? <ComparisonResults candidate={candidate} reference={reference} referenceLabel={referenceLabel} layers={layers} />
        : <div className="empty-comparison"><strong>No matching reference result</strong><p>Try another run or configuration.</p></div>}
    </section>
  );
}

function CycleHistory({ points }: { points: { runId: string; date: string | null; cycles: number }[] }) {
  const width = 760;
  const height = 230;
  const plot = { left: 56, right: 24, top: 24, bottom: 42 };
  const values = points.map((point) => point.cycles);
  const minimum = values.length ? Math.min(...values) : 0;
  const maximum = values.length ? Math.max(...values) : 1;
  const spread = Math.max(maximum - minimum, maximum * 0.002, 1);
  const low = minimum - spread * 0.2;
  const high = maximum + spread * 0.2;
  const x = (index: number) => plot.left + (points.length <= 1 ? 0 : index * ((width - plot.left - plot.right) / (points.length - 1)));
  const y = (cycles: number) => plot.top + ((high - cycles) / (high - low)) * (height - plot.top - plot.bottom);
  const line = points.map((point, index) => `${x(index)},${y(point.cycles)}`).join(" ");
  return <section className="trend-panel">
    <div className="trend-heading"><div><p className="eyebrow">Performance history</p><h3>Cycles</h3></div><span>{points.length} validation runs</span></div>
    {points.length ? <div className="chart-wrap">
      <svg className="cycle-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Total cycles over validation runs">
        {[0, 0.5, 1].map((ratio) => {
          const value = high - ratio * (high - low);
          const lineY = plot.top + ratio * (height - plot.top - plot.bottom);
          return <g key={ratio}><line x1={plot.left} x2={width - plot.right} y1={lineY} y2={lineY} className="chart-gridline" /><text x={plot.left - 10} y={lineY + 4} textAnchor="end" className="chart-label">{formatNumber(value)}</text></g>;
        })}
        {points.length > 1 && <polyline points={line} className="chart-line" />}
        {points.map((point, index) => <g key={point.runId}><circle cx={x(index)} cy={y(point.cycles)} r="5" className="chart-point" /><text x={x(index)} y={height - 14} textAnchor="middle" className="chart-label">{point.date ? new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(new Date(point.date)) : "Run"}</text><title>{formatDate(point.date)}: {formatNumber(point.cycles)} cycles</title></g>)}
      </svg>
      <div className="trend-latest"><span>Selected</span><strong>{formatNumber(points.at(-1)?.cycles)}</strong><small>cycles</small></div>
    </div> : <div className="drawer-state">No historical cycle measurements are available.</div>}
  </section>;
}

function ComparisonResults({ candidate, reference, referenceLabel, layers }: { candidate: Case; reference: Case; referenceLabel: string; layers: { reference: Layer[]; candidate: Layer[] } | null }) {
  const metrics = [
    ["Total cycles", "total_cycles", "cycles"],
    ["Profiled latency", "profiled_infer_avg_us", "µs"],
    ["Clean latency", "clean_infer_avg_us", "µs"],
    ["Arena allocated", "arena_allocated_bytes", "bytes"],
  ] as const;
  const aligned = layers && layers.reference.length === layers.candidate.length && layers.reference.every((layer, index) => layer.op === layers.candidate[index]?.op);
  const layerRows = aligned ? layers!.reference.map((baseline, index) => {
    const next = layers!.candidate[index];
    return { baseline, next, delta: (next.cycles || 0) - (baseline.cycles || 0), pct: deltaPct(baseline.cycles, next.cycles) };
  }) : [];
  const maxCycles = Math.max(1, ...layerRows.flatMap((row) => [row.baseline.cycles || 0, row.next.cycles || 0]));
  return <>
    <div className="comparison-context"><div><span>Reference</span><strong>{referenceLabel}</strong></div><div className="context-arrow">→</div><div><span>Selected</span><strong>{candidate.identity.toolchain}</strong></div></div>
    <div className="metric-grid">{metrics.map(([label, key, unit]) => <article key={key}><span>{label}</span><strong>{formatNumber(candidate.metrics[key])}</strong><small>{unit}</small><Delta value={deltaPct(reference.metrics[key], candidate.metrics[key])} /></article>)}</div>
    <div className="layers-heading"><div><p className="eyebrow">Operator breakdown</p><h3>Layer summary</h3></div><span>{aligned ? `${layerRows.length} layers · execution order` : "Layer sequences differ"}</span></div>
    {!layers && <div className="drawer-state">Loading layer measurements…</div>}
    {layers && !aligned && <div className="drawer-state"><strong>Layer sequences differ.</strong><p>Run-level metrics remain comparable, but positional layer alignment would be misleading.</p></div>}
    {aligned && <div className="layer-table-card">
      <div className="layer-table-row layer-table-header"><span>#</span><span>Operation</span><span>Reference</span><span>Selected</span><span>Δ cycles</span><span>Change</span><span>Share</span></div>
      {layerRows.map(({ baseline, next, delta, pct }) => <div className="layer-table-row" key={`${baseline.index}-${baseline.op}`}>
        <span className="layer-index">{baseline.index}</span>
        <strong className="layer-operation">{baseline.op}</strong>
        <span className="mono layer-number">{formatNumber(baseline.cycles)}</span>
        <div className="compact-cycle"><span style={{ width: `${((next.cycles || 0) / maxCycles) * 100}%` }} /><strong>{formatNumber(next.cycles)}</strong></div>
        <span className={`mono layer-cycle-delta ${delta > 0 ? "bad" : delta < 0 ? "good" : ""}`}>{delta > 0 ? "+" : ""}{formatNumber(delta)}</span>
        <Delta value={pct} />
        <span className="mono layer-share">{(next.cycles_pct || 0).toFixed(1)}%</span>
      </div>)}
    </div>}
  </>;
}
