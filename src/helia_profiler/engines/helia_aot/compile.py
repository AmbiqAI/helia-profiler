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

from ...config import DEFAULT_ARENA_SIZE_BYTES, ProfileConfig
from ...errors import EngineError
from ...placement import Placement, resolve_fastest_fit_placement
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
HELIAAOT_MAX_VERSION_EXCLUSIVE = "0.19.0"

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
# (transient activations) — each planned into its own arena. Coarse model
# arena/weights controls map onto these kinds, while precise AOT placement
# belongs in ``engine.config.aot_args.memory.tensors``.
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
) -> tuple[Placement, Placement]:
    """Resolve ``(arena, weights)`` placement for AOT from the profiler config.

    ``arena`` covers the read-write scratch + persistent tensors; ``weights``
    covers the read-only constants.
    """
    try:
        weights_size = config.model.path.stat().st_size
    except OSError:
        weights_size = 0
    arena, weights = resolve_fastest_fit_placement(
        arena_size=config.model.arena_size or DEFAULT_ARENA_SIZE_BYTES,
        weights_size=weights_size,
        tcm_cap=soc.memory.dtcm_kb * 1024 if soc else 1 << 31,
        sram_cap=soc.memory.sram_kb * 1024 if soc else 1 << 31,
    )
    # Explicit locations are preflight-validated; Placement() is identity for members.
    if config.model.arena_location:
        arena = Placement(config.model.arena_location)
    if config.model.weights_location:
        weights = Placement(config.model.weights_location)
    return arena, weights


def _resolve_aot_tensor_rulesets(
    config: ProfileConfig, soc: SocDef | None
) -> list[dict[str, Any]]:
    """Build heliaAOT per-kind attribute rulesets (constant/persistent/scratch).
    """
    arena, weights = _resolve_aot_placement_intent(config, soc)
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
        from helia_aot.cli.defines import ConvertArgs  # type: ignore[import-untyped]
        from helia_aot.converter import AotConverter  # type: ignore[import-untyped]
        from helia_aot.defines import ModuleType  # type: ignore[import-untyped]
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
        with open(cfg_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if not isinstance(loaded, dict):
            raise EngineError(
                "heliaAOT config must contain a YAML mapping",
                hint="Check engine.config_path and use key/value YAML fields.",
            )
        base_data = loaded

    # Merge any engine.config.aot_args overrides (dict form)
    extra = config.engine.config.get("aot_args", {})
    if isinstance(extra, dict):
        _deep_merge(base_data, extra)

    # Pin the three AIR tensor kinds (constant/persistent/scratch) onto the
    # profiler's requested memories via wildcard attribute rulesets.  A
    # user-supplied wildcard for a kind replaces the profiler's coarse rule;
    # this is important for constants because the coarse rule may also carry a
    # ``constant_destination_memory`` staging attribute.
    profiler_rulesets = _resolve_aot_tensor_rulesets(
        config, get_soc_for_board(config.target.board, registry=config.platform_registry)
    )
    mem, user_tensors = _prepare_aot_memory_config(base_data)
    merged_rulesets = _merge_aot_tensor_rulesets(profiler_rulesets, user_tensors)
    if merged_rulesets:
        mem["tensors"] = merged_rulesets
        scratch_memory, constant_attributes = _summarize_aot_tensor_rulesets(
            merged_rulesets
        )
        log.info(
            "AOT tensor placement: scratch/persistent=%s, constant=%s",
            scratch_memory,
            constant_attributes,
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


def _merge_aot_tensor_rulesets(
    profiler_rulesets: list[dict[str, Any]],
    user_tensors: list[Any],
) -> list[Any]:
    """Merge user AOT tensor rules without leaking coarse attributes.

    A type-only user rule is a wildcard for that tensor kind.  More specific
    user rules (for example, a constant selected by ``id``) remain additive
    and override the matching wildcard in heliaAOT.
    """
    wildcard_kinds = {
        str(rule.get("type"))
        for rule in user_tensors
        if isinstance(rule, dict) and "type" in rule and "id" not in rule
    }
    return [
        rule for rule in profiler_rulesets if rule.get("type") not in wildcard_kinds
    ] + list(user_tensors)


def _prepare_aot_memory_config(
    base_data: dict[str, Any],
) -> tuple[dict[str, Any], list[Any]]:
    """Validate the free-form AOT memory config before merging placement rules."""

    raw_memory = base_data.get("memory")
    if raw_memory is None:
        memory: dict[str, Any] = {}
        base_data["memory"] = memory
    elif isinstance(raw_memory, dict):
        memory = raw_memory
    else:
        raise EngineError(
            "engine.config.aot_args.memory must be a mapping",
            hint="Use memory: {tensors: [...]} in the heliaAOT configuration.",
        )

    raw_tensors = memory.get("tensors")
    if raw_tensors is None:
        return memory, []
    if not isinstance(raw_tensors, list):
        raise EngineError(
            "engine.config.aot_args.memory.tensors must be a list",
            hint="Provide a YAML list of heliaAOT tensor placement rules.",
        )
    return memory, raw_tensors


def _summarize_aot_tensor_rulesets(
    rulesets: list[Any],
) -> tuple[Any, Any]:
    """Return logging-only placement summaries without validating user rules."""

    by_kind: dict[str, dict[str, Any]] = {}
    for rule in rulesets:
        if not isinstance(rule, dict):
            continue
        kind = rule.get("type")
        attributes = rule.get("attributes")
        if isinstance(kind, str) and isinstance(attributes, dict):
            by_kind.setdefault(kind, attributes)

    scratch_memory = by_kind.get("scratch", {}).get("memory", "custom")
    constant_attributes: Any = by_kind.get("constant", "custom")
    return scratch_memory, constant_attributes


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

    content = platform_h.read_text(encoding="utf-8")

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
        ),
        # The template carries non-ASCII (arrows/em-dashes in comments);
        # Windows' default cp1252 raised UnicodeEncodeError the first time
        # a test exercised this write (surfaced by PR #98's branch).
        encoding="utf-8",
    )
    return header_path


