# hpx vs AutoDeploy — Apollo510 (non-B) EVB, MLPerf Tiny

Board: `apollo510_evb` (J-Link serial `1160002204`, Cortex-M55 confirmed via
raw JLinkExe attach). Joulescope JS110, 3-wire GPIO lock-step
(sync=GPIO29, state=GPIO36, go=GPIO14), dedicated transport-free power
binary, all 4 models placed to match AutoDeploy's own memory layout
(VWW: MRAM weights/TCM arena; KWS/IC/AD: TCM/TCM).

AutoDeploy (AD) baseline source: `~/OneDrive/Ambiq/profiling/Perfresults.xlsx`,
sheet `AP510`, `ns_tflm_2025_02_14`, `apollo510_evb`, `gcc 14.2.Rel1` rows.

All hpx captures used `power.firmware: dedicated`, `power.lockstep: true`.
Handshake (`ready_observed`) and gated-window `duration_ratio` were
healthy (0.99–1.01) on every run reported here unless noted.

---

## LP clock (96 MHz) — heliaRT (gcc) vs heliaAOT (ATfE, full TCM)

heliaRT: gcc toolchain, matched AD placement per model.
heliaAOT: ATfE (LLVM-based Arm Toolchain for Embedded) toolchain, full TCM
residency (weights hydrated into DTCM rather than cold-read from MRAM;
confirmed to fit for all 4 models, e.g. VWW = 289.3KB/512KB DTCM used).

| Model | Placement (AD / RT / AOT) | Metric | AD | hpx RT (gcc) | Δ RT | hpx AOT (ATfE/TCM) | Δ AOT |
|---|---|---|---:|---:|---:|---:|---:|
| **VWW** | MRAM/TCM · MRAM/TCM · TCM/TCM | Latency (ms) | 80.783 | 76.799 | -4.9% | 65.470 | -19.0% |
| | | Power (mW) | 8.360 | 6.328 | -24.3% | 6.508 | -22.2% |
| | | Energy (µJ) | 675.34 | 482.48 | -28.6% | 423.69 | -37.3% |
| **KWS** | TCM/TCM · TCM/TCM · TCM/TCM | Latency (ms) | 22.673 | 21.159 | -6.7% | 18.535 | -18.3% |
| | | Power (mW) | 8.642 | 6.630 | -23.3% | 6.912 | -20.0% |
| | | Energy (µJ) | 195.96 | 140.42 | -28.3% | 127.98 | -34.7% |
| **IC** | TCM/TCM · TCM/TCM · TCM/TCM | Latency (ms) | 55.332 | 53.161 | -3.9% | 45.862 | -17.1% |
| | | Power (mW) | 9.420 | 7.169 | -23.9% | 7.521 | -20.2% |
| | | Energy (µJ) | 521.21 | 382.52 | -26.6% | 346.47 | -33.5% |
| **AD** (anomaly detection model) | TCM/TCM · TCM/TCM · TCM/TCM | Latency (ms) | 1.428 | 0.767 | -46.3% | 0.663 | -53.6% |
| | | Power (mW) | 9.136 | 8.329 | -8.8% | 8.681 | -5.0% |
| | | Energy (µJ) | 13.05 | 6.389 | -51.0% | 5.755 | -55.9% |

Placement format is `weights/arena`. AD/RT match on all 4 models (apples-to-
apples); AOT upgrades VWW from MRAM/TCM to full TCM/TCM (weights hydrated
into DTCM), so VWW's Δ AOT column reflects both the AOT toolchain *and* a
placement change, not toolchain alone — see the "full TCM" note above the
table.

Note: "AD" appears twice above — once as the AutoDeploy baseline column
header, once as the 4th MLPerf Tiny model name ("Anomaly Detection"). The
model row's own latency (0.767/0.663ms) is extremely short; re-run with an
8x longer gated window (7.6s, 10,000 inferences vs original 1.5s/2,000)
reproduced the same 6.389µJ/inf figure to within 0.2%, confirming this is
a repeatable measurement, not short-window timing jitter.

### Re-verification after AP330 bring-up fixes (KWS/RT/gcc)

