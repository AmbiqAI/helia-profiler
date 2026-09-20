/*
 * Resolve the source ref of the generated Python reference at build time.
 *
 * The committed pages and artifacts carry a placeholder where a git ref would
 * go. A committed branch name would link to that branch whatever this build is
 * of, and a committed commit sha would rewrite every generated file on every
 * change under src/. The ref is a property of the build, not of the source, so
 * it is substituted here from build-info.json, which carries the ref the
 * workflow checked out: a release tag, a branch, or main.
 *
 * This runs over the artifact rather than through a Markdown plugin because
 * Astro 7 defaults to the Sätteri processor, where remark plugins need
 * @astrojs/markdown-remark and a processor switch for the whole site. It has
 * to be the last integration: Starlight and helia-ui write the Markdown
 * renditions and the llms exports in astro:build:done too, and hooks run in
 * the order the integrations are declared.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const SOURCE_REF_TOKEN = '__DOCS_SOURCE_REF__';

const REWRITTEN = new Set(['.html', '.json', '.md', '.txt', '.xml']);

/**
 * The ref this build documents.
 *
 * build-info.mjs has already resolved it: the workflow's source_ref, else the
 * release tag, else the branch that was checked out. Recomputing a guess here
 * would send a workflow_dispatch build of docs-migration to main.
 */
export const sourceRef = (buildInfo) =>
  buildInfo?.sourceRef || buildInfo?.releaseTag || 'main';

export function readBuildInfo(siteDir) {
  const file = path.join(siteDir, 'src/data/build-info.json');
  if (!fs.existsSync(file)) {
    throw new Error(`No ${file}. It is written by npm run prepare:docs, which runs in prebuild.`);
  }
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

export function sourceRefArtifacts() {
  let siteDir;
  return {
    name: 'helia-profiler:source-ref',
    hooks: {
      'astro:config:setup': ({ config, updateConfig }) => {
        siteDir = fileURLToPath(config.root);
        /* The dev server never reaches astro:build:done, and a source link
         * reading `__DOCS_SOURCE_REF__` in a preview is a link nobody can
         * follow. Rewriting the module source covers dev; the build:done pass
         * still owns public/ and the renditions written after it. */
        updateConfig({
          vite: {
            plugins: [
              {
                name: 'helia-profiler:source-ref-mdx',
                enforce: 'pre',
                transform(code, id) {
                  if (!id.endsWith('.mdx') || !code.includes(SOURCE_REF_TOKEN)) return null;
                  const ref = sourceRef(readBuildInfo(siteDir));
                  return { code: code.replaceAll(SOURCE_REF_TOKEN, ref), map: null };
                },
              },
            ],
          },
        });
      },
      'astro:build:done': ({ dir }) => {
        const ref = sourceRef(readBuildInfo(siteDir));
        const root = fileURLToPath(dir);
        let rewritten = 0;
        const walk = (directory) => {
          for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
            const entryPath = path.join(directory, entry.name);
            if (entry.isDirectory()) {
              walk(entryPath);
              continue;
            }
            if (!REWRITTEN.has(path.extname(entry.name))) continue;
            const body = fs.readFileSync(entryPath, 'utf8');
            if (!body.includes(SOURCE_REF_TOKEN)) continue;
            fs.writeFileSync(entryPath, body.replaceAll(SOURCE_REF_TOKEN, ref), 'utf8');
            rewritten += 1;
          }
        };
        walk(root);
        console.log(`source ref: ${ref} written into ${rewritten} files.`);
      },
    },
  };
}
