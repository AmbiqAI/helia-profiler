# Spike #324: CLI and configuration reference extraction

Branch `324-spike-cli-config`, based on `origin/main` at `fd2d510`.

The sibling scaffold spike (#323) had not landed `astro-site/`, so the issue's
fallback applies: the two proof pages are rendered by a minimal local Astro
project at `spike/render/`, against a local copy of helia-ui
`0.1.0-alpha.14`. Everything below is measured in that project.

## What was built

| Path | Role |
| --- | --- |
| `tools/docs/extract_cli.py` | Walks the Typer app through click and writes `cli.json`. |
| `tools/docs/extract_schema.py` | Writes `schema.json` from the pydantic config models. |
| `tools/docs/source_audit.py` | Parses `cli/*.py` as AST, independently of the import path. |
| `tools/docs/check_reference.py` | `--write` / `--check` for both artifacts. |
| `tools/docs/_common.py` | JSON coercion, version stamping. |
| `spike/data/cli.json`, `spike/data/schema.json` | The committed artifacts. |
| `spike/render/` | Minimal Astro project that renders both pages. |

Home chosen: `tools/docs/`, not `src/helia_profiler/_docs/`. The generators are
build tooling for the docs site, not a shipped feature. Putting them in `src/`
would place them in the wheel and inside the public API surface that the
planned pyref pass documents. They still satisfy the clean-environment
constraint because they only import `helia_profiler`, so the package's own
runtime dependencies are enough. `tools/` already holds
`gen_config_reference.py` and friends, so this is the established home.

## Acceptance

### 1. `cli.json` produced, 14 leaf commands and 4 parent groups, counted against the registrations

```
$ uv run --isolated --no-dev python tools/docs/check_reference.py --write
wrote spike/data/cli.json and spike/data/schema.json
```

`cli.json` carries its own `counts` block:

```
leaf_commands       14   analyze, boards, cache info, cache purge, compare,
                         doctor, engines, ports list, power-on, probes list,
                         probes match, profile, target reset, validate
groups               5   hpx (root), cache, ports, probes, target
top_level_entries   12   8 commands + 4 sub-apps
epilogs              6   analyze, cache, compare, power-on, profile, validate
options            102
arguments            4
options_with_panel  42
options_with_envvar  0
```

The comparison is not against those numbers written down by hand. It is against
`tools/docs/source_audit.py`, which parses `cli/app.py`, `cli/inspect_app.py`
and `cli/validation_app.py` as AST and never imports them. It recognises both
registration forms this package uses, the `@app.command(...)` decorator and the
`app.command(...)(fn)` call, plus `add_typer` mounts. `check_reference.py
--check` runs that comparison on every invocation:

```
$ uv run --isolated --no-dev python tools/docs/check_reference.py --check
ok: 14 leaf commands, 5 groups, 102 options, 4 arguments; 15 config classes, 106 fields
exit=0
```

Zero paths differ between the AST audit and the click walk. The issue's count of
4 epilogs in `app.py` plus 2 in `validation_app.py` resolves at runtime to
`profile`, `analyze`, `compare` and the `cache` group from `app.py`, and
`power-on` and `validate` from `validation_app.py`. One of the six sits on a
group, not a leaf, which is why the extractor records epilogs on groups too.

### 2. Every option appears in `cli.json`, per command, against the source

Source AST versus `cli.json`, by name and not only by count:

```
command                src opt json opt src arg json arg  delta
hpx                          1        1       0        0  OK
hpx analyze                  5        5       1        1  OK
hpx boards                   0        0       0        0  OK
hpx cache                    0        0       0        0  OK
hpx cache info               0        0       0        0  OK
hpx cache purge              0        0       0        0  OK
hpx compare                  4        4       2        2  OK
hpx doctor                  10       10       0        0  OK
hpx engines                  0        0       0        0  OK
hpx ports                    0        0       0        0  OK
hpx ports list               2        2       0        0  OK
hpx power-on                 2        2       0        0  OK
hpx probes                   0        0       0        0  OK
hpx probes list              3        3       0        0  OK
hpx probes match             3        3       0        0  OK
hpx profile                 44       44       1        1  OK
hpx target                   0        0       0        0  OK
hpx target reset             3        3       0        0  OK
hpx validate                25       25       0        0  OK
mismatched commands: 0
TOTALS src 102 json 102
```

`delta` is a set comparison of parameter names, so a swap of two options at the
same count would still show. Nothing is missing and nothing is extra. The one
root option is `--version` from the `@app.callback`; `--help` is dropped
deliberately because it is click's own and appears on all 19 entries.

Cross-checked against the rendered page as well: all 45 declared parameters of
`hpx profile` appear in `dist/cli/profile/index.html`, none missing.

### 3. Option groups, epilogs, defaults, argument versus option, types, and the empty `envvar`

Every parameter record carries `kind` (`option` or `argument`), `declaration`,
`opts`, `aliases`, `secondary_opts`, `metavar`, `type`, `python_type`,
`default`, `required`, `help`, `panel`, `is_flag`, `multiple`, `count`,
`nargs`, `hidden` and `envvar`. A real record:

```json
{
  "name": "engine_config", "kind": "option", "declaration": "--engine-config",
  "opts": ["--engine-config"], "aliases": [], "secondary_opts": [],
  "type": { "name": "path", "class": "TyperPath", "exists": false,
            "file_okay": true, "dir_okay": true, "readable": true,
            "writable": false },
  "python_type": "Optional[Path]", "default": null, "required": false,
  "help": "Engine-specific YAML config", "panel": "engine",
  "is_flag": false, "multiple": false, "count": false, "nargs": 1,
  "hidden": false, "envvar": []
}
```

Types come from two independent places, so a docs page can show whichever reads
better: click's `ParamType` (with `choices` for `TyperChoice`, and the
`exists`/`file_okay`/`dir_okay` flags for `TyperPath`) and the resolved Python
annotation. `cli/app.py` uses `from __future__ import annotations`, so the
annotation is a string in the signature; the extractor resolves it with
`typing.get_type_hints(..., include_extras=True)` and unwraps `Annotated`.

