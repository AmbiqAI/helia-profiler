// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import { heliaStarlight } from '@ambiqai/helia-ui/starlight';
import apiSidebar from './src/generated/api-sidebar.json' with { type: 'json' };
import variant from './src/data/variant.json' with { type: 'json' };

const site = 'https://ambiqai.github.io';
const base = '/helia-profiler';
const basePath = `${base}/`;

/* The React and Tailwind packages are only installed in the react variant, so
 * they are resolved at config time rather than imported at the top. The
 * specifier is a variable on purpose: a literal one makes `astro check` demand
 * the types in the baseline, where the packages are not installed. See
 * scripts/spike-variant.mjs. */
/** @param {string} specifier */
const load = (specifier) => import(/* @vite-ignore */ specifier);
const react = variant.react ? (await load('@astrojs/react')).default : null;
const tailwind = variant.react
  ? (await load('@tailwindcss/vite')).default
  : null;

export default defineConfig({
  site,
  base,
  integrations: [
    starlight({
      title: 'heliaPROFILER',
      description:
        'Profile LiteRT and ExecuTorch models on Ambiq Apollo hardware.',
      customCss: variant.react ? ['./src/styles/tailwind.css'] : [],
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
              sidebar: [
                { label: 'Overview', slug: 'reference' },
                /* pyref's --sidebar writes one group object; Starlight's
                 * `items` wants an array. */
                {
                  label: 'Python API',
                  collapsed: false,
                  items: Array.isArray(apiSidebar) ? apiSidebar : [apiSidebar],
                },
              ],
            },
          ],
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
    ...(react ? [react()] : []),
  ],
  vite: { plugins: tailwind ? [tailwind()] : [] },
});
