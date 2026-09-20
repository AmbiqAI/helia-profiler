/*
 * The board and engine lines Home renders, built from the generated catalog.
 *
 * A module rather than `export const` blocks in index.mdx: the Markdown
 * rendition of a page reproduces the body of a multi-line MDX export as if it
 * were content, and the rendition is the copy an agent reads. Single-line
 * imports are all the page needs to carry.
 *
 * Identity comes from `src/data/catalog.json`, which build-catalog.mjs reads
 * out of src/helia_profiler. The wording here is quoted from the docs pages
 * named beside it, because the registry does not carry it.
 */
import catalog from '../data/catalog.json';

/**
 * Boards the docs label experimental, keyed by id.
 *
 * `docs/guide/boards.md:19`. Not derived from the registry's `isFpga`: that
 * field says the target is an FPGA carrier, which is not the same claim as the
 * support status, and a future FPGA board must not inherit a label no page has
 * given it.
 */
export const EXPERIMENTAL_BOARDS = new Set(['atomiq110_fpga_turbo']);

/** Display name and "best for" per engine id, `docs/guide/engines.md:12-15`. */
export const ENGINE_LINES = {
  tflm: 'Vanilla TFLM (tflm): reference or CMSIS-NN interpreter baseline',
  'helia-rt': 'heliaRT (helia-rt): Ambiq-optimized interpreter performance',
  'helia-aot':
    'heliaAOT (helia-aot): ahead-of-time compilation and fine-grained placement',
  executorch: 'ExecuTorch (executorch): Cortex-M PTE programs and CMSIS-NN kernels',
};

/* An engine with no line yet still appears, by id. Dropping it silently would
 * let the registry grow past the page without the artifact check noticing. */
export const engineItems = catalog.engines.map(
  (engine) => ENGINE_LINES[engine.id] ?? engine.id,
);

const boardsIn = (channel, experimental) =>
  catalog.boards
    .filter(
      (board) =>
        board.channel === channel &&
        EXPERIMENTAL_BOARDS.has(board.id) === experimental,
    )
    .map((board) => board.id);

/** Board ids grouped by the registry's own channel field, empty groups dropped. */
export const boardItems = [
  ['Stable channel', boardsIn('stable', false)],
  ['Preview channel', boardsIn('preview', false)],
  ['Preview channel, experimental', boardsIn('preview', true)],
]
  .filter(([, ids]) => ids.length > 0)
  .map(([label, ids]) => `${label}: ${ids.join(', ')}`);

export const counts = catalog.counts;
