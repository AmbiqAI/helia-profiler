"""heliaRT engine adapter.

Generates a local NSX wrapper module for heliaRT prebuilt static libraries.
The wrapper is a temporary shim until heliaRT ships a native nsx-module.yaml.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import jinja2

from ..config import ProfileConfig
from ..errors import EngineError
from ..platform import CoreArch, get_soc
from ..results import NsxModuleRef
from .base import EngineArtifacts

log = logging.getLogger("hpx")

# ---------------------------------------------------------------------------
# Pinned heliaRT release — bump this when a new release is adopted.
# ---------------------------------------------------------------------------
HELIART_VERSION = "1.7.0"
HELIART_GH_REPO = "AmbiqAI/helia-rt"
HELIART_RELEASE_TAG = f"v{HELIART_VERSION}"
HELIART_ASSET_PREFIX = f"nsx-heliart-{HELIART_RELEASE_TAG}"

# ---------------------------------------------------------------------------
# Jinja2 template environment
# ---------------------------------------------------------------------------

_jinja_env = jinja2.Environment(
    loader=jinja2.PackageLoader("helia_profiler.engines", "templates"),
    keep_trailing_newline=True,
    undefined=jinja2.StrictUndefined,
)


def _core_tag(board: str) -> str:
    """Map a board name to the heliaRT library core tag (cm4 or cm55)."""
    soc = get_soc(_board_to_soc(board))
    if soc.core is CoreArch.CORTEX_M55:
        return "cm55"
    return "cm4"


def _board_to_soc(board: str) -> str:
    """Resolve board name to SoC name via the platform registry."""
    from ..platform import get_board

    return get_board(board).soc


class HeliaRTAdapter:
    """Adapter for heliaRT — Ambiq's optimized TFLM fork.

    Generates a local NSX wrapper module that makes a pinned heliaRT release
    appear as ``nsx::heliart`` to the profiler firmware's CMake build.  The
    wrapper is placed inside ``work_dir/modules/nsx-heliart/``.

    Once heliaRT ships a native ``nsx-module.yaml``, this adapter can simply
    declare a module dependency and skip wrapper generation entirely.
    """

    @property
    def name(self) -> str:
        return "heliaRT"

    def prepare(self, config: ProfileConfig, work_dir: Path) -> EngineArtifacts:
        backend = config.engine.backend or "helia"
        variant = config.engine.config.get("variant", "release-with-logs")

        # Validate variant
        valid_variants = ("debug", "release-with-logs", "release")
        if variant not in valid_variants:
            raise EngineError(
                f"Invalid heliaRT variant '{variant}'",
                hint=f"Valid variants: {', '.join(valid_variants)}",
            )

        # Resolve the heliaRT distribution path
        dist_path = _resolve_dist_path(config)

        # Generate the NSX wrapper module
        module_dir = work_dir / "modules" / "nsx-heliart"
        module_dir.mkdir(parents=True, exist_ok=True)

        _write_wrapper(module_dir, variant=variant)
        _link_distribution(module_dir, dist_path)

        log.info(
            "Generated NSX wrapper for heliaRT %s (variant=%s, dist=%s)",
            HELIART_VERSION,
            variant,
            dist_path,
        )

        return EngineArtifacts(
            extra_modules=[
                NsxModuleRef(
                    name="nsx-heliart",
                    path=module_dir,
                    version=HELIART_VERSION,
                ),
            ],
            template_vars={
                "engine_type": "helia_rt",
                "engine_backend": backend,
                "engine_header": "tensorflow/lite/micro/micro_interpreter.h",
                "heliart_version": HELIART_VERSION,
                "heliart_variant": variant,
            },
        )


def _write_wrapper(module_dir: Path, *, variant: str) -> None:
    """Write the NSX wrapper files into *module_dir*."""
    (module_dir / "nsx-module.yaml").write_text(
        _jinja_env.get_template("heliart_nsx_module.yaml.j2").render(
            version=HELIART_VERSION,
        ),
    )
    (module_dir / "CMakeLists.txt").write_text(
        _jinja_env.get_template("heliart_CMakeLists.txt.j2").render(
            version=HELIART_VERSION,
            variant=variant,
        ),
    )


# ---------------------------------------------------------------------------
# heliaRT distribution resolution
# ---------------------------------------------------------------------------

# Directories from the heliaRT release that must be present in the wrapper.
_DIST_DIRS = ("lib", "tensorflow", "third_party", "signal")


def _resolve_dist_path(config: ProfileConfig) -> Path:
    """Resolve the heliaRT distribution directory.

    Checks (in order):
    1. ``engine.config.dist_path`` — explicit user-provided path
    2. ``HELIART_DIST_PATH`` environment variable
    3. Raise an error with helpful guidance.
    """
    # 1. Explicit config
    raw = config.engine.config.get("dist_path")
    if raw:
        p = Path(raw).expanduser().resolve()
        _validate_dist(p)
        return p

    # 2. Environment variable
    env = os.environ.get("HELIART_DIST_PATH")
    if env:
        p = Path(env).expanduser().resolve()
        _validate_dist(p)
        return p

    raise EngineError(
        "heliaRT distribution path not provided",
        hint=(
            "Set engine.config.dist_path in your config YAML, "
            "or export HELIART_DIST_PATH to the directory containing "
            f"the heliaRT {HELIART_VERSION} release (lib/, tensorflow/, third_party/)."
        ),
    )


def _validate_dist(dist: Path) -> None:
    """Verify that *dist* looks like a heliaRT release directory."""
    if not dist.is_dir():
        raise EngineError(
            f"heliaRT dist path does not exist: {dist}",
            hint="Provide a valid directory containing the heliaRT release.",
        )
    for d in _DIST_DIRS:
        if not (dist / d).is_dir():
            raise EngineError(
                f"heliaRT dist missing '{d}/' directory: {dist}",
                hint=f"Expected: {', '.join(d + '/' for d in _DIST_DIRS)}",
            )


def _link_distribution(module_dir: Path, dist_path: Path) -> None:
    """Copy heliaRT distribution directories into the wrapper module.

    Copies lib/, tensorflow/, and third_party/ so the CMake build can find
    them via ``CMAKE_CURRENT_LIST_DIR``.  Uses copies instead of symlinks
    for Windows compatibility.
    """
    import shutil

    for d in _DIST_DIRS:
        target = module_dir / d
        source = dist_path / d
        if target.is_dir():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        log.debug("Copied %s → %s", target, source)
