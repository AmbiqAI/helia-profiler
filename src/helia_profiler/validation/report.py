"""Validation-suite report and manifest writers."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .._version import __version__
from ..errors import ReportError
from .runner import CaseResult


SCHEMA_VERSION = 2


def write_validation_reports(
    results: list[CaseResult],
    output_dir: Path,
    *,
    validation_options: dict[str, Any] | None = None,
    repo_root: Path | None = None,
) -> list[Path]:
    """Write validation JSON, Markdown, and manifest artifacts."""
    out_dir = output_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    options = validation_options or {}
    root = repo_root.expanduser().resolve() if repo_root is not None else _discover_repo_root()

    paths = [
        out_dir / "validation_report.json",
        out_dir / "validation_report.md",
        out_dir / "validation_manifest.json",
    ]

    paths[0].write_text(
        json.dumps(
            {
                "cases": [r.to_dict() for r in results],
                "summary": summary_stats(results),
            },
            indent=2,
            default=str,
        )
    )
    paths[1].write_text(render_markdown(results))
    paths[2].write_text(
        json.dumps(
            build_manifest(
                results,
                out_dir,
                validation_options=options,
                repo_root=root,
            ),
            indent=2,
            default=str,
        )
        + "\n"
    )
    return paths


def summary_stats(results: list[CaseResult]) -> dict[str, int]:
    """Return pass/fail/skip totals for validation results."""
    return {
        "total": len(results),
        "pass": sum(1 for r in results if r.status == "pass"),
        "fail": sum(1 for r in results if r.status == "fail"),
        "skip": sum(1 for r in results if r.status == "skip"),
    }


def build_manifest(
    results: list[CaseResult],
    output_dir: Path,
    *,
    validation_options: dict[str, Any] | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build the portable validation manifest document."""
    out_dir = output_dir.expanduser().resolve()
    root = repo_root.expanduser().resolve() if repo_root is not None else _discover_repo_root()
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "hpx_version": __version__,
        "repo": _repo_metadata(root),
        "validation": _json_safe(validation_options or {}),
        "summary": summary_stats(results),
        "cases": [_case_manifest(result, out_dir) for result in results],
    }


def render_markdown(results: list[CaseResult]) -> str:
    """Render the human-readable validation report."""
    stats = summary_stats(results)
    lines = [
        "# heliaPROFILER - Hardware Validation Report",
        "",
        f"- total: **{stats['total']}**",
        f"- pass: **{stats['pass']}**",
        f"- fail: **{stats['fail']}**",
        f"- skip: **{stats['skip']}**",
        "",
        "| Case | Status | Duration (s) | Toolchain | Interface | Memory | Layers | Cycles | Energy (uJ) | Avg (mA) | Peak (mA) | Notes |",
        "|------|--------|-------------:|-----------|-----------|--------|-------:|-------:|------------:|---------:|----------:|-------|",
    ]
    for r in results:
        note = r.error or ""
        lines.append(
            "| {cid} | {st} | {dur:.1f} | {toolchain} | {transport} | {memory} | {layers} | {cyc} | {energy} | {avg} | {peak} | {note} |".format(
                cid=r.case_id,
                st=r.status,
                dur=r.duration_s,
                toolchain=r.toolchain,
                transport=r.transport,
                memory=r.memory,
                layers=r.layers if r.layers is not None else "-",
                cyc=r.total_cycles if r.total_cycles is not None else "-",
                energy=f"{r.energy_uj:.1f}" if r.energy_uj is not None else "-",
                avg=f"{r.avg_current_ma:.2f}" if r.avg_current_ma is not None else "-",
                peak=f"{r.peak_current_ma:.2f}" if r.peak_current_ma is not None else "-",
                note=note.replace("|", r"\|") if note else "",
            )
        )
    return "\n".join(lines) + "\n"


