/*
 * The mapping from cli.json, schema.json and issues.json onto the reference
 * parts, and onto the Markdown rendition of the same page.
 *
 * It is local rather than helia-ui's `renderReference` because the shared
 * reference model cannot describe a command line without lying. It has no
 * `command` or `group` kind and no shell language, so a command is rendered as
 * a function in a `python` symbol with a `bash` signature; `RefParamsRow`
 * carries name, type, default and description and nothing else, so an option's
 * aliases, its panel and every JSON Schema constraint fold into those four
 * columns. `renderReference` also lays a whole model onto one page, and twelve
 * top-level entries on one page is not a reference anyone can read. Widening
 * the model is Phase 5 (helia-ui#74); this file is what the current model can
 * carry without misrepresenting the CLI.
 *
 * One module for both renditions on purpose. The HTML page and the Markdown
 * beside it have to agree about what is on the page, and the way to make two
 * renderers agree is to give them one set of rows.
 */

/** Options click never grouped into a rich help panel, rendered first. */
export const UNGROUPED = 'Options';

export const SOURCE_URL =
  'https://github.com/AmbiqAI/helia-profiler/blob/__DOCS_SOURCE_REF__/{path}#L{line}';

/** Longest meta description before search engines truncate it. */
const DESCRIPTION_LIMIT = 160;

export const commandLabel = (path) => ['hpx', ...path].join(' ');

/** The route a top-level entry is published at, without the site base. */
export const commandSlug = (name) => `reference/cli/${name}`;

export function sourceOf(node) {
  if (!node.source) return undefined;
  return {
    path: node.source.path,
    line: node.source.line,
    url: SOURCE_URL.replace('{path}', node.source.path).replace('{line}', node.source.line),
  };
}

/**
 * Option help as it can actually be rendered in a parameter table.
 *
 * Resolves the spike's open question about Markdown in this column: RefParams
 * writes the description cell as `<td>{row.description}</td>`, which Astro
 * escapes, so Markdown there reaches the reader as its own source text. There
 * is no `set:html` to opt into and adding one would put unescaped author text
 * in a table. The column is therefore plain text, and the backticks the help
 * strings use for literals are dropped rather than shown as characters. The
 * Markdown rendition keeps the help verbatim, where a code span is a code
 * span.
 */
export const plainHelp = (text) =>
  (text ?? '')
    .replaceAll('`', '')
    .replace(/\s+/g, ' ')
    .trim();

/** A Markdown table cell: the pipe is the delimiter and a newline ends the row. */
export const cell = (text) =>
  (text ?? '')
    .replace(/\s*\n\s*/g, ' ')
    .replaceAll('|', '\\|')
    .trim();

const defaultText = (value) => {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
};

/**
 * The name cell of a parameter row.
 *
 * The declaration leads, so a check can anchor on it; the short aliases follow
 * because `RefParamsRow` has no field for them.
 */
export const paramName = (param) =>
  param.aliases?.length ? `${param.declaration}, ${param.aliases.join(', ')}` : param.declaration;

export const paramType = (param) =>
  param.type?.choices?.length
    ? param.type.choices.join(' | ')
    : (param.python_type ?? param.type?.name ?? '');

/** What the four columns cannot say: the flags that are part of the contract. */
export function paramNotes(param) {
  const notes = [];
  if (param.required) notes.push('Required.');
  if (param.multiple) notes.push('Repeatable.');
  if (param.envvar?.length) notes.push(`Environment: ${param.envvar.join(', ')}.`);
  return notes;
}

export function paramRow(param) {
  return {
    name: paramName(param),
    type: paramType(param),
    default: defaultText(param.default),
    description: [plainHelp(param.help), ...paramNotes(param)].filter(Boolean).join(' '),
  };
}

/** Options by panel, the ungrouped ones first, then the authored captions. */
export function panelsOf(command) {
  const groups = new Map();
  for (const option of command.options) {
    const key = option.panel ?? UNGROUPED;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(option);
  }
  return [...groups.entries()].sort((a, b) =>
    a[0] === UNGROUPED ? -1 : b[0] === UNGROUPED ? 1 : a[0].localeCompare(b[0]),
  );
}

/**
 * Every command rendered on one page, the page's own entry first.
 *
 * A group and its leaves share a page; `hpx` itself does not, because its
 * twelve entries are twelve pages and repeating them on the overview would
 * put the whole reference on one route. Encoded here rather than in each
 * renderer so the page and its Markdown cannot disagree about it.
 */
export const commandsOn = (node) =>
  node.commands && node.path.length > 0 ? [node, ...node.commands] : [node];

export const isRoot = (node) => node.path.length === 0;

