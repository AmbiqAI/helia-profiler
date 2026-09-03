"""ns-cmsis-nn (heliaCORE) NSX module resolution and build options.

Shared by the heliaRT, heliaAOT, and ExecuTorch-ns source routes: the module is
declared at the compatibility baseline's qualified ref unless the user overrides
it, and :func:`cmsis_nn_cmake_vars` supplies the kernel switches it needs.
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

# NSX registry identity for ns-cmsis-nn.
CMSIS_NN_PROJECT = "ns-cmsis-nn"  # registry project (path: modules/ns-cmsis-nn)
CMSIS_NN_MODULE = "nsx-cmsis-nn"  # registry module name


def _kernel_family(family: str) -> dict[str, str]:
    """Both spellings of one float kernel switch.

    WORKAROUND helia-aot#349: heliaRT checks ns-cmsis-nn's ``NSX_CMSIS_NN_*``
    option, heliaAOT's generated module checks the exported ``ARM_NN_*`` define.
    """
    return {f"NSX_CMSIS_NN_ENABLE_{family}": "ON", f"ARM_NN_ENABLE_{family}": "ON"}


def _float_compute_types(config: ProfileConfig) -> set[int]:
    """Float precisions the model works in; empty when the file is unreadable."""
    try:
        return read_float_compute_types(Path(config.model.path).read_bytes())
    except (OSError, struct.error, IndexError):
        return set()


def cmsis_nn_cmake_vars(config: ProfileConfig) -> dict[str, str]:
    """CMake cache options for a source-built ``nsx-cmsis-nn`` module.

    The template renders these before any module is included (an ``option()``
    default cannot be overridden afterwards). Requantize inline-asm is
    configurable; fp32 kernels are always on (helia-rt#253); fp16 kernels only
    for a model carrying FLOAT16 tensors on an MVE-F core (helia-rt#254).
    """
    cmake_vars: dict[str, str] = {}
    if config.engine.config.get("cmsis_nn_requantize_inline_asm", True):
        cmake_vars["NSX_CMSIS_NN_USE_REQUANTIZE_INLINE_ASM"] = "ON"
    cmake_vars |= _kernel_family("F32")
    soc = get_soc_for_board(config.target.board, registry=config.platform_registry)
    if soc.has_mve and TENSOR_TYPE_FLOAT16 in _float_compute_types(config):
        cmake_vars |= _kernel_family("F16")
    return cmake_vars


def _baseline_cmsis_nn_ref(config: ProfileConfig) -> str:
    """The compatibility baseline's qualified ``nsx-cmsis-nn`` ref."""
    return config.compatibility_baseline.module(CMSIS_NN_MODULE).ref


def cmsis_nn_module_ref(config: ProfileConfig, work_dir: Path) -> NsxModuleRef:
    """Resolve the ns-cmsis-nn NSX module reference.

    Declared at the baseline's qualified ref by default (the packaged registry's
    own default is older than heliaAOT accepts -- helia-aot#356);
    ``engine.config.cmsis_nn_ref`` overrides the ref, ``cmsis_nn_path`` /
    ``CMSIS_NN_PATH`` vendors a local checkout under ``modules/ns-cmsis-nn``.
    """
    configured_path = config.engine.config.get("cmsis_nn_path")
    requested_ref = config.engine.config.get("cmsis_nn_ref")
    if configured_path and requested_ref:
        raise EngineError("engine.config.cmsis_nn_path and cmsis_nn_ref are mutually exclusive")

    # Explicit config wins over the environment fallback so a resolved commit
    # stays a git-backed lock entry rather than an unversioned local module.
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

    ref = requested_ref or _baseline_cmsis_nn_ref(config)
    log.info(
        "ns-cmsis-nn — resolving %s from NSX registry (project=%s, ref=%s)",
        CMSIS_NN_MODULE,
        CMSIS_NN_PROJECT,
        ref,
    )
    return NsxModuleRef(
        name=CMSIS_NN_MODULE,
        path=Path(),
        local=False,
        project=CMSIS_NN_PROJECT,
        # None must reach the dependency-lock digest as null; "" is a different key.
        ref=ref,
    )


def _validate_cmsis_nn(path: Path) -> None:
    """Verify that *path* looks like an ns-cmsis-nn checkout.

    The header-revision heuristic below is known-stale (#247).
    """
    if not path.is_dir():
        raise EngineError(f"CMSIS-NN path does not exist: {path}")
    for d in ("Include", "Source"):
        if not (path / d).is_dir():
            raise EngineError(
                f"CMSIS-NN path missing '{d}/' directory: {path}",
                hint="Expected an ns-cmsis-nn repository with Include/ and Source/.",
            )

    # WORKAROUND #247: this revision heuristic no longer separates the fork from upstream.
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
