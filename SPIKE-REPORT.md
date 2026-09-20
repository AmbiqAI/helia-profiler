# Spike report: Astro + helia-ui scaffold and Python reference extraction

Issue: AmbiqAI/helia-profiler#323 (child of #320).
Branch: `323-spike-scaffold-pyref`, based on `origin/main` at `fd2d510`.
Scaffold: `astro-site/`. Throwaway by design; the production scaffold is the
skeleton issue.

Every section below answers one Acceptance checkbox from #323, in order.

Environment for every number in this report: macOS 25.6.0 (arm64), Node
v24.12.0, npm 11.6.2, uv 0.11.26, Docker 29.8 (`node:24`, `linux/amd64`) for
the lockfiles.

---

## 1. Scaffold, pin and Linux lockfile

`astro-site/` follows the heliaRT layout: `astro.config.mjs` with one
`heliaStarlight()` plugin, `src/content/docs/**` for authored pages, `scripts/`
for the generators, `src/generated/` for the sidebar fragment.

Five sections are declared and resolve: Home, Getting started, User guide,
Examples, Reference.

Pin, verbatim from `astro-site/package.json`:

```json
"@ambiqai/helia-ui": "github:AmbiqAI/helia-ui#v0.1.0-alpha.14"
```

Lockfile generated on Linux:

```sh
docker run --rm --platform linux/amd64 -v "$PWD":/w -w /w node:24 \
  sh -c "npm install --package-lock-only --ignore-scripts && npm ci --ignore-scripts"
```

| Fact | Value |
| --- | --- |
| Node in the container | v24.21.0 |
| npm in the container | 11.19.0 |
| helia-ui resolved | `git+ssh://git@github.com/AmbiqAI/helia-ui.git#95eb9f1d2dc461533ed2c9477d6d52d0ab4969e5` |
| helia-ui version | 0.1.0-alpha.14 |
| `npm ci --ignore-scripts` in the container | exit 0 |

`95eb9f1d2dc461533ed2c9477d6d52d0ab4969e5` is the commit `v0.1.0-alpha.14`
points at; it matches `git rev-parse HEAD` in a checkout of the tag.

Two lockfiles are kept, one per variant of the interactivity measurement
(section 11): `astro-site/spike/package-lock.baseline.json` (335,125 bytes,
635 packages) and `astro-site/spike/package-lock.react.json` (379,670 bytes,
723 packages). `scripts/spike-variant.mjs` copies the right one into place.

Note for the skeleton issue: npm records the resolved URL as `git+ssh://`. The
container had no SSH key and `npm ci` still succeeded, so npm fetched over
HTTPS, but a CI job with a restricted git config should be checked against this
rather than assumed.

## 2. `npm ci`, `npm run build`, `npm run check`

In `astro-site/`, after `npm ci` on the Linux lockfile:

| Command | Exit | Output |
| --- | --- | --- |
| `npm ci` | 0 | 515 packages added, 0 vulnerabilities |
| `npm run check` | 0 | 8 files, 0 errors, 0 warnings, 0 hints |
| `npm run build` | 0 | 28 pages built |
| `npm run check:reference` | 0 | 22 pages and 25 artifacts up to date |

Clean-checkout run: see the appendix.

Three things had to be fixed to get there, all worth carrying into the
skeleton:

- `helia-ui-pyref --sidebar` writes a single group **object**; Starlight's
  `items` requires an **array**, so passing the fragment straight through fails
  config validation. `astro.config.mjs` wraps it. Upstream candidate.
- `astro check` type-checks `astro.config.mjs`, so a literal
  `await import('@astrojs/react')` demands the package's types even in the
  Astro-only variant where it is not installed. The config loads optional
  integrations through a variable specifier instead.
- A clean checkout has no generated API pages, so the sidebar referenced slugs
  that did not exist and `npm run build` exited 1. `package.json` now carries
  `"prebuild": "node scripts/build-reference.mjs"`, which regenerates the 22
  pages from the committed griffe dump. heliaRT uses the same pattern.

