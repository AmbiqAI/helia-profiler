"""heliaML engine adapter.

Consumes a heliaML **v2 generated module directory** — the output of
``heliaml.export_module(artifact, outdir, integration="nsx")``:

.. code-block:: text

    <name>_params.h    the weights (static const, HELIAML_PARAMS_SECTION)
    <name>_model.h     init/run entry points and the dimension macros
    <name>_model.c     one public heliaml_* call — and no mathematics
    nsx-module.yaml    package helia_ml_model, target nsx::helia_ml_model
    CMakeLists.txt     nsx_helia_ml_model STATIC, links nsx::helia_ml
    manifest.json      schema v2, with a ``module`` block
    arrays.npz         the provenance arrays the manifest hashes

That directory is already a complete NSX module, so — unlike the v0.x
adapter this file replaces — nothing is generated at ``prepare()`` time:
``extra_modules`` points NSX at the model directory itself plus heliaML's
own native module (``nsx::helia_ml``). The v0.x ``load_bundle`` /
``model_data.c`` bundle format is gone; heliaML v2's loader is
``heliaml.emit.manifest.load``, which verifies every array hash and the
params header itself and raises ``ValueError`` with a user-facing
message for every rejection.

Design notes carried over from v0.x, still true:

* **No per-op operator manifest.** heliaML's execution is one opaque
  call, so ``aot_op_manifest`` stays ``None`` and the firmware template
  records exactly one whole-model PMU row per iteration. Inventing an op
  graph would make per-op reporting look available when it is not.
* **No arena plumbing.** A v2 module's scratch is ``static`` inside
  ``<name>_model.c`` (tagged ``HELIAML_SCRATCH_SECTION``), and its
  parameters compile in as ``static const``. There is nothing to
  allocate and nothing to bind; the ``ArenaRegion``s this adapter emits
  are for the profiler's memory *reporting* only.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from ...config import ProfileConfig
from ...errors import ConfigError, EngineError
from ...placement import ArenaRole, MemoryRegion, Placement
from ...results import (
    MemoryConsumer,
    MemoryPlan,
    MemoryRegionUsage,
    NsxModuleRef,
)
from .. import EngineType
from ..base import ArenaRegion, HeliaMlArtifacts, PsramWeightsSource
from .nsx_module import (
    HELIAML_MODEL_MODULE,
    HELIAML_MODULE,
    resolve_heliaml_root,
    write_library_module_wrapper,
)

log = logging.getLogger("hpx")

#: The manifest schema this adapter understands. heliaML's loader
#: refuses both older and newer manifests rather than guessing; the
#: adapter mirrors that so the error names the version the artifact
#: needs, not just what was found.
_SUPPORTED_SCHEMA = 2

#: run_signature -> the call shape the firmware template renders.
_RUN_SHAPES = {
    "(const float *input, float *scores, size_t *out_class)": "scores",
    "(const float *input, size_t *out_class)": "class",
    "(const float *input, float *out_value)": "value",
}


class HeliaMLAdapter:
    """Adapter for heliaML — Ambiq's traditional-ML deployment library."""

    @property
    def name(self) -> str:
        return "heliaML"

    @property
    def engine_type(self) -> EngineType:
        return EngineType.HELIA_ML

    @property
    def psram_weights_source(self) -> PsramWeightsSource:
        # A generated module's weights are static const in flash
        # (HELIAML_PARAMS_SECTION) -- compiled in, never uploaded and
        # never written to PSRAM. There is no PSRAM story to tell, so
        # preflight refuses the placement rather than letting the run
        # hang on a handshake this firmware will never send.
        return PsramWeightsSource.UNSUPPORTED

    def check_psram_placement(self, config: ProfileConfig) -> None:
        # Nothing engine-specific to validate: preflight has already
        # refused any PSRAM placement via ``psram_weights_source``.
        del config

    def default_auto_placement(
        self, *, tcm_cap: int, sram_cap: int
    ) -> tuple[Placement, Placement] | None:
        # Fall through to the shared greedy fastest-fit policy.
        del tcm_cap, sram_cap
        return None

    def apply_arena_placement_override(
        self, regions: list[ArenaRegion], target: Placement
    ) -> list[ArenaRegion]:
        """Reporting-only for heliaML v2: nothing rebinds at run time.

        A generated module's scratch is static, tagged
        HELIAML_SCRATCH_SECTION — moving it between TCM and SRAM is a
        compile definition plus a rebuild, not a runtime bind. The
        regions this adapter emits describe the compiled-in layout, so an
        override cannot change them and pretending otherwise would make
        the report lie.
        """
        del target
        return regions

    def prepare(self, config: ProfileConfig, work_dir: Path) -> HeliaMlArtifacts:
        manifest_module = _import_heliaml_manifest()
        model_dir = config.model.path
        try:
            manifest, _arrays = manifest_module.load(model_dir)
        except ValueError as exc:
            raise EngineError(
                f"heliaML artifact failed verification: {model_dir}\n{exc}",
                hint="Regenerate with heliaml.export_module(...).",
            ) from exc
        module = _require_nsx_module_block(manifest, model_dir)
        _verify_module_file_hashes(module, model_dir)

        heliaml_root = resolve_heliaml_root(config)
        # The checkout's nsx/ dir carries metadata but no build entry, and
        # a vendored copy could not reach the sources through a relative
        # path anyway -- generate the absolute-path wrapper (heliaRT's
        # source-mode pattern).
        library_module_dir = work_dir / "modules" / HELIAML_MODULE
        write_library_module_wrapper(library_module_dir, heliaml_root=heliaml_root)
        prefix = _model_prefix(module)
        run_shape = _run_shape(module)

        return HeliaMlArtifacts(
            extra_modules=[
                NsxModuleRef(name=HELIAML_MODULE, path=library_module_dir, local=True),
                NsxModuleRef(name=HELIAML_MODEL_MODULE, path=model_dir, local=True),
            ],
            engine_header=str(module["header"]),
            aot_prefix=prefix,
            helia_ml_run_shape=run_shape,
            # The one CMake target the firmware links; it pulls
            # nsx::helia_ml transitively (the module's own CMakeLists).
            aot_cmake_target="nsx::helia_ml_model",
            aot_allocate_arenas=False,
            aot_arena_regions=_arena_regions_from_manifest(manifest),
            memory_plan=_memory_plan_from_manifest(manifest),
        )


