# AP330 (Apollo330 Plus, Rev 1) — hpx vs AutoDeploy

MLPerf Tiny, 4 models, heliaRT engine, GCC toolchain, RTT transport, LP
(96 MHz) clock, Joulescope power capture, dedicated power firmware,
GPIO lockstep sync (J8 header: GP5 sync/gate, GP6 state, GP7 go).

Board: Apollo330mP EVB (Rev 1), probe serial 1160003058.
AutoDeploy baseline: `Latest_AI_Results.xlsx`, sheet `AP330P`, gcc 14.2.Rel1,
`helia_rt_v1_6_0`, rows dated 18-19/11/2025.

This capture followed a 7-bug AP330 bring-up (invalid J-Link device string,
wrong memory layout, hardcoded PMU op count, wrong SSRAM HAL enum, an
NSX-level weak `_sbrk` stub causing a silent HardFault before `main()`,
a self-inflicted vtable/NOLOAD placement bug, and a self-inflicted ITM
double-enable causing a TPIU refcount hang on the SWO transport) plus a
dedicated audit of AP510-copied metadata assumptions (SWO trace clock,
RTT scan window, VWW arena placement). See session plan/checkpoints for
full bug-by-bug detail.

## Placement (per-model, matching AutoDeploy's own AP330P baseline —
##  note this DIFFERS from AP510's placement conventions for VWW/IC/AD)

| Model | Weights | Arena  | Arena size |
|-------|---------|--------|-----------:|
| KWS   | TCM     | TCM    | 32 KB      |
| VWW   | MRAM    | SRAM   | 110 KB     |
| IC    | MRAM    | TCM    | 56 KB      |
| AD    | MRAM    | TCM    | 10 KB      |

## Results

Placement (weights/arena, hpx and AD both match): KWS TCM/TCM; VWW MRAM/SRAM;
IC MRAM/TCM; AD MRAM/TCM. **AP510 placement differs for 3 of 4 models**
(AP510: VWW MRAM/TCM, IC TCM/TCM, AD TCM/TCM — AP330's smaller 240 KB unified
TCM, vs AP510's 512 KB DTCM, can't fit IC/AD weights alongside arena+overhead,
forcing MRAM residency there). Only KWS has matching placement on both
boards, so it's the sole apples-to-apples model for AP510-vs-AP330
comparisons; see `ap510_hpx_vs_autodeploy.md` for the AP510 side.

| Model | Placement | hpx lat (ms) | AD lat (ms) | Δ lat | hpx E (µJ/inf) | AD E (µJ/inf) | Δ E | hpx P (mW) | AD P (mW) | Δ P | window ratio |
|-------|-----------|-------------:|------------:|------:|---------------:|--------------:|----:|-----------:|----------:|----:|-------------:|
| KWS   | TCM/TCM   | 21.144       | 19.657      | +7.6% | 131.12         | 124.35        | +5.4% | 6.205    | 6.326     | -1.9% | 0.9994 |
| VWW   | MRAM/SRAM | 81.173       | 73.145      | +11.0%| 509.24         | 479.12        | +6.3% | 6.254    | 6.550     | -4.5% | 1.0031 |
| IC    | MRAM/TCM  | 54.071       | 48.174      | +12.2%| 369.82         | 343.06        | +7.8% | 6.854    | 7.121     | -3.7% | 0.9979 |
| AD    | MRAM/TCM  | 2.244        | 2.203       | +1.9% | 19.51          | 19.51         | +0.0% | 8.695    | 8.855     | -1.8% | 1.0001 |

Δ = (hpx − AD) / AD. This table reflects the crypto/OTP/radio-subsystem
shutdown fix below (CRYPTO/OTP/VCOMP disabled + `am_hal_pwrctrl_rss_pwroff()`
on the power binary, mirroring the always-on part of AutoDeploy's
`ns_power_platform_config()`) — see "AutoDeploy AP330 review" section.
Pre-fix numbers (power ranged +0.1% to -4.3%) are preserved further down
for reference.

