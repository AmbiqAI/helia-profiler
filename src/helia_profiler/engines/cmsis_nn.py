"""ns-cmsis-nn (CMSIS-NN fork) resolution, NSX module wrapping, and build options.

Both heliaAOT-generated code and source-built heliaRT link against
``ns-cmsis-nn`` (the AmbiqAI CMSIS-NN fork; upstream ``cmsis-nn`` V.19+ has
dropped parameters they target). By default the module is resolved from the
NSX registry (NSX clones it from GitHub during ``nsx sync``); a user-provided
local checkout is vendored as a local NSX module instead. The CMake options
the kernels need (:func:`cmsis_nn_cmake_vars`) are shared by both engines.
"""

from __future__ import annotations

import logging
import os
import shutil
import struct
from pathlib import Path

from ..config import ProfileConfig
from ..errors import EngineError
from ..modelcost._tflite_reader import TENSOR_TYPE_FLOAT16, read_float_compute_types
from ..platform import get_soc_for_board
from ..results import NsxModuleRef

log = logging.getLogger("hpx")

#: First ns-cmsis-nn release whose float kernels HPX considers measurable --
#: the core heliaRT 1.19.0 targets. The baseline ref predates it; see
#: docs/architecture/compatibility-baseline.md.
CMSIS_NN_FLOAT_MIN_REF = "v7.31.0"

# NSX registry identity for ns-cmsis-nn. By default hpx declares this module
# and lets NSX clone it from the registered GitHub upstream; a user-provided
# local path (cmsis_nn_path / CMSIS_NN_PATH) vendors it instead.
CMSIS_NN_PROJECT = "ns-cmsis-nn"  # registry project (path: modules/ns-cmsis-nn)
CMSIS_NN_MODULE = "nsx-cmsis-nn"  # registry module name


def _kernel_family(family: str) -> dict[str, str]:
    """Both spellings of one float kernel switch.

    ``NSX_CMSIS_NN_ENABLE_*`` is ns-cmsis-nn's option (source selection; what
    heliaRT checks). ``ARM_NN_ENABLE_*`` is the define it exports, which
    heliaAOT's generated module checks in the CMake cache -- ns-cmsis-nn only
    derives it as a directory-scoped variable, so the cache copy is set here.
    """
    return {f"NSX_CMSIS_NN_ENABLE_{family}": "ON", f"ARM_NN_ENABLE_{family}": "ON"}


def _float_compute_types(config: ProfileConfig) -> set[int]:
    """Float precisions the model computes in; empty when the file is unreadable
    (the preflight stage has already rejected anything that is not a flatbuffer)."""
    try:
        return read_float_compute_types(Path(config.model.path).read_bytes())
    except (OSError, struct.error, IndexError):
        return set()


def _core_is_overridden(config: ProfileConfig) -> bool:
    return bool(
        config.engine.config.get("cmsis_nn_ref")
        or config.engine.config.get("cmsis_nn_path")
        or os.environ.get("CMSIS_NN_PATH")
    )


def cmsis_nn_cmake_vars(config: ProfileConfig) -> dict[str, str]:
    """CMake cache options for a source-built ``nsx-cmsis-nn`` module.

    Rendered as ``CACHE ... FORCE`` before any module is added -- modules read
    these while they are included, so setting them later is too late.

    * ``NSX_CMSIS_NN_USE_REQUANTIZE_INLINE_ASM`` -- on unless
      ``engine.config.cmsis_nn_requantize_inline_asm`` is false.
    * fp32 kernels -- always on (heliaRT >= 1.19 refuses to configure without
      them; heliaAOT float models call them directly).
    * fp16 kernels -- on only when the model computes in FLOAT16 on an MVE-F
      core (Cortex-M55). Never otherwise: ns-cmsis-nn < v7.30.0's fp16 sources
      ICE on GCC 14 (PR 118460), so an int8/fp32 build must not compile them.

    A float model on the baseline ns-cmsis-nn ref gets a warning: those float
    kernels predate the fixes in :data:`CMSIS_NN_FLOAT_MIN_REF`.
    """
    cmake_vars: dict[str, str] = {}
    if config.engine.config.get("cmsis_nn_requantize_inline_asm", True):
        cmake_vars["NSX_CMSIS_NN_USE_REQUANTIZE_INLINE_ASM"] = "ON"
    cmake_vars |= _kernel_family("F32")

    float_types = _float_compute_types(config)
    if float_types and not _core_is_overridden(config):
        log.warning(
            "%s computes in float, but ns-cmsis-nn is the baseline ref whose float "
            "kernels predate the %s fixes; set engine.config.cmsis_nn_ref to %s or "
            "newer for float measurements (the run is still stamped qualified)",
            Path(config.model.path).name,
            CMSIS_NN_FLOAT_MIN_REF,
            CMSIS_NN_FLOAT_MIN_REF,
        )
    soc = get_soc_for_board(config.target.board, registry=config.platform_registry)
    if soc.has_mve and TENSOR_TYPE_FLOAT16 in float_types:
        cmake_vars |= _kernel_family("F16")
    return cmake_vars


