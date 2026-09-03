"""Shared model table and helpers for the MLPerf Tiny export scripts.

The pipeline has two stages with the .pt2 fixture as the interface:

  make_pt2.py    canonical float model -> PT2E static INT8 -> .pt2 (LFS)
  export_pte.py  .pt2 -> Cortex-M lowering -> .pte (LFS)

Everything model-specific lives in MODELS below; the stages themselves are
model-agnostic.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SEED = 20260817
CALIBRATION_SEED = SEED + 1
EXPECTED_EXECUTORCH_COMMIT = "3a97429b0ce0c192861fc3e3729fb81432fd22cf"
EXPECTED_TORCH_VERSION = "2.12.0"
REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "mlperf_tiny"


@dataclass(frozen=True)
class ModelSpec:
    key: str
    model_id: str
    description: str
    model_class: str  # in executorch.examples.models.mlperf_tiny
    pt2_path: str  # relative to tests/fixtures/mlperf_tiny/
    pte_path: str  # relative to tests/fixtures/mlperf_tiny/
    calibration_samples: int
    channels_last: bool
    output_semantics: str


MODELS: dict[str, ModelSpec] = {
    spec.key: spec
    for spec in (
        ModelSpec(
            key="ad",
            model_id="mlperf-tiny-ad-deep-autoencoder-random-int8",
            description="MLPerf Tiny anomaly detection DeepAutoEncoder (ToyADMOS)",
            model_class="DeepAutoEncoderModel",
            pt2_path="ad/deep_autoencoder_random_int8.pt2",
            pte_path="ad/deep_autoencoder_int8_random.pte",
            calibration_samples=8,
            channels_last=False,
            output_semantics="reconstructed 640-element feature vector",
        ),
        ModelSpec(
            key="ic",
            model_id="mlperf-tiny-ic-resnet8-cifar10-random-int8",
            description="MLPerf Tiny image classification ResNet-8 (CIFAR-10)",
            model_class="ResNet8Model",
            pt2_path="ic/ic_resnet8_random_int8.pt2",
            pte_path="ic/ic_resnet8_random_int8.pte",
            calibration_samples=32,
            channels_last=True,
            output_semantics="10 CIFAR-10 class logits",
        ),
        ModelSpec(
            key="kws",
            model_id="mlperf-tiny-kws-dscnn-random-cortex-m55",
            description="MLPerf Tiny keyword spotting DS-CNN",
            model_class="DSCNNKWSModel",
            pt2_path="kws/kws_dscnn_random_int8.pt2",
            pte_path="kws/kws_dscnn_random_cortex_m55.pte",
            calibration_samples=32,
            channels_last=True,
            output_semantics="12 keyword-class logits",
        ),
        ModelSpec(
            key="vww",
            model_id="mlperf-tiny-vww-mobilenetv1-025-random-int8",
            description="MLPerf Tiny visual wake words MobileNetV1 0.25 (96x96)",
            model_class="MobileNetV1025Model",
            pt2_path="vww/vww_mobilenetv1_025_random_int8.pt2",
            pte_path="vww/vww_mobilenetv1_random_int8.pte",
            calibration_samples=16,
            channels_last=True,
            output_semantics="2 class logits [not_person, person]",
        ),
    )
}


def parse_model_keys(models_arg: str | None) -> list[str]:
    """Parse a --models value ('ad, ic') into validated MODELS keys."""
    if models_arg is None:
        return list(MODELS)
    keys = [key.strip() for key in models_arg.split(",") if key.strip()]
    unknown = [key for key in keys if key not in MODELS]
    if not keys or unknown:
        raise SystemExit(
            f"--models must be a comma-separated subset of {', '.join(MODELS)}; got {models_arg!r}"
        )
    return keys


def configure_import_path(executorch_root: Path) -> None:
    if not (executorch_root / "src" / "executorch" / "exir").exists():
        raise SystemExit(f"Not an ExecuTorch source checkout: {executorch_root}")
    for path in (executorch_root / "src", executorch_root):
        sys.path.insert(0, str(path))


def configure_nsx_import_path(executorch_root: Path) -> Path:
    """Make the helia-torch AOT package (nsx_cortex_m) importable.

    `executorch_root` must be the `external/executorch` directory of an
    nsx-executorch checkout; returns the nsx-executorch root.
    """
    nsx_root = executorch_root.parents[1]
    aot_dir = nsx_root / "aot"
    if not (aot_dir / "nsx_cortex_m").is_dir():
        raise SystemExit(
            f"{aot_dir} does not contain the nsx_cortex_m package; point "
            "--executorch-root at <nsx-executorch>/external/executorch"
        )
    sys.path.insert(0, str(aot_dir))
    return nsx_root


def git_commit(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def check_pins(torch, executorch_root: Path) -> str:
    commit = git_commit(executorch_root)
    if commit != EXPECTED_EXECUTORCH_COMMIT:
        raise SystemExit(f"ExecuTorch commit is {commit}; expected {EXPECTED_EXECUTORCH_COMMIT}")
    if torch.__version__.split("+", 1)[0] != EXPECTED_TORCH_VERSION:
        raise SystemExit(f"PyTorch is {torch.__version__}; expected {EXPECTED_TORCH_VERSION}")
    return commit


def use_checkout_schema_resources(executorch_root: Path) -> None:
    """Supply resources normally copied into an installed ExecuTorch wheel."""
    import executorch.exir._serialize._flatbuffer as flatbuffer

    package_schema = Path(flatbuffer.__file__).parent / "program.fbs"
    if package_schema.is_file():
        return
    schema_dir = executorch_root / "schema"

    class CheckoutResourceFiles:
        def __init__(self, resource_names) -> None:
            self._files = {name: (schema_dir / name).read_bytes() for name in resource_names}

        def patch_files(self, patch_fn) -> None:
            self._files = {name: patch_fn(data) for name, data in self._files.items()}

        def get(self, name):
            return self._files[name]

        def write_to(self, out_dir) -> None:
            for name, data in self._files.items():
                (Path(out_dir) / name).write_bytes(data)

    flatbuffer._ResourceFiles = CheckoutResourceFiles


def deterministic_example(torch, shape: list[int]):
    """A fixed full-range example input independent of any RNG stream."""
    numel = 1
    for dim in shape:
        numel *= dim
    return torch.linspace(-1.0, 1.0, numel).reshape(shape)


def calibration_data(torch, input_shape, count: int, channels_last: bool):
    generator = torch.Generator().manual_seed(CALIBRATION_SEED)
    samples = []
    for index in range(count):
        sample = torch.rand(list(input_shape), generator=generator) * 2.0 - 1.0
        # Deterministic full-range samples keep the observers independent of
        # unusually narrow random extrema.
        if index == 0:
            sample.fill_(-1.0)
        elif index == 1:
            sample.fill_(1.0)
        if channels_last:
            sample = sample.to(memory_format=torch.channels_last)
        samples.append(sample)
    return samples


def quantization_ops(exported_program) -> dict[str, int]:
    """Histogram of quantize/dequantize ops in an ExportedProgram graph."""
    counts: dict[str, int] = {}
    for node in exported_program.graph_module.graph.nodes:
        if node.op != "call_function":
            continue
        name = getattr(node.target, "_name", None) or str(node.target)
        if "quantize" in name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def load_quantized_pt2(torch, pt2_path: Path):
    """Load an INT8 .pt2 fixture, refusing unquantized programs."""
    # Deserialization resolves the graph's quantized_decomposed.* ops, which
    # only exist once torchao's PT2E op library is registered.
    import torchao.quantization.pt2e  # noqa: F401

    exported = torch.export.load(pt2_path)
    example_args, example_kwargs = exported.example_inputs
    if example_kwargs or len(example_args) != 1:
        raise SystemExit(f"{pt2_path} must take exactly one positional tensor input")
    if not quantization_ops(exported):
        raise SystemExit(
            f"{pt2_path} contains no quantize/dequantize ops; expected an INT8 "
            "PT2E-quantized ExportedProgram (regenerate with make_pt2.py)"
        )
    return exported