# ---------------------------------------------------------------------------
# helia-aot version check
# ---------------------------------------------------------------------------


def _check_helia_aot_version(config: ProfileConfig | None = None) -> str:
    """Verify the installed ``helia-aot`` package satisfies the qualified range.

    Raises ``EngineError`` with installation guidance if the package is
    missing, older than the minimum version, or at/above the exclusive
    maximum version. When the baseline sets an explicit range policy for
    ``helia-aot`` (either bound), it is used standalone — a bound the
    baseline leaves unset is treated as unbounded, not backfilled from
    ``HELIAAOT_MIN_VERSION`` / ``HELIAAOT_MAX_VERSION_EXCLUSIVE``, since a
    single-sided baseline range is intentionally allowed (see
    ``_parse_baseline()``). The local constants are the fallback only when
    no baseline is resolved at all, or the baseline governs ``helia-aot``
    some other way (pinned ``version`` or ``governed_by_modules``). Logs
    the detected version on success so it shows up in run logs.
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
    maximum: tuple[int, int, int] | None = _parse_semver(HELIAAOT_MAX_VERSION_EXCLUSIVE)
    if config is not None and config.compatibility is not None:
        policy = config.compatibility.baseline.engine("helia-aot")
        if policy.min_version is not None or policy.max_version_exclusive is not None:
            # The baseline sets an explicit range policy (validated to allow
            # a single-sided range — see _parse_baseline()). Use it standalone
            # rather than layering it on top of the local constants: falling
            # back to HELIAAOT_MAX_VERSION_EXCLUSIVE for a baseline that only
            # sets min_version (or vice versa) could silently re-bound the
            # baseline's floor with an unrelated constant ceiling, rejecting
            # every version instead of leaving that side unbounded.
            minimum = (
                _parse_semver(policy.min_version) if policy.min_version is not None else (0, 0, 0)
            )
            maximum = (
                _parse_semver(policy.max_version_exclusive)
                if policy.max_version_exclusive is not None
                else None
            )
    minimum_str = f"{minimum[0]}.{minimum[1]}.{minimum[2]}"
    maximum_str = f"{maximum[0]}.{maximum[1]}.{maximum[2]}" if maximum is not None else "unbounded"
    # Log messages below use minimum_display/maximum_display (not the bare
    # *_str values) so the range renders as ">=v0.18.0, <unbounded" instead
    # of the malformed "<vunbounded" that a hard-coded "v" prefix would
    # produce once max_version_exclusive is left unset.
    minimum_display = f"v{minimum_str}"
    maximum_display = "unbounded" if maximum is None else f"v{maximum_str}"
    if actual == (0, 0, 0):
        log.warning(
            "Could not parse helia-aot version %r — skipping qualified-range "
            "check (supported: >=%s, <%s).",
            installed,
            minimum_display,
            maximum_display,
        )
        return installed

    if actual < minimum:
        raise EngineError(
            f"helia-aot v{installed} is below the minimum supported "
            f"version (v{minimum_str}).",
            hint=(
                f"Upgrade with: pip install -U 'helia-aot>={minimum_str}'\n"
                "or pin a specific newer version / fork / local checkout."
            ),
        )

    if maximum is not None and actual >= maximum:
        raise EngineError(
            f"helia-aot v{installed} is outside the qualified policy "
            f"(supported: >={minimum_str}, <{maximum_str})",
            hint=(
                f"Install a qualified helia-aot >={minimum_str},<{maximum_str} release "
                "or use an explicit development setup."
            ),
        )

    log.debug(
        "Using helia-aot v%s (qualified range: >=%s, <%s).",
        installed,
        minimum_display,
        maximum_display,
    )

    return installed


def _parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a semver-ish string into (major, minor, patch); (0,0,0) on failure."""
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version)
    if not m:
        return (0, 0, 0)
    return int(m.group(1)), int(m.group(2)), int(m.group(3))
