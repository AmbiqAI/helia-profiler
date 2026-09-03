"""Runner for a single validation case.

Builds a temporary YAML config for one :class:`~helia_profiler.validation.CaseSpec`,
invokes ``hpx profile`` as a subprocess, and parses the resulting artifacts
into a structured :class:`CaseResult` dict.

The subprocess boundary is deliberate: it exercises the true user-facing
code path (same as typing ``hpx profile --config foo.yml``), isolates state
between cases, and maps 1:1 to what a GHA runner will eventually do.

Set ``HPX_VALIDATE_INPROCESS=1`` (or pass ``in_process=True`` to
:func:`run_case`) to bypass the subprocess and call
:func:`helia_profiler.cli.main` directly instead.  This is faster, lets
``coverage.py`` see the pipeline code, and surfaces live tracebacks.  The
subprocess remains the default so CI still exercises the CLI surface.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..engines import EngineType
from ..results.run_summary import load_run_summary
from .matrix import CaseSpec, MemoryProfile

_TRANSIENT_POWER_LOCK_RETRY_DELAY_S = 5.0
_TRANSIENT_POWER_LOCK_MARKERS = (
    "is already in use by another process",
    "busy during open; retrying",
)

# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    """Outcome of running a single :class:`CaseSpec`."""

    case_id: str
    status: str  # "pass" | "fail" | "skip"
    duration_s: float
    engine: str
    model_id: str
    board: str
    power: bool
    toolchain: str
    transport: str
    memory: str
    backend: str | None = None
    cmsis_nn_provider: str | None = None
    comparison_group: str | None = None
    jlink_serial: str | None = None
    power_serial: str | None = None
    attempt: int = 1
    repeat_total: int = 1
    health_issues: tuple[str, ...] = ()

    # Metrics — populated on success
    layers: int | None = None
    total_cycles: int | None = None
    latency_avg_us: float | None = None
    binary_text_bytes: int | None = None
    binary_data_bytes: int | None = None
    binary_bss_bytes: int | None = None
    binary_total_bytes: int | None = None
    arena_size_bytes: int | None = None
    allocated_arena_bytes: int | None = None
    model_size_bytes: int | None = None
    energy_uj: float | None = None
    avg_current_ma: float | None = None
    avg_power_mw: float | None = None
    peak_current_ma: float | None = None
    power_capture_duration_s: float | None = None
    energy_per_inference_uj: float | None = None
    inferences_per_joule: float | None = None
    gated_window_duration_suspect: bool = False
    gate_duration_integrity_valid: bool | None = None
    #: Present when the run's est*count band missed but the firmware's own
    #: window clock confirmed the gate (#142/#181): a stale profile-phase
    #: reference, not a capture defect. Health checks must not fail it.
    gated_window_reference_drift: str | None = None
    #: The model's arbitration verdict (#202): captured at parse time from
    #: PowerSection.gate_duration_unarbitrated_failure so the health check
    #: consults ONE interpretation. ``None`` = parsed by an older hpx that
    #: did not record it; the health check falls back to the two-field
    #: predicate for those legacy records only.
    gate_duration_unarbitrated_failure: bool | None = None
    power_observation_mode: str | None = None
    power_observation_integrity: str | None = None
    power_gate_failure_kind: str | None = None
    aot_operator_count: int | None = None

    # Diagnostics
    output_dir: str | None = None
    stdout_tail: str | None = None
    stderr_tail: str | None = None
    log_path: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------


def _find_local_cmsis_nn_checkout(repo_root: Path) -> Path | None:
    """Return a usable local ns-cmsis-nn checkout for validation, if present.

    ``hpx validate`` should work with an explicit ``CMSIS_NN_PATH`` override,
    but it can also opportunistically use a nearby checkout in common local
    workspace layouts. Candidates are validated against the native NSX module
    metadata expected by the heliaAOT adapter.
    """
    raw_env = os.environ.get("CMSIS_NN_PATH")
    candidates: list[Path] = []
    if raw_env:
        candidates.append(Path(raw_env).expanduser())

    candidates.extend(
        [
            repo_root / "modules" / "ns-cmsis-nn",
            repo_root.parent / "nsx-modules" / "ns-cmsis-nn",
            repo_root.parent.parent / "neuralspotx" / "nsx-modules" / "ns-cmsis-nn",
        ]
    )

    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "nsx" / "nsx-module.yaml").is_file():
            return resolved
    return None


def _build_config(
    case: CaseSpec,
    repo_root: Path,
    output_dir: Path,
    *,
    ns_cmsis_nn_ref: str | None = None,
) -> dict[str, Any]:
    """Materialise an hpx profile YAML config for a single case.

    The shape mirrors the existing `hpx_kws_*.yml` files so any change
    there is trivially transferable.
    """
    placement: dict[str, str] = {}
    if case.memory is MemoryProfile.TCM:
        placement = {"arena_location": "tcm", "weights_location": "tcm"}
    elif case.memory is MemoryProfile.SRAM:
        placement = {"arena_location": "sram", "weights_location": "sram"}
    elif case.memory is MemoryProfile.MRAM:
        placement = {"weights_location": "mram"}
    elif case.memory is MemoryProfile.PSRAM:
        placement = {"arena_location": "sram", "weights_location": "psram"}
    elif case.engine is EngineType.EXECUTORCH:
        # Keep mutable runtime workspace in SRAM so it fits Apollo330's
        # 240 KB TCM; the immutable PTE executes directly from MRAM.
        placement = {"arena_location": "sram", "weights_location": "mram"}

    engine_cfg: dict[str, object] = {
        "type": case.engine.value,
    }
    target_cfg: dict[str, object] = {
        "board": case.board.id,
        "toolchain": case.toolchain.value,
        "transport": case.transport.value,
    }
    power_cfg: dict[str, object] = {
        "enabled": bool(case.power),
    }
    cfg: dict[str, Any] = {
        "model": {
            "path": str((repo_root / case.model.fixture_for(case.engine)).resolve()),
            "arena_size": case.model.arena_size_for(case.engine),
            **placement,
        },
        "engine": engine_cfg,
        "target": target_cfg,
        "profiling": {
            "pmu_counters": {"cpu": "default"},
            "per_layer": True,
            "iterations": 3,
            "warmup": 1,
        },
        "power": power_cfg,
        "output": {
            "format": "csv",
            "dir": str(output_dir),
            "model_explorer": False,
            # Powered validation cases publish detailed/power_summary.csv as
            # part of the portable dashboard artifact contract.
            "detailed": bool(case.power),
        },
        # No explicit work_dir: cases build in the shared incremental cache
        # under ~/.cache/helia-profiler/workspaces/ (same as ad-hoc `hpx
        # profile`). Dependency-workspace fingerprints already isolate
        # incompatible cases from each other, and repeat runs of the same
        # case reuse the configured build tree instead of paying a clean
        # CMake configure + full compile every time.
    }

    if case.power:
        power_cfg.update(
            {
                "driver": "joulescope",
                "mode": "external",
                "duration_s": 20,
                "io_voltage": 1.8,
            }
        )
        if case.power_serial:
            power_cfg["serial"] = case.power_serial
        if case.power_gpio_pins:
            sync, state, go = case.power_gpio_pins
            power_cfg.update(
                {
                    "sync_gpio_pin": sync,
                    "state_gpio_pin": state,
                    "go_gpio_pin": go,
                }
            )

    if case.jlink_serial:
        target_cfg["jlink_serial"] = case.jlink_serial

    if case.engine is EngineType.EXECUTORCH:
        contract = case.model.executorch
        if contract is None or case.cmsis_nn_backend is None:
            raise ValueError(f"Incomplete ExecuTorch contract for {case.case_id}")
        nsx_root = Path(
            os.environ.get("NSX_EXECUTORCH_ROOT", repo_root.parent / "nsx-executorch")
        ).expanduser()
        engine_config: dict[str, Any] = {
            "source_path": str(nsx_root.resolve()),
            "planned_arena_size": contract.planned_arena_size,
            "method_arena_size": contract.method_arena_size,
            "temporary_arena_size": contract.temporary_arena_size,
            "input_size": contract.input_size,
            "output_size": contract.output_size,
            "portable_ops": list(contract.portable_ops),
        }
        # The ns provider is declared at the baseline's qualified ref and the
        # arm provider is registry-governed and verified against the baseline
        # post-lock, so the case stamps `qualified` either way; an explicit ns
        # ref (a branch under test) is the only reason to override.
        if case.cmsis_nn_backend.value == "ns" and ns_cmsis_nn_ref:
            engine_config["cmsis_nn_ref"] = ns_cmsis_nn_ref
        engine_cfg.update(
            {
                "backend": case.cmsis_nn_backend.value,
                "config": engine_config,
            }
        )
    elif case.engine in {EngineType.HELIA_RT, EngineType.HELIA_AOT} and ns_cmsis_nn_ref:
        # Hardware CI resolves branches to an exact commit before creating
        # cases. Put that commit in the profile config so it reaches the NSX
        # manifest and lock instead of relying on a process environment path.
        engine_cfg["config"] = {"cmsis_nn_ref": ns_cmsis_nn_ref}
    elif case.engine is EngineType.HELIA_AOT:
        # Point heliaAOT at an explicit or nearby ns-cmsis-nn checkout when one
        # is available, instead of assuming a single sibling-repo layout.
        cmsis_nn_candidate = _find_local_cmsis_nn_checkout(repo_root)
        if cmsis_nn_candidate is not None:
            engine_cfg["config"] = {
                "prefix": "hpx",
                "module_name": "hpx_model",
                "cmsis_nn_path": str(cmsis_nn_candidate),
            }
    elif case.engine is EngineType.TFLM:
        # Validation exercises the optimized upstream CMSIS-NN baseline. The
        # reference-kernel backend remains available to ad-hoc profile runs.
        engine_cfg["backend"] = "cmsis_nn"

    return cfg


def _engine_backend(case: CaseSpec) -> str | None:
    """Return the engine-level backend value written to the profile config."""
    if case.engine is EngineType.TFLM:
        return "cmsis_nn"
    if case.engine is EngineType.EXECUTORCH:
        return case.cmsis_nn_provider.value
    return None


def _tail_text(stream: str | bytes | None, limit: int = 2000) -> str | None:
    """Return the last *limit* characters of captured process output.

    ``subprocess.TimeoutExpired`` carries ``bytes`` when the command was not
    run in text mode; decode defensively so the tail stays JSON-serialisable.
    """
    if not stream:
        return None
    text = stream.decode("utf-8", errors="replace") if isinstance(stream, bytes) else stream
    return text[-limit:]


# ---------------------------------------------------------------------------
# Case runner
# ---------------------------------------------------------------------------


def _env_truthy(name: str) -> bool:
    """Return True iff the environment variable is set to a truthy value."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class _ProcResult:
    """Lightweight stand-in for ``subprocess.CompletedProcess``."""

    returncode: int
    stdout: str
    stderr: str


