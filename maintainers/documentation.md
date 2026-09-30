# Documentation release checks

The product site is built from `astro-site/` with Node 24 and the committed npm
lockfile. Python and uv are needed to generate the API, CLI and configuration
reference. Keep generated reference changes with their source changes.

## Review locally

From `astro-site/`:

```bash
npm ci
npm run build
npm run check
npm run check:links
npm run check:output
npm run check:search
npm run check:redirects
npm run check:reference
npm run check:guard
npx playwright install chromium
npm test
npm run preview
```

The reference gate reads committed artifacts as well as the working tree. After
regenerating reference data, commit it locally before running the final gate.

## When the generated reference changes

The API, CLI, configuration, issue-code and PMU reference, and the Home
catalogue, are committed so that a change to them shows up in review. They are
a function of the documented content only: they record no commit sha, no git
ref and no hash of `src/helia_profiler`. Regenerate with `npm run prepare:docs`
and commit the result when a change touches what they document (a public
symbol or docstring, a command or option, a config field, an issue code, a PMU
counter, a board or engine, the package version, or a locked typer, click or
pydantic version). A change under `src/` that leaves all of that alone
regenerates to identical bytes, so there is nothing to commit and no reason for
open pull requests to conflict on these files. Line numbers are documented
content too: moving a documented definition moves its source link.

`npm run check:reference` is the gate. It regenerates into a scratch directory
from `HEAD` and requires the committed files to match byte for byte, which is
what ties them to the source. It also fails any committed file that carries an
absolute path, a ref, the source tree, or a 40-hex hash the compatibility
baseline does not declare.

Provenance is added at build time. `scripts/build-info.mjs` records the commit,
ref and `src/helia_profiler` tree in `build-info.json` (not committed), and
`src/integrations/source-ref.mjs` substitutes the `__DOCS_SOURCE_REF__` and
`__DOCS_SOURCE_TREE__` placeholders in the built site, so each published page
still names the tree it documents and links source at the right ref.
Browser tests serve the built site on port 8874 in their own process. They check
content-route overflow, example filtering, keyboard tabs, site search and the
example index without JavaScript. Review representative screenshots in light and
dark themes as well; these checks do not establish hardware correctness.

## Publish

`.github/workflows/docs.yml` builds and validates on pull requests. After merge,
a push to `main` that touches `astro-site/` or the docs workflow deploys the tested
artifact to GitHub Pages. `publish.yml` also calls this workflow after package
publishing. Documentation-only changes do not need a package version bump.

For an explicit docs refresh, run **Documentation site** with **Run workflow**
from `main`, leaving `source_ref` as `main` unless intentionally publishing a tag
or commit. The deploy guard refuses an older artifact when newer documentation
is already live. GitHub Pages must use **GitHub Actions** as its source.

After deployment, verify the workflow's deploy job, the public `build-info.json`
source commit, a content page, site search, a legacy redirect and the Markdown
rendition of the changed page. A successful local build is not a deployment.
