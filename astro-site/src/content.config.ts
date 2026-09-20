import { defineCollection } from 'astro:content';
import { docsLoader } from '@astrojs/starlight/loaders';
import { docsSchema } from '@astrojs/starlight/schema';
import { heliaFrontmatterSchema } from '@ambiqai/helia-ui/starlight';

export const collections = {
  docs: defineCollection({
    loader: docsLoader(),
    /* Home opens on the `Hero` part, which carries the page's own `h1`. The
     * extension is what lets its frontmatter ask the shell not to draw a
     * second one. */
    schema: docsSchema({ extend: heliaFrontmatterSchema }),
  }),
};
