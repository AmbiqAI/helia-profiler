"""Board → heliaAOT platform mapping and AOT compiler invocation.

heliaAOT has its own platform registry (apollo3p_evb, apollo4p_evb,
apollo510_evb, …). The profiler board names are close but not always
identical; this module maps profiler boards onto AOT platform names, derives
per-kind (constant/persistent/scratch) tensor placement rulesets from the
profiler's memory-placement config, invokes the heliaAOT Python API to
compile a ``.tflite`` model into an NSX module, validates the generated
memory-placement pragmas, and enforces the installed ``helia-aot`` package's
minimum-supported version.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import jinja2

from ...config import ProfileConfig
from ...errors import EngineError
from ...placement import ModelLocation, Placement
from ...platform import SocDef, get_soc_for_board

log = logging.getLogger("hpx")

# ---------------------------------------------------------------------------
# heliaAOT version policy
#
# heliaAOT ships as a Python package, so version resolution is handled
# entirely by pip. heliaAOT is not on PyPI, so the [aot] extra in
# helia-aot is published on PyPI. Users get three install modes:
#
#   1. Default       : pip install 'helia-profiler[aot]'
#                      → installs the version pinned in pyproject.toml.
#   2. Specific ver.  : pip install 'helia-aot>=0.18.0'
#   3. Local checkout: pip install -e /path/to/helia-aot
#
# We don't manage downloads/caches like we do for heliaRT — pip already
# does that better. We just enforce a minimum-supported version at runtime
# so a user with an older install gets a clear error instead of a confusing
# build failure (e.g. missing ModuleType.nsx).
# ---------------------------------------------------------------------------
HELIAAOT_MIN_VERSION = "0.18.0"

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
    "apollo4p_blue_kbr_evb": "apollo4p_blue_kbr_evb",
    "apollo4p_blue_kxr_evb": "apollo4p_blue_kxr_evb",
    "apollo4l_evb": "apollo4l_evb",
    "apollo4l_blue_evb": "apollo4l_blue_evb",
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
    "PUT_IN_DRAM",
    "PUT_IN_DRAM_INIT",
    "PUT_IN_SRAM",
    "PUT_IN_SRAM_INIT",
    "PUT_IN_MRAM",
    "PUT_IN_MRAM_INIT",
    "PUT_IN_PSRAM",
    "PUT_IN_PSRAM_INIT",
    "PUT_IN_ITCM",
    "PUT_IN_ITCM_INIT",
)


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
            hint=(f"Set engine.config.platform_name explicitly, or use a supported board: {known}"),
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
# Per-kind tensor placement → heliaAOT attribute rulesets
#
# heliaAOT splits the model into three AIR tensor kinds — ``constant``
# (read-only weights), ``persistent`` (read-write state) and ``scratch``
# (transient activations) — each planned into its own arena.  Compatibility
# ``model_location`` presets map onto these three kinds, but precise AOT
# placement belongs in ``engine.config.aot_args.memory.tensors`` where users can
# specify constant/persistent/scratch rules directly.
# ---------------------------------------------------------------------------

_PLACEMENT_TO_AOT_MEMTYPE: dict[Placement, str] = {
    Placement.TCM: "dtcm",
    Placement.SRAM: "sram",
    Placement.MRAM: "mram",
    Placement.PSRAM: "psram",
}

# Extra AOT physical-memory-name strings that map onto an existing Placement
# but aren't its canonical string above (e.g. heliaAOT's "itcm" kind also
# means "tightly-coupled", same logical region as "dtcm"). Kept separate from
# _PLACEMENT_TO_AOT_MEMTYPE so that dict stays a clean 1:1 canonical mapping.
_AOT_MEMORY_ALIASES: dict[str, Placement] = {
    "itcm": Placement.TCM,
}


def _resolve_aot_placement_intent(
    config: ProfileConfig, soc: SocDef | None
) -> tuple[Placement, Placement] | None:
    """Resolve ``(arena, weights)`` placement for AOT from the profiler config.

    ``arena`` covers the read-write scratch + persistent tensors; ``weights``
    covers the read-only constants.
    """
    location = config.model.model_location
    has_tcm = bool(soc and soc.memory.dtcm_kb > 0)

    if location == ModelLocation.AUTO:
        arena = Placement.TCM if has_tcm else Placement.SRAM
        weights = Placement.MRAM
        return arena, weights

    if location == ModelLocation.TCM:
        arena = weights = Placement.TCM
    elif location == ModelLocation.SRAM:
        arena = weights = Placement.SRAM
    elif location == ModelLocation.PSRAM:
        arena = Placement.SRAM
        weights = Placement.PSRAM
    else:  # AUTO (with override) or MRAM: arena in fastest RAM, weights in MRAM
        arena = Placement.TCM if has_tcm else Placement.SRAM
        weights = Placement.MRAM

    return arena, weights


def _resolve_aot_tensor_rulesets(
    config: ProfileConfig, soc: SocDef | None
) -> list[dict[str, Any]]:
    """Build heliaAOT per-kind attribute rulesets (constant/persistent/scratch).
    """
    intent = _resolve_aot_placement_intent(config, soc)
    arena, weights = intent
    arena_mem = _PLACEMENT_TO_AOT_MEMTYPE[arena]

    # scratch + persistent are read-write: their runtime memory is the arena.
    rulesets: list[dict[str, Any]] = [
        {"type": "scratch", "attributes": {"memory": arena_mem}},
        {"type": "persistent", "attributes": {"memory": arena_mem}},
    ]

    # Constants are read-only: their cold source must be non-volatile (MRAM, or
    # XIP PSRAM).  When the requested weights region is writable RAM (TCM/SRAM),
    # keep the cold blob in MRAM and stage a runtime copy there via
    # ``constant_destination_memory``.
    if weights in (Placement.MRAM, Placement.PSRAM):
        rulesets.append(
            {"type": "constant", "attributes": {"memory": _PLACEMENT_TO_AOT_MEMTYPE[weights]}}
        )
    else:
        rulesets.append(
            {
                "type": "constant",
                "attributes": {
                    "memory": "mram",
                    "constant_destination_memory": _PLACEMENT_TO_AOT_MEMTYPE[weights],
                },
            }
        )
    return rulesets


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
            hint=("Install helia-aot: pip install 'helia-profiler[aot]' or pip install helia-aot"),
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

    # Pin the three AIR tensor kinds (constant/persistent/scratch) onto the
    # profiler's requested memories via wildcard attribute rulesets.  These are
    # the *base* rules; any user-supplied ``aot_args.memory.tensors`` are kept
    # and appended after so they take precedence (equal-specificity ties resolve
    # to the later rule; explicit per-id rules are strictly more specific).
    profiler_rulesets = _resolve_aot_tensor_rulesets(
        config, get_soc_for_board(config.target.board, registry=config.platform_registry)
    )
    if profiler_rulesets:
        mem = base_data.setdefault("memory", {})
        user_tensors = mem.get("tensors") or []
        mem["tensors"] = profiler_rulesets + list(user_tensors)
        log.info(
            "AOT tensor placement: scratch/persistent=%s, constant=%s",
            profiler_rulesets[0]["attributes"]["memory"],
            profiler_rulesets[2]["attributes"],
        )

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
        config.model.path,
        output_dir,
        module_name,
        aot_platform,
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
            "AOT platform header not found (%s) — cannot validate memory-placement macros.",
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


# ---------------------------------------------------------------------------
# helia-aot version check
# ---------------------------------------------------------------------------


def _check_helia_aot_version() -> None:
    """Verify the installed ``helia-aot`` package satisfies the floor.

    Raises ``EngineError`` with installation guidance if the package is
    missing or older than ``HELIAAOT_MIN_VERSION``. Logs the detected
    version on success so it shows up in run logs.
    """
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    try:
        installed = _pkg_version("helia-aot")
    except PackageNotFoundError as exc:
        raise EngineError(
            "helia-aot is not installed.",
            hint=(
                "Install the AOT engine extra:\n"
                "  pip install 'helia-profiler[aot]'\n"
                "or pin a specific version / fork / local checkout, e.g.:\n"
                "  pip install helia-aot==X.Y.Z\n"
                "  pip install 'git+https://github.com/<fork>/helia-aot.git@<ref>'\n"
                "  pip install -e /path/to/helia-aot"
            ),
        ) from exc

    actual = _parse_semver(installed)
    minimum = _parse_semver(HELIAAOT_MIN_VERSION)
    if actual == (0, 0, 0):
        log.warning(
            "Could not parse helia-aot version %r — skipping floor check (min supported: v%s)",
            installed,
            HELIAAOT_MIN_VERSION,
        )
        return

    if actual < minimum:
        raise EngineError(
            f"helia-aot v{installed} is below the minimum supported "
            f"version (v{HELIAAOT_MIN_VERSION}).",
            hint=(
                f"Upgrade with: pip install -U 'helia-aot>={HELIAAOT_MIN_VERSION}'\n"
                "or pin a specific newer version / fork / local checkout."
            ),
        )

    log.debug("Using helia-aot v%s (>= floor v%s).", installed, HELIAAOT_MIN_VERSION)


def _parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a semver-ish string into (major, minor, patch); (0,0,0) on failure."""
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version)
    if not m:
        return (0, 0, 0)
    return int(m.group(1)), int(m.group(2)), int(m.group(3))