def _case_manifest(result: CaseResult, output_dir: Path) -> dict[str, Any]:
    case_dir = (
        Path(result.output_dir).expanduser().resolve()
        if result.output_dir
        else output_dir / result.case_id
    )
    artifact_paths = {
        "case_dir": case_dir,
        "config": case_dir / "config.yml",
        "work_dir": case_dir / "work",
        "summary": case_dir / "summary.json",
        "run_metadata": case_dir / "run_metadata.json",
        "profile_results": case_dir / "profile_results.csv",
        "hpx_profile_log": case_dir / "hpx_profile.log",
        "stdout_log": case_dir / "hpx_stdout.log",
        "stderr_log": case_dir / "hpx_stderr.log",
        "aot_memory_layers": case_dir / "aot_memory_layers.csv",
        "aot_operator_manifest": case_dir / "aot_operator_manifest.json",
        "power_summary": case_dir / "power_summary.csv",
    }
    artifacts = {
        name: {
            "path": _bundle_relative(path, output_dir),
            "available": path.exists(),
        }
        for name, path in artifact_paths.items()
    }
    metadata = _read_optional_json(case_dir / "run_metadata.json")
    summary = _read_optional_json(case_dir / "summary.json")
    model_config = _nested_dict(metadata, "config", "model")
    requested_memory = {
        "preset": result.memory,
        "arena_location": model_config.get("arena_location"),
        "weights_location": model_config.get("weights_location"),
    }
    requested_memory = _strip_none(requested_memory)
    engine = _enum_value(result.engine)
    identity = {
        "model_id": result.model_id,
        "engine": engine,
        "board": result.board,
        "toolchain": result.toolchain,
        "transport": result.transport,
        "requested_memory": requested_memory,
        "requested_power": {"enabled": result.power},
        "attempt": result.attempt,
    }
    case_data: dict[str, Any] = {
        "case_id": result.case_id,
        "status": result.status,
        "duration_s": result.duration_s,
        "identity": identity,
        "repeat": {"attempt": result.attempt, "total": result.repeat_total},
        "health_issues": list(result.health_issues),
        "provenance": _strip_none(
            {
                "jlink_serial": result.jlink_serial,
                "model_sha256": _nested(metadata, "model", "sha256"),
                "compiler": _nested(metadata, "toolchain", "compiler"),
                "compiler_version": _nested(metadata, "toolchain", "compiler_version"),
                "effective_memory": summary.get("memory_plan"),
            }
        ),
        "metrics": {
            "layers": result.layers,
            "total_cycles": result.total_cycles,
            "energy_uj": result.energy_uj,
            "avg_current_ma": result.avg_current_ma,
            "peak_current_ma": result.peak_current_ma,
            "aot_operator_count": result.aot_operator_count,
        },
        "artifacts": artifacts,
    }
    if result.error:
        case_data["error"] = result.error
    return _strip_none(case_data)


def _repo_metadata(repo_root: Path | None) -> dict[str, Any]:
    if repo_root is None:
        return {"sha": None, "branch": None, "dirty": None}
    return {
        "sha": _git(repo_root, "rev-parse", "HEAD"),
        "branch": _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": _git_dirty(repo_root),
    }


def _discover_repo_root() -> Path | None:
    return Path(__file__).resolve().parents[3]


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def _git_dirty(repo_root: Path) -> bool | None:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return bool(proc.stdout.strip())


def _rel(path: Path, root: Path) -> str:
    return _bundle_relative(path, root)


def _bundle_relative(path: Path, root: Path) -> str:
    """Return a portable bundle-relative path, rejecting writer escapes."""

    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ReportError(f"Validation artifact escapes bundle root: {path}") from exc
    return relative.as_posix()


def _read_optional_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _nested(value: dict[str, Any], *parts: str) -> Any:
    current: Any = value
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _nested_dict(value: dict[str, Any], *parts: str) -> dict[str, Any]:
    nested = _nested(value, *parts)
    return nested if isinstance(nested, dict) else {}


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value]
    return value