Option groups: 42 options carry a non-null `panel`, all on `hpx profile`, across
seven groups: PMU profiling, advanced, build overrides, engine, output, power
measurement, target hardware. Source check: `grep -c rich_help_panel
src/helia_profiler/cli/app.py` returns 42.

Epilogs: preserved verbatim, not `cleandoc`-ed. The epilogs contain indented
example blocks and `inspect.cleandoc` flattens the indentation, which silently
destroys the code block on the page.

Environment variables: `grep -rn envvar src/helia_profiler/` returns 0 matches,
so the issue's statement still holds at `fd2d510`. `envvar` is normalised to a
list and emitted as `[]` on all 106 parameters, never omitted, and `cli.json`
records `options_with_envvar: 0` in its counts block. When the first `envvar=`
lands, the `--check` diff reports `envvar [] -> ["HPX_..."]` on that option.

One gotcha worth recording: typer vendors click. `typer.core.TyperGroup`
derives from `typer._click.core.Command`, so `isinstance(cmd, click.Group)`
against the top-level `click` package is always False and a naive walk silently
returns one leaf and no groups. The extractor duck-types on `.commands`
instead. Anyone writing the production generator will hit this.

### 4. `schema.json`: 106 fields across 15 classes, per class, checked by coverage and type

```
$ uv run --isolated --no-dev python tools/docs/extract_schema.py
wrote ... (15 classes, 106 declared fields, 106 schema properties)
```

Per class, declared fields versus properties present in the schema:

