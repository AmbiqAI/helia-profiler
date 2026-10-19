"""Run API conformance: freeze, build, save, load and run one typed fixture.

The script walks the whole run API once, as a benchmark harness would:

1. freeze a :class:`~helia_profiler.FrozenRun` plan from a session;
2. :func:`~helia_profiler.build` it for a
   :class:`~helia_profiler.fixture.TypedFixture` with fixed input bytes;
3. save the :class:`~helia_profiler.CompiledBuild` and
   :func:`~helia_profiler.load_build` it back, as another host would;
4. :func:`~helia_profiler.run_build` the loaded build on the board and compare
   its outputs with host LiteRT.

It prints the run's identity, the per-tensor verification facts and the key
measurements as JSON.

Before running it:

- Install ATfE; fixture builds use no other toolchain.
- For ``helia-rt`` or ``tflm``, prepare the runtime once with
  ``hpx runtimes prepare helia-rt`` (or ``tflm``). heliaAOT needs nothing.
- Replace :class:`PlaceholderGuard`. It refuses every capture: a harness
  passes its own guard to :func:`run_conformance`, one that proves the
  physical board behind the probe serial and holds it for the capture.

The plan states what a fixture build requires: the ``lp`` clock, an SRAM
arena of ``--arena-size`` bytes and MRAM weights. The build works under
``<out>/build``. The saved build names two roots, ``run`` for ``--out`` and
``cache`` for the HPX cache that holds the prepared runtime, so both
directories can move to another host.

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
from helia_profiler import Lifecycle, RunResult
from helia_profiler.expect import Expect, Tolerance
from helia_profiler.fixture import (
    FixtureCaptureGuard,
    FixtureFile,
    FixtureIO,
    FixtureMethod,
    FixtureOutput,
    FixtureTarget,
    FixtureTimingScope,
    PreparedUpstreamRuntime,
    TypedFixture,
    analyze_typed_fixture_model,
)
from helia_profiler.hostenv.cache_dirs import hpx_cache_root
from helia_profiler.prepared_runtimes import prepared_runtime

#: The kernel backend each prepared-runtime engine builds its fixture with.
BACKENDS = {"helia-rt": "helia", "tflm": "cmsis_nn"}


class PlaceholderGuard:
    """Refuses every capture until a harness replaces it with a real board guard."""

    def check(self, *, require_free: bool, remaining_s: float) -> None:
        del require_free, remaining_s
        raise RuntimeError(
            "PlaceholderGuard refuses to capture: pass your harness's board guard "
            "to run_conformance(guard=...)"
        )

    def verify_target(self, *, target: FixtureTarget, jlink_serial: str) -> None:
        del target, jlink_serial
        self.check(require_free=True, remaining_s=0.0)


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


def runtime_for(engine: str) -> PreparedUpstreamRuntime | None:
    """The prepared runtime *engine* links, or ``None`` for heliaAOT."""
    return prepared_runtime(engine).runtime if engine in BACKENDS else None


def run_conformance(
    *,
    model: Path,
    engine: str,
    board: str,
    jlink_serial: str,
    out: Path,
    guard: FixtureCaptureGuard,
    arena_size: int = 128 * 1024,
    settle_seconds: float = 60.0,
) -> RunResult:
    """Freeze, build, save, load and run *model* once; return the run's result."""
    fixture = typed_fixture(model, out)
    plan = (
        hpx.Session()
        .with_model(
            fixture.model.path,
            arena_size=arena_size,
            arena_location="sram",
            weights_location="mram",
        )
        .with_engine(engine, backend=BACKENDS.get(engine))
        .with_target(board=board, toolchain="atfe", clock={"cpu": "lp"})
        .freeze()
    )
    built = hpx.build(
        plan,
        fixture,
        method=FixtureMethod(FixtureTimingScope.INVOKE_ONLY),
        lifecycle=Lifecycle.CARRY,
        runtime=runtime_for(engine),
        host={"work_dir": str(out / "build")},
    )
    roots = {"run": out, "cache": hpx_cache_root()}
    record = hpx.load_build(built.save(out / "build.json", roots=roots), roots=roots)
    return hpx.run_build(
        record,
        guard=guard,
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
    parser.add_argument("--engine", choices=("helia-rt", "helia-aot", "tflm"), default="helia-rt")
    parser.add_argument("--board", default="apollo510_evb")
    parser.add_argument("--arena-size", type=int, default=128 * 1024)
    parser.add_argument("--out", type=Path, default=Path("runs") / "conformance")
    args = parser.parse_args(argv)
    result = run_conformance(
        model=args.model or hpx.examples.tiny_cnn(),
        engine=args.engine,
        board=args.board,
        jlink_serial=args.jlink_serial,
        out=args.out.resolve(),
        guard=PlaceholderGuard(),
        arena_size=args.arena_size,
    )
    print(json.dumps(report(result), indent=2))
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