Two fixes landed during the AP330 bring-up this session that also apply
to AP510: (1) `-Wl,-u,_sbrk` (forces the linker to prefer NSX's bounded
`sbrk.c` over a weak stub in `libam_hal.a` that silently returned a
constant heap break landing in ITCM@0x0 — AP510 linked the same stub but
survived silently since 0x58 is writable ITCM there, unlike AP330 where
it HardFaults); (2) a new narrow crypto/OTP/VCOMP power-down
(`crypto_otp_shutdown`, AP5-family) — though AP510's SocDef has
`has_radio_subsystem=False` (its HAL lacks
`am_hal_pwrctrl_rss_pwroff()`), so it only gets the small CRYPTO/OTP/
VCOMP portion, not the more impactful radio-subsystem power-off that
helped AP330. Re-ran KWS/RT/gcc to check whether either fix moved the
numbers:

| Metric | Before (this doc, above) | After (`_sbrk` + `crypto_otp_shutdown`) | AD | Δ before | Δ after |
|---|---:|---:|---:|---:|---:|
| Latency (ms) | 21.159 | 21.140 | 22.673 | -6.7% | -6.8% |
| Power (mW) | 6.630 | 6.692 | 8.642 | -23.3% | -22.6% |
| Energy (µJ) | 140.42 | 141.44 | 195.96 | -28.3% | -27.8% |

**Essentially unchanged** (within ~0.5-1% run-to-run noise) — confirms
neither fix perturbs AP510's steady-state measurement (both only affect
boot-time/idle-domain behavior, not the inference loop), and that
AP510's ~-23%-to-28% power/energy advantage over AutoDeploy is a real,
stable result, not an artifact of either bug. Sharpens the open "AP330-
vs-AP510 inversion" question below: the two boards are now being
compared on the same fixed-bug footing, and the divergence (AP510 beats
AD by ~23-28%; AP330 barely edges it by 2-5%, see
`ap330_hpx_vs_autodeploy.md`) is a genuine platform difference, not a
measurement artifact on either side.

### JS320 re-capture (KWS/RT/gcc, gate-only sync)

The same AP510 KWS configuration was re-run through a Joulescope JS320
(`25QG`) connected to J-Link probe `1160002255` after adding JS320 support
to hpx. The JS320 uses the JS220-style statistics topics but its connected
firmware does not expose a usable GPO topic, so hpx safely falls back from
3-wire lockstep to GPI gate-only capture. The first attempt also caught
reversed JS320 IN/OUT wiring: raw current was negative and hpx rejected the
window as corrupt rather than applying a polarity-flipping `abs()`. After
correcting the wiring:

| Metric | JS110 prior re-run | JS320 | JS320 vs JS110 | AutoDeploy |
|---|---:|---:|---:|---:|
| Latency (ms) | 21.140 | 21.145 | +0.02% | 22.673 |
| Power (mW) | 6.692 | 6.730 | +0.57% | 8.642 |
| Energy (µJ/inf) | 141.44 | 142.15 | +0.50% | 195.96 |
| Gated window ratio | 0.9998 | 0.9990 | — | — |

Relative to AutoDeploy, the JS320 run is **-6.7% latency, -22.1% power,
and -27.5% energy**. It agrees with the prior JS110 capture within 0.6%
for power/energy, well within observed Joulescope/board run-to-run
variation. The comparison is not a lockstep-controller comparison:
JS110 used GPI/GPO lockstep, while JS320 used the gate-only fallback.
Both runs used the same dedicated power firmware, KWS TCM/TCM placement,
GCC compiler, and LP clock.

### KWS toolchain comparison (LP, heliaRT)

Placement: TCM/TCM (weights/arena) for all three toolchains and the AD
baseline — apples-to-apples, no placement variable in this comparison.

| Toolchain | hpx Latency | AD Latency | Δ Lat | hpx Energy/inf | AD Energy/inf | Δ Energy | hpx Power | AD Power | Δ Power |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gcc | 21.16 ms | 22.67 ms | -6.7% | 140.4 µJ | 196.0 µJ | -28.3% | 6.63 mW | 8.64 mW | -23.3% |
| armclang | 19.59 ms | 22.64 ms | -13.5% | 133.0 µJ | 195.5 µJ | -32.0% | 6.80 mW | 8.64 mW | -21.3% |
| ATfE | 18.61 ms | — (no AD baseline for ATfE) | — | 131.1 µJ | — | — | 7.06 mW | — | — |

