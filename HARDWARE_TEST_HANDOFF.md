# Remote Hardware Test Handoff

## Purpose

Validate the hardware-facing changes delivered in commit
`bbeb123fe10bd746d830f7992dafd2318493cda5`
(`fix(power): harden capture and deployment lifecycle`) on the remote bench.
Run from the repository root through the project environment:

```bash
uv sync
uv run hpx --help
```

Do not weaken power integrity checks, bypass terminal validation, or change the
host-selected inference count to make a run pass. Preserve every failed run's
artifact directory before retrying.

## Changes Under Test

- The host records the `go_signaled` phase before asserting GO, closing a race
  where a fast GATE rising edge could be rejected as premature.
- Profile and dedicated-power flashing now share one bounded Joulescope rail
  power-cycle recovery path.
- Dedicated power firmware retries flashing once only when rail recovery
  succeeds.
- NSX no longer redirects process-global stdout/stderr file descriptors.
- Durable human results use stdout; progress, logs, warnings, and errors use
  stderr.
- J-Link Commander discovery supports `JLinkExe`, `JLink.exe`, `JLINK_PATH`,
  and common macOS/Windows install locations.
- CI now covers Python 3.11 and 3.12.

Local software validation before this handoff was clean:

```text
1166 passed, 1 skipped, 480 deselected
ruff check .                 passed
mkdocs build --strict        passed
package layout tests         passed
```

Physical validation was not run locally because neither the J-Link nor JS320
was visible to that host.

## Required Bench Coverage

Minimum target families:

| Family | Preferred board ID | Power sync wiring |
| --- | --- | --- |
| AP3 | `apollo3p_evb` | GATE GPIO 26, STATE GPIO 24, GO GPIO 25 |
| AP4 | `apollo4p_evb` | GATE GPIO 22, STATE GPIO 23, GO GPIO 24 |
| AP5 | `apollo510_evb` | GATE GPIO 29, STATE GPIO 36, GO GPIO 14 |

Apollo330P is also AP5-family coverage and may be added as
`apollo330mP_evb`, but it does not replace the Apollo510 regression because
Apollo510 was the last locally proven power target.

Power instruments:

- JS110
- JS320

`hpx validate` currently auto-selects the Joulescope and has no power-serial
option. Connect or expose exactly one Joulescope while running each power case.
Record its model and serial alongside the output directory. Do not run with a
JS110 and JS320 simultaneously visible.

## 1. Record Provenance

Before changing the bench, capture the host and checkout state:

```bash
git fetch origin
git checkout main
git pull --ff-only
git rev-parse HEAD
uv run python --version
uv run hpx doctor
uv run hpx probes list --json
uv run hpx ports list --all --json
```

The tested checkout must contain `bbeb123` (ideally current `origin/main`). If
Commander is installed outside normal locations, set `JLINK_PATH` to the full
`JLinkExe` or `JLink.exe` path.

Create a durable root for this campaign:

```bash
export HPX_HW_OUT="$PWD/results/remote-hardware-bbeb123"
mkdir -p "$HPX_HW_OUT"
```

Record the J-Link serial for each board. The commands below use placeholders
such as `<AP510_JLINK>`; replace them with actual serials.

## 2. Baseline Without Power

Run one no-power smoke before attaching the Joulescope wiring. Preview every
case first and confirm it resolves to KWS, heliaRT, GCC, RTT, and auto memory.

```bash
uv run hpx validate --suite smoke --boards apollo510_evb \
  --jlink-serials apollo510_evb=<AP510_JLINK> --power off --list

uv run hpx validate --suite smoke --boards apollo510_evb \
  --jlink-serials apollo510_evb=<AP510_JLINK> --power off \
  --output-dir "$HPX_HW_OUT/ap510-no-power"
```

Repeat for `apollo3p_evb` and `apollo4p_evb`, using their matching probe
serials and distinct output directories. Each report must contain one passing
case before proceeding to power.

## 3. Apollo510 Power Regression

Test JS320 first because it was used for the last known-good local result, then
repeat with JS110. For each instrument:

1. Make only that Joulescope visible to the host.
2. Connect its power output and GATE/STATE/GO lines using the board table.
3. Enable the target rail if required by the bench.
4. Confirm the pinned J-Link appears after rail power-up.
5. Run the command with a fresh output directory.

Optional explicit rail/probe check, with the instrument's real serial:

```bash
uv run hpx power-on --driver joulescope --power-serial <JS_SERIAL>
uv run hpx probes match --board apollo510_evb \
  --jlink-serial <AP510_JLINK>
```

JS320 run:

