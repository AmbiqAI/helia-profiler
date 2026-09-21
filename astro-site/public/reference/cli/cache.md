# hpx cache

Manage hpx/nsx caches

## hpx cache

```bash
hpx cache COMMAND [ARGS]...
```

Manage hpx/nsx caches

Defined in `src/helia_profiler/cli/app.py` line 696.

### Examples

```text
Manage local caches used by hpx and its nsx dependency:

  hpx cache purge      Remove all cached data (module artifacts,

                       git-artifact hashes, resolved refs,

                       generated workspaces).

                       Forces fresh network

                       fetches on next run.

  hpx cache info       Show cache location and size.
```

## hpx cache info

```bash
hpx cache info
```

Show cache location and disk usage

Defined in `src/helia_profiler/cli/app.py` line 710.

## hpx cache purge

```bash
hpx cache purge
```

Remove all NSX caches and HPX workspaces

Defined in `src/helia_profiler/cli/app.py` line 703.

Generated from the `src/helia_profiler` tree `411e58fd9f2f268366576b621932ae2bc4f2f0c2` with typer 0.26.8 and click 8.3.3.