---

## HP clock (192 MHz) — heliaAOT (ATfE, full TCM)

hpx placement is full TCM/TCM (weights+arena) for all 4 models at HP clock
(same as the LP AOT table above). AD baseline placement per model matches
the gcc reference table below (`AutoDeploy baseline reference data`) —
VWW is MRAM/TCM there while hpx AOT is TCM/TCM, so VWW's Δ column again
reflects a placement change alongside the toolchain/clock change, not a
toolchain-only comparison.

| Model | Placement (AD / hpx AOT) | Metric | AD (gcc, HP) | hpx (AOT/ATfE/TCM, HP) | Δ |
|---|---|---|---:|---:|---:|
| **VWW** | MRAM/TCM · TCM/TCM | Latency (ms) | 31.408 | 25.120 | -20.0% |
| | | Power (mW) | 27.780 | 21.738 | -21.7% |
| | | Energy (µJ) | 872.51 | 546.17 | -37.4% |
| **KWS** | TCM/TCM · TCM/TCM | Latency (ms) | 8.693 | 7.113 | -18.2% |
| | | Power (mW) | 28.927 | 23.518 | -18.7% |
| | | Energy (µJ) | 251.47 | 167.38 | -33.4% |
| **IC** | TCM/TCM · TCM/TCM | Latency (ms) | 21.211 | 17.598 | -17.0% |
| | | Power (mW) | 33.136 | 25.719 | -22.4% |
| | | Energy (µJ) | 702.85 | 452.71 | -35.6% |
| **AD** (anomaly detection model) | TCM/TCM · TCM/TCM | Latency (ms) | 0.552 | 0.254 | -54.0% |
| | | Power (mW) | 27.779 | 30.206 | **+8.7%** |
| | | Energy (µJ) | 15.336 | 7.672 | -50.0% |

Note: AD's power reads *above* AD-baseline here (+8.7%) — the only
above-baseline power result across all 8 model×clock combinations in this
document. Flagged for follow-up (e.g. a long-window HP re-check similar
to the LP one) given AD's very short (<0.6ms) inference window at HP
clock.

---

## Raw hpx capture data (for reference)

### LP, heliaRT (gcc)

Placement (weights/arena): VWW MRAM/TCM, KWS/IC/AD TCM/TCM — matches AD
baseline placement per model (see `AutoDeploy baseline reference data`
below).

| Model | Placement | Latency (ms) | Energy/inf (µJ) | Avg Power (mW) | ready_observed | duration_ratio |
|---|---|---:|---:|---:|---|---:|
| VWW | MRAM/TCM | 76.799 | 482.481 | 6.328 | true | 0.9928 |
| KWS | TCM/TCM | 21.159 | 140.422 | 6.630 | true | 1.0009 |
| IC | TCM/TCM | 53.161 | 382.523 | 7.169 | true | 1.0036 |
| AD | TCM/TCM | 0.767 | 6.389 | 8.329 | true | 1.0000 |

### LP, heliaRT — KWS toolchain sweep

Placement: TCM/TCM (weights/arena) for all three toolchains.

| Toolchain | Latency (ms) | Energy/inf (µJ) | Avg Power (mW) | ready_observed | duration_ratio |
|---|---:|---:|---:|---|---:|
| gcc | 21.159 | 140.422 | 6.630 | true | 1.0009 |
| armclang | 19.588 | 132.970 | 6.797 | true | 0.9987 |
| ATfE | 18.605 | 131.145 | 7.057 | true | 0.9989 |

### LP, heliaAOT (ATfE, full TCM)

Placement: full TCM/TCM (weights hydrated into DTCM) for all 4 models —
note this differs from the AD baseline's MRAM/TCM placement for VWW (see
`AutoDeploy baseline reference data` below), so VWW here is not
apples-to-apples with the VWW AD baseline row.