Expressive Code: nothing had to be added. `RefSymbol.astro` renders code
through `CodeBlock.astro`, which imports `Code` from
`@astrojs/starlight/components`, so the Ref* pages depend transitively on
`astro-expressive-code`. This config never sets `expressiveCode: false`, so
Starlight's own registration stands; the built site carries
`dist/_astro/ec.l39k6.css`, which is the proof it ran.

`scripts/lib/reference-render.mjs` was **not** imported. It is in helia-ui's
`files` but not in its `exports` map, and a deep import is refused:

```
BLOCKED: ERR_PACKAGE_PATH_NOT_EXPORTED
Package subpath './scripts/lib/reference-render.mjs' is not defined by "exports"
```

That matters to the CLI and configuration spike (#324), which would need the
renderer without the Python extractor. Upstream candidate.

## 3. griffe pin and dump

griffe is pinned to **1.7.3**, the value of `GRIFFE_VERSION` in
`scripts/lib/pyref-extract.mjs` of helia-ui v0.1.0-alpha.14.

```sh
uv run --with griffe==1.7.3 griffe dump helia_profiler --docstyle google -f > dump.json
```

Wrapped as `npm run reference:dump` (`scripts/dump-python.mjs`), which prints
the installed version before dumping:

```
griffe: griffe 1.7.3
dump: 9561976 bytes to spike/griffe.json
tiers: 98 in __all__, {"stable":45,"experimental":40,"implementation":13}
```

Exit 0. One griffe warning, unrelated to the reference model:
`src/helia_profiler/transport/protocol.py:182: No type or annotation for
parameter 'read_fn'`.

## 4. How many of the 85 symbols rendered

With the scoping pass (section 5): **85 of 85 names are represented, 84 of them
as documented symbols.**

| Outcome | Count | Names |
| --- | --- | --- |
| Rendered as a documented symbol | 84 | all stable + experimental except `examples` |
| Rendered as a module page, not a symbol | 1 | `examples` |
| Not rendered at all | 0 | none |
| Rendered more than once | 0 | none |
| Present in `/reference/api/llms-full.txt` | 85 | none missing |

`examples` is a submodule that `__init__.py` publishes through `__all__`, not a
class or a function. pyref documents it as a module page
(`/reference/api/helia_profiler/examples/`), which is the right rendition, but
the skeleton issue should decide whether a submodule belongs in a symbol
manifest at all.

For contrast, the **unscoped** run of the same dump with pyref's default
filters produced 187 module pages and 897 symbols, and only **45 of the 85**
re-export links on the package page resolved. Both `__version__` and `examples`
produced no symbol. The 40 dangling links are the chained-alias problem in
section 9.

## 5. How the 85-symbol scope is passed to pyref

**Not through `--filter`.** pyref's filters are mkdocstrings-style name
regexes, compiled once and applied to the members of *every* node, classes
included (`compileFilters` and `members` in `scripts/lib/pyref-extract.mjs`).
A pattern pair that keeps exactly the 85 top-level names
(`--filter '!^' --filter '^(Name1|Name2|...)$'`) therefore also deletes every
class attribute, every method and every enum member, because none of those
names is in the manifest. Filters can express "drop these 13 names" but not
"keep only these 85 symbols".

**Mechanism used: the dump is pre-filtered.**
`astro-site/scripts/scope-dump.mjs` reads `spike/griffe.json` plus
`src/data/api-tiers.json` (the package's own `__api_stability__`, extracted at
dump time) and writes `spike/griffe.scoped.json`, which pyref then renders. The
pass does three things:

1. Keeps only the names whose tier is `stable` or `experimental`.
2. Resolves each alias chain to the defining node, then re-homes the symbol at
   the module its **public import path** names, which is griffe's one-hop
   target. A symbol whose one-hop home is a private module is hoisted to the
   package. This is what makes the package page's re-export table resolve.
3. Rewrites `__all__` and the alias entries beside it to the scoped set, so the
   re-export table lists 85 rows and no more.

Result: 22 module pages, 85 symbols, **zero pyref warnings** (the unscoped run
emitted 56).

`--filter` is still used for one thing: `^__version__$`, to re-include the one
dunder the package publishes, which `!^_` would otherwise drop.

### Can the 13 implementation names be kept out of the nav and `llms-full.txt`?

| Surface | Answer | Evidence |
| --- | --- | --- |
| Nav / sidebar | Yes | 0 of 13 in `src/generated/api-sidebar.json` |
| Pages and `reference.json` | Yes | 0 of 13 are symbols in the model |
| `/reference/api/llms-full.txt`, as documented symbols | Yes | 0 of 13 have a section |
| `/reference/api/llms-full.txt`, as text | **No, 8 of 13** | they appear inside in-scope signatures |

The eight are `NsxModuleOverride`, `DoctorResult`, `BoardDef`,
`build_platform_registry`, `PmuCounter`, `JLinkProbe`, `JLinkProbeMatch`,
`SerialPortInfo`. Every occurrence is a type annotation or a default in a
signature that is itself in scope:

```
doctor() -> DoctorResult
ports(*, include_all: bool = False) -> tuple[SerialPortInfo, ...]
nsx_modules: dict[str, NsxModuleOverride] = field(default_factory=dict)
```

This is not a tooling gap and does not go upstream. The signature is the
contract; hiding the return type would make it wrong. What the skeleton issue
has to decide is whether a public function returning an implementation-tier
type is acceptable API design, which is a source question.

Verified separately: none of the 13 names collides with a class member name
anywhere in the package, so a name-based exclusion would have been safe had one
been needed.

## 6. Tier assertion against `__all__`

```
len(__all__): 98   unique: 98   duplicates: []
_STABLE_API 45, _EXPERIMENTAL_API 40, _IMPLEMENTATION_API 13, sum 98
pairwise intersections: set(), set(), set()
union size: 98
in __all__ but in no tier set: []
in a tier set but not in __all__: []
union == set(__all__) exactly: True
```

**45 + 40 + 13 = 98 = the names in `__all__`, exactly. No entry is in no tier
set, none is in two, and `__all__` has no duplicates.**

The tiers are also reachable at runtime as `helia_profiler.__api_stability__`,
a `{name: tier}` mapping of all 98. That is what the scoping pass consumes, so
the manifest is not restated in the site.

## 7. Parity against the current mkdocstrings pages

Evidence: `uv run --group docs mkdocs build` (exit 0, "Documentation built in
2.09 seconds"), then the generated HTML for the eight pages was read directly.

Current pages and what they carry (`:::` directives per file):

| Page | Symbols | Built HTML |
| --- | --- | --- |
| config.md | 23 | 107,146 B |
| errors.md | 14 | 82,142 B |
| index.md | 0 (prose) | 56,665 B |
| platform.md | 3 | 75,536 B |
| power-results.md | 7 | 87,066 B |
| profile.md | 2 | 56,853 B |
| results.md | 36 | 181,080 B |
| session.md | 12 | 144,462 B |
| **Total** | **97** | |

The 97 include implementation-tier symbols (`get_soc`, `SocDef`,
`build_platform_registry`, `NsxModuleOverride`) that the new scope removes.

| Feature | mkdocstrings today | pyref | Verdict |
| --- | --- | --- | --- |
| Signature shown, separated, annotated | Yes | Yes | **Present** |
| Constructor signature on a pydantic dataclass | No (`PowerConfig` heading, no parameters) | No (`PowerConfig()`) | **Present in neither**, parity |
| Pydantic **fields** on the class page | **No**, `PowerConfig` renders its three `@property` members only | **Yes**, all 21 fields | **Different, pyref better** |
| Field type annotation | Properties only | Every field | **Different, pyref better** |
| Field **default value** | Not shown | Shown (`enabled: bool = False`, `driver: str = DEFAULT_POWER_DRIVER`) | **Different, pyref better** |
| Parameters / Attributes table | Yes where the docstring has a NumPy section (`HeartbeatConfig`: Name/Type/Description) | **No** (section 9) | **Different, mkdocstrings better** |
| Enum members | **No**, `Toolchain`'s member list renders empty | **Yes**, with values (`GCC = 'gcc'`) | **Different, pyref better** |
| `Bases:` line | **Yes** (`Bases: StrEnum`; `ConfigError` cross-linked to `HpxError`) | **No**, `RefSymbol` has no `bases` field | **Absent in pyref**, regression |
| Cross-references inside signatures | **Yes** (`<a class="autorefs">` on return types) | **No**, plain code text | **Absent in pyref**, regression |
| Cross-references in prose | Yes (`:class:` resolved by the handler) | Model-level `[name][path]` links resolve after the scoping pass; Sphinx roles render literally | **Different** |
| `merge_init_into_class` | On, nothing to merge (no source `__init__`) | On by default, nothing to merge | **Present in neither**, parity |
| pydantic validators | Not rendered | Not rendered | **Absent in both**, parity |
| Source link | Off (`show_source: false`) | On, with a GitHub blob URL | **Different, pyref adds it** |
| Undocumented members | Hidden (`show_if_no_docstring: false`) | Shown | **Different** |
| Page shape | 8 authored pages, curated groups | 22 module pages, one per import path | **Different** (section 12) |

Longest signature the current site renders, for calibration
(`session/index.html`):

```
Session(yaml_path: Path | None = None, _base: Mapping[str, Any] = (lambda: MappingProxyType({}))(), _overrides: Mapping[str, Any] = (lambda: MappingProxyType({}))())
```

## 8. Three-symbol content-completeness probe

Samples: `PowerConfig` (frozen pydantic dataclass, 21 fields),
`collect_support_bundle` (longest in-scope signature, 105 characters),
`Toolchain` (enum, 4 members).

Three published surfaces were checked. Note there are **two** `llms-full.txt`
and they do not carry the same content:

- `/reference/api/llms-full.txt`, written by pyref from the model.
- `/llms-full.txt`, written by helia-ui's discoverability integration by
  concatenating the pages' `.md` renditions.

| Sample | Rendered page | `/reference/api/llms-full.txt` | `<route>/index.md` | `/llms-full.txt` |
| --- | --- | --- | --- | --- |
| `PowerConfig` signature | `PowerConfig()` | `PowerConfig()` | **absent** | **absent** |
| `PowerConfig` 21 fields, types and defaults | Present | Present | **absent** | **absent** |
| `collect_support_bundle` full signature | Present | Present (`collect_support_bundle(options: SupportBundleOptions = SupportBundleOptions()) -> SupportBundleCollection`) | **absent** | **absent** (the name appears only in the package page's re-export table) |
| `Toolchain` 4 members with values | Present | Present (`GCC = 'gcc'`, `ARM_NONE_EABI_GCC = 'arm-none-eabi-gcc'`, `ARMCLANG`, `ATFE`) | **absent** (member names appear only because the class docstring's prose names them) | **absent**, same reason |
| Parameter table | **absent for all three** (section 9) | absent | absent | absent |

**Result: pyref's own text artifact passes; the site's `.md` renditions and
site-level `llms-full.txt` fail.**
`dist/reference/api/helia_profiler/config/index.md` is 8,362 bytes of docstring
prose with no heading, no signature and no symbol name for any of the 14
classes on the page.

Root cause, read in `starlight/discoverability.ts`: the markdown rendition
drops component tags and keeps their text children (`reduceTags`). `RefSymbol`
carries `name`, `signature`, `kind` and `source` as **props**, so all of it is
discarded and only the docstring, which is the component's children, survives.
This is the highest-value item on the upstream list, because the plan's whole
agent-facing premise is that `llms-full.txt` is what an agent reads instead of
the pages.

## 9. Named breakage list

1. **Docstring style is NumPy, not Google.** The package writes
   `Attributes` over a `----------` rule, not `Attributes:`. `mkdocs.yml` uses
   `docstring_style: auto`, so griffe detects NumPy and parses the sections;
   `--docstyle google`, which pyref mandates, does not. Measured across the
   whole package: the google dump parses 4 `parameters` and 5 `returns`
   sections; a numpy dump parses 6 `parameters`, 2 `attributes`, 2 `raises` and
   1 `returns`. **Net cost of the google mandate: the two `Attributes` tables
   the current site renders, including `HeartbeatConfig`.** Everything else is a
   wash, because the package has almost no structured sections at all: 1,223
   nodes have a docstring and fewer than 20 carry a section of any kind. The
   missing parameter tables in sections 7 and 8 are therefore a **source**
   problem for Phase 3 content work, not a pyref defect. Two options: dump with
   `--docstyle numpy` (pyref reads already-parsed sections from the dump and
   does not re-parse, so it accepts a numpy dump while `--docstring-style`
   stays `google`), or convert the docstrings. The second is the honest fix.
2. **Frozen pydantic dataclass defaults: works, and better than today.** These
   classes keep their class body, so griffe sees every field statically. All 21
   `PowerConfig` fields render with annotation and default. The default renders
   as the **symbolic name** (`DEFAULT_POWER_DRIVER`), not the resolved value:
   correct for a source-truth reference, but a reader cannot see the actual
   default without following the constant.
3. **Frozen pydantic dataclass constructor: absent, in both tools.** The
   `__init__` is synthesised at runtime and is not in the source, so
   `merge_init_into_class` has nothing to merge and the class signature is
   `PowerConfig()`. Parity with today, but no reference page states argument
   order or which fields are required, and `extra="forbid"` and `frozen=True`
   are invisible.
4. **`field_validator` and `model_validator` are invisible, in both tools.**
   Every validator in the package is named `_validate`, so `!^_` drops it. Even
   unfiltered, pyref would render a method, not the constraint: the message
   `"power.sync_input_index must be >= 0"` lives in a `raise` inside the body.
   No generator recovers it. Validation rules have to be authored prose, or
   come from the configuration-schema generator in #324, which sees pydantic's
   JSON Schema rather than the source.
5. **Re-exports and canonical paths: pyref resolves one hop, this package needs
   two or three.** `helia_profiler.ProfileResult` aliases
   `helia_profiler.results.ProfileResult`, which aliases
   `helia_profiler.results.models.ProfileResult`. pyref writes the one-hop
   target into the re-export table verbatim, so on a raw dump **40 of the 85**
   links point at nothing and emit
   `unresolved cross-reference [ProfileResult][helia_profiler.results.ProfileResult]`.
   The scoping pass fixes it by following the chain and re-homing the symbol at
   the public path. Without that pass pyref is not usable on this package.
   Upstream candidate.
6. **Re-homing leaves nested member ids at the canonical path.** The scoping
   pass rewrites the top-level symbol's `id`, but a class's members keep theirs:
   `PowerConfig` is `helia_profiler.config.PowerConfig` while its field is
   `helia_profiler.config.power.PowerConfig.enabled`. Anchors stay unique and
   the page works; a deep link written against the public path would not
   resolve. Fixable in the pass, left for the production generator.
7. **`merge_init_into_class` parity: equal, and moot.** Both tools default it
   on; no class in the 85 has a source `__init__` with documented parameters,
   so no rendered page differs because of it.
8. **Class bases are not in the model at all.** `RefSymbol` in
   `reference-model.ts` has no `bases` field, so `ConfigError()` renders with no
   sign that it subclasses `HpxError`, and `Toolchain()` gives no hint of
   `StrEnum`. For an error hierarchy whose documented contract is "catch
   `HpxError` for a catch-all", this is the worst single regression against
   `show_bases: true`. Upstream candidate.
9. **Signature cross-references are lost.** `signature_crossrefs: true` today
   links `ProfileConfig` inside `Session.resolve()`'s signature; pyref emits
   plain code text. Upstream candidate.
10. **Sphinx inline roles render literally.** The package uses 129 `:class:`
    and 135 `:attr:` / `:meth:` / `:func:` roles. The Python handler resolves
    some of them today; pyref passes them through, so a reader sees the role
    markup as text. Phase 3 content work.
11. **One module page can be enormous.** `helia_profiler.results` carries 19
    symbols and builds to a 447,982-byte HTML page, 1.8x heliaRT's 250 KB
    per-page budget. This is the page-shape consequence of one page per module
    and is unrelated to React. Section 11.
12. **`--check` against `--commit $(git rev-parse HEAD)` never passes.** The
    model records `sourceCommit`, so every commit makes committed output stale.
    `scripts/build-reference.mjs` uses
    `git log -1 --format=%H -- src/helia_profiler` instead, which only moves
    when the documented source moves. Worth carrying into the skeleton.
13. **The `--sidebar` fragment is an object where `items` needs an array.**
    Section 2.
14. **`scripts/lib/reference-render.mjs` is unreachable through `exports`.**
    Section 2. Blocks #324 more than it blocks this spike.

## 10. Baseline: dist size, largest page, build wall time

Astro-only, via `scripts/spike-measure.mjs`, which clears `dist/` and `.astro/`
before every build and generates the reference outside the timer. 28 pages: 6
authored, 22 generated.

| Measure | Baseline |
| --- | --- |
| `dist` total | 5,025,101 B (4.79 MiB), 169 files |
| CSS in `dist` | 208,759 B |
| Client JS in `dist` | 545,745 B, 13 files (Starlight shell and Pagefind) |
| Largest page HTML | 447,982 B, `reference/api/helia_profiler/results/index.html` |
| Largest page gzip | 22,355 B |
| Build wall time | 2,131 ms (median of 5: 2231, 2131, 2129, 2141, 2129) |
| Lockfile | 335,125 B, 635 packages |
| Examples page HTML / gzip | 35,288 B / 7,253 B |
| Scripts the Examples page loads | 4 files, 6,147 B, 3,020 B gzip |

## 11. React + Tailwind with one island, against the baseline

Variant: `@astrojs/react`, `react`, `react-dom`, `tailwindcss`,
`@tailwindcss/vite`, `@astrojs/starlight-tailwind`, plus one island , 
`src/components/ExamplesTable.tsx`, a filterable table of the nine example
configs, `client:load` on the Examples index. Identical content in both
variants; the baseline renders the same nine rows as a static table.

Reproduce with `node scripts/spike-variant.mjs <baseline|react>`, then
`npm ci && npm run measure`. The variant script owns the three things that
differ: the dependency block, the Examples index page, and the flag
`astro.config.mjs` reads. Results land in `astro-site/spike/results/`.

| Measure | Baseline | React + Tailwind | Delta |
| --- | --- | --- | --- |
| `dist` total | 5,025,101 B | 5,269,938 B | +244,837 B (+4.9%) |
| `dist` files | 169 | 172 | +3 |
| CSS in `dist` | 208,759 B | 221,607 B | +12,848 B (+6.2%) |
| Client JS in `dist` | 545,745 B | 768,596 B | +222,851 B (+40.8%) |
| Largest page HTML | 447,982 B | 447,982 B | 0 |
| Largest page gzip | 22,355 B | 22,352 B | -3 B |
| Build wall time (median of 5) | 2,131 ms | 2,393 ms | +262 ms (+12.3%) |
| Lockfile | 335,125 B / 635 pkgs | 379,670 B / 723 pkgs | +44,545 B / +88 pkgs |
| Examples page HTML | 35,288 B | 44,732 B | +9,444 B (+26.8%) |
| Examples page gzip | 7,253 B | 9,550 B | +2,297 B (+31.7%) |
| Scripts the Examples page loads | 6,147 B / 3,020 B gzip | 221,099 B / 69,523 B gzip | +214,952 B / +66,503 B gzip |

Against heliaRT's budget of **250 KB HTML and 40 KB gzip per page**:

- **The largest page breaches the HTML budget in both variants**: 447,982 B is
  1.79x the limit. React contributes nothing to it. This is the
  one-page-per-module shape of the reference (breakage 11), and it is the real
  page-size problem on this site.
- Gzip is inside budget everywhere: worst page 22,355 B, 56% of the limit, in
  both variants.
- The Examples page is comfortably inside both limits in both variants.
- The budget as heliaRT writes it covers HTML and gzip **per page** and says
  nothing about script payload. The island's 221,099 B of JS (69,523 B gzip) on
  one page is outside anything the budget measures, and is 1.7x that page's
  budgeted gzip allowance.

**Decision: defer React.** What the island buys on the Examples index is
client-side filtering of a nine-row table. What it costs is 215 KB more
JavaScript (66.5 KB gzip) on that page, 27% more HTML for the same rows, 88
more packages and 44.5 KB more lockfile to keep current on an alpha-pinned
dependency, and 12% more build time. The static table already renders every
row, is indexed by Pagefind, and survives into the `.md` rendition; the
island's rows do not, which matters more here than interactivity given
section 8. Revisit when the examples catalogue is large enough that scanning it
is genuinely hard, and measure again at that size.

## 12. helia-ui upstream issue candidates

One line each; the symptom observed here, not a proposed fix.

1. **pyref: pydantic dataclass constructors are invisible.** `PowerConfig()`
   renders with no parameters, so no page states argument order, required
   fields, `frozen=True` or `extra="forbid"`.
2. **pyref: `field_validator` / `model_validator` constraints never reach the
   model.** The rule lives in a `raise` inside a private method, so the
   documented type says `int` where the real contract is `>= 0`.
3. **pyref: alias chains are followed one hop.** A package that re-exports
   through an intermediate module gets a re-export table whose links point at
   nothing: 40 of 85 dangled here.
4. **pyref / reference model: no notion of a public manifest or canonical
   path.** A symbol is documented where it is defined, so the page a reader
   lands on is `helia_profiler.results.models`, not the
   `helia_profiler.results` they import from.
5. **Reference model: `RefSymbol` has no `bases`.** `ConfigError()` renders
   with no sign that it subclasses `HpxError`; enums lose `StrEnum`.
6. **pyref: signatures carry no cross-references.** mkdocstrings links types
   inside the signature; pyref emits plain code text.
7. **Discoverability: `.md` renditions and site-level `llms-full.txt` drop
   everything a Ref* part carries as a prop.** A generated reference page
   reduces to docstring prose: no signature, no parameter table, no enum
   members, not even the symbol name. Highest-value item on this list.
8. **pyref: `--sidebar` writes a group object where Starlight's `items` needs
   an array.** Passing the fragment straight into a section fails config
   validation.
9. **Packaging: `scripts/lib/reference-render.mjs` ships in `files` but is not
   in `exports`.** A repository that wants to render a non-Python model is
   refused with `ERR_PACKAGE_PATH_NOT_EXPORTED`.
10. **pyref: no tier or stability badge.** The plan requires every API page
    badged stable or experimental; the model has no field for it and
    `RefSymbol` renders no badge from one.
11. **pyref: one page per module has no ceiling.** A 19-symbol module builds a
    448 KB HTML page, 1.8x heliaRT's own per-page budget.
12. **helia-ui #74, "Reference generators: grouping config and a rendered group
    index"**, open, and directly on the path here: the current site curates 97
    symbols into 8 grouped pages, and module-per-page is a regression against
    that.
13. **helia-ui #76, "CodeBlock: extend CodeLanguage with cmake, yaml, xml,
    kconfig, ini"**, open. `CodeLanguage` in `astro/CodeBlock.astro` is
    `bash | c | cpp | diff | json | plaintext | python | sh | tsx | typescript`;
    every heliaPROFILER config example is YAML.
14. **No 404 part.** Nothing under `astro/` or `starlight/` renders a
    not-found page; the plan needs a real 404 with Home and Reference links for
    61 redirected routes.
15. **No product version in the shell.** heliaRT hand-rolls `build-info.mjs`
    and a footer link to carry version and commit; the plan wants it in the
    shell and the plugin has no slot for it.

## 13. Spike PR and mirror to #320

**Not done, deliberately.** The dispatch for this work said to open no PR and
comment on no issue until the owner has reviewed this report. The branch is
pushed. Opening the draft PR and mirroring to #320 is the owner's call.

---

## Recommendation

**Astro-only. Go on pyref as the API pipeline, with one blocking dependency.**

Astro-only: adopt the Astro-only baseline and do not add `@astrojs/react` or
Tailwind in the skeleton. The one island measured here costs 215 KB more
JavaScript on its page, 27% more HTML for the same rows, 88 more packages and
12% more build time, and buys filtering of a nine-row table that the static
version already renders in full. Nothing about the site's real page-size
problem is caused by React: the 448 KB reference page is 1.8x heliaRT's budget
in both variants, and fixing that is a page-shape decision, not an
interactivity one. Keep `scripts/spike-variant.mjs` so the measurement can be
repeated when the examples catalogue is large enough to justify asking again.

pyref: go. The extraction half works on this package once the dump is scoped.
All 85 stable and experimental symbols render, exactly once each, with zero
warnings; the 13 implementation names are out of the nav, the pages, the model
and the llms sections; pydantic field types and defaults, and enum members with
their values, render better than the current mkdocstrings site does. The
scoping pass is about 150 lines and belongs in this repository, not upstream,
until helia-ui grows a manifest concept.

The blocking dependency is item 7 on the upstream list. The plan's stated
premise is that agents read `llms-full.txt` and `<route>/index.md` instead of
the pages, and today those two surfaces reduce a generated reference page to
docstring prose with no signature, no parameter table, no enum members and no
symbol name. pyref's own `/reference/api/llms-full.txt` is complete, so the
content exists; the discoverability integration throws it away. Either that is
fixed upstream, or the site publishes pyref's artifact at the agent-facing URL
and the CI completeness check asserts against it. Decide before Phase 2 starts,
because the content-completeness tests in the plan are written against the
wrong file otherwise.

Two things the owner has to decide, not the spike:

- **Page shape.** Module-per-page gives 22 pages, one of them 448 KB; the
  current site curates 97 symbols into 8 pages. helia-ui #74 is the upstream
  path. Keeping the curated shape means either waiting on #74 or grouping
  locally.
- **Docstring style.** The package is NumPy-style prose with fewer than 20
  structured sections in 1,223 docstrings. `--docstyle google` as mandated
  costs the two `Attributes` tables the current site renders. Converting the
  docstrings is the honest fix and is Phase 3 content work; dumping with
  `--docstyle numpy` is the cheap one and works today.

Not covered by this spike: CLI and configuration extraction (#324), production
pages, content rewrites, and any change to `mkdocs.yml`, `deploy-pages.yml` or
the published site.

---

## Appendix: clean-checkout verification

`git clone --branch 323-spike-scaffold-pyref --single-branch` into `/tmp`, at
`5237f6558b5b491320db8f58b49f8afbf80621d0`. Host Node v24.12.0, npm 11.6.2.
Nothing was run before these four commands: no `reference:dump`, no manual
generation.

| Command | Exit | Output |
| --- | --- | --- |
| `npm ci` | 0 | 0 vulnerabilities |
| `npm run check` | 0 | 8 files, 0 errors, 0 warnings, 0 hints |
| `npm run build` | 0 | 28 pages built in 2.87 s |
| `npm run check:reference` | 0 | 22 pages and 25 artifacts up to date |

The `prebuild` hook ran inside `npm run build` and regenerated the reference
from the committed dump, with no uv and no griffe on the path:

```
> node scripts/build-reference.mjs
scope: 85 in scope, 85 placed, 0 unresolved, 22 module pages.
pyref: 22 pages written to src/content/docs/reference/api, 25 artifacts to public.
reference: 85 symbols across 22 modules.
```

`npm run reference:dump` is the only step that needs uv and griffe, and it is
only run when the Python source changes.
