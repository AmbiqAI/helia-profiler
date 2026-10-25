# hpx run

Capture once from a run directory on a board you name

## hpx run

```bash
hpx run [OPTIONS] DIRECTORY
```

Capture once from a run directory on a board you name

Defined in `src/helia_profiler/cli/run_app.py` line 280.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `directory` | `Path` |  | Run directory written by hpx build, or its build.json Required. |

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--settle-seconds` | `float` | 60 | Bound on each wait for the board to start and to finish, in seconds (more than 0, at most 300) |
| `--evidence-dir` | `Optional[Path]` |  | New directory inside DIR for the capture's evidence and its saved result.json (default: DIR/evidence-<N>) |
| `--verbose, -v` | `int` | 0 | Increase verbosity |

### output verification

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--expect` | `host-litert \| none` |  | Reference outputs: host-litert runs the model on the host (default), none skips verification |
| `--tolerance` | `float` | 0 | Largest accepted absolute error per element, in stored units |
| `--reference` | `Optional[list[Path]]` |  | Reference bytes for one output; repeat once per output, in model order. Replaces --expect. Repeatable. |

### target hardware

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--board` | `str` |  | The board wired to the probe; it must be the build's board Required. |
| `--jlink-serial` | `str` |  | J-Link probe serial number Required. |

### Examples

```text
The operator vouches for the physical board behind the probe, as hpx profile assumes: --board names the board wired to the probe that --jlink-serial names, and the operator holds it for the capture. A --board other than the build's board is refused before any device is touched; HPX cannot check the physical board itself.

Each capture writes its evidence and its saved run result, result.json, to a fresh directory. Prints the outcome, the result's path, identity, verification and timing as JSON. Exit status: 0 when the capture succeeds and its outputs match, 1 on a refusal or a failed capture, 3 when the outputs do not match.

Example:

  hpx run runs/kws --board apollo510_evb --jlink-serial 1160002255
```

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__` with typer 0.27.3 and click 8.3.3.