const firstSentence = (text) => {
  const flat = (text ?? '').replace(/\s+/g, ' ').trim();
  if (!flat) return '';
  const stop = /[.!?](\s|$)/.exec(flat);
  const sentence = stop ? flat.slice(0, stop.index + 1) : flat;
  return sentence.length > DESCRIPTION_LIMIT
    ? `${sentence.slice(0, DESCRIPTION_LIMIT - 1).trimEnd()}…`
    : sentence;
};

/**
 * The page's `description` frontmatter.
 *
 * helia-ui's discoverability integration fails the whole build, late, on a
 * page without one, and a command whose callback has no docstring and no
 * `help=` is a real case, so the fallback is a template rather than an empty
 * string.
 */
export function commandDescription(node) {
  const authored = firstSentence(plainHelp(node.short_help) || plainHelp(node.help));
  if (authored) return authored;
  const label = commandLabel(node.path);
  return node.kind === 'group'
    ? `The ${label} command group, generated from the hpx command line.`
    : `Options and arguments for ${label}, generated from the hpx command line.`;
}

/* --- configuration --------------------------------------------------- */

/*
 * RefParamsRow has name, type, default and description, so every JSON Schema
 * constraint has to be folded into the description column.
 */
const CONSTRAINTS = [
  ['minimum', 'min'],
  ['exclusiveMinimum', 'min (exclusive)'],
  ['maximum', 'max'],
  ['exclusiveMaximum', 'max (exclusive)'],
  ['minLength', 'min length'],
  ['maxLength', 'max length'],
  ['pattern', 'pattern'],
  ['minItems', 'min items'],
  ['maxItems', 'max items'],
];

export function typeLabel(prop) {
  if (!prop) return '';
  if (prop.$ref) return prop.$ref.split('/').pop();
  if (prop.anyOf) return prop.anyOf.map(typeLabel).filter(Boolean).join(' | ');
  if (prop.enum) return prop.enum.join(' | ');
  if (prop.type === 'array' && prop.items) return `array of ${typeLabel(prop.items)}`;
  return prop.type ?? '';
}

export function fieldRow(prop, field) {
  const notes = [];
  if (prop?.description) notes.push(plainHelp(prop.description));
  else if (prop?.title) notes.push(plainHelp(prop.title));
  for (const [key, label] of CONSTRAINTS) {
    if (prop && prop[key] !== undefined) notes.push(`${label} ${prop[key]}`);
  }
  if (field.required) notes.push('Required.');
  if (field.defaultFromFactory) notes.push('Default built per instance.');
  return {
    name: field.name,
    type: field.pythonType || typeLabel(prop),
    default: defaultText(field.default),
    description: notes.join(' '),
  };
}

/* Lookups by class name live here rather than inline in the component: the
 * imported schema is a JSON literal to TypeScript, and indexing it by a name
 * only known at runtime is an error there and ordinary JavaScript here. */
export const fieldCount = (schema, cls) => schema['x-hpx'].fieldIndex[cls].length;

export const classDescription = (schema, cls) => schema.$defs[cls]?.description ?? undefined;

export const fieldRows = (schema, cls) => {
  const props = schema.$defs[cls]?.properties ?? {};
  return schema['x-hpx'].fieldIndex[cls].map((field) => fieldRow(props[field.name], field));
};

/**
 * Every class the page renders, in reading order: the sections reachable from
 * the root by their dotted key, then the classes the walk cannot reach.
 *
 * `MonitorBoardPreset` is the value type of a lookup table rather than a
 * field, so nothing walking down from `ProfileConfig` finds it; dropping it
 * would publish a configuration reference missing a documented type.
 */
export function configurationEntries(schema) {
  const sections = Object.entries(schema['x-hpx'].sections).map(([key, cls]) => ({
    cls,
    key,
    name: key === '' ? 'hpx.yml (top level)' : key,
    signature: key === '' ? cls : `${key}:  # ${cls}`,
    reachable: true,
  }));
  const unreachable = schema.counts.unreachableFromRoot.map((cls) => ({
    cls,
    key: cls,
    name: cls,
    signature: cls,
    reachable: false,
  }));
  return [...sections, ...unreachable];
}

export const configurationDescription = (schema) =>
  `Every hpx.yml key: ${schema.counts.declaredFields} fields across ` +
  `${schema.counts.classes} configuration models, generated from the package.`;

/* --- issue codes ------------------------------------------------------ */

export const severityCell = (issue) =>
  issue.modeDependent
    ? `${issue.internalSeverity} (internal) / ${issue.externalSeverity} (external)`
    : issue.severity;

export const issuesDescription = (issues) =>
  `Every machine-readable diagnostic code hpx can emit: ${issues.counts.issues} run-validity ` +
  `codes and ${issues.counts.comparability} comparability codes, generated from the registry.`;

/* --- the Markdown rendition ------------------------------------------- */

