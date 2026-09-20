// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import { heliaStarlight } from '@ambiqai/helia-ui/starlight';
import redirects from './src/data/redirects.json' with { type: 'json' };

const site = 'https://ambiqai.github.io';
const base = '/helia-profiler';
const basePath = `${base}/`;

export default defineConfig({
  site,
  base,
  /* Legacy MkDocs routes. `served` entries are already real pages at the same
   * path, so they must stay out of this map or Astro sees a route collision. */
  redirects: redirects.redirects,
  integrations: [
    starlight({
      title: 'heliaPROFILER',
      description:
        'Profile LiteRT and ExecuTorch models on Ambiq Apollo hardware.',
      favicon: '/heliaprofiler-icon.png',
      plugins: [
        heliaStarlight({
          accent: 'helia-profiler',
          sidebar: 'always',
          header: {
            title: 'heliaPROFILER',
            hub: {
              label: 'HELIA',
              href: 'https://ambiqai.github.io/helia-developer-hub/',
            },
          },
          sections: [
            { label: 'Home', href: basePath, sidebar: false },
            {
              label: 'Getting started',
              href: `${basePath}getting-started/`,
              sidebar: [{ label: 'Overview', slug: 'getting-started' }],
            },
            {
              label: 'User guide',
              href: `${basePath}guide/`,
              sidebar: [{ label: 'Overview', slug: 'guide' }],
            },
            {
              label: 'Examples',
              href: `${basePath}examples/`,
              sidebar: [{ label: 'Overview', slug: 'examples' }],
            },
            {
              label: 'Reference',
              href: `${basePath}reference/`,
              sidebar: [{ label: 'Overview', slug: 'reference' }],
            },
          ],
          discoverability: {
            ogImage: true,
            jsonLd: true,
            markdown: true,
            llms: true,
          },
          footer: {
            links: [
              { label: 'Getting started', href: `${basePath}getting-started/` },
              { label: 'User guide', href: `${basePath}guide/` },
              { label: 'Examples', href: `${basePath}examples/` },
              { label: 'Reference', href: `${basePath}reference/` },
              {
                label: 'GitHub',
                href: 'https://github.com/AmbiqAI/helia-profiler',
              },
            ],
            tagline: 'Part of the Ambiq HELIA developer ecosystem.',
            logo: 'ambiq',
          },
        }),
      ],
    }),
  ],
});
