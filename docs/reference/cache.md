# `hpx cache`

Manage local caches used by hpx and its NSX build dependency.

## Synopsis

```bash
hpx cache info
hpx cache purge
```

## Description

hpx caches NSX module clones, resolved refs, and generated firmware
workspaces between runs so repeat profiles avoid network fetches and
full rebuilds.

- `hpx cache info` — show the cache location and disk usage.
- `hpx cache purge` — remove all cached data (module clones, resolved
  refs, generated workspaces). The next run re-fetches and rebuilds
  everything from scratch.

## When to purge

- To force a fresh resolve of NSX module dependencies (e.g. to pick up
  a fix newly merged to a tracked branch).
- To reclaim disk space.
- To rule out stale build state when debugging.

For fast repeat builds with a verified, frozen module tree, see the
`--frozen` flag on [`hpx profile`](profile.md).