/*
 * The agent-facing rendition of a page. Starlight writes a `<route>/index.md`
 * of its own next to every page, but it reduces the reference parts to their
 * prose and drops the parameter tables (helia-ui#122), so it cannot be what a
 * completeness check asserts against or what a reader is pointed at.
 */

const table = (headers, rows) => {
  if (rows.length === 0) return [];
  return [
    `| ${headers.join(' | ')} |`,
    `| ${headers.map(() => '---').join(' | ')} |`,
    ...rows.map((row) => `| ${row.map(cell).join(' | ')} |`),
    '',
  ];
};

/* The help is verbatim here, backticks and all: a Markdown code span is a code
 * span in this rendition, unlike the escaped table cell on the page. */
const paramTable = (params) =>
  table(
    ['Name', 'Type', 'Default', 'Description'],
    params.map((param) => {
      const row = paramRow(param);
      return [
        `\`${row.name}\``,
        row.type ? `\`${row.type}\`` : '',
        row.default,
        [param.help, ...paramNotes(param)].filter(Boolean).join(' '),
      ];
    }),
  );

export function renderCommandMarkdown(node, generatedFrom) {
  const lines = [`# ${commandLabel(node.path)}`, ''];
  if (node.short_help) lines.push(node.short_help, '');
  for (const command of commandsOn(node)) {
    lines.push(`## ${commandLabel(command.path)}`, '');
    lines.push('```bash', command.usage, '```', '');
    if (command.help) lines.push(command.help, '');
    if (command.deprecated) lines.push('Deprecated.', '');
    if (command.source) {
      lines.push(`Defined in \`${command.source.path}\` line ${command.source.line}.`, '');
    }
    if (command.arguments.length > 0) {
      lines.push('### Arguments', '', ...paramTable(command.arguments));
    }
    for (const [panel, options] of panelsOf(command)) {
      lines.push(`### ${panel}`, '', ...paramTable(options));
    }
    if (command.epilog) {
      lines.push('### Examples', '', '```text', command.epilog.replace(/\s+$/, ''), '```', '');
    }
  }
  lines.push(
    `Generated from the \`src/helia_profiler\` tree \`${generatedFrom.sourceTree}\` with ` +
      `typer ${generatedFrom.typer} and click ${generatedFrom.click}.`,
    '',
  );
  return lines.join('\n');
}

export function renderConfigurationMarkdown(schema) {
  const lines = ['# Configuration reference', '', configurationDescription(schema), ''];
  for (const entry of configurationEntries(schema)) {
    lines.push(`## ${entry.cls}`, '');
    lines.push('```python', entry.signature, '```', '');
    const described = schema.$defs[entry.cls]?.description;
    if (described) lines.push(described, '');
    if (!entry.reachable) {
      lines.push('Not reachable from `ProfileConfig`; surfaced through a lookup table.', '');
    }
    lines.push(
      ...table(
        ['Key', 'Type', 'Default', 'Description'],
        fieldRows(schema, entry.cls).map((row) => [
          `\`${row.name}\``,
          row.type ? `\`${row.type}\`` : '',
          row.default,
          row.description,
        ]),
      ),
    );
  }
  for (const [name, members] of Object.entries(schema['x-hpx'].enums)) {
    lines.push(`## ${name}`, '', ...table(['Value'], members.map((member) => [`\`${member}\``])));
  }
  const { sourceTree, pydantic } = schema.generatedFrom;
  lines.push(
    `Generated from the \`src/helia_profiler\` tree \`${sourceTree}\` with pydantic ${pydantic}.`,
    '',
  );
  return lines.join('\n');
}

export function renderIssuesMarkdown(issues) {
  const lines = ['# Issue codes', '', issuesDescription(issues), ''];
  lines.push('## Run-validity issues', '');
  lines.push(
    ...table(
      ['Code', 'Severity', 'Description'],
      issues.issues.map((issue) => [
        `\`${issue.code}\``,
        severityCell(issue),
        issue.description,
      ]),
    ),
  );
  lines.push('## Comparability issues', '');
  lines.push(
    ...table(
      ['Code', 'Severity', 'Description'],
      issues.comparability.map((issue) => [
        `\`${issue.code}\``,
        issue.severity,
        issue.description,
      ]),
    ),
  );
  lines.push('## Parameterized families', '');
  for (const family of issues.families) {
    lines.push(`### ${family.pattern}`, '', `${family.severity}. ${family.description}`, '');
    lines.push(...table(['Code', 'Dimension'], family.codes.map((code, index) => [`\`${code}\``, `\`${family.dimensions[index]}\``])));
  }
  lines.push(
    `Generated from the \`src/helia_profiler\` tree \`${issues.generatedFrom.sourceTree}\`.`,
    '',
  );
  return lines.join('\n');
}