```
  BuildConfig            declared=  5 inSchema=  5 reachable=True
  ClockSelection         declared=  1 inSchema=  1 reachable=True
  EngineConfig           declared=  4 inSchema=  4 reachable=True
  HeartbeatConfig        declared=  5 inSchema=  5 reachable=True
  Ina228Config           declared=  9 inSchema=  9 reachable=True
  ModelConfig            declared=  4 inSchema=  4 reachable=True
  MonitorBoardPreset     declared=  4 inSchema=  4 reachable=False
  NsxModuleOverride      declared=  3 inSchema=  3 reachable=True
  OutputConfig           declared=  5 inSchema=  5 reachable=True
  PowerConfig            declared= 17 inSchema= 17 reachable=True
  ProfileConfig          declared= 15 inSchema= 15 reachable=True
  ProfilingConfig        declared= 13 inSchema= 13 reachable=True
  PsramConfig            declared=  1 inSchema=  1 reachable=True
  TargetConfig           declared= 13 inSchema= 13 reachable=True
  TimeoutsConfig         declared=  7 inSchema=  7 reachable=True
```

106 of 106. No field is absent. There is one finding behind that number: four
of the 106 are absent if you generate the schema the obvious way. These are
pydantic dataclasses, not `BaseModel`, so there is no `model_json_schema`; the
route is `TypeAdapter(ProfileConfig).json_schema()`. That walks down from the
root and cannot reach `MonitorBoardPreset`, because it is the value type of the
`INA228_BOARD_PRESETS` lookup table in `config/power.py` and is surfaced through
the `board_preset` property rather than through any field. The extractor
therefore enumerates all 15 classes from the two config modules and merges a
per-class schema for anything the root walk missed, and records the fact in
`counts.unreachableFromRoot: ["MonitorBoardPreset"]` so it is visible rather
than quietly dropped.

Method: pydantic's own JSON Schema, not a custom walk, so the document stays
standard and any JSON Schema renderer can take it. What that loses is the
declared Python annotation (it becomes an `anyOf`/`$ref` shape), the source
field order, and the distinction between a plain default and one built by a
`default_factory`. Those three go into an `x-hpx.fieldIndex` sidecar in the same
file rather than into a hand-rolled walk, so the standard half stays standard.
`x-hpx` also carries `sections` (the dotted `hpx.yml` key each class hangs off,
14 entries) and `enums` (the 4 `StrEnum` vocabularies: `CleanWindowProbe`,
`OutputFormat`, `PowerFirmware`, `WindowMode`).

`extra="forbid"` appears 15 times across the two config modules (12 plus 3) and
surfaces in the schema as `additionalProperties: false` on every class.

The check compares field coverage and types per class, never bytes. For each
class it compares the field name set, and for each field the `pythonType`, a
canonical schema type signature (`$ref`, `anyOf[...]`, `enum[...]`,
`array[...]` or the bare `type`), `required` and `default`. Enum member lists
are compared separately. Key order, `$defs` order, and the recorded tool
versions are all excluded, so a pydantic upgrade that reshuffles the document
without changing the contract stays green. pydantic version used: **2.13.4**,
recorded in `schema.json` under `generatedFrom`.

### 5. Clean environment, no board, no secrets

Both extractors run under `uv run --isolated --no-dev`, which builds a throwaway
environment from the package's runtime dependencies only (29 packages) and
ignores the worktree's `.venv`:

```
$ uv run --isolated --no-dev python -c "..."
Installed 29 packages in 58ms
typer 0.26.8
click 8.3.3
pydantic 2.13.4
sys.prefix = /Users/adam.page/.cache/uv/builds-v0/.tmp70fH9A

$ uv run --isolated --no-dev python tools/docs/check_reference.py --write
wrote spike/data/cli.json and spike/data/schema.json
exit=0

$ uv run --isolated --no-dev python tools/docs/check_reference.py --check
ok: 14 leaf commands, 5 groups, 102 options, 4 arguments; 15 config classes, 106 fields
exit=0
real 1.6s
```