def check_helia_ml_artifact(path: Path) -> None:
    """Validate the heliaML generated-module directory at ``path``.

    Called by preflight (stage 0) once it has confirmed the directory
    shape. Delegates to heliaML's own loader — array-hash mismatches,
    schema gates, and an edited params header all fail here, with
    heliaML's own actionable message — then requires the ``module``
    block with NSX integration, because a bare ``heliaml.write()``
    directory has weights but no compilable entry points.
    """
    manifest_module = _import_heliaml_manifest()
    try:
        manifest, _arrays = manifest_module.load(path)
    except ValueError as exc:
        raise ConfigError(
            f"Not a valid heliaML artifact directory: {path}\n{exc}",
            hint=(
                "Point model.path at a directory written by "
                "heliaml.export_module(artifact, outdir, integration='nsx')."
            ),
        ) from exc
    _require_nsx_module_block(manifest, path)


def _import_heliaml_manifest():
    try:
        from heliaml.emit import manifest as manifest_module
    except ImportError as exc:
        raise EngineError(
            "heliaml is not importable.",
            hint=(
                "heliaML is not yet published — install it from a local checkout, "
                "e.g. `uv pip install /path/to/heliaml`, or add its python/ "
                "directory to this environment's PYTHONPATH."
            ),
        ) from exc
    return manifest_module


def _require_nsx_module_block(manifest: dict, path: Path) -> dict:
    schema = manifest.get("schema_version")
    if schema != _SUPPORTED_SCHEMA:
        raise ConfigError(
            f"heliaML manifest at {path} has schema_version={schema}; "
            f"this hpx build supports {_SUPPORTED_SCHEMA}.",
            hint="Re-export the model with a matching heliaML release.",
        )
    module = manifest.get("module")
    if not module:
        raise ConfigError(
            f"heliaML artifact at {path} has no generated module.",
            hint=(
                "heliaml.write() emits weights only. Use "
                "heliaml.export_module(artifact, outdir, integration='nsx') "
                "so the directory carries compilable entry points."
            ),
        )
    if module.get("integration") != "nsx":
        raise ConfigError(
            f"heliaML module at {path} was generated for "
            f"integration={module.get('integration')!r}, not 'nsx'.",
            hint="Regenerate with integration='nsx'.",
        )
    return module


