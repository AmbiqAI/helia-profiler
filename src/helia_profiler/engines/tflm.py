"""Stock TFLM engine adapter."""

from __future__ import annotations

from pathlib import Path

from ..config import ProfileConfig
from ..errors import EngineError
from ..lifecycle import Lifecycle
from ..results import NsxModuleRef
from . import TFLM_ENGINE_HEADER, EngineType
from .base import (
    FixtureModelPreparation,
    PsramWeightsSource,
    SingleArenaPlacementMixin,
    TflmArtifacts,
)
from .cmsis_nn import ARM_CMSIS_NN_MODULE, ARM_CMSIS_NN_PROJECT

TFLITE_MICRO_MODULE = "nsx-tflite-micro"
TFLITE_MICRO_PROJECT = "nsx-tflite-micro"
_SUPPORTED_BACKENDS = frozenset(("reference", "cmsis_nn"))


def _reject_config_path(config: ProfileConfig) -> None:
    if config.engine.config_path is not None:
        raise EngineError(
            "TFLM does not read engine.config_path",
            hint="Remove engine.config_path; select the TFLM backend with engine.backend.",
        )


class TFLMAdapter(SingleArenaPlacementMixin):
    """Adapter for stock TensorFlow Lite for Microcontrollers."""

    prepared_runtime_contract = ("upstream", "cmsis_nn")

    def prepare_runtime(self, config: ProfileConfig, work_dir: Path, runtime) -> TflmArtifacts:
        from ..fixture_runtime import prepare_runtime_module

        stack, backend = self.prepared_runtime_contract
        if runtime.record.stack != stack or config.engine.backend != backend:
            raise EngineError(f"Prepared TFLM requires {stack}/{backend}")
        _reject_config_path(config)
        return TflmArtifacts(
            extra_modules=[prepare_runtime_module(runtime, work_dir)],
            engine_header=TFLM_ENGINE_HEADER,
        )

    @property
    def name(self) -> str:
        return "Stock TFLM"

    def prepare_fixture_model(self, path: Path, *, single_io: bool) -> FixtureModelPreparation:
        from ..fixture_analysis import litert_fixture_resolver, prepare_litert_fixture

        return prepare_litert_fixture(path, single_io=single_io, resolver=litert_fixture_resolver)

    @property
    def engine_type(self) -> EngineType:
        return EngineType.TFLM

    @property
    def psram_weights_source(self) -> PsramWeightsSource:
        # The flatbuffer lives on the host; firmware asks for it via
        # HPX_PSRAM_READY and the J-Link upload path fills PSRAM.
        return PsramWeightsSource.HOST_UPLOAD

    @property
    def lifecycles(self) -> frozenset[Lifecycle]:
        return frozenset({Lifecycle.CARRY, Lifecycle.SINGLE})

    def lifecycle_refusal(self, lifecycle: Lifecycle) -> str:
        return f"{self.name} firmware does not implement the {lifecycle.value} lifecycle"

    def prepare(self, config: ProfileConfig, work_dir: Path) -> TflmArtifacts:
        """Resolve the reference or upstream-CMSIS-NN TFLM NSX modules."""
        _reject_config_path(config)
        del work_dir

        backend = config.engine.backend or "reference"
        if backend not in _SUPPORTED_BACKENDS:
            raise EngineError(
                f"Invalid TFLM backend '{backend}'. "
                f"Valid backends: {', '.join(sorted(_SUPPORTED_BACKENDS))}."
            )

        extra_modules: list[NsxModuleRef] = []
        if backend == "cmsis_nn":
            # This must precede nsx-tflite-micro in NSX_MODULES: the runtime
            # validates nsx::arm_cmsis_nn while its CMakeLists is processed.
            extra_modules.append(NsxModuleRef.registry(ARM_CMSIS_NN_MODULE, ARM_CMSIS_NN_PROJECT))
        extra_modules.append(NsxModuleRef.registry(TFLITE_MICRO_MODULE, TFLITE_MICRO_PROJECT))

        return TflmArtifacts(
            engine_type=EngineType.TFLM,
            extra_modules=extra_modules,
            cmake_vars={"NSX_TFLITE_MICRO_BACKEND": backend},
            engine_header=TFLM_ENGINE_HEADER,
        )
