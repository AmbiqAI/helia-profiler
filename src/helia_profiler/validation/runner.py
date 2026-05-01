"""Runner for a single validation case.

Builds a temporary YAML config for one :class:`~helia_profiler.validation.CaseSpec`,
invokes ``hpx profile`` as a subprocess, and parses the resulting artifacts
into a structured :class:`CaseResult` dict.

The subprocess boundary is deliberate: it exercises the true user-facing
code path (same as typing ``hpx profile --config foo.yml``), isolates state
between cases, and maps 1:1 to what a GHA runner will eventually do.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .matrix import CaseSpec

# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    """Outcome of running a single :class:`CaseSpec`."""

    case_id: str
    status: str                              # "pass" | "fail" | "skip"
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
            "type": case.engine,
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
        cfg["power"].update({
            "driver": "joulescope",
            "mode": "external",
            "duration_s": 20,
            "io_voltage": 1.8,
            "sync_gpio_pin": 10,
        })

    if case.engine == "helia-aot":
        # Point heliaAOT at the vendored nsx-cmsis-nn so `hpx validate`
        # works out of the box from the monorepo checkout.
        cmsis_nn_candidate = repo_root.parent / "nsx-modules" / "ns-cmsis-nn"
        if cmsis_nn_candidate.exists():
            cfg["engine"]["config"] = {
                "prefix": "hpx",
                "module_name": "hpx_model",
                "cmsis_nn_path": str(cmsis_nn_candidate),
            }

    return cfg


# ---------------------------------------------------------------------------
# Case runner
# ---------------------------------------------------------------------------


def run_case(
    case: CaseSpec,
    repo_root: Path,
    output_root: Path,
    timeout_s: float = 900.0,
    verbose: bool = False,
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
        Wall-clock timeout for the ``hpx profile`` subprocess.
    verbose:
        If true, stream the subprocess output live in addition to
        capturing it.
    """
    case_dir = output_root / case.case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    config_path = case_dir / "config.yml"
    config = _build_config(case, repo_root, case_dir)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))

    cmd = ["hpx", "profile", "--config", str(config_path)]
    if verbose:
        cmd.append("-v")

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env={**os.environ},
        )
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
            result.layers = int(summary.get("layers")) if summary.get("layers") is not None else None
            result.total_cycles = (
                int(summary.get("total_cycles"))
                if summary.get("total_cycles") is not None else None
            )
            power_blob = summary.get("power") or {}
            if power_blob:
                if "total_energy_uj" in power_blob:
                    result.energy_uj = float(power_blob["total_energy_uj"])
                elif "energy_uJ" in power_blob:
                    result.energy_uj = float(power_blob["energy_uJ"])
                if "avg_current_ma" in power_blob:
                    result.avg_current_ma = float(power_blob["avg_current_ma"])
                if "peak_current_ma" in power_blob:
                    result.peak_current_ma = float(power_blob["peak_current_ma"])
        except (ValueError, OSError) as exc:
            result.error = f"could not parse summary.json: {exc}"
            result.status = "fail"
            return result
    else:
        result.error = "summary.json not produced"
        result.status = "fail"
        return result

    if case.engine == "helia-aot":
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
    assert result.status == "pass", (
        f"{result.case_id}: run failed — {result.error}"
    )
    assert result.layers and result.layers >= 1, (
        f"{result.case_id}: summary.json reports no layers"
    )
    assert result.total_cycles and result.total_cycles > 0, (
        f"{result.case_id}: total_cycles == 0 (PMU capture looks broken)"
    )
    if result.engine == "helia-aot":
        assert result.aot_operator_count and result.aot_operator_count >= 1, (
            f"{result.case_id}: AOT manifest empty or missing"
        )
    if result.power:
        assert result.energy_uj and result.energy_uj > 0.0, (
            f"{result.case_id}: power enabled but zero energy captured"
        )