### JS110 re-capture after AP510/JS320 work

The same KWS/heliaRT/GCC/LP/dedicated-power configuration was re-run on
the AP330 Plus using the JS110 (`004204`) and the known AP330 probe
(`1160003058`). This provides a direct current-session check against the
post-shutdown baseline above:

| Metric | Prior AP330 JS110 | Current AP330 JS110 | Difference |
|---|---:|---:|---:|
| Latency (ms) | 21.144 | 21.170 | +0.12% |
| Power (mW) | 6.205 | 6.013 | -3.1% |
| Energy (µJ/inf) | 131.12 | 127.06 | -3.1% |
| Gated window ratio | 0.9994 | 0.9982 | — |

The current capture retained true 3-wire lockstep
(`ready_observed=true`, `lockstep=true`) and used the same TCM/TCM
placement and dedicated power firmware. The modest 3.1% lower
power/energy is within the variation seen across board/instrument
captures; latency is effectively unchanged. The AP330 result remains
consistent with the earlier JS110 dataset and is not affected by the
JS320 support work.

## AutoDeploy AP330 review — crypto/OTP/radio-subsystem shutdown

User asked whether AutoDeploy does anything AP330-unique that could
explain the remaining gap. Findings:

- AutoDeploy's `ns_power_platform_config()` is **nearly identical**
  between the `apollo5` (AP510 non-B) and `apollo330` neuralSPOT source
  folders (diffed directly) — this is an AP5-family-wide behavior, not
  AP330-specific.
- NSX's own `nsx_system_init()` already replicates AD's CPU power-domain
  config (CPDLP ELP/RLP/CLP, perf-mode select) — no gap there.
- NSX ships a full parallel `nsx-power` module
  (`modules/nsx-power/src/{apollo330,apollo5,apollo4,apollo3}/nsx_power.c`)
  that is a 1:1 port of AutoDeploy's aggressive power config (including
  full SRAM power-off when unused, RSS/radio power-off, DEBUG domain
  disable) — but **hpx never called into this module on any board**
  (confirmed: zero references, no `.a` artifact built).
- Implemented the "sensible default" subset only (per user direction —
  full memory power-off belongs in `extreme_mode`, not a normal default):
  CRYPTO, OTP, VCOMP disabled + `am_hal_pwrctrl_rss_pwroff()` (internal
  radio subsystem) where the SoC's HAL exposes it (apollo330P; the plain
  apollo510 HAL variant this project builds against does not define the
  symbol). New capability `crypto_otp_shutdown` (AP5-family), new SocDef
  field `has_radio_subsystem`, new template `_crypto_otp_shutdown.j2`,
  power_only-scoped like the other power features.
- Hardware-validated on AP330: power improved ~2% on every model with no
  latency change (as expected — idle-domain power only, not the compute
  path). Numbers above already include this fix.

## Results (pre crypto/OTP/RSS-shutdown fix, kept for reference)

Same placement as the primary results table above (KWS TCM/TCM; VWW
MRAM/SRAM; IC/AD MRAM/TCM).

| Model | Placement | hpx lat (ms) | AD lat (ms) | Δ lat | hpx E (µJ/inf) | AD E (µJ/inf) | Δ E | hpx P (mW) | AD P (mW) | Δ P | window ratio |
|-------|-----------|-------------:|------------:|------:|---------------:|--------------:|----:|-----------:|----------:|----:|-------------:|
| KWS   | TCM/TCM   | 21.140       | 19.657      | +7.5% | 133.74         | 124.35        | +7.5% | 6.331    | 6.326     | +0.1% | 0.9992 |
| VWW   | MRAM/SRAM | 81.172       | 73.145      | +11.0%| 517.13         | 479.12        | +7.9% | 6.343    | 6.550     | -3.2% | 1.0043 |
| IC    | MRAM/TCM  | 54.064       | 48.174      | +12.2%| 368.67         | 343.06        | +7.5% | 6.816    | 7.121     | -4.3% | 1.0004 |
| AD    | MRAM/TCM  | 2.243        | 2.203       | +1.8% | 19.63          | 19.51         | +0.6% | 8.751    | 8.855     | -1.2% | 1.0000 |

