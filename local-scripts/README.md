# `local-scripts/` — developer-local runners (untracked)

Drop one-off Python/shell runners here when they:

- hardcode paths into your local checkout (e.g. `~/Ambiq/engines/helia-aot`),
- pin engine SHAs or branches for an A/B experiment,
- orchestrate `hpx profile` over your private model fixtures.

Everything in this folder is `.gitignore`d *except* this README, so each
clone starts empty.  Promote a script into [`scripts/`](../scripts/) (and
track it) only once it works on a clean checkout with no machine-specific
assumptions.

See also [`local-experiments/`](../local-experiments/) for sweep outputs,
CSVs, and other artifacts these scripts produce.
