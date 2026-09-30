# hpx cache

Manage hpx/nsx caches

## hpx cache

```bash
hpx cache COMMAND [ARGS]...
```

Manage hpx/nsx caches

Defined in `src/helia_profiler/cli/app.py` line 678.

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

Defined in `src/helia_profiler/cli/app.py` line 692.

## hpx cache purge

```bash
hpx cache purge
```

Remove all NSX caches and HPX workspaces

Defined in `src/helia_profiler/cli/app.py` line 685.

Generated from the `src/helia_profiler` tree `ab75e9a0e2ab3a0fe548514c535d29e150f2bcec` with typer 0.26.8 and click 8.3.3.
