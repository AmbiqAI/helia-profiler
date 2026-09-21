Every stable code a run or a comparison can report, generated from the
registry in `src/helia_profiler/results/issues.py` by
`tools/docs/extract_issues.py` at the source tree this site was built from.
Run-validity codes appear in `summary.json` and `result_manifest.json`
under `issues[]` with a severity (`error` makes the run `invalid`,
`warning` makes it `degraded`); comparability codes appear in an
`hpx compare` report and say why a delta was withheld. The code strings
are the contract; the messages may change. [Troubleshooting](../../guide/troubleshooting/)
and [Analysis and comparison](../../guide/analysis-comparison/) explain
the fixes.
