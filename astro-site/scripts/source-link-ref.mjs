export function hasSourceRef(link, ref) {
  return link.startsWith(`blob/${ref}/src/`);
}