def _looks_like_transient_power_lock(proc: subprocess.CompletedProcess[str] | _ProcResult) -> bool:
    text = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
    return any(marker in text for marker in _TRANSIENT_POWER_LOCK_MARKERS)


def _run_profile_command(
    cmd: list[str],
    repo_root: Path,
    timeout_s: float,
    in_process: bool,
) -> subprocess.CompletedProcess[str] | _ProcResult:
    if in_process:
        return _run_case_inprocess(cmd, repo_root, timeout_s)
    return subprocess.run(
        cmd,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
        env={**os.environ},
    )


def _resolve_hpx_command() -> list[str]:
    """Return the best CLI invocation for the current Python environment."""
    python_dir = Path(sys.executable).expanduser().parent
    candidates = [python_dir / "hpx"]
    if os.name == "nt":
        candidates.insert(0, python_dir / "hpx.exe")

    for candidate in candidates:
        if candidate.is_file():
            return [str(candidate)]

    found = shutil.which("hpx")
    if found:
        return [found]
    return ["hpx"]


def _run_case_inprocess(
    cmd: list[str],
    cwd: Path,
    timeout_s: float,
) -> _ProcResult:
    """Invoke :func:`helia_profiler.cli.main` directly and capture I/O.

    *cmd* is the same argv the subprocess path would execute (starting
    with ``"hpx"``).  The leading program name is stripped before the
    call.  ``cwd`` is changed for the duration so any relative paths in
    the YAML config are resolved consistently with the subprocess path.

    Returns a :class:`_ProcResult` mimicking
    :class:`subprocess.CompletedProcess` so the caller can treat both
    branches uniformly.
    """
    # Local import so a missing optional dep at import time of this
    # module doesn't break subprocess-only users.
    from helia_profiler.cli import main as cli_main

    argv = list(cmd[1:])  # drop "hpx"
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    prev_cwd = Path.cwd()
    rc = 0
    try:
        os.chdir(cwd)
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            try:
                cli_main(argv)
            except SystemExit as exc:
                rc = int(exc.code) if isinstance(exc.code, int) else (0 if exc.code is None else 1)
            except KeyboardInterrupt:
                raise
            except BaseException as exc:  # noqa: BLE001 — capture full diagnostic
                traceback.print_exc(file=err_buf)
                err_buf.write(f"\nin-process run raised {type(exc).__name__}: {exc}\n")
                rc = 1
    finally:
        os.chdir(prev_cwd)

    # ``timeout_s`` is intentionally unused here — in-process runs honor
    # any timeout enforced inside the pipeline itself (NSX subprocess
    # watchdog, capture timeouts, etc.).  Wall-clock enforcement at this
    # layer would require threading and isn't worth the complexity.
    del timeout_s

    return _ProcResult(returncode=rc, stdout=out_buf.getvalue(), stderr=err_buf.getvalue())


