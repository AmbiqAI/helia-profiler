"""Full profiling results as a single JSON document (``--format json``)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .csv_writer import _layer_to_flat_dict
from .contracts import PROFILE_RESULTS_SCHEMA, PROFILE_RESULTS_SCHEMA_VERSION
from .metadata import _firmware_meta_to_dict, _metadata_to_dict

if TYPE_CHECKING:
    from ..power.base import PowerResult
    from ..results import PmuResult, RunMetadata

log = logging.getLogger("hpx")


def _write_json(
    pmu: PmuResult,
    power: PowerResult | None,
    run_metadata: RunMetadata,
    output_dir: Path,
    power_terminal: dict[str, Any] | None = None,
    on_device_summary: dict[str, Any] | None = None,
) -> Path:
    """Write full profiling results as JSON."""
    out_path = output_dir / "profile_results.json"
    total_cycles = sum(layer.cycles or 0 for layer in pmu.layers)
    preset_totals = {
        name: sum(layer.cycles or 0 for layer in pr.layers) for name, pr in pmu.presets.items()
    }

    data: dict[str, Any] = {
        "schema": PROFILE_RESULTS_SCHEMA,
        "schema_version": PROFILE_RESULTS_SCHEMA_VERSION,
        "metadata": _metadata_to_dict(run_metadata),
        "summary": _firmware_meta_to_dict(pmu.meta),
        "layers": [_layer_to_flat_dict(l, total_cycles=total_cycles) for l in pmu.layers],
        "presets": {
            name: {
                "layers": [
                    _layer_to_flat_dict(l, total_cycles=preset_totals[name])
                    for l in pr.layers
                ],
                "iteration_count": len(pr.iterations),
            }
            for name, pr in pmu.presets.items()
        },
        "overflow_detected": pmu.overflow_detected,
    }

    if power is not None:
        data["power"] = {
            "avg_current_a": power.summary.avg_current_a,
            "avg_power_w": power.summary.avg_power_w,
            "peak_current_a": power.summary.peak_current_a,
            "energy_j": power.summary.energy_j,
            "duration_s": power.summary.duration_s,
            "sample_count": power.summary.sample_count,
        }
        observation = {
            key: power.metadata[key]
            for key in (
                "measurement_scope",
                "observation_mode",
                "integrity",
                "gate_rise_observed",
                "gate_fall_observed",
                "observation_deadline_s",
            )
            if key in power.metadata
        }
        if "gate_failure" in power.metadata:
            observation["gate_failure"] = power.metadata["gate_failure"]
        if observation:
            data["power"]["observation"] = observation
        if power_terminal is not None:
            data["power"]["terminal"] = power_terminal
        if on_device_summary is not None:
            data["power"]["on_device_summary"] = on_device_summary

    out_path.write_text(json.dumps(data, indent=2, default=str))
    log.info("Wrote JSON report: %s", out_path)
    return out_path
