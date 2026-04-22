"""heliaAOT engine adapter.

Invokes the heliaAOT compiler to produce an NSX module from a .tflite model,
generates a memory-placement attribute header, and wraps ns-cmsis-nn as a
local NSX module for the profiler firmware build.

When ns-cmsis-nn contains a native ``nsx/`` directory (``feat/nsx-module-type``
branch or later), the upstream NSX manifest and CMakeLists.txt are used
directly instead of the generated Jinja2 wrapper templates.

Uses the heliaAOT Python API (``AotConverter``) programmatically so we can
extract the post-transform operator graph directly from ``CodeGenContext``
rather than parsing generated C source.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

import jinja2

from ..config import ProfileConfig
from ..errors import EngineError
from ..platform import get_soc_for_board
from ..results import NsxModuleRef
from .base import EngineArtifacts

log = logging.getLogger("hpx")

# Default AOT configuration
_DEFAULT_PREFIX = "hpx"
_DEFAULT_MODULE_NAME = "hpx_model"

# Jinja2 template environment (shared loader with heliaRT adapter)
_jinja_env = jinja2.Environment(
    loader=jinja2.PackageLoader("helia_profiler.engines", "templates"),
    keep_trailing_newline=True,
    undefined=jinja2.StrictUndefined,
)

# ---------------------------------------------------------------------------
# Board → heliaAOT platform name mapping
#
# heliaAOT has its own platform registry (apollo3p_evb, apollo4p_evb,
# apollo510_evb, …).  The profiler board names are close but not always
# identical.  Boards without a direct match fall back to the closest
# compatible AOT platform.
# ---------------------------------------------------------------------------

_BOARD_TO_AOT_PLATFORM: dict[str, str] = {
    "apollo3p_evb": "apollo3p_evb",
    "apollo4p_evb": "apollo4p_evb",
    "apollo510_evb": "apollo510_evb",
    "apollo510b_evb": "apollo510_evb",  # same SoC family / memory layout
    "apollo5b_evb": "apollo510_evb",
    "apollo330mP_evb": "apollo510_evb",  # Cortex-M55, AP5 family
}

# Expected memory-placement macro suffixes emitted by heliaAOT's
# MemoryType.to_qualifiers().  Used to validate pragma consistency.
_EXPECTED_PRAGMA_SUFFIXES = (
    "PUT_IN_DTCM",
    "PUT_IN_DTCM_INIT",
    "PUT_IN_SRAM",
    "PUT_IN_SRAM_INIT",
    "PUT_IN_MRAM",
    "PUT_IN_MRAM_INIT",
    "PUT_IN_PSRAM",
    "PUT_IN_PSRAM_INIT",
    "PUT_IN_ITCM",
)


class HeliaAOTAdapter:
    """Adapter for heliaAOT — Ambiq's ahead-of-time neural network compiler.

    Workflow:
    1. Validate profiler board maps to a known AOT platform.
    2. Invoke ``helia-aot convert`` on the input .tflite model (ModuleType.nsx).
    3. Validate generated memory-placement pragmas match expectations.
    4. Resolve the ns-cmsis-nn (CMSIS-NN fork) source tree.
    5. Generate an attribute header mapping AOT macros → Ambiq sections.
    6. Wrap ns-cmsis-nn as a local NSX module (AOT output is already NSX-native).
    7. Return ``EngineArtifacts`` with template vars and cmake_vars.
    """

    @property
    def name(self) -> str:
        return "heliaAOT"

    def prepare(self, config: ProfileConfig, work_dir: Path) -> EngineArtifacts:
        prefix = config.engine.config.get("prefix", _DEFAULT_PREFIX)
        module_name = config.engine.config.get("module_name", _DEFAULT_MODULE_NAME)

        # 1. Resolve AOT platform from profiler board
        aot_platform = _resolve_aot_platform(config)

        # 2. Run AOT compilation (programmatic API → CodeGenContext)
        aot_output_dir = work_dir / "aot_output"
        aot_module_dir = aot_output_dir / module_name
        codegen_ctx = _run_aot_compiler(
            config, aot_output_dir, module_name, prefix, aot_platform,
        )

        # 3. Extract operator manifest from the CodeGenContext.
        #    heliaAOT transforms/fuses ops — the AIR graph may differ
        #    significantly from the original TFLite flatbuffer.  The
        #    manifest captures what the AOT compiler *actually* emits.
        op_manifest = _extract_operator_manifest(codegen_ctx)
        if op_manifest:
            manifest_path = work_dir / "aot_operator_manifest.json"
            manifest_path.write_text(json.dumps(op_manifest, indent=2))
            log.info(
                "Extracted %d AOT operators from CodeGenContext",
                len(op_manifest),
            )
        else:
            log.warning(
                "Could not extract operator manifest from AOT — "
                "per-layer names will fall back to op_N."
            )

        # 4. Validate memory-placement pragmas in generated code
        _validate_pragmas(aot_module_dir, prefix)

        # 5. Resolve ns-cmsis-nn source tree
        cmsis_nn_path = _resolve_cmsis_nn(config)

        # 6. Create engine modules
        modules_dir = work_dir / "modules"

        # CMSIS-NN wrapper module
        cmsis_nn_mod_dir = modules_dir / "nsx-cmsis-nn"
        _write_cmsis_nn_wrapper(cmsis_nn_mod_dir, cmsis_nn_path)

        # AOT output is already a valid NSX module (ModuleType.nsx).
        # Just generate the memory-placement attribute header and tell
        # the AOT module's CMakeLists.txt where to find it.
        attr_header = _write_attributes_header(aot_module_dir, prefix)
        cmake_name = module_name.replace("-", "_")
        attr_var = f"{cmake_name.upper()}_ATTRIBUTES_HEADER"

        log.info(
            "AOT compiled %s → %s (prefix=%s, platform=%s)",
            config.model.path.name,
            aot_module_dir,
            prefix,
            aot_platform,
        )

        return EngineArtifacts(
            extra_modules=[
                NsxModuleRef(name="nsx-cmsis-nn", path=cmsis_nn_mod_dir),
                NsxModuleRef(name=module_name, path=aot_module_dir),
            ],
            cmake_vars={
                attr_var: str(attr_header),
            },
            template_vars={
                "engine_type": "helia_aot",
                "engine_header": f"{prefix}_model.h",
                "aot_prefix": prefix,
                "aot_module_name": module_name,
                "aot_cmake_target": f"nsx::{cmake_name}",
                "aot_op_manifest": op_manifest,
            },
        )


# ---------------------------------------------------------------------------
# Platform resolution
# ---------------------------------------------------------------------------


def _resolve_aot_platform(config: ProfileConfig) -> str:
    """Map the profiler's target board to a heliaAOT platform name.

    Resolution order:
    1. Explicit ``engine.config.platform_name`` override.
    2. Built-in ``_BOARD_TO_AOT_PLATFORM`` mapping.
    3. Raise ``EngineError`` with guidance.
    """
    # Explicit override always wins
    explicit = config.engine.config.get("platform_name")
    if explicit:
        log.info("Using explicit AOT platform override: %s", explicit)
        return str(explicit)

    board = config.target.board
    aot_platform = _BOARD_TO_AOT_PLATFORM.get(board)

    if aot_platform is None:
        known = ", ".join(sorted(_BOARD_TO_AOT_PLATFORM))
        raise EngineError(
            f"No heliaAOT platform mapping for board '{board}'",
            hint=(
                f"Set engine.config.platform_name explicitly, or use a "
                f"supported board: {known}"
            ),
        )

    if aot_platform != board:
        log.warning(
            "Board '%s' has no exact heliaAOT platform — using '%s'. "
            "Memory sizes and capabilities may differ.  "
            "Set engine.config.platform_name to override.",
            board,
            aot_platform,
        )

    return aot_platform


# ---------------------------------------------------------------------------
# AOT compiler invocation (programmatic API)
# ---------------------------------------------------------------------------


def _run_aot_compiler(
    config: ProfileConfig,
    output_dir: Path,
    module_name: str,
    prefix: str,
    aot_platform: str,
) -> Any:
    """Run heliaAOT via its Python API and return the ``CodeGenContext``.

    Uses ``AotConverter.convert()`` so we get the full post-transform graph
    (operator list, AIR model, memory plan) without parsing generated C.

    Config passthrough:
    * ``engine.config_path``  — loaded as a YAML dict and merged into
      ``ConvertArgs``.  The profiler's mandatory fields (model, module,
      platform) override any YAML values.
    * ``engine.config.aot_args`` — dict of additional ConvertArgs overrides
      (applied last).
    """
    try:
        from helia_aot.cli.defines import ConvertArgs
        from helia_aot.converter import AotConverter
        from helia_aot.defines import ModuleType
    except ImportError:
        raise EngineError(
            "heliaAOT package not installed",
            hint=(
                "Install helia-aot: pip install 'helia-profiler[aot]' or "
                "pip install git+https://github.com/AmbiqAI/helia-aot.git"
            ),
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    # Start from a user-supplied YAML config if provided
    base_data: dict[str, Any] = {}
    if config.engine.config_path is not None:
        import yaml

        cfg_path = Path(config.engine.config_path).expanduser().resolve()
        if not cfg_path.is_file():
            raise EngineError(
                f"heliaAOT config file not found: {cfg_path}",
                hint="Check engine.config_path in your profiler YAML.",
            )
        with open(cfg_path) as f:
            base_data = yaml.safe_load(f) or {}

    # Merge any engine.config.aot_args overrides (dict form)
    extra = config.engine.config.get("aot_args", {})
    if isinstance(extra, dict):
        _deep_merge(base_data, extra)

    # Build ConvertArgs — profiler mandatory fields always win
    try:
        convert_args = ConvertArgs(**base_data)
    except Exception as exc:
        raise EngineError(
            f"Failed to build heliaAOT ConvertArgs: {exc}",
            hint="Check engine.config_path and engine.config.aot_args.",
        )

    convert_args.model.path = config.model.path
    convert_args.module.path = output_dir
    convert_args.module.name = module_name
    convert_args.module.prefix = prefix
    convert_args.module.type = ModuleType.nsx
    convert_args.platform.name = aot_platform
    convert_args.force = True

    log.info(
        "heliaAOT convert: model=%s, module=%s/%s, platform=%s",
        config.model.path, output_dir, module_name, aot_platform,
    )
    log.debug("ConvertArgs: %s", convert_args)

    try:
        converter = AotConverter(config=convert_args)
        codegen_ctx = converter.convert()
    except Exception as exc:
        raise EngineError(
            f"heliaAOT compilation failed: {exc}",
            hint=str(exc)[:500],
        )

    # Verify output exists
    module_dir = output_dir / module_name
    if not module_dir.is_dir():
        raise EngineError(
            f"AOT output directory not found: {module_dir}",
            hint="Expected helia-aot to create the module directory.",
        )
    for required_dir in ("src", "includes-api"):
        if not (module_dir / required_dir).is_dir():
            raise EngineError(
                f"AOT output missing {required_dir}/ directory: {module_dir}",
            )

    return codegen_ctx


def _deep_merge(base: dict, override: dict) -> None:
    """Recursively merge *override* into *base* in place."""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# ---------------------------------------------------------------------------
# Pragma / memory-placement validation
# ---------------------------------------------------------------------------

_PRAGMA_RE = re.compile(r"#ifndef\s+(\w+_PUT_IN_\w+)")


def _validate_pragmas(aot_module_dir: Path, prefix: str) -> None:
    """Scan the AOT-generated platform header for memory-placement macros
    and verify they match what our attribute header provides.

    Raises a warning (not an error) on mismatch so the build can proceed,
    but the user is alerted that memory placement may be incorrect.
    """
    platform_h = aot_module_dir / "includes-api" / f"{prefix}_platform.h"
    if not platform_h.is_file():
        log.warning(
            "AOT platform header not found (%s) — cannot validate "
            "memory-placement macros.",
            platform_h,
        )
        return

    content = platform_h.read_text()

    # Collect all PUT_IN_* macros that the generated code expects
    found_macros = set(_PRAGMA_RE.findall(content))

    prefix_upper = prefix.upper()
    expected_macros = {f"{prefix_upper}_{s}" for s in _EXPECTED_PRAGMA_SUFFIXES}

    # Macros in the generated code that we don't provide
    uncovered = found_macros - expected_macros
    if uncovered:
        log.warning(
            "heliaAOT generated memory macros that heliaPROFILER does not "
            "define: %s.  These will be no-ops — memory may not be placed "
            "as intended.  Update the profiler's attribute header or set "
            "engine.config.aot_args to control placement.",
            ", ".join(sorted(uncovered)),
        )

    # Macros we define that the generated code doesn't use (info only)
    extra = expected_macros - found_macros
    if extra:
        log.debug(
            "heliaPROFILER attribute header defines macros not found in "
            "generated platform.h (harmless): %s",
            ", ".join(sorted(extra)),
        )


# ---------------------------------------------------------------------------
# Operator manifest extraction (from CodeGenContext)
# ---------------------------------------------------------------------------


def _extract_operator_manifest(
    codegen_ctx: Any,
) -> list[dict[str, Any]]:
    """Build the operator manifest from the ``CodeGenContext``.

    heliaAOT may transform, fuse, or remove operators compared to the
    original TFLite flatbuffer.  ``codegen_ctx.operators`` is the
    authoritative post-transform list of ``AotOperator`` objects — each
    with a stable ``.TYPE`` (``AirOpType``) and ``.id`` (original TFLite
    operator index, preserved through transforms).

    Returns a list of dicts ordered by execution sequence::

        [
            {"idx": 0, "id": 0, "op_type": "CONV_2D",  "name": "conv_2d_0"},
            {"idx": 1, "id": 3, "op_type": "ADD",       "name": "add_3"},
            ...
        ]

    Where:
    - ``idx``     — sequential execution index (matches firmware CSV "Layer")
    - ``id``      — AIR operator ID passed to the callback
    - ``op_type`` — operator type string (from ``AirOpType``)
    - ``name``    — full operator name as emitted by heliaAOT
    """
    operators = getattr(codegen_ctx, "operators", None)
    if not operators:
        return []

    manifest: list[dict[str, Any]] = []
    for idx, aot_op in enumerate(operators):
        manifest.append({
            "idx": idx,
            "id": int(aot_op.id),
            "op_type": str(aot_op.TYPE),
            "name": aot_op.name,
        })
    return manifest


# ---------------------------------------------------------------------------
# ns-cmsis-nn resolution
# ---------------------------------------------------------------------------


def _resolve_cmsis_nn(config: ProfileConfig) -> Path:
    """Resolve the ns-cmsis-nn source tree path.

    Checks (in order):
    1. ``engine.config.cmsis_nn_path`` — explicit user-provided path
    2. ``CMSIS_NN_PATH`` environment variable
    3. Raise an error with guidance.
    """
    raw = config.engine.config.get("cmsis_nn_path")
    if raw:
        p = Path(raw).expanduser().resolve()
        _validate_cmsis_nn(p)
        return p

    env = os.environ.get("CMSIS_NN_PATH")
    if env:
        p = Path(env).expanduser().resolve()
        _validate_cmsis_nn(p)
        return p

    raise EngineError(
        "CMSIS-NN source path not provided",
        hint=(
            "Set engine.config.cmsis_nn_path in your config YAML, "
            "or export CMSIS_NN_PATH to the ns-cmsis-nn repository root "
            "(containing Include/ and Source/ directories)."
        ),
    )


def _validate_cmsis_nn(path: Path) -> None:
    """Verify that *path* looks like an ns-cmsis-nn checkout.

    Also checks the header revision against what heliaAOT expects.
    heliaAOT generates code targeting ns-cmsis-nn (AmbiqAI fork) — the
    upstream ``cmsis-nn`` V.19+ has incompatible API changes (e.g. dropped
    ``weight_sum_ctx`` parameter from ``arm_convolve_1x1_s8_fast``).
    """
    if not path.is_dir():
        raise EngineError(f"CMSIS-NN path does not exist: {path}")
    for d in ("Include", "Source"):
        if not (path / d).is_dir():
            raise EngineError(
                f"CMSIS-NN path missing '{d}/' directory: {path}",
                hint="Expected an ns-cmsis-nn repository with Include/ and Source/.",
            )

    # Warn if the header revision looks like upstream V.19+ (incompatible).
    header = path / "Include" / "arm_nnfunctions.h"
    if header.is_file():
        import re as _re
        text = header.read_text(errors="replace")[:2048]
        m = _re.search(r"\$Revision:\s*V\.(\d+)\.", text)
        if m and int(m.group(1)) >= 19:
            raise EngineError(
                f"CMSIS-NN at {path} is V.{m.group(1)}.x (upstream) — "
                "heliaAOT requires ns-cmsis-nn (AmbiqAI fork) V.18 or earlier.",
                hint=(
                    "Point cmsis_nn_path to a ns-cmsis-nn checkout. "
                    "See https://github.com/AmbiqAI/ns-cmsis-nn"
                ),
            )


# ---------------------------------------------------------------------------
# NSX module generation — CMSIS-NN
# ---------------------------------------------------------------------------


def _write_cmsis_nn_wrapper(module_dir: Path, cmsis_nn_path: Path) -> None:
    """Write the NSX module for ns-cmsis-nn.

    If the repo contains a native ``nsx/`` directory (i.e. the
    ``feat/nsx-module-type`` branch or later), use the upstream NSX
    manifest and CMakeLists.txt directly.  A thin root shim delegates
    to ``nsx/CMakeLists.txt`` so that its ``../Source`` relative paths
    resolve correctly against the copied Source/ tree.

    Otherwise falls back to generated Jinja2 wrapper templates.
    """
    module_dir.mkdir(parents=True, exist_ok=True)

    native_nsx = cmsis_nn_path / "nsx"
    if (native_nsx / "CMakeLists.txt").is_file() and (
        native_nsx / "nsx-module.yaml"
    ).is_file():
        log.info("Using native nsx/ module from %s", cmsis_nn_path)

        # Copy the native manifest to the module root
        shutil.copy2(native_nsx / "nsx-module.yaml", module_dir / "nsx-module.yaml")

        # Place the native CMakeLists.txt in a subdirectory so its
        # relative paths (../Source, ../Include) resolve against the
        # copied Source/ and Include/ trees at the module root.
        nsx_subdir = module_dir / "nsx"
        nsx_subdir.mkdir(exist_ok=True)
        shutil.copy2(native_nsx / "CMakeLists.txt", nsx_subdir / "CMakeLists.txt")

        # Root shim delegates to the native build
        (module_dir / "CMakeLists.txt").write_text(
            "# Shim — delegates to the native ns-cmsis-nn NSX build.\n"
            "add_subdirectory(nsx)\n"
        )
    else:
        log.info("No native nsx/ in %s — using generated wrapper", cmsis_nn_path)
        (module_dir / "nsx-module.yaml").write_text(
            _jinja_env.get_template("cmsisnn_nsx_module.yaml.j2").render()
        )
        (module_dir / "CMakeLists.txt").write_text(
            _jinja_env.get_template("cmsisnn_CMakeLists.txt.j2").render()
        )

    # Copy the CMSIS-NN source tree into the module (no symlinks — Windows-safe)
    for d in ("Include", "Source"):
        target = module_dir / d
        source = cmsis_nn_path / d
        if target.is_dir():
            shutil.rmtree(target)
        shutil.copytree(source, target)


# ---------------------------------------------------------------------------
# Attribute header generation (memory placement overrides)
# ---------------------------------------------------------------------------


def _write_attributes_header(aot_module_dir: Path, prefix: str) -> Path:
    """Generate the memory-placement attribute header inside the AOT module.

    Returns the absolute path to the generated header so the caller can
    pass it as a CMake variable (``<CMAKE_NAME>_ATTRIBUTES_HEADER``).
    """
    header_path = aot_module_dir / f"{prefix}_hpx_attributes.h"
    header_path.write_text(
        _jinja_env.get_template("heliaaot_attributes.h.j2").render(
            prefix=prefix,
        )
    )
    return header_path