def cmsis_nn_module_ref(config: ProfileConfig, work_dir: Path) -> NsxModuleRef:
    """Resolve the ns-cmsis-nn NSX module reference.

    By default the module is resolved from the NSX registry (NSX clones it
    from the registered GitHub upstream during ``nsx sync``). When the user
    provides a local checkout via ``engine.config.cmsis_nn_path`` or the
    ``CMSIS_NN_PATH`` environment variable, it is vendored as a local module
    under its registry-derived project directory (``modules/ns-cmsis-nn``).
    """
    configured_path = config.engine.config.get("cmsis_nn_path")
    requested_ref = config.engine.config.get("cmsis_nn_ref")
    if configured_path and requested_ref:
        raise EngineError(
            "engine.config.cmsis_nn_path and cmsis_nn_ref are mutually exclusive"
        )

    # Explicit config always wins over the legacy environment fallback. This
    # matters in hardware CI, where a resolved commit must remain a git-backed
    # NSX lock entry rather than silently becoming an unversioned local module.
    raw = configured_path or (None if requested_ref else os.environ.get("CMSIS_NN_PATH"))
    if raw:
        cmsis_nn_path = Path(str(raw)).expanduser().resolve()
        _validate_cmsis_nn(cmsis_nn_path)
        mod_dir = work_dir / "modules" / CMSIS_NN_PROJECT
        _write_cmsis_nn_wrapper(mod_dir, cmsis_nn_path)
        log.info("ns-cmsis-nn: vendoring local module from %s", cmsis_nn_path)
        return NsxModuleRef(
            name=CMSIS_NN_MODULE,
            path=mod_dir,
            local=True,
            project=CMSIS_NN_PROJECT,
        )

    if requested_ref is not None and (
        not isinstance(requested_ref, str) or not requested_ref.strip()
    ):
        raise EngineError("engine.config.cmsis_nn_ref must be a non-empty git ref")

    log.info(
        "ns-cmsis-nn — resolving %s from NSX registry (project=%s%s)",
        CMSIS_NN_MODULE,
        CMSIS_NN_PROJECT,
        f", ref={requested_ref}" if requested_ref else "",
    )
    return NsxModuleRef(
        name=CMSIS_NN_MODULE,
        path=Path(),
        local=False,
        project=CMSIS_NN_PROJECT,
        # None must reach the dependency-lock digest as null; "" is a different key.
        ref=requested_ref,
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

    Uses the native ``nsx/`` module that ships with ns-cmsis-nn (>= v7.23.0).
    A thin root shim delegates to ``nsx/CMakeLists.txt`` so that its
    ``../Source`` relative paths resolve correctly against the copied
    Source/ tree.
    """
    module_dir.mkdir(parents=True, exist_ok=True)

    native_nsx = cmsis_nn_path / "nsx"
    if (
        not (native_nsx / "CMakeLists.txt").is_file()
        or not (native_nsx / "nsx-module.yaml").is_file()
    ):
        raise EngineError(
            f"ns-cmsis-nn at {cmsis_nn_path} is missing native nsx/ module",
            hint=(
                "Expected nsx/CMakeLists.txt and nsx/nsx-module.yaml. "
                "Use ns-cmsis-nn >= v7.23.0 (AmbiqAI/ns-cmsis-nn)."
            ),
        )

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
        "# Shim — delegates to the native ns-cmsis-nn NSX build.\nadd_subdirectory(nsx)\n"
    )

    # Copy the CMSIS-NN source tree into the module (no symlinks — Windows-safe)
    for d in ("Include", "Source", "cmake"):
        target = module_dir / d
        source = cmsis_nn_path / d
        if not source.is_dir():
            continue
        if target.is_dir():
            shutil.rmtree(target)
        shutil.copytree(source, target)
