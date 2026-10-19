"""Run API conformance: freeze, build, save, load and run one typed fixture.

The script walks the whole run API once, as a benchmark harness would:

1. freeze a :class:`~helia_profiler.FrozenRun` plan from a session;
2. build it for a :class:`~helia_profiler.fixture.TypedFixture` with fixed input bytes;
3. save the :class:`~helia_profiler.compiled_build.CompiledBuild` and load it back,
   as another host would;
4. run the loaded record on the board and compare its outputs with host LiteRT.

It prints the run's identity, the per-tensor verification facts and the key
measurements as JSON. Everything it writes lives under ``--out``, which is the
only root the record names, so the directory can move to another host.

Example::

    uv run python examples/run_api_conformance.py --jlink-serial <probe-serial> \\
        --engine helia-rt --board apollo510_evb --out runs/conformance
"""

from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path

import helia_profiler as hpx
from helia_profiler import CompiledBuild, RunResult
from helia_profiler.expect import Expect, Tolerance
from helia_profiler.fixture import (
    FixtureFile,
    FixtureIO,
    FixtureMethod,
    FixtureOutput,
    FixtureTarget,
    FixtureTimingScope,
    TypedFixture,
    analyze_typed_fixture_model,
)
from helia_profiler.lifecycle import Lifecycle


class OperatorGuard:
    """Trust the operator's word that *jlink_serial* is the intended, idle board.

    A harness replaces this with its board lock, which proves the physical board
    and holds it exclusively for the capture.
    """

    def __init__(self, jlink_serial: str) -> None:
        self.jlink_serial = jlink_serial

    def check(self, *, require_free: bool, remaining_s: float) -> None:
        del require_free, remaining_s

    def verify_target(self, *, target: FixtureTarget, jlink_serial: str) -> None:
        del target
        if jlink_serial != self.jlink_serial:
            raise RuntimeError(f"Probe {jlink_serial} is not the declared {self.jlink_serial}")


def _pin(path: Path, data: bytes) -> FixtureFile:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return FixtureFile(path, sha256(data).hexdigest())


def typed_fixture(model: Path, out: Path) -> TypedFixture:
    """A fixture of *model* with zero-filled inputs, its files copied under *out*."""
    pinned = _pin(out / "inputs" / model.name, model.read_bytes())
    analysis = analyze_typed_fixture_model(pinned.path)
    return TypedFixture(
        pinned,
        tuple(
            FixtureIO(tensor, _pin(out / "inputs" / f"{tensor.name}.bin", bytes(tensor.size_bytes)))
            for tensor in analysis.inputs
        ),
        tuple(FixtureOutput(tensor) for tensor in analysis.outputs),
    )


def run_conformance(
    *,
    model: Path,
    engine: str,
    board: str,
    toolchain: str,
    jlink_serial: str,
    out: Path,
    settle_seconds: float = 60.0,
) -> RunResult:
    """Freeze, build, save, load and run *model* once; return the run's result."""
    fixture = typed_fixture(model, out)
    plan = (
        hpx.Session()
        .with_model(fixture.model.path)
        .with_engine(engine)
        .with_target(board=board, toolchain=toolchain)
        .freeze()
    )
    built = plan.build(
        fixture,
        method=FixtureMethod(FixtureTimingScope.INVOKE_ONLY),
        lifecycle=Lifecycle.CARRY,
        host={"output": {"dir": str(out / "build")}},
    )
    roots = {"run": out}
    record = CompiledBuild.load(built.save(out / "record.json", roots=roots), roots=roots)
    return record.run(
        guard=OperatorGuard(jlink_serial),
        jlink_serial=jlink_serial,
        evidence_dir=out / "evidence",
        settle_seconds=settle_seconds,
        expect=Expect.host_litert(tolerance=Tolerance(max_abs_error=1)),
    )


def report(result: RunResult) -> dict[str, object]:
    """The run's identity, verification facts and key measurements, JSON-ready."""
    capture, verification = result.capture, result.verification
    timing, memory = capture.timing, capture.memory
    return {
        "succeeded": result.succeeded,
        "error": capture.error,
        "identity": result.identity.to_dict(),
        "verification": None
        if verification is None
        else {
            "reference": verification.reference.origin.kind,
            "matched": verification.matched,
            "tensors": [
                {
                    "name": tensor.name,
                    "identical": tensor.identical,
                    "mismatched_elements": tensor.mismatched_elements,
                    "max_abs_error": tensor.max_abs_error,
                }
                for tensor in verification.tensors
            ],
        },
        "measurements": {
            "latency_s": None
            if timing is None
            else timing.ticks / timing.timer_hz / timing.iterations,
            "iterations": None if timing is None else timing.iterations,
            "warmups": None if timing is None else timing.warmups,
            "arena_used_after_invoke_bytes": None if memory is None else memory.after_invoke,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jlink-serial", required=True)
    parser.add_argument("--model", type=Path, help="LiteRT model; defaults to the tiny CNN")
    parser.add_argument("--engine", default="helia-rt")
    parser.add_argument("--board", default="apollo510_evb")
    parser.add_argument("--toolchain", default="arm-none-eabi-gcc")
    parser.add_argument("--out", type=Path, default=Path("runs") / "conformance")
    args = parser.parse_args(argv)
    result = run_conformance(
        model=args.model or hpx.examples.tiny_cnn(),
        engine=args.engine,
        board=args.board,
        toolchain=args.toolchain,
        jlink_serial=args.jlink_serial,
        out=args.out.resolve(),
    )
    print(json.dumps(report(result), indent=2))
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
