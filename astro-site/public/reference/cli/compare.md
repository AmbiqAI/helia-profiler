# hpx compare

Compare two hpx result directories

## hpx compare

```bash
hpx compare [OPTIONS] BASELINE CANDIDATE
```

Compare two hpx result directories

Defined in `src/helia_profiler/cli/app.py` line 625.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `baseline` | `Path` |  | Baseline hpx result directory Required. |
| `candidate` | `Path` |  | Candidate hpx result directory Required. |

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--output-dir` | `Optional[Path]` |  | Write comparison artifacts to this directory |
| `--profile` | `Optional[Path]` |  | Versioned JSON comparison profile for regression verdicts |
| `--validation` | `bool` | false | Compare portable validation bundles instead of profile runs |
| `--top-layers` | `int` | 10 | Number of layer deltas to show in terminal output (default: 10) |

### Examples

```text
Examples:

  hpx compare results/rt_gcc results/rt_atfe

  hpx compare results/rt results/aot --output-dir results/rt_vs_aot

  hpx compare results/baseline-validation results/candidate-validation --validation --output-dir results/validation-compare
```

Generated from the `src/helia_profiler` tree `d8fc7a994d8f2b900482cdc6119d92751a2b7315` with typer 0.26.8 and click 8.3.3.