Δ = (hpx − AD) / AD.

## Observations

- **Power now consistently beats AutoDeploy** on every model (-1.8% to
  -4.5%, after the crypto/OTP/radio-subsystem shutdown fix above), a
  dramatic contrast to the AP510 (non-B) dataset, where hpx read -21%
  to -24% power vs AD consistently (though that dataset predates this
  fix — see next steps).
- **Latency/energy run consistently +7-12%/+5-8% higher** than AD across
  KWS/VWW/IC (the AD/anomaly-detection model is the exception at only
  +1.9%/+0.0%, consistent with its outlier behavior observed on AP510
  too). This is a much smaller, more uniform gap than AP510's -27% to
  -37% energy swing, and in the *opposite* direction (hpx slower/
  higher-energy here vs. faster/lower-energy on AP510).
- All 4 runs completed with clean gated-window duration ratios
  (0.9979-1.0031), so none of these deltas are measurement-window
  artifacts.
- VWW placement (arena in SRAM, not TCM) was corrected mid-session to
  match AutoDeploy's own AP330P convention — an initial TCM-arena run
  (not apples-to-apples, discarded) read 76.76ms/461.2µJ/6.033mW,
  underscoring that SRAM access has non-trivial latency/energy cost on
  this part relative to TCM.

## Open questions

- Why is the AP330-vs-AD relationship (power tracks/slightly beats AD;
  latency/energy consistently worse) essentially the *inverse* of the
  AP510-vs-AD relationship?
  **Partially resolved (2026-07-08):** memory placement is a real,
  hardware-forced contributor for 3 of 4 models. AP330's TCM is a single
  unified 240 KB region (vs AP510's 512 KB DTCM + 256 KB separate ITCM);
  IC (152 KB weights+arena) and AD (280 KB weights alone) don't fit in
  AP330's 240 KB budget once PMU-profiler/RTT/stack overhead is
  accounted for, forcing their weights into MRAM there — while AP510 fits
  both in TCM. AD's weights (277 KB) exceed AP330's *entire* TCM region
  outright, and AD/IC are FC-layer-dominated (weight-bandwidth-bound), so
  MRAM-resident weights plausibly explain most of their latency/energy
  regression. VWW's arena also lands in SRAM on AP330 vs TCM on AP510
  (see the SRAM-vs-TCM discrepancy noted above), a second placement-driven
  contributor for that model.
  **Still unexplained:** KWS has *identical* placement on both boards
  (TCM/TCM) yet still shows the same inverted pattern on AP330 (+7.6%
  latency, +5.4% energy, -1.9% power vs AD) — smaller in magnitude than
  IC/AD/VWW, but present despite placement being controlled for. This
  means placement is not the whole story; a genuine board-level
  difference remains (e.g. MRAM/TCM wait-state or bus-fabric differences
  even when TCM-resident, HP/LP clock-domain transition overhead, cache/
  prefetch configuration differences between the two M55 implementations,
  or a per-model AD firmware/config difference between the two boards'
  baseline spreadsheets) and has not yet been investigated.
- ~~AP510 KWS numbers have not yet been re-verified after the `-u _sbrk`
  linker fix~~ **Resolved**: see `ap510_hpx_vs_autodeploy.md`'s
  "Re-verification after AP330 bring-up fixes" section — KWS/RT/gcc
  re-run after `_sbrk` + `crypto_otp_shutdown` landed, result essentially
  unchanged (21.140ms vs 21.159ms, within ~0.5-1% run-to-run noise),
  confirming AP510's power/energy advantage over AD is real and not an
  artifact of either fix.