def run_case(
    case: CaseSpec,
    repo_root: Path,
    output_root: Path,
    timeout_s: float = 900.0,
    verbose: bool = False,
    in_process: bool | None = None,
    ns_cmsis_nn_ref: str | None = None,
) -> CaseResult:
    """Run one validation case end-to-end.

    Parameters
    ----------
    case:
        The case to execute.
    repo_root:
        Absolute path to the helia-profiler repo root (used to resolve
        fixture paths).
    output_root:
        Directory under which each case's artifacts are written to
        ``output_root/<case_id>/``.
    timeout_s:
        Wall-clock timeout for the ``hpx profile`` subprocess (ignored in
        in-process mode — see module docstring).
    verbose:
        If true, stream the subprocess output live in addition to
        capturing it.
    in_process:
        If True, call :func:`helia_profiler.cli.main` directly instead of
        spawning ``hpx profile`` as a subprocess.  If ``None`` (default),
        honor the ``HPX_VALIDATE_INPROCESS`` environment variable.
    """
    case_dir = output_root / case.case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    config_path = case_dir / "config.yml"
    config = _build_config(case, repo_root, case_dir, ns_cmsis_nn_ref=ns_cmsis_nn_ref)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))

    if in_process is None:
        in_process = _env_truthy("HPX_VALIDATE_INPROCESS")

    if in_process:
        cmd = ["hpx", "profile", "--config", str(config_path)]
    else:
        cmd = [*_resolve_hpx_command(), "profile", "--config", str(config_path)]
    if verbose:
        cmd.append("-v")

    start = time.monotonic()
    timed_out = False
    attempts = 2 if case.power else 1
    proc: subprocess.CompletedProcess[str] | _ProcResult
    for attempt in range(attempts):
        try:
            proc = _run_profile_command(cmd, repo_root, timeout_s, in_process)
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start
            return CaseResult(
                case_id=case.case_id,
                status="fail",
                duration_s=duration,
                engine=case.engine,
                model_id=case.model.id,
                comparison_group=case.model.decision_group,
                board=case.board.id,
                power=case.power,
                toolchain=case.toolchain.value,
                transport=case.transport.value,
                memory=case.memory.value,
                backend=_engine_backend(case),
                cmsis_nn_provider=case.cmsis_nn_provider.value,
                jlink_serial=case.jlink_serial,
                power_serial=case.power_serial,
                attempt=case.attempt,
                repeat_total=case.repeat_total,
                output_dir=str(case_dir),
                error=f"timeout after {timeout_s:.0f}s",
                stdout_tail=_tail_text(exc.stdout),
                stderr_tail=_tail_text(exc.stderr),
            )

        if (
            proc.returncode != 0
            and case.power
            and attempt == 0
            and _looks_like_transient_power_lock(proc)
        ):
            time.sleep(_TRANSIENT_POWER_LOCK_RETRY_DELAY_S)
            continue
        break

    duration = time.monotonic() - start
    stdout_tail = proc.stdout[-2000:] if proc.stdout else None
    stderr_tail = proc.stderr[-2000:] if proc.stderr else None

    # Persist raw logs for debugging.
    (case_dir / "hpx_stdout.log").write_text(proc.stdout or "")
    (case_dir / "hpx_stderr.log").write_text(proc.stderr or "")

    # Always persist the full child output (final attempt) for diagnostics.
    log_path: str | None = None
    log_file = case_dir / "hpx_profile.log"
    try:
        log_file.write_text(
            f"$ {shlex.join(cmd)}\n"
            "\n"
            "--- stdout ---\n"
            f"{proc.stdout or ''}\n"
            "\n"
            "--- stderr ---\n"
            f"{proc.stderr or ''}\n"
        )
        log_path = str(log_file)
    except OSError:
        pass

    if proc.returncode != 0:
        return CaseResult(
            case_id=case.case_id,
            status="fail",
            duration_s=duration,
            engine=case.engine,
            model_id=case.model.id,
            comparison_group=case.model.decision_group,
            board=case.board.id,
            power=case.power,
            toolchain=case.toolchain.value,
            transport=case.transport.value,
            memory=case.memory.value,
            backend=_engine_backend(case),
            cmsis_nn_provider=case.cmsis_nn_provider.value,
            jlink_serial=case.jlink_serial,
            power_serial=case.power_serial,
            attempt=case.attempt,
            repeat_total=case.repeat_total,
            output_dir=str(case_dir),
            error=f"hpx profile exited {proc.returncode}",
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            log_path=log_path,
        )

    # Parse artifacts.
    result = CaseResult(
        case_id=case.case_id,
        status="pass",
        duration_s=duration,
        engine=case.engine,
        model_id=case.model.id,
        comparison_group=case.model.decision_group,
        board=case.board.id,
        power=case.power,
        toolchain=case.toolchain.value,
        transport=case.transport.value,
        memory=case.memory.value,
        backend=_engine_backend(case),
        cmsis_nn_provider=case.cmsis_nn_provider.value,
        jlink_serial=case.jlink_serial,
        power_serial=case.power_serial,
        attempt=case.attempt,
        repeat_total=case.repeat_total,
        output_dir=str(case_dir),
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        log_path=log_path,
    )

    summary_path = case_dir / "summary.json"
    if summary_path.exists():
        try:
            # The typed, cross-version reader (#202): legacy key spellings
            # and the drift-arbitration interpretation live on the model's
            # properties, not re-derived here.
            summary = load_run_summary(summary_path)
            result.layers = summary.layers or None
            latency = summary.latency
            if latency is not None and latency.device_clean_infer_avg_cycles is not None:
                result.total_cycles = int(latency.device_clean_infer_avg_cycles)
            else:
                result.total_cycles = summary.total_cycles_int
            if latency is not None:
                result.latency_avg_us = latency.best_latency_avg_us
            binary = summary.binary
            if binary is not None:
                result.binary_text_bytes = binary.text
                result.binary_data_bytes = binary.data
                result.binary_bss_bytes = binary.bss
                result.binary_total_bytes = binary.total
            memory = summary.memory
            if memory is not None:
                result.arena_size_bytes = memory.arena_size
                result.allocated_arena_bytes = memory.allocated_arena
                result.model_size_bytes = memory.model_size
            power = summary.power
            if power is not None:
                result.energy_uj = power.energy_uj
                result.avg_current_ma = power.avg_current_ma
                result.avg_power_mw = power.avg_power_mw
                result.peak_current_ma = power.peak_current_ma
                if power.capture_duration_s is not None:
                    result.power_capture_duration_s = float(power.capture_duration_s)
                result.energy_per_inference_uj = power.energy_per_inference_uj
                if power.inferences_per_joule is not None:
                    result.inferences_per_joule = float(power.inferences_per_joule)
                result.gated_window_duration_suspect = bool(
                    power.gated_window_duration_suspect
                )
                result.gate_duration_integrity_valid = (
                    power.gate_duration_integrity_valid
                )
                result.gated_window_reference_drift = (
                    str(power.gated_window_reference_drift)
                    if power.gated_window_reference_drift is not None
                    else None
                )
                result.power_observation_mode = (
                    str(power.observation_mode)
                    if power.observation_mode is not None
                    else None
                )
                result.power_observation_integrity = (
                    str(power.integrity) if power.integrity is not None else None
                )
                result.power_gate_failure_kind = power.gate_failure_kind
                result.gate_duration_unarbitrated_failure = (
                    power.gate_duration_unarbitrated_failure
                )
        except (ValueError, TypeError, OSError) as exc:
            # TypeError included (#205 review): a hostile artifact shape must
            # fail THIS case, not crash the whole validation sweep.
            result.error = f"could not parse summary.json: {exc}"
            result.status = "fail"
            return result
    else:
        result.error = "summary.json not produced"
        result.status = "fail"
        return result

    if case.engine is EngineType.HELIA_AOT:
        manifest_path = case_dir / "aot_operator_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
                if isinstance(manifest, list):
                    result.aot_operator_count = len(manifest)
            except ValueError:
                pass

    result.health_issues = validation_health_issues(result)
    return result


