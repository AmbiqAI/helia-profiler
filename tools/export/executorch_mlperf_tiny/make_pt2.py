#!/usr/bin/env python3
"""Save the canonical MLPerf Tiny models as INT8 .pt2 fixtures.

The model architectures come from the pinned ExecuTorch checkout's
``executorch.examples.models.mlperf_tiny`` package — the canonical PyTorch
ports of the MLCommons Tiny reference models. Nothing is re-authored here:
this script seeds the RNG, instantiates the upstream classes, quantizes
through helia-torch (``nsx_cortex_m.quantize`` — PT2E static INT8 with the
arm provider's ``CortexMQuantizer`` and deterministic synthetic
calibration), and saves the quantized ExportedProgram to
``tests/fixtures/mlperf_tiny/<category>/*_int8.pt2`` with
``torch.export.save``.

Quantization happens HERE, not in the .pte exporter, so the checked-in .pt2
is datatype-equivalent to the INT8 .tflite reference fixture beside it:
int8 weights and activations with quantize/dequantize at the float32 method
boundary. export_pte.py only lowers.

The .pt2 files are the single source of truth for weights and quantization.
Upstream ships no trained checkpoints, so the weights are deterministic
random initialization (seed 20260817), which the ``_random`` file names
advertise. A drop-in .pt2 with trained weights re-points the whole pipeline.

Run inside the pinned export environment:

  ~/.cache/nsx-executorch-export-venv/bin/python make_pt2.py \
      --executorch-root ~/nsx-executorch-package/external/executorch
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from common import (
    CALIBRATION_SEED,
    FIXTURE_ROOT,
    MODELS,
    SEED,
    calibration_data,
    check_pins,
    configure_import_path,
    configure_nsx_import_path,
    deterministic_example,
    quantization_ops,
)

DEFAULT_EXECUTORCH_ROOT = Path(
    os.environ.get(
        "EXECUTORCH_ROOT",
        Path(os.environ.get("NSX_EXECUTORCH_ROOT", ".")) / "external" / "executorch",
    )
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--executorch-root",
        type=Path,
        default=DEFAULT_EXECUTORCH_ROOT,
    )
    parser.add_argument("--models", default=",".join(MODELS))
    parser.add_argument("--fixture-root", type=Path, default=FIXTURE_ROOT)
    args = parser.parse_args()
    executorch_root = args.executorch_root.resolve()
    configure_import_path(executorch_root)
    configure_nsx_import_path(executorch_root)

    import torch
    import torchao
    from executorch.examples.models import mlperf_tiny
    from nsx_cortex_m import quantize

    commit = check_pins(torch, executorch_root)
    torch.use_deterministic_algorithms(True)

    manifest_path = args.fixture_root / "pt2_models.json"
    previous_models: dict = {}
    if manifest_path.is_file():
        previous_models = json.loads(manifest_path.read_text(encoding="utf-8")).get(
            "models", {}
        )
    manifest: dict = {
        "schema": "hpx.mlperf-tiny-pt2-fixtures",
        "schema_version": 2,
        "seed": SEED,
        "weights": "deterministic random initialization; not trained",
        "source": "executorch.examples.models.mlperf_tiny (pinned checkout)",
        "quantization": {
            "pipeline": "helia-torch nsx_cortex_m.quantize(kernel_provider='arm')",
            "scheme": "PT2E static INT8 (CortexMQuantizer); float32 method I/O",
            "calibration": "deterministic synthetic uniform [-1,1] plus full-range fills",
            "calibration_seed": CALIBRATION_SEED,
        },
        "executorch_commit": commit,
        "torch_version": torch.__version__,
        "torchao_version": torchao.__version__,
        "pt2_reproducibility": (
            "graph and weights are deterministic (the derived .pte is "
            "byte-identical across runs), but .pt2 archive bytes are not: "
            "torch.export.save embeds a per-save serialization id and "
            "process-global graph counters in debug metadata"
        ),
        "models": previous_models,
    }
    for key in args.models.split(","):
        spec = MODELS[key]
        torch.manual_seed(SEED)
        wrapper = getattr(mlperf_tiny, spec.model_class)()
        model = wrapper.get_eager_model().eval()
        example_shape = list(wrapper.get_example_inputs()[0].shape)
        example = deterministic_example(torch, example_shape)

        graph_module = torch.export.export(model, (example,), strict=True).module()
        if spec.channels_last:
            graph_module = graph_module.to(memory_format=torch.channels_last)
            example = example.to(memory_format=torch.channels_last)
        calibration = [
            (sample,)
            for sample in calibration_data(
                torch, example_shape, spec.calibration_samples, spec.channels_last
            )
        ]
        with torch.no_grad():
            quantized_exported = quantize(
                graph_module,
                (example,),
                kernel_provider="arm",
                calibration_samples=calibration,
            )

        qdq_ops = quantization_ops(quantized_exported)
        if not qdq_ops:
            raise SystemExit(f"[{key}] quantization produced no quantize/dequantize ops")

        out_path = args.fixture_root / spec.pt2_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        torch.export.save(quantized_exported, out_path)

        pt2_bytes = out_path.read_bytes()
        manifest["models"][key] = {
            "path": spec.pt2_path,
            "model_class": f"executorch.examples.models.mlperf_tiny.{spec.model_class}",
            "input_shape": example_shape,
            "memory_format": "channels_last" if spec.channels_last else "contiguous",
            "parameters": sum(p.numel() for p in model.parameters()),
            "calibration_samples": spec.calibration_samples,
            "quantize_dequantize_ops": qdq_ops,
            "sha256": hashlib.sha256(pt2_bytes).hexdigest(),
            "byte_size": len(pt2_bytes),
        }
        print(f"[{key}] wrote {out_path} ({len(pt2_bytes)} bytes, {sum(qdq_ops.values())} q/dq ops)")

    manifest_path = args.fixture_root / "pt2_models.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {manifest_path}")


if __name__ == "__main__":
    main()