def _verify_module_file_hashes(module: dict, model_dir: Path) -> None:
    """Re-hash the generated module files against ``module.files``.

    heliaML's own loader verifies the arrays and the params header;
    ``module.files`` covers the rest of the generated tree (model source,
    module header, nsx-module.yaml, CMakeLists.txt). Re-verifying here is
    what makes "consume pre-generated" as trustworthy as "generate
    fresh": a stale regeneration or a hand-edited file is a build-time
    integrity failure, not a silent wrong answer.
    """
    files = module.get("files") or {}
    if not files:
        raise EngineError(
            f"heliaML module at {model_dir} records no generated-file hashes.",
            hint="Regenerate with heliaml.export_module(...).",
        )
    for filename, recorded in files.items():
        file_path = model_dir / filename
        if not file_path.is_file():
            raise EngineError(
                f"Module manifest references {filename} but it is missing "
                f"from {model_dir}.",
                hint="Regenerate with heliaml.export_module(...).",
            )
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if digest != recorded:
            raise EngineError(
                f"{filename} does not match the manifest's recorded hash.",
                hint=(
                    f"{file_path} was modified after export, or is stale "
                    f"relative to {model_dir / 'manifest.json'}. Regenerate "
                    f"the module."
                ),
            )


def _model_prefix(module: dict) -> str:
    """The model name, recovered from the init entry point.

    ``gesture_model_init`` -> ``gesture``. The generated symbols and
    dimension macros all derive from this prefix, which is what the
    firmware template renders.
    """
    init = str(module.get("entry_points", {}).get("init", ""))
    match = re.fullmatch(r"(\w+)_model_init", init)
    if not match:
        raise EngineError(
            f"heliaML module init entry point {init!r} does not follow the "
            f"<name>_model_init contract.",
            hint="Regenerate the module with a current heliaML release.",
        )
    return match.group(1)


def _run_shape(module: dict) -> str:
    signature = str(module.get("run_signature", ""))
    shape = _RUN_SHAPES.get(signature)
    if shape is None:
        raise EngineError(
            f"heliaML module run signature {signature!r} is not one this "
            f"hpx build knows how to call.",
            hint=(
                "Known shapes: classifier with scores, scoreless classifier, "
                "regressor. Update hpx if heliaML added a new module shape."
            ),
        )
    return shape


def _arena_regions_from_manifest(manifest: dict) -> list[ArenaRegion]:
    """Reporting-only regions from the v2 ``memory.arrays[]`` block.

    v2 reports each learned array with a role, its bytes, and a
    preferred alignment — strictly more information than v0.x's three
    scalars — so this groups-and-sums by role. ``scratch`` is
    deliberately not a number in that block (for kNN and SVC it depends
    on the model, and the honest answer is the runtime
    ``*_scratch_query``), so no SCRATCH region is invented here; the
    generated module's static scratch is part of the image, not an
    allocation.
    """
    arrays = (manifest.get("memory") or {}).get("arrays") or []
    totals: dict[ArenaRole, tuple[int, int]] = {}
    for entry in arrays:
        role = ArenaRole.CONSTANT if entry.get("role") == "constant" else ArenaRole.PERSISTENT
        size, alignment = totals.get(role, (0, 16))
        totals[role] = (
            size + int(entry.get("bytes", 0)),
            max(alignment, int(entry.get("preferred_alignment", 16))),
        )
    regions: list[ArenaRegion] = []
    for region_id, (role, (size, alignment)) in enumerate(sorted(totals.items())):
        if size == 0:
            continue
        placement = Placement.MRAM if role is ArenaRole.CONSTANT else Placement.TCM
        regions.append(
            ArenaRegion(
                region_id=region_id,
                name=f"helia_ml_{role.value}_bytes",
                enum_name=f"HELIA_ML_ARENA_{role.value.upper()}",
                size=size,
                alignment=alignment,
                role=role,
                memory=placement.value,
                placement=placement,
            )
        )
    return regions


def _memory_plan_from_manifest(manifest: dict) -> MemoryPlan:
    """The honest heliaML memory plan: parameters compiled into flash.

    Without this the plan_memory stage synthesises the interpreter shape
    -- a "model_flatbuffer" plus a default-sized "tensor_arena" -- and
    the report shows a 256 KB arena heliaML does not have. A v2 model's
    parameters are `static const` in the image (MRAM) and its scratch is
    a handful of static buffers already counted in `.bss`; neither is a
    runtime allocation, so the plan carries exactly one consumer.
    """
    memory = manifest.get("memory") or {}
    parameter_bytes = int(memory.get("parameter_bytes") or 0)
    regions = ()
    if parameter_bytes > 0:
        regions = (
            MemoryRegionUsage(
                region=MemoryRegion.MRAM,
                capacity=0,  # filled by plan_memory's _apply_capacities
                used=parameter_bytes,
                consumers=(
                    MemoryConsumer(
                        name="model_parameters",
                        size=parameter_bytes,
                        kind="weights",
                    ),
                ),
            ),
        )
    return MemoryPlan(
        engine=EngineType.HELIA_ML,
        regions=regions,
        model_weight_bytes=parameter_bytes,
    )