| Model | Placement | Latency (ms) | Energy/inf (µJ) | Avg Power (mW) | ready_observed | duration_ratio |
|---|---|---:|---:|---:|---|---:|
| KWS | TCM/TCM | 18.535 | 127.975 | 6.912 | true | 0.9989 |
| VWW | TCM/TCM | 65.470 | 423.689 | 6.508 | true | 0.9944 |
| IC | TCM/TCM | 45.862 | 346.465 | 7.521 | true | 1.0044 |
| AD | TCM/TCM | 0.663 | 5.755 | 8.681 | true | 1.0000 |

### HP, heliaAOT (ATfE, full TCM)

Placement: full TCM/TCM (weights/arena) for all 4 models, same caveat as
the LP heliaAOT table above (VWW differs from its AD-baseline placement).

| Model | Placement | Latency (ms) | Energy/inf (µJ) | Avg Power (mW) | ready_observed | duration_ratio |
|---|---|---:|---:|---:|---|---:|
| KWS | TCM/TCM | 7.113 | 167.384 | 23.518 | true | 1.0006 |
| VWW | TCM/TCM | 25.120 | 546.174 | 21.738 | true | 1.0002 |
| IC | TCM/TCM | 17.598 | 452.714 | 25.719 | true | 1.0002 |
| AD | TCM/TCM | 0.254 | 7.672 | 30.206 | true | 1.0000 |

---

## AutoDeploy baseline reference data

Source: `~/OneDrive/Ambiq/profiling/Perfresults.xlsx`, sheet `AP510`,
`ns_tflm_2025_02_14`, `apollo510_evb`.

### gcc 14.2.Rel1

| Model | Placement | LP Latency (ms) | LP Energy (µJ) | LP Power (mW) | HP Latency (ms) | HP Energy (µJ) | HP Power (mW) |
|---|---|---:|---:|---:|---:|---:|---:|
| VWW | MRAM/TCM | 80.783 | 675.34 | 8.360 | 31.408 | 872.51 | 27.780 |
| KWS | TCM/TCM | 22.673 | 195.96 | 8.642 | 8.693 | 251.47 | 28.927 |
| IC | TCM/TCM | 55.332 | 521.21 | 9.420 | 21.211 | 702.85 | 33.136 |
| AD | TCM/TCM | 1.428 | 13.05 | 9.136 | 0.552 | 15.336 | 27.779 |

### arm Essential (armclang), LP only (used for KWS toolchain comparison)

| Model | Placement | LP Latency (ms) | LP Energy (µJ) | LP Power (mW) |
|---|---|---:|---:|---:|
| VWW | MRAM/TCM | 80.785 | 674.336 | 8.347 |
| KWS | TCM/TCM | 22.636 | 195.536 | 8.638 |
| IC | TCM/TCM | 55.330 | 520.206 | 9.402 |
| AD | TCM/TCM | 1.342 | 11.701 | 8.718 |

---

## Key findings / caveats

1. **Pattern is highly consistent across VWW/KWS/IC**, both clocks, all
   toolchains tried: hpx reads meaningfully lower power/energy than
   AutoDeploy, with heliaAOT+ATfE+full-TCM giving the largest additional
   improvement over heliaRT+gcc (~9-12% further energy reduction).
2. **AD (anomaly detection) model is a repeatable outlier** — latency/
   energy gaps 2-3x larger than the other three models, in both clock
   modes. Confirmed NOT a short-window measurement artifact (8x longer
   window reproduced the LP figure to within 0.2%). Its HP-mode power
   result is the only above-AD-baseline power figure in this dataset.
   Root cause not yet identified — likely candidates: AD-model-specific
   codegen/optimization difference, or a difference in how AutoDeploy's
   own methodology handles such a short (<1ms) inference in its
   benchmark harness.
3. Memory placement was independently verified (not just requested) to
   match AutoDeploy's own layout for every model via the runtime memory
   plan (`detailed/memory.json`), ruling out a placement-mismatch
   explanation for the observed gaps.
4. Toolchain does not explain the gap — gcc, armclang, and ATfE all land
   in the same tight power/energy delta band on KWS, ruling out a
   compiler-specific codegen quirk as the primary driver.
