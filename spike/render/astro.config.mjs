import { defineConfig } from 'astro/config';
import expressiveCode from 'astro-expressive-code';

// helia-ui's CodeBlock (pulled in by RefSymbol for the usage line) renders
// through expressive-code, so the integration has to be registered here.
export default defineConfig({
  base: '/reference',
  build: { format: 'directory' },
  integrations: [expressiveCode({ themes: ['github-dark'] })],
});
