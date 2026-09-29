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
