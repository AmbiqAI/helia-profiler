// Does cli.json fit the helia-ui reference model? Build the best-effort
// ReferenceModel, push it through the shipped renderer, and print every
// field of cli.json that had nowhere to go.
import { readFileSync } from 'node:fs';
import { renderReference } from '../node_modules/@ambiqai/helia-ui/scripts/lib/reference-render.mjs';

const cli = JSON.parse(readFileSync(new URL('../../data/cli.json', import.meta.url)));

const unhoused = new Map();
const note = (field, where) => {
  if (!unhoused.has(field)) unhoused.set(field, new Set());
  unhoused.get(field).add(where);
};

const typeLabel = (p) =>
  p.type.choices ? `{${p.type.choices.join('|')}}` : (p.python_type ?? p.type.name);

function toParam(p, where) {
  if (p.panel) note('options[].panel (rich_help_panel)', where);
  if (p.required) note('options[].required', where);
  if (p.aliases.length) note('options[].aliases (short flags)', where);
  if (p.is_flag || p.secondary_opts.length) note('options[].is_flag / secondary_opts', where);
  if (p.multiple || p.count || p.nargs !== 1) note('options[].multiple / count / nargs', where);
  note('kind (argument vs option)', where);
  note('envvar', where);
  return {
    name: p.aliases.length ? `${p.declaration}, ${p.aliases.join(', ')}` : p.declaration,
    type: typeLabel(p),
    default: p.default === null ? '' : String(p.default),
    description: p.help ?? '',
  };
}

function toSymbol(node) {
  const id = ['hpx', ...node.path].join('.');
  const where = ['hpx', ...node.path].join(' ');
  if (node.epilog) note('epilog', where);
  if (node.panels.length) note('panels (option group order)', where);
  return {
    id,
    name: ['hpx', ...node.path].join(' '),
    // No 'command' or 'group' in SymbolKind; 'function'/'namespace' is the
    // closest lie the model allows.
    kind: node.commands ? 'namespace' : 'function',
    // No 'bash'/'cli' in ReferenceLanguage either.
    language: 'python',
    signature: node.usage,
    summary: node.short_help ?? '',
    description: [node.help, node.epilog && '```\n' + node.epilog + '\n```']
      .filter(Boolean)
      .join('\n\n'),
    params: [...node.arguments, ...node.options].map((p) => toParam(p, where)),
    returns: [],
    raises: [],
    examples: [],
    source: node.source
      ? { path: node.source.path, line: node.source.line }
      : { path: 'src/helia_profiler/cli/app.py', line: 1 },
    members: (node.commands ?? []).map(toSymbol),
  };
}

const model = {
  name: 'hpx CLI',
  language: 'python',
  generatedFrom: { tool: 'tools/docs/extract_cli.py', version: cli.generatedFrom.helia_profiler },
  modules: [
    {
      path: 'cli',
      name: 'hpx',
      summary: cli.root.short_help ?? '',
      description: cli.root.help ?? '',
      symbols: (cli.root.commands ?? []).map(toSymbol),
      submodules: [],
    },
  ],
};

let status = 'rendered';
let pages = 0;
try {
  const out = renderReference(model, { base: '/reference/cli' });
  pages = out.pages?.length ?? 0;
} catch (error) {
  status = `ReferenceRenderError: ${error.message}`;
}

console.log(`renderReference: ${status} (${pages} page(s))`);
console.log(`symbols: ${model.modules[0].symbols.length} top level`);
console.log('\ncli.json fields with no home in the reference model:');
for (const [f, where] of [...unhoused].sort()) {
  const list = [...where];
  console.log(`  - ${f}  (${list.length} command(s), e.g. ${list.slice(0, 3).join(', ')})`);
}