# ---------------------------------------------------------------------------
# Assertion helpers used by the pytest test bodies
# ---------------------------------------------------------------------------


def assert_healthy(result: CaseResult) -> None:
    """Raise AssertionError if ``result`` does not meet minimum bar."""
    issues = validation_health_issues(result)
    assert not issues, f"{result.case_id}: " + "; ".join(issues)


def validation_health_issues(result: CaseResult) -> tuple[str, ...]:
    """Return validation-health failures separately from execution status."""

    issues: list[str] = []
    if result.status != "pass":
        issues.append(f"run failed — {result.error}")
        return tuple(issues)
    if not result.layers or result.layers < 1:
        issues.append("summary.json reports no layers")
    if not result.total_cycles or result.total_cycles <= 0:
        issues.append("total_cycles == 0 (PMU capture looks broken)")
    engine = result.engine.value if isinstance(result.engine, EngineType) else result.engine
    if engine == EngineType.HELIA_AOT.value and (
        not result.aot_operator_count or result.aot_operator_count < 1
    ):
        issues.append("AOT manifest empty or missing")
    if result.power and (not result.energy_uj or result.energy_uj <= 0.0):
        issues.append("power enabled but zero energy captured")
    if result.power and result.gated_window_duration_suspect:
        issues.append("GPIO-gated power window duration is suspect")
    unarbitrated = result.gate_duration_unarbitrated_failure
    if unarbitrated is None:
        # Legacy record (parsed before the verdict field existed): fall back
        # to the two-field predicate. New records carry the model's own
        # PowerSection.gate_duration_unarbitrated_failure, so the #142/#181
        # interpretation lives in exactly one place (#205 review).
        unarbitrated = (
            result.gate_duration_integrity_valid is False
            and result.gated_window_reference_drift is None
        )
    if result.power and unarbitrated:
        issues.append("GPIO-gated power window failed duration integrity")
    if result.power and result.power_observation_integrity not in {None, "valid"}:
        detail = result.power_gate_failure_kind or result.power_observation_mode or "unknown"
        issues.append(f"power observation is degraded ({detail})")
    return tuple(issues)
