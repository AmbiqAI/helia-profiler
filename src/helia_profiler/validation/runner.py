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
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..engines import EngineType
from .matrix import CaseSpec

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

    # Metrics — populated on success
    layers: int | None = None
    total_cycles: int | None = None
    energy_uj: float | None = None
    avg_current_ma: float | None = None
    peak_current_ma: float | None = None
    aot_operator_count: int | None = None

    # Diagnostics
    output_dir: str | None = None
    stdout_tail: str | None = None
    stderr_tail: str | None = None
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


def _build_config(case: CaseSpec, repo_root: Path, output_dir: Path) -> dict[str, Any]:
    """Materialise an hpx profile YAML config for a single case.

    The shape mirrors the existing `hpx_kws_*.yml` files so any change
    there is trivially transferable.
    """
    cfg: dict[str, Any] = {
        "model": {
            "path": str((repo_root / case.model.fixture_path).resolve()),
            "arena_size": case.model.arena_size,
        },
        "engine": {
            "type": case.engine.value,
        },
        "target": {
            "board": case.board.id,
            "toolchain": "arm-none-eabi-gcc",
        },
        "profiling": {
            "pmu_presets": ["basic_cpu"],
            "per_layer": True,
            "iterations": 3,
            "warmup": 1,
        },
        "power": {
            "enabled": bool(case.power),
        },
        "output": {
            "format": "csv",
            "dir": str(output_dir),
            "model_explorer": False,
        },
    }

    if case.power:
        cfg["power"].update(
            {
                "driver": "joulescope",
                "mode": "external",
                "duration_s": 20,
                "io_voltage": 1.8,
            }
        )

    if case.engine is EngineType.HELIA_AOT:
        # Point heliaAOT at an explicit or nearby ns-cmsis-nn checkout when one
        # is available, instead of assuming a single sibling-repo layout.
        cmsis_nn_candidate = _find_local_cmsis_nn_checkout(repo_root)
        if cmsis_nn_candidate is not None:
            cfg["engine"]["config"] = {
                "prefix": "hpx",
                "module_name": "hpx_model",
                "cmsis_nn_path": str(cmsis_nn_candidate),
            }

    return cfg


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
    config = _build_config(case, repo_root, case_dir)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))

    cmd = ["hpx", "profile", "--config", str(config_path)]
    if verbose:
        cmd.append("-v")

    if in_process is None:
        in_process = _env_truthy("HPX_VALIDATE_INPROCESS")

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
                board=case.board.id,
                power=case.power,
                output_dir=str(case_dir),
                error=f"timeout after {timeout_s:.0f}s",
                stdout_tail=(exc.stdout or "")[-2000:] if exc.stdout else None,
                stderr_tail=(exc.stderr or "")[-2000:] if exc.stderr else None,
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

    if proc.returncode != 0:
        return CaseResult(
            case_id=case.case_id,
            status="fail",
            duration_s=duration,
            engine=case.engine,
            model_id=case.model.id,
            board=case.board.id,
            power=case.power,
            output_dir=str(case_dir),
            error=f"hpx profile exited {proc.returncode}",
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
        )

    # Parse artifacts.
    result = CaseResult(
        case_id=case.case_id,
        status="pass",
        duration_s=duration,
        engine=case.engine,
        model_id=case.model.id,
        board=case.board.id,
        power=case.power,
        output_dir=str(case_dir),
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
    )

    summary_path = case_dir / "summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text())
            result.layers = (
                int(summary.get("layers")) if summary.get("layers") is not None else None
            )
            result.total_cycles = (
                int(summary.get("total_cycles"))
                if summary.get("total_cycles") is not None
                else None
            )
            power_blob = summary.get("power") or {}
            if power_blob:
                if "total_energy_uj" in power_blob:
                    result.energy_uj = float(power_blob["total_energy_uj"])
                elif "energy_uJ" in power_blob:
                    result.energy_uj = float(power_blob["energy_uJ"])
                elif "energy_j" in power_blob:
                    result.energy_uj = float(power_blob["energy_j"]) * 1e6
                if "avg_current_ma" in power_blob:
                    result.avg_current_ma = float(power_blob["avg_current_ma"])
                elif "avg_current_a" in power_blob:
                    result.avg_current_ma = float(power_blob["avg_current_a"]) * 1e3
                if "peak_current_ma" in power_blob:
                    result.peak_current_ma = float(power_blob["peak_current_ma"])
                elif "peak_current_a" in power_blob:
                    result.peak_current_ma = float(power_blob["peak_current_a"]) * 1e3
        except (ValueError, OSError) as exc:
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

    return result


# ---------------------------------------------------------------------------
# Assertion helpers used by the pytest test bodies
# ---------------------------------------------------------------------------


def assert_healthy(result: CaseResult) -> None:
    """Raise AssertionError if ``result`` does not meet minimum bar."""
    assert result.status == "pass", f"{result.case_id}: run failed — {result.error}"
    assert result.layers and result.layers >= 1, f"{result.case_id}: summary.json reports no layers"
    assert result.total_cycles and result.total_cycles > 0, (
        f"{result.case_id}: total_cycles == 0 (PMU capture looks broken)"
    )
    if result.engine == EngineType.HELIA_AOT.value:
        assert result.aot_operator_count and result.aot_operator_count >= 1, (
            f"{result.case_id}: AOT manifest empty or missing"
        )
    if result.power:
        assert result.energy_uj and result.energy_uj > 0.0, (
            f"{result.case_id}: power enabled but zero energy captured"
        )
