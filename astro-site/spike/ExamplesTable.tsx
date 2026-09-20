import { useMemo, useState } from 'react';

interface Example {
  slug: string;
  title: string;
  engine: string;
  toolchain: string;
  board: string;
  measurement: string;
  config: string;
  status: string;
}

/* The island exists to measure what one interactive component costs, so it does
 * the one thing a static table cannot: filter without a round trip. */
export default function ExamplesTable({ examples }: { examples: Example[] }) {
  const [engine, setEngine] = useState('all');
  const [query, setQuery] = useState('');

  const engines = useMemo(
    () => ['all', ...new Set(examples.map((item) => item.engine))],
    [examples],
  );

  const rows = examples.filter((item) => {
    const matchesEngine = engine === 'all' || item.engine === engine;
    const haystack = `${item.title} ${item.board} ${item.measurement}`;
    return matchesEngine && haystack.toLowerCase().includes(query.toLowerCase());
  });

  return (
    <div className="not-content flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <input
          className="rounded border border-gray-400 px-2 py-1 text-sm"
          placeholder="Filter examples"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        {engines.map((name) => (
          <button
            key={name}
            type="button"
            className={
              name === engine
                ? 'rounded bg-gray-800 px-2 py-1 text-sm text-white'
                : 'rounded border border-gray-400 px-2 py-1 text-sm'
            }
            onClick={() => setEngine(name)}
          >
            {name}
          </button>
        ))}
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr>
            <th className="text-left">Example</th>
            <th className="text-left">Engine</th>
            <th className="text-left">Toolchain</th>
            <th className="text-left">Board</th>
            <th className="text-left">Measurement</th>
            <th className="text-left">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((item) => (
            <tr key={item.slug}>
              <td>{item.title}</td>
              <td>{item.engine}</td>
              <td>{item.toolchain}</td>
              <td>{item.board}</td>
              <td>{item.measurement}</td>
              <td>{item.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>
        {rows.length} of {examples.length} examples
      </p>
    </div>
  );
}
