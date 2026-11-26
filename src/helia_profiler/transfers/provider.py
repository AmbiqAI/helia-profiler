"""Build a probe-only provider without changing the model calibration's AOT settings."""

import json
import shutil
from collections.abc import Iterable
from pathlib import Path
from tempfile import TemporaryDirectory

from ..config import ProfileConfig
from ..engines.helia_aot.adapter import _report_files
from ..engines.helia_aot.compile import _compiler_target, _run_aot_compiler, effective_aot_args
from ..errors import FirmwareError
from .matrix import Cell, schedule_cells


def emit_provider(config: ProfileConfig, directory: Path, plan: Path) -> tuple[Cell, ...]:
    args = effective_aot_args(config)
    args["optimization"] = {**args.get("optimization", {}), "plan": str(plan.resolve())}
    target = _compiler_target(config, args)
    with TemporaryDirectory(dir=directory) as temporary:
        output = Path(temporary)
        _run_aot_compiler(
            config, output, "probe_provider", "costprobe", target.name, args, target=target
        )
        module = output / "probe_provider"
        if not (module / "src/costprobe_nsx_transfer.c").is_file():
            raise FirmwareError("The transfer probe plan did not emit an asynchronous NSX provider")
        converted = converted_plan(_report_files(output, "costprobe"), "costprobe")
        geometry = schedule_cells(json.loads(converted.read_bytes()))
        destination = directory / "probe_provider"
        shutil.copytree(module, destination, dirs_exist_ok=True)
        shutil.copyfile(converted, destination / "costprobe_plan.json")
        return geometry


def converted_plan(files: Iterable[Path], prefix: str) -> Path:
    for path in files:
        if path.name == f"{prefix}_plan.json":
            return path
    raise FirmwareError(f"The conversion did not emit {prefix}_plan.json for transfer calibration")
