# local-configs/

Scratch space for developer-local heliaPROFILER configs used during
hardware bring-up, manual validation, and ad-hoc profiling runs.

The contents of this folder (other than this `README.md`) are
**git-ignored** — drop any `.yml` you want to keep around without
polluting the repo.

For shared / reference configs, use [`configs/`](../configs/) or
[`examples/`](../examples/) instead.

## Usage

```bash
hpx profile -c local-configs/my_scratch.yml
```
