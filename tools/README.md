# Repository tools

Start commands below from the repository root unless a linked guide specifies
another working directory. These scripts support development, asset generation
and release checks; the installed product interface is `hpx` and the Python API.

Use the repository's locked environment for HPX development:

```bash
uv sync --locked --all-groups --extra aot --extra analysis
```

Exporter and TensorFlow tools have additional environments described below.
Some generators overwrite tracked assets. Run the specific tool for the source
you changed, then inspect its outputs; do not run every tool as a setup step.

## Documentation and shipped metadata

| Entry point | Responsibility and effect |
| --- | --- |
| [`docs/`](docs/) | Extract CLI/config/issue/PMU reference data and check API tiers, committed reference and source ownership. Use the orchestrated commands in [documentation.md](../maintainers/documentation.md), rather than calling individual extractors in an arbitrary order. |
| [`gen_wire_protocol_reference.py`](gen_wire_protocol_reference.py) | Writes the wire-protocol reference from the wire registry. Run after changing the registry. |
| [`gen_fixture_closure.py`](gen_fixture_closure.py) | Writes the shipped-file closure inventory. Stage source moves first so tracked-file discovery sees the intended inventory. |
| [`sync_pmu_catalog.py`](sync_pmu_catalog.py) | Copies an upstream PMU catalog into package data. Pass the upstream JSON path explicitly when the default sibling-checkout layout does not apply; review the resulting catalog/reference changes. |

Generation and drift-check commands are in [AGENTS.md](../AGENTS.md#commands).
Maintainer process documentation belongs in [`maintainers/`](../maintainers/README.md);
published product documentation belongs in `astro-site/src/content/docs/`.

## Models and export assets

| Entry point | Prerequisites and outputs |
| --- | --- |
| [`gen_example_model.py`](gen_example_model.py) | HPX analysis dependencies; writes the deterministic packaged tiny CNN and manifest under `src/helia_profiler/data/models/`. |
| [`gen_softmax_preflight_fixture.py`](gen_softmax_preflight_fixture.py) | Analysis dependencies; writes the synthetic unsupported-Softmax test fixture. |
| [`gen_float_model.py`](gen_float_model.py), [`cast_fp16.py`](cast_fp16.py) | Floating-point test models. The generator requires a separate TensorFlow environment; casting uses LiteRT schema/FlatBuffers dependencies. Distinguish true FP16 compute from FP16 weights converted back to FP32. Follow the scripts' usage and inspect generated assets. |
| [`export/executorch_mlperf_tiny/`](export/executorch_mlperf_tiny/README.md) | Pinned ExecuTorch export environment; generates PT2/PTE fixtures and provider exports. See its guide for pin checks, model provenance and manifest updates. |
| [`export/executorch_tier1/export_tier1.py`](export/executorch_tier1/export_tier1.py) | Pinned ExecuTorch/helia-torch export environment; generates paired ARM/NS comparison models and instruction maps. See the [kernel guide](../astro-site/src/content/docs/guide/executorch-ns-kernels.mdx). |
| [`export/executorch_tier1/compare_tier1.py`](export/executorch_tier1/compare_tier1.py) | Reads paired saved runs and the instruction-map manifest; prints a comparison without invoking a compiler or device. It handles the different PTE serializations that ordinary `hpx compare` refuses to treat as one model. |
| [`zoo_registry.py`](zoo_registry.py), [`zoo_models.yaml`](zoo_models.yaml) | Fetch pinned Zoo models, verify their hashes and write an `hpx validate --models-file` registry. `uv run tools/zoo_registry.py --output build/zoo/models.yaml` uses the script's isolated dependency environment and downloads assets. See [hardware CI](../maintainers/hardware-ci.md). |

The scripts and adjacent manifests own asset generation; config recipes consume
the resulting files. Do not infer accuracy qualification from synthetic or
random-weight models.

## Validation and release

| Entry point | Scope |
| --- | --- |
| [`software_only.py`](software_only.py) | Installs the device guard before importing HPX, then runs pytest or a Python probe. See [software-only checks](../README.md#development). A marker alone does not install the guard. |
| [`firmware_codegen_gate.py`](firmware_codegen_gate.py) | `record` compiles rendered firmware objects with a warm Tier 2 workspace and records code/section evidence; `compare` compares two saved records. Needs the compiler workspace described in [compile-gate.md](../maintainers/compile-gate.md), not a connected board. |
| [`verify_executorch_kernels.py`](verify_executorch_kernels.py) | Inspects an existing ELF through an `nm` tool for provider/kernel evidence; does not build or capture. |
| [`verify_distribution.py`](verify_distribution.py) | Checks release archives, metadata and shipped files before publication. Called by CI and release workflows; see [releasing.md](../maintainers/releasing.md). |
| [`hooks/`](hooks/) | Pre-commit marker validation and commit-message attribution cleanup, configured in [`.pre-commit-config.yaml`](../.pre-commit-config.yaml). |
| [`tests/`](tests/) | Tests for tooling. Product tests and test assets remain under the repository's `tests/`. |

The existing read-only nightly job summary helper is
[`.claude/skills/nightly-triage/legs.sh`](../.claude/skills/nightly-triage/legs.sh).
It queries GitHub Actions using Bash and authenticated `gh`; it does not dispatch
runs. Its invocation and triage process remain in the
[adjacent skill](../.claude/skills/nightly-triage/SKILL.md).

## Adding or changing a tool

Keep new tools with the subsystem they serve and link their entry point here.
Document required environment, input/output paths and whether they download,
generate, compile or access hardware. Reuse maintained workflows and the public
HPX API where applicable. Keep reusable behavior out of agent instructions;
relocating an existing helper still requires checking its callers.
