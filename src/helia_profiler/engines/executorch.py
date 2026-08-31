"""ExecuTorch engine adapter for NSX target profiling."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..config import ProfileConfig
from ..errors import EngineError
from ..results import NsxModuleRef
from . import EngineType
from .base import ExecutorchArtifacts, PsramWeightsSource, SingleArenaPlacementMixin

log = logging.getLogger("hpx")

EXECUTORCH_MODULE = "nsx-executorch"
EXECUTORCH_PROJECT = "nsx-executorch"
ARM_CMSIS_NN_MODULE = "arm-cmsis-nn"
ARM_CMSIS_NN_PROJECT = "arm-cmsis-nn"
NS_CMSIS_NN_MODULE = "nsx-cmsis-nn"
NS_CMSIS_NN_PROJECT = "ns-cmsis-nn"


# Nested ExecuTorch submodules required by nsx-executorch's stock CMake
# configuration — the "minimal Cortex-M checkout" set from its README at the
# baseline-pinned commit. Review against that README on every pin bump.
_EXECUTORCH_MINIMAL_SUBMODULES = (
    "backends/xnnpack/third-party/FXdiv",
    "third-party/flatbuffers",
    "third-party/flatcc",
    "third-party/gflags",
    "third-party/json",
)


def _manual_checkout_hint(url: str, ref: str) -> str:
    return (
        "Prepare a checkout manually and set engine.config.source_path:\n"
        f"  git clone {url} /path/to/nsx-executorch\n"
        f"  git -C /path/to/nsx-executorch checkout {ref}\n"
        "  git -C /path/to/nsx-executorch submodule update --init external/executorch\n"
        "  git -C /path/to/nsx-executorch/external/executorch submodule update --init \\\n"
        "    " + " ".join(_EXECUTORCH_MINIMAL_SUBMODULES)
    )


def _run_git(args: list[str], cwd: Path, *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        # str(exc) so a missing git binary or a timeout still reports what
        # happened, not just which subcommand died.
        detail = f": {stderr.strip() or exc}"
        raise EngineError(
            f"git {args[0]} failed while preparing the nsx-executorch auto-clone cache{detail}"
        ) from exc


def _auto_clone_nsx_executorch(url: str, ref: str) -> Path:
    """Materialize the baseline-pinned nsx-executorch checkout in the cache.

    Uses the same cache root as the ns-cmsis-nn auto-clone but a stricter
    strategy: the exact pinned ref is checked out (re-synced when the pin
    moves) and the minimal Cortex-M submodule set from the repository README
    is initialized. The caller's verification of version/ref/submodule pins
    runs unchanged afterwards, so a stale or corrupted cache can never be
    profiled silently.
    """
    try:
        return _sync_executorch_cache(url, ref)
    except EngineError as exc:
        if exc.hint is None:
            exc.hint = _manual_checkout_hint(url, ref)
        raise


def _sync_executorch_cache(url: str, ref: str) -> Path:
    from ..hostenv.cache_dirs import hpx_cache_root

    cache = hpx_cache_root() / "nsx-executorch"
    head: str | None = None
    if (cache / ".git").exists():
        try:
            head = _run_git(["rev-parse", "HEAD"], cache, timeout=30).stdout.strip()
        except EngineError as exc:
            # Only a git error is evidence of a corrupt cache; a missing git
            # binary or a timeout must not discard a good clone.
            if not isinstance(exc.__cause__, subprocess.CalledProcessError):
                raise
            log.warning("nsx-executorch: unusable cache at %s — recloning", cache)
    if head is None:
        # Remove stale partial clone
        if cache.exists():
            shutil.rmtree(cache)
        cache.parent.mkdir(parents=True, exist_ok=True)
        log.info("Cloning nsx-executorch from %s ...", url)
        _run_git(["clone", url, str(cache)], cache.parent, timeout=600)
    if head == ref:
        log.info("nsx-executorch: cache hit at %s", cache)
    else:
        log.info("nsx-executorch: syncing cache to pinned ref %s", ref)
    # The cache is machine-managed and its runs are stamped fully qualified,
    # so the pinned commit + gitlinks must define the whole tree: --force
    # checkouts discard local edits to tracked files (even when HEAD already
    # matches) and clean removes untracked files. Development changes belong
    # in a source_path checkout, never in this cache.
    try:
        _run_git(["checkout", "--force", "--detach", ref], cache, timeout=120)
    except EngineError:
        # The pin is not in the clone yet (cache predates a baseline bump).
        _run_git(["fetch", "origin", ref], cache, timeout=600)
        _run_git(["checkout", "--force", "--detach", ref], cache, timeout=120)
    _run_git(["clean", "-fdq"], cache, timeout=120)
    _run_git(
        ["submodule", "update", "--init", "--force", "external/executorch"], cache, timeout=1800
    )
    _run_git(
        ["submodule", "update", "--init", "--force", *_EXECUTORCH_MINIMAL_SUBMODULES],
        cache / "external" / "executorch",
        timeout=1800,
    )
    return cache


def _resolve_source_root(config: ProfileConfig) -> Path:
    """Resolve the nsx-executorch checkout root.

    Default: auto-clone the baseline-pinned revision into the cache.
    ``engine.config.source_path`` overrides with a development checkout.
    """
    source_value = config.engine.config.get("source_path")
    if source_value is None:
        baseline = config.compatibility_baseline
        # Clone at the same ref the checkout verification enforces; the
        # project entry contributes the URL.
        ref = baseline.engine("executorch").ref or baseline.project(EXECUTORCH_PROJECT).ref
        return _auto_clone_nsx_executorch(baseline.project(EXECUTORCH_PROJECT).url, ref)
    if not isinstance(source_value, (str, Path)) or not str(source_value).strip():
        raise EngineError(
            "engine.config.source_path must be a non-empty filesystem path",
            hint=(
                "Point it at a local nsx-executorch checkout, or omit it to "
                "auto-clone the revision pinned by the compatibility baseline."
            ),
        )
    return Path(source_value).expanduser().resolve()


def _checkout_commit(path: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise EngineError(
            f"Cannot verify git revision for ExecuTorch dependency checkout: {path}",
            hint="Use a git checkout at the exact HPX compatibility-baseline commit.",
        ) from exc
    return completed.stdout.strip()


def _gitlink_commit(path: Path, submodule: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "ls-tree", "HEAD", "--", submodule],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        fields = completed.stdout.split()
        if len(fields) >= 3 and fields[1] == "commit":
            return fields[2]
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise EngineError(
            f"Cannot verify nsx-executorch submodule pin for {submodule}",
            hint="Use the recursively initialized git checkout pinned by HPX.",
        ) from exc
    raise EngineError(
        f"nsx-executorch does not record the expected submodule: {submodule}",
        hint="Use the exact nsx-executorch commit pinned by HPX.",
    )


def _provider_module_ref(
    config: ProfileConfig, work_dir: Path, provider: str
) -> NsxModuleRef:
    """Resolve exactly one provider through the normal NSX module contract."""
    if provider == "ns":
        from .cmsis_nn import cmsis_nn_module_ref

        return cmsis_nn_module_ref(config, work_dir)

    configured_path = config.engine.config.get("cmsis_nn_path")
    requested_ref = config.engine.config.get("cmsis_nn_ref")
    if configured_path and requested_ref:
        raise EngineError(
            "engine.config.cmsis_nn_path and cmsis_nn_ref are mutually exclusive"
        )
    if configured_path is not None:
        if not isinstance(configured_path, (str, Path)) or not str(configured_path).strip():
            raise EngineError(
                "engine.config.cmsis_nn_path must be a non-empty filesystem path"
            )
        source = Path(configured_path).expanduser().resolve()
        if not (source / "nsx-module.yaml").is_file() or not (
            source / "CMakeLists.txt"
        ).is_file():
            raise EngineError(
                f"Invalid arm-cmsis-nn checkout: {source}",
                hint="Expected nsx-module.yaml and CMakeLists.txt at the repository root.",
            )
        return NsxModuleRef(
            name=ARM_CMSIS_NN_MODULE,
            path=source,
            local=True,
            project=ARM_CMSIS_NN_PROJECT,
        )
    if requested_ref is not None and (
        not isinstance(requested_ref, str) or not requested_ref.strip()
    ):
        raise EngineError("engine.config.cmsis_nn_ref must be a non-empty git ref")
    return NsxModuleRef(
        name=ARM_CMSIS_NN_MODULE,
        path=Path(),
        local=False,
        project=ARM_CMSIS_NN_PROJECT,
        # None must reach the dependency-lock digest as null; "" is a different key.
        ref=requested_ref,
    )


def _positive_int(config: dict[str, Any], name: str, default: int | None = None) -> int:
    value = config.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise EngineError(
            f"engine.config.{name} must be a positive integer",
            hint="ExecuTorch PTE tensor and arena sizes are explicit target-build inputs.",
        )
    return value


def _operator_list(config: dict[str, Any], name: str, default: list[str] | None = None) -> str:
    value = config.get(name, default if default is not None else [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EngineError(f"engine.config.{name} must be a list of operator names")
    return ",".join(value)


PTE_SIDECAR_SCHEMA = "nsx-executorch.pte-manifest/1"


def _load_pte_sidecar(model_path: Path) -> dict[str, Any] | None:
    """Load the export-time `<model>.pte.json` manifest, if one exists.

    helia-torch (nsx_cortex_m.export) writes this sidecar next to every PTE
    with the kernel and memory contract the target build needs. Sidecar
    values act only as DEFAULTS — explicit engine.config keys always win —
    but a sidecar that does not describe this exact PTE is a hard error,
    never silently ignored.
    """
    import hashlib
    import json

    sidecar = Path(f"{model_path}.json")
    if not sidecar.is_file():
        return None
    try:
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EngineError(
            f"Unreadable PTE sidecar: {sidecar}",
            hint="Re-export the model with helia-torch, or delete the sidecar.",
        ) from exc
    if manifest.get("schema") != PTE_SIDECAR_SCHEMA:
        raise EngineError(
            f"{sidecar} has unsupported sidecar schema {manifest.get('schema')!r}",
            hint=f"Expected {PTE_SIDECAR_SCHEMA}; re-export with a matching helia-torch.",
        )
    actual = hashlib.sha256(model_path.read_bytes()).hexdigest()
    if manifest.get("pte_sha256") != actual:
        raise EngineError(
            f"{sidecar} describes a different PTE (sha {manifest.get('pte_sha256')}, "
            f"model is {actual})",
            hint="Re-export the model so the PTE and its sidecar match.",
        )
    _validate_sidecar_fields(sidecar, manifest)
    return manifest


def _validate_sidecar_fields(sidecar: Path, manifest: dict[str, Any]) -> None:
    """Type-check the sidecar fields the adapter consumes.

    The sidecar is external file input; validate here so downstream reads
    can trust the shapes and a hand-edited or truncated file fails with an
    actionable error rather than a coercion surprise (e.g. the JSON string
    "false" being truthy) or an AttributeError.
    """

    def fail(field: str, expected: str) -> None:
        raise EngineError(
            f"{sidecar}: field {field!r} must be {expected}",
            hint="Re-export the model with helia-torch instead of editing the sidecar.",
        )

    provider = manifest.get("kernel_provider")
    if provider is not None and provider not in ("arm", "ns"):
        fail("kernel_provider", "'arm' or 'ns'")
    requires_ns_ops = manifest.get("requires_ns_ops")
    if requires_ns_ops is not None and not isinstance(requires_ns_ops, bool):
        fail("requires_ns_ops", "a JSON boolean")
    planned = manifest.get("planned_arena_size")
    if planned is not None and (
        not isinstance(planned, int) or isinstance(planned, bool) or planned <= 0
    ):
        fail("planned_arena_size", "a positive integer")
    for key in ("inputs", "outputs"):
        entries = manifest.get(key)
        if entries is None:
            continue
        if not isinstance(entries, list) or not all(
            isinstance(entry, dict) for entry in entries
        ):
            fail(key, "a list of tensor objects")
        for entry in entries:
            size = entry.get("size_bytes")
            if size is not None and (
                not isinstance(size, int) or isinstance(size, bool) or size <= 0
            ):
                fail(f"{key}[].size_bytes", "a positive integer")
    operators = manifest.get("operators")
    if operators is not None:
        if not isinstance(operators, dict):
            fail("operators", "an object of operator lists")
        portable = operators.get("portable")
        if portable is not None and (
            not isinstance(portable, list)
            or not all(isinstance(name, str) for name in portable)
        ):
            fail("operators.portable", "a list of operator names")


class ExecuTorchAdapter(SingleArenaPlacementMixin):
    """Prepare the local nsx-executorch module and explicit PTE contract."""

    @property
    def name(self) -> str:
        return "ExecuTorch"

    @property
    def engine_type(self) -> EngineType:
        return EngineType.EXECUTORCH

    @property
    def psram_weights_source(self) -> PsramWeightsSource:
        # The PTE program is always embedded; main_executorch.cc.j2 renders
        # no psram_info and preflight refuses the placement outright.
        return PsramWeightsSource.UNSUPPORTED

    def prepare(self, config: ProfileConfig, work_dir: Path) -> ExecutorchArtifacts:
        engine_config = config.engine.config
        sidecar = _load_pte_sidecar(config.model.path)
        provider = config.engine.backend or (sidecar or {}).get("kernel_provider") or "arm"
        if provider not in {"arm", "ns"}:
            raise EngineError("ExecuTorch backend must be 'arm' or 'ns'")

        sidecar_ns_ops = bool(sidecar.get("requires_ns_ops")) if sidecar else False
        ns_ops = engine_config.get("ns_ops", sidecar_ns_ops)
        if not isinstance(ns_ops, bool):
            raise EngineError("engine.config.ns_ops must be a boolean")
        if ns_ops and provider != "ns":
            raise EngineError(
                "engine.config.ns_ops requires engine.backend 'ns'",
                hint="The cortex_m_ns:: kernels only exist in ns-cmsis-nn.",
            )
        if sidecar_ns_ops and not ns_ops:
            raise EngineError(
                "The PTE sidecar declares cortex_m_ns:: operators, but ns_ops is disabled",
                hint="This PTE fails at Method::load without NS ops. Set "
                "engine.config.ns_ops: true and engine.backend: ns.",
            )

        def _sidecar_io(key: str) -> int | None:
            if not sidecar:
                return None
            entries = sidecar.get(key) or []
            size = entries[0].get("size_bytes") if entries else None
            return size if isinstance(size, int) and size > 0 else None

        # ExecuTorch runtime buffers are RAM-resident; the coarse
        # model.arena_location knob and the per-buffer overrides below may
        # only name RAM regions.
        if config.model.arena_location is not None and config.model.arena_location not in (
            "tcm",
            "sram",
        ):
            raise EngineError(
                f"model.arena_location '{config.model.arena_location}' is not "
                "valid for ExecuTorch runtime buffers",
                hint="Use tcm or sram; weights_location controls the PTE placement.",
            )

        def _buffer_region(name: str) -> str | None:
            value = engine_config.get(name)
            if value is None:
                return None
            if value not in ("tcm", "sram"):
                raise EngineError(
                    f"engine.config.{name} must be 'tcm' or 'sram'",
                    hint="Each ExecuTorch runtime buffer is a static RAM allocation.",
                )
            return value

        planned_region = _buffer_region("planned_arena_location")
        method_region = _buffer_region("method_arena_location")
        temporary_region = _buffer_region("temporary_arena_location")
        io_region = _buffer_region("io_location")

        planned_size = _positive_int(
            engine_config,
            "planned_arena_size",
            config.model.arena_size
            or (sidecar.get("planned_arena_size") if sidecar else None),
        )
        method_size = _positive_int(engine_config, "method_arena_size", 64 * 1024)
        temporary_size = _positive_int(engine_config, "temporary_arena_size", 32 * 1024)
        input_size = _positive_int(engine_config, "input_size", _sidecar_io("inputs"))
        output_size = _positive_int(engine_config, "output_size", _sidecar_io("outputs"))
        sidecar_portable = (
            list((sidecar.get("operators") or {}).get("portable") or []) if sidecar else None
        )

        # Materialize the checkout only after the pure-config validation above:
        # a bad YAML must fail immediately, not after a multi-minute clone.
        source_root = _resolve_source_root(config)
        # nsx-executorch is a real NSX module at its checkout root — its own
        # nsx-module.yaml + CMakeLists.txt (not a retired nsx/ subdirectory).
        if (
            not (source_root / "version.txt").is_file()
            or not (source_root / "nsx-module.yaml").is_file()
            or not (source_root / "CMakeLists.txt").is_file()
        ):
            raise EngineError(
                f"Invalid nsx-executorch checkout: {source_root}",
                hint="Expected version.txt, nsx-module.yaml, and CMakeLists.txt at the checkout root.",
            )
        expected_engine = config.compatibility_baseline.engine("executorch")
        actual_version = (source_root / "version.txt").read_text(encoding="utf-8").strip()
        if actual_version != expected_engine.version:
            raise EngineError(
                f"nsx-executorch version {actual_version!r} does not match the qualified "
                f"version {expected_engine.version!r}",
                hint="Check out the exact nsx-executorch commit pinned by HPX.",
            )
        actual_ref = _checkout_commit(source_root)
        if actual_ref != expected_engine.ref:
            raise EngineError(
                f"nsx-executorch checkout is at {actual_ref}, expected {expected_engine.ref}",
                hint="Fetch nsx-executorch main and check out the exact commit pinned by HPX.",
            )
        if not (source_root / "external" / "executorch" / "version.txt").is_file():
            raise EngineError(
                f"nsx-executorch checkout is missing its ExecuTorch submodule: {source_root}",
                hint=(
                    "Initialize external/executorch and the minimal Cortex-M submodules "
                    "listed in nsx-executorch's README."
                ),
            )
        expected_submodule_ref = _gitlink_commit(source_root, "external/executorch")
        actual_submodule_ref = _checkout_commit(source_root / "external" / "executorch")
        if actual_submodule_ref != expected_submodule_ref:
            raise EngineError(
                f"nsx-executorch external/executorch is at {actual_submodule_ref}, "
                f"expected {expected_submodule_ref}",
                hint="Run git submodule update --init external/executorch at the pinned checkout.",
            )
        if not (source_root / "tools" / "python" / "torchgen" / "__init__.py").is_file():
            raise EngineError(
                f"nsx-executorch checkout is missing its pinned torchgen sources: {source_root}"
            )

        # Generate a thin NSX module wrapper around the local checkout: mirror
        # its own nsx-module.yaml and delegate build logic to its own
        # CMakeLists.txt via add_subdirectory() (not include()) — the
        # runtime's own CMakeLists.txt references its sources with paths
        # relative to CMAKE_CURRENT_SOURCE_DIR (e.g. `src/nsx_executorch.cpp`
        # in add_library()), which only resolves correctly when CMake
        # actually descends into source_root as its own directory scope.
        # include() would leave CMAKE_CURRENT_SOURCE_DIR pointed at this
        # wrapper directory instead, breaking every relative source path.
        wrapper = work_dir / "engine" / "nsx-executorch"
        wrapper.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / "nsx-module.yaml", wrapper / "nsx-module.yaml")
        (wrapper / "CMakeLists.txt").write_text(
            "# Auto-generated by hpx ExecuTorchAdapter.\n"
            "# Do not edit — regenerated on every hpx run.\n"
            "cmake_minimum_required(VERSION 3.24)\n"
            "if(TARGET nsx::executorch)\n  return()\nendif()\n"
            "add_subdirectory(\n"
            f'  "{source_root.as_posix()}"\n'
            '  "${CMAKE_CURRENT_BINARY_DIR}/nsx-executorch-root")\n',
            encoding="utf-8",
        )

        provider_module = _provider_module_ref(config, work_dir, provider)
        return ExecutorchArtifacts(
            engine_type=EngineType.EXECUTORCH,
            extra_modules=[
                # The provider must precede nsx-executorch so NSX configures it
                # exactly once before the runtime creates its idempotent bridge.
                provider_module,
                NsxModuleRef(
                    name=EXECUTORCH_MODULE,
                    path=wrapper,
                    local=True,
                    project=EXECUTORCH_PROJECT,
                ),
            ],
            cmake_vars={
                "NSX_EXECUTORCH_ENABLE_PROFILING": "ON",
                "NSX_EXECUTORCH_CMSIS_NN_PROVIDER": provider,
                "NSX_EXECUTORCH_ENABLE_NS_OPS": "ON" if ns_ops else "OFF",
                "NSX_EXECUTORCH_PORTABLE_SELECT_OPS_LIST": _operator_list(
                    engine_config, "portable_ops", default=sidecar_portable
                ),
                # nsx-executorch wraps this interpreter with its pinned
                # torchgen sources. Point CMake discovery at HPX's own Python
                # environment, which is Python 3.11+ and already includes
                # PyYAML, rather than whichever `python3` happens to be first.
                # Do not resolve this symlink: uv-managed environments point
                # `.venv/bin/python` at a base interpreter whose standalone
                # site-packages do not contain HPX's PyYAML dependency.
                "Python3_EXECUTABLE": Path(sys.executable).absolute().as_posix(),
            },
            engine_header="nsx_executorch.h",
            executorch_method_arena_size=method_size,
            executorch_planned_arena_size=planned_size,
            executorch_temporary_arena_size=temporary_size,
            executorch_input_size=input_size,
            executorch_output_size=output_size,
            executorch_planned_arena_region=planned_region,
            executorch_method_arena_region=method_region,
            executorch_temporary_arena_region=temporary_region,
            executorch_io_region=io_region,
        )
