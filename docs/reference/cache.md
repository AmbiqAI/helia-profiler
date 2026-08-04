# `hpx cache`

Manage local caches used by hpx and its NSX build dependency.

## Synopsis

```bash
hpx cache info
hpx cache purge
```

## Description

hpx caches NSX module artifacts, git-artifact content hashes, resolved refs,
and generated firmware workspaces between runs so repeat profiles avoid
network fetches and full rebuilds.

- `hpx cache info` — show the cache location and disk usage.
- `hpx cache purge` — remove all NSX persistent cache items (module artifacts,
  legacy/v1/v2 git-artifact hash files and their lock sidecars, and resolved
  refs) through the neuralSPOT-X cache API, plus generated HPX workspaces.
  Unrelated files under `NSX_CACHE_DIR` are preserved. The next run re-fetches,
  re-hashes, and rebuilds everything from scratch.

## When to purge

- To force a fresh resolve of NSX module dependencies (e.g. to pick up
  a fix newly merged to a tracked branch).
- To reclaim disk space.
- To rule out stale build state when debugging.

For fast repeat builds with a verified, frozen module tree, see the
`--frozen` flag on [`hpx profile`](profile.md).
