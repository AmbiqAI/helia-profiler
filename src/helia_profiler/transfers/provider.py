"""Build a probe-only provider without changing the model calibration's AOT settings."""

from pathlib import Path

from ..config import ProfileConfig
from ..engines.helia_aot.compile import _compiler_target, _run_aot_compiler, effective_aot_args
from ..errors import FirmwareError


def emit_provider(config: ProfileConfig, directory: Path, plan: Path) -> None:
    args = effective_aot_args(config)
    args["optimization"] = {**args.get("optimization", {}), "plan": str(plan.resolve())}
    target = _compiler_target(config, args)
    _run_aot_compiler(
        config, directory, "probe_provider", "costprobe", target.name, args, target=target
    )
    if not (directory / "probe_provider/src/costprobe_nsx_transfer.c").is_file():
        raise FirmwareError("The transfer probe plan did not emit an asynchronous NSX provider")