Resolved versions, also stamped into both JSON files under `generatedFrom`:
typer **0.26.8**, click **8.3.3**, pydantic **2.13.4**, helia_profiler from the
worktree. Python 3.13 (the project's pinned interpreter).

No board was attached for any of these runs and no secrets or tokens were set.
The extraction only imports `helia_profiler.cli.app` and `helia_profiler.config`
and constructs the click command objects; it never invokes a command callback,
so no probe enumeration, serial open, J-Link call or network fetch happens.
Import-time work in the CLI module is limited to building `TyperChoice` lists
from the engine, placement and transport enums.

### 6. One rendered CLI page and one rendered configuration page

```
$ cd spike/render && npm run build
[build] 13 page(s) built in 421ms
[build] Complete!
```

Artifact at `spike/render/dist/`. 13 pages: one configuration page and 12 CLI
pages, one per top-level entry, from the dynamic route
`src/pages/cli/[command].astro`. The two proof pages are

- `spike/render/dist/cli/profile/index.html`
- `spike/render/dist/configuration/index.html`

Both are composed from the helia-ui `Ref*` parts:
`RefSymbol` per command or config class, `RefSection` per option group or key
table, `RefParams` for the rows, `RefMembers` for the on-page index. No local
component was needed to render either page; see section 9 for what had to be
distorted to make that work.

Inspect with `npx astro preview` from `spike/render/`, or open the HTML
directly. Reproduce from scratch with
`cd spike/render && npm install --install-links && npm run build`.

Two integration facts for the production scaffold:

- `RefSymbol` pulls in helia-ui's `CodeBlock`, which renders through
  expressive-code, so `astro-expressive-code` has to be registered as an
  integration in `astro.config.mjs` or the build fails to resolve
  `virtual:astro-expressive-code/config`.
- `scripts/lib/reference-render.mjs` ships in the package `files` list but is
  not in the `exports` map, so `import ... from
  '@ambiqai/helia-ui/scripts/lib/reference-render.mjs'` fails with
  `ERR_PACKAGE_PATH_NOT_EXPORTED`. The fit test reaches it by a deep
  `node_modules/` path. Worth an upstream fix.

### 7. `--check` design

**What it introspects.** `check_reference.py --check` imports the package,
rebuilds both artifacts in process by calling `extract_cli.build()` and
`extract_schema.build()`, and compares them with the committed files. It never
reads rendered `--help` output, so terminal width, `rich` styling, colour and
locale cannot affect the result. There is no `COLUMNS` pin because there is
nothing to pin. It runs three comparisons:

1. **CLI tree**, flattened to one entry per command path and one entry per
   parameter declaration. Compared per command: `kind`, `help`, `short_help`,
   `epilog`, `deprecated`, `hidden`, `panels`. Compared per parameter: `kind`,
   `type`, `python_type`, `default`, `required`, `help`, `panel`, `aliases`,
   `secondary_opts`, `is_flag`, `multiple`, `count`, `nargs`, `hidden`,
   `envvar`.
2. **Configuration**, per class: field name set, and per field `pythonType`, the
   canonical schema type signature, `required` and `default`, plus the enum
   member lists. Not byte equality; `$defs` ordering and key order are ignored.
3. **Source cross-check**, the AST audit from section 1, so a command that is
   registered but somehow unreachable through the app object is still caught.

Recorded tool versions are reported as a `note:` line and are never a failure,
so a typer or pydantic bump does not turn CI red on its own.

**Where the committed JSON lives.** In this spike, `spike/data/cli.json` and
`spike/data/schema.json`. In production, per the #320 plan, the extractors write
`astro-site/src/data/cli.json` and `astro-site/src/data/schema.json` (that is
already the default `--out` in both extractors), and the site build stages a
copy into `astro-site/public/reference/cli/` and
`astro-site/public/reference/configuration/`. Both files are committed, so a
docs build never needs a Python environment, and a reviewer sees the command
tree change in the PR diff.

**What CI failure looks like.** The check exits 1 and names the drift, one line
per change:

```
reference drift detected: the committed JSON no longer matches the source.
  - hpx profile: option added in source, absent from cli.json: --arena-size
  - hpx profile --engine: type {"name": "choice", ... "choices": ["tflm"]}
      -> {"name": "choice", ... "choices": ["tflm", "helia-rt", "helia-aot", "executorch"]}
  - hpx profile --engine: panel "engines" -> "engine"
  - TimeoutsConfig.download_asset_s: field added in source, absent from schema.json
  - enum OutputFormat: members changed

Regenerate with:
  uv run --isolated --no-dev python tools/docs/check_reference.py --write
then review the diff and update the authored prose that references the changed
commands or keys.
```

That output is real: it was produced by mutating the committed JSON (dropping
`--arena-size`, narrowing the `--engine` choices, renaming its panel, dropping a
`TimeoutsConfig` field and truncating `OutputFormat`), running `--check`, and
restoring. Exit status 1.

The intent is that a new option fails the docs job until the author regenerates,
which is also the prompt to write the prose for it. The failure names the
option, so the fix is obvious without reading the generator.

### 8. Serving URLs, startup data, page weight

**URLs.** Following the #320 plan's `/reference/<section>/` convention:

- `/reference/cli/cli.json`
- `/reference/configuration/schema.json`

Both exist in the build artifact at `spike/render/dist/reference/cli/cli.json`
(106,418 bytes) and `spike/render/dist/reference/configuration/schema.json`
(87,189 bytes).

**Browser startup data: no, neither.** Both pages `import` the JSON in Astro
frontmatter, so it is consumed at build time and the rows are baked into the
HTML. Evidence: no `_astro/*.js` asset contains `leaf_commands`, `x-hpx` or
`ProfileConfig`; neither page contains an `astro-island` element or an
`application/json` script block; total client JavaScript across the whole
artifact is a single 2,523 byte file, expressive-code's copy-to-clipboard
handler, and nothing else. The `public/` copies are static files for agents and
external tooling, not page dependencies. Nothing fetches them.

**Page weight.**

| Page | HTML | gzip |
| --- | --- | --- |
| `/configuration/` | 102,399 B | 10,893 B |
| `/cli/profile/` (largest CLI page) | 26,187 B | 4,013 B |
| `/cli/validate/` | 13,657 B | 2,651 B |
| `/cli/probes/` | 8,651 B | 1,498 B |
| smallest, `/cli/engines/` | 2,217 B | 803 B |

Largest CLI page: **`/cli/profile/`, 26,187 bytes, 4,013 bytes gzipped**, with
44 options across 7 groups plus 1 argument. Both proof pages clear the plan's
250 KB HTML and 40 KB gzip budget with a wide margin, and that is before the
site shell, sidebar and search are added.

### 9. Recommendation

See the next section.

## Recommendation

**Reference model: no. Ref parts: yes.**

The helia-ui `Ref*` parts render both pages with no local component at all.
`RefSymbol` for a command or a config class, `RefSection` plus `RefParams` for
each option group or key table, `RefMembers` for the on-page index. That is the
whole configuration page and the whole CLI page. Keep using them.

The shared reference model is a different matter, and it does not fit. Pushing
`cli.json` through it (`spike/render/scripts/reference-model-fit.mjs`) does
produce a document the shipped renderer accepts, but only after lying twice and
flattening nine field families into prose. `SymbolKind` has no `command` or
`group`, so commands are declared `function` and sub-apps `namespace`.
`ReferenceLanguage` has no `shell` or `cli`, so the CLI claims to be `python`.
`RefParam` is `name`, `type`, `default`, `description` and nothing else, so
`required`, short-flag `aliases`, `is_flag`/`secondary_opts`,
`multiple`/`count`/`nargs`, the `rich_help_panel` group, the argument-versus-
option distinction, and `envvar` all have to be glued into the description
string or the name column, where no check can see them again. The epilog has no
home either and gets appended to `description` as a fenced block. On the
configuration side the same squeeze applies to JSON Schema constraints:
`minimum`, `maximum`, `pattern`, `minLength` and the rest have no column and end
up in the description text. `renderReference` also collapses all 12 top-level
commands onto a single module page, which is the wrong route shape for a CLI
where each command wants its own URL.

So: keep `cli.json` and `schema.json` as first-class artifacts with their own
schema, and treat the `Ref*` parts as presentation primitives fed by a local
mapping layer. Do not route either surface through `reference.json`. That keeps
the lossy mapping in one small page file where it is visible, instead of baking
it into the artifact that the `--check` job is supposed to police.

**Local versus upstream: extractors local, one small upstream ask.**

Both extractors stay in this repo, in `tools/docs/`. They have to import
`helia_profiler` to introspect a constructed Typer app and live pydantic
models; that is Python work and cannot move into a Node generator the way
`helia-ui-pyref` does, because the command tree is only knowable by building the
app, not by parsing the source. There is no upstream shape for them as written.
They also cost nothing to keep local: 29 runtime packages, 1.6 seconds,
no board, no secrets.

The page mapping (`[command].astro`, `configuration.astro`) also stays local for
now. It is roughly 200 lines and it encodes hpx-specific judgements, which panel
order reads best, what goes in the description column. Proposing it upstream
before a second consumer exists would freeze those judgements for someone else.

What is worth proposing upstream to helia-ui, in order of value:

1. Export `scripts/lib/reference-render.mjs` in the `exports` map. It ships in
   `files` today but is unreachable by specifier, so any consumer that wants the
   renderer has to deep-path into `node_modules/`. Pure packaging fix, no API
   change.
2. Widen the reference model so a CLI can be described without lying: add
   `command` and `group` to `SymbolKind`, add `shell` to `ReferenceLanguage`,
   and add four optional fields to `RefParam`: `group` (the `rich_help_panel`
   equivalent), `required`, `aliases` and `kind` (`argument` or `option`). Also
   an optional `constraints` record on `RefParam` for the JSON Schema case. All
   additive and all optional, so nothing existing breaks. If that lands, the
   mapping layer stops being lossy and the question of an upstream `RefCli` part
   becomes worth asking again.
3. Nothing else. Do not propose an upstream CLI or schema generator: it would
   mean a Python dependency in a Node package.

Revisit item 2 at Phase 5 with a second CLI consumer in hand. Until then the
local mapping is the cheaper and more honest option.

## Verification

| Check | Command | Result |
| --- | --- | --- |
| Lint | `uv run ruff check tools/docs/` | pass |
| Format | `uv run ruff format --check tools/docs/` | pass |
| Types | `uv run ty check tools/docs/` | pass (note: CI's `ty` config covers `src/helia_profiler` and `tests` only, not `tools/`) |
| Tests | `uv run pytest tests/ tools/tests/ -q` | 3374 passed, 7 skipped, 977 deselected, 64 s |
| Extractors | `uv run --isolated --no-dev python tools/docs/check_reference.py --check` | exit 0 |
| Drift detection | same, against deliberately mutated JSON | exit 1, five drifts named |
| Site build | `cd spike/render && npm run build` | 13 pages, exit 0 |

No new tests were added: the spike ships prototypes, and the production
generators land with their own tests under the #320 children.

## Deferred

- `TODO(#324): decide whether the option help column should render Markdown;
  today the help strings are emitted as plain text and any backticks in them
  show literally.`
- The production generators, their CI wiring and the `astro-site/` integration
  are out of scope here and belong to the #320 children for CLI reference
  generation and configuration schema reference generation.
- Whether `MonitorBoardPreset` should be documented on the configuration page at
  all, given it is not settable through `hpx.yml`, is a content decision for the
  configuration reference issue.
