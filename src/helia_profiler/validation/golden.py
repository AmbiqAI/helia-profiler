"""Fixed-input INT8 validation data and exact target-output comparison."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import struct
from zipfile import BadZipFile

from ..errors import ConfigError
from ..wire._model import (
    HPX_GOLDEN_OUTPUT_PREFIX,
    HPX_GOLDEN_OUTPUT_PATTERN,
    HPX_START_SENTINEL,
    HPX_END_SENTINEL,
)


@dataclass(frozen=True)
class GoldenData:
    input_bytes: bytes
    expected_bytes: bytes
    model_sha256: str
    input_sha256: str

    @property
    def input_literals(self) -> str:
        return ", ".join(f"0x{value:02x}" for value in self.input_bytes)


def load_golden(model_path: Path, data_path: Path) -> GoldenData:
    """Load one fixed-shape INT8 pair and verify it against the flatbuffer."""
    try:
        import numpy as np
        from ai_edge_litert import schema_py_generated as schema
    except ImportError as exc:
        raise ConfigError(
            "validation_data requires the analysis extra",
            hint="Install helia-profiler[analysis] in the profiling environment.",
        ) from exc

    try:
        model_bytes = model_path.read_bytes()
        if not schema.Model.ModelBufferHasIdentifier(model_bytes, 0):
            raise ValueError("model is not a TFLite flatbuffer")
        model = schema.Model.GetRootAsModel(model_bytes, 0)
        if model.SubgraphsLength() != 1:
            raise ValueError("validation_data requires one subgraph")
        graph = model.Subgraphs(0)
        if graph.InputsLength() != 1 or graph.OutputsLength() != 1:
            raise ValueError("validation_data requires exactly one input and output")
        if any(graph.Tensors(i).IsVariable() for i in range(graph.TensorsLength())):
            raise ValueError("validation_data does not support variable tensors")
        tensors = (graph.Tensors(graph.Inputs(0)), graph.Tensors(graph.Outputs(0)))
        with np.load(data_path, allow_pickle=False) as archive:
            if sorted(archive.files) != ["input_0", "output_0"]:
                raise ValueError("validation_data requires only input_0 and output_0")
            arrays = (archive["input_0"], archive["output_0"])
            for name, tensor, array in zip(("input_0", "output_0"), tensors, arrays):
                if tensor.Type() != schema.TensorType.INT8 or array.dtype != np.dtype("int8"):
                    raise ValueError(f"{name} must be INT8 in both model and validation_data")
                shape = tuple(int(tensor.Shape(i)) for i in range(tensor.ShapeLength()))
                if not shape or any(n <= 0 for n in shape) or tuple(array.shape) != shape:
                    raise ValueError(f"{name} shape does not match the fixed model shape")
            data, expected = (array.tobytes(order="C") for array in arrays)
        if not data or len(data) > 1048576 or not expected or len(expected) > 256:
            raise ValueError("validation_data supports up to 1 MiB input and 256 output bytes")
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        EOFError,
        struct.error,
        BadZipFile,
    ) as exc:
        raise ConfigError(f"Invalid validation_data: {exc}") from exc
    return GoldenData(
        data, expected, hashlib.sha256(model_bytes).hexdigest(), hashlib.sha256(data).hexdigest()
    )


def check_golden_output(text: str, golden: GoldenData) -> bytes:
    """Require one complete matching output record; absent or stale data fails."""
    lines = [line.strip() for line in text.splitlines()]
    starts = [i for i, line in enumerate(lines) if line == HPX_START_SENTINEL]
    ends = [i for i, line in enumerate(lines) if line == HPX_END_SENTINEL]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError("Numerical validation requires one complete capture frame")
    record_indices = [
        i for i, line in enumerate(lines) if line.startswith(HPX_GOLDEN_OUTPUT_PREFIX)
    ]
    if any(not starts[0] < i < ends[0] for i in record_indices):
        raise ValueError("Numerical output record lies outside the capture frame")
    records = [lines[i] for i in record_indices]
    if len(records) != 1:
        raise ValueError(f"Expected one HPX_GOLDEN_OUTPUT record, found {len(records)}")
    match = re.fullmatch(HPX_GOLDEN_OUTPUT_PATTERN, records[0])
    if match is None:
        raise ValueError("Malformed HPX_GOLDEN_OUTPUT record")
    model_hash, input_hash, encoded = match.groups()
    if model_hash != golden.model_sha256 or input_hash != golden.input_sha256:
        raise ValueError("Target model/input identity does not match validation_data")
    actual = bytes.fromhex(encoded)
    if actual != golden.expected_bytes:
        raise ValueError("Target output differs from validation_data (exact INT8 comparison)")
    return actual
