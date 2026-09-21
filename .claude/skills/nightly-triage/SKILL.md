---
name: nightly-triage
description: Diagnose a red Hardware Validation nightly (or any hardware-validation run) — which legs failed, on which bench, bench fault vs code regression vs known issue. Trigger on "why is the nightly red", "triage the nightly", "/nightly-triage [run_id]". NOT for host CI (ci.yml) failures or for running hpx on a board yourself.
---

One job per board, but the job lands on whichever bench carries the board's
label, so a leg is only meaningful as (board, runner, failed step). The goal
is a verdict per red leg with evidence, not a fix: report, then let Nishant
decide whether to rerun, file, or dig.

1. `legs.sh` (this dir) prints the leg table for the last five nightlies, or
   `legs.sh <run_id>` for one. Same board red on two different runners →
   code. Red on one runner while its sibling bench passes → bench. Note the
   first night each leg went red; compare against `git log origin/main` for
   that day.
2. Read only the failed step: fetch the job log with
   `gh api repos/AmbiqAI/helia-profiler/actions/jobs/<job_id>/logs`, then
   cut between the `##[group]Run` marker of the failed step and its
   `##[error]`. Which step failed is most of the answer:
   - guard / `hpx probes match` → bench (probe unpowered, held, wrong serial).
   - `hpx doctor` or a missing tool → runner contract drift; the runner
     services expose only the fleet's declared package set on PATH.
   - Real-toolchain compile gate (compile_hw) → code or template context;
     the host Tier 1 gate does not cover the same render inputs, so a
     template variable can slip through to here.
   - Run hardware validation → open the bundle (step 3).
3. Bundles are small (≈1 MB, `work/` is excluded). `gh run download <run_id>
   -n hardware-validation-<run_id>-<board>`. Group failed cases by the first
   `Error:` line of `<case>/hpx_stderr.log`; the pattern across cases is the
   verdict (every `-power` case failing at `ensure_board_powered` = Joulescope,
   not firmware).
4. Before diagnosing, check whether it is already known: recalled memory for
   bench faults and pending fixes, then `gh issue list` / `gh pr list` for the
   board, step, or error text.

Report: one paragraph per red leg — step, runner, verdict, the evidence line,
known or new, suggested action. Mark each claim measured or inferred. Do not
rerun jobs, file issues, or touch benches from here.