```bash
uv run hpx validate --suite smoke --boards apollo510_evb \
  --jlink-serials apollo510_evb=<AP510_JLINK> --power on \
  --output-dir "$HPX_HW_OUT/ap510-js320" -v
```

JS110 run:

```bash
uv run hpx validate --suite smoke --boards apollo510_evb \
  --jlink-serials apollo510_evb=<AP510_JLINK> --power on \
  --output-dir "$HPX_HW_OUT/ap510-js110" -v
```

After both single passes succeed, run `--repeat 3` for Apollo510 with each
instrument. Use new `ap510-js320-repeat3` and `ap510-js110-repeat3` output
directories so the single-pass evidence remains intact.

## 4. AP3/AP4/AP5 Sweep

Run the same isolated-instrument power smoke for:

- `apollo3p_evb` with JS110, then JS320;
- `apollo4p_evb` with JS110, then JS320;
- `apollo510_evb` with JS110, then JS320.

Use one board per invocation and pin its J-Link serial. Example template:

```bash
uv run hpx validate --suite smoke --boards <BOARD_ID> \
  --jlink-serials <BOARD_ID>=<JLINK_SERIAL> --power on \
  --output-dir "$HPX_HW_OUT/<BOARD>-<JS_MODEL>" -v
```

AP3 note: its GPIO 24/25/26 power wiring conflicts with PSRAM claims. The smoke
suite uses auto placement; do not widen AP3 power testing to PSRAM until the
smoke result and generated config have been reviewed.

Once every family/instrument smoke passes, broader model/engine/toolchain
sweeps may begin. Keep those in separate directories and always preview with
`--list` first. Do not remove the legacy `PipelineContext` mirrors during this
campaign; consolidation is intentionally deferred until cross-family evidence
exists.

## Acceptance Criteria

For every invocation:

- `validation_report.json` reports zero failures and zero skips.
- `validation_manifest.json` exists and identifies the expected commit, board,
  engine, toolchain, transport, memory request, and power mode.
- Each case publishes `summary.json`, `run_metadata.json`,
  `profile_results.csv`, `hpx_stdout.log`, `hpx_stderr.log`, and
  `hpx_profile.log`; power cases also publish `power_summary.csv`.
- Top-level run validity is `valid`.
- Power measurement scope is `gpio_gated_clean_window`.
- Dedicated power firmware is used unless the generated config explicitly
  requests shared firmware.
- `power.terminal.status` is `ok`, `error_code` is `0`, `final_phase` is
  `complete`, and both `gate_asserted` and `gate_lowered` are true.
- Host-selected N is authoritative: `power.power_plan.inference_count` is
  positive and equals `power.terminal.requested_count` and
  `power.terminal.completed_count`. Do not require N to equal the earlier
  Apollo510 observation of 237; it may legitimately vary with timing.
- `power.gate_duration_integrity` is valid and
  `gated_window_duration_suspect` is absent or false.
- Energy/current values are finite and physically plausible for the bench;
  no per-inference metric is accepted when gate integrity is invalid.
- Repeated runs complete without intermittent premature-GATE, lost-probe,
  locked-debug-domain, or dedicated-power flash failures.

Useful inspection command for a case summary:

```bash
uv run python - path/to/summary.json <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text())
power = summary.get("power", {})
print(json.dumps({"validity": summary.get("validity"), "power": power}, indent=2))
PY
```

## Failure Triage

Do not overwrite or delete a failed output directory. Record:

- board revision and board ID;
- J-Link serial;
- Joulescope model, serial, firmware, and connection topology;
- host OS, Python version, HPX commit, SEGGER version, and compiler version;
- exact command and whether the target rail was already on;
- whether failure occurred during probe resolution, profile flash, dedicated
  power flash, READY/GO/GATE synchronization, terminal collection, or report
  publication.

Inspect these first:

```text
validation_report.md
validation_manifest.json
<case>/hpx_profile.log
<case>/hpx_stdout.log
<case>/hpx_stderr.log
<case>/summary.json
<case>/run_metadata.json
<case>/power_summary.csv
```

A single successful retry does not erase a lifecycle defect. If the first
flash fails and the shared recovery succeeds, preserve logs showing both the
initial failure and recovery. If recovery fails, stop retrying that case and
report the rail/probe state. Never bypass strict manifest, terminal-count, or
gate-duration checks.

## Handoff Report

Return a compact matrix with one row per board/instrument combination:

| Board | J-Link | Joulescope | No-power | Power | Repeat 3 | N requested/completed | Gate integrity | Artifact path | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

Include failed rows, the first failing artifact directory, and the exact commit
tested. Archive or transfer the complete `$HPX_HW_OUT` directory rather than
only copying the Markdown report.
