/**
 * The per-page budget every published page must meet: 250,000 B of HTML and
 * 40,000 B gzipped. The reference generator enforces the same numbers on its
 * own pages; this is the site-wide version for authored content.
 */
import zlib from "node:zlib";

export const HTML_BUDGET = 250_000;
export const GZIP_BUDGET = 40_000;

export function measure(html) {
  const bytes = Buffer.byteLength(html, "utf8");
  const gzip = zlib.gzipSync(html, { level: 9 }).length;
  return { bytes, gzip };
}

export function overBudget({ bytes, gzip }) {
  const reasons = [];
  if (bytes > HTML_BUDGET) reasons.push(`${bytes} B HTML > ${HTML_BUDGET}`);
  if (gzip > GZIP_BUDGET) reasons.push(`${gzip} B gzip > ${GZIP_BUDGET}`);
  return reasons;
}
