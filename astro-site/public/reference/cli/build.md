# hpx build

Build a frozen plan for fixed inputs into a run directory

## hpx build

```bash
hpx build [OPTIONS] PLAN
```

Build a frozen plan for fixed inputs into a run directory

Defined in `src/helia_profiler/cli/run_app.py` line 211.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `plan` | `Path` |  | Plan written by hpx freeze Required. |

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--out` | `Path` |  | Run directory to write Required. |
| `--input` | `Optional[list[Path]]` |  | Raw bytes for one model input; repeat once per input, in model order (default: zero-filled inputs) Repeatable. |
| `--lifecycle` | `carry \| single` | carry | carry keeps the plan's iterations and warmups and carries model state; single times one invocation per boot |
| `--timing-scope` | `invoke_only \| restore_and_invoke` | invoke_only | invoke_only times each invocation; restore_and_invoke times the loop, input restoration included |
| `--model` | `Optional[Path]` |  | The plan's model, when it is no longer at the path the plan records |
| `--work-dir` | `Optional[Path]` |  | Firmware build directory inside OUT (default: OUT/work); the saved build records its files relative to OUT |
| `--verbose, -v` | `int` | 0 | Increase verbosity |

### Examples

```text
Writes OUT/build.json, the model and one input_<index>.bin per model input under OUT/inputs, and the firmware under OUT/work. OUT must be new or empty, so a failed build never touches an earlier one. heliaRT and LiteRT link the runtime that hpx runtimes prepare cached under the HPX cache. Prints the saved build's path, inputs and identity as JSON.

Example:

  hpx build plan.json --out runs/kws --input kws_input.bin
```

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__` with typer 0.27.3 and click 8.3.3.
