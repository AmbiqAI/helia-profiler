"""Implementations of ``hpx freeze``, ``hpx build``, ``hpx validate build`` and ``hpx run``.

Each one calls the run API (``helia_profiler.run_directory`` lays out the run
directory), prints its JSON summary on stdout, and reports an error through
the console on stderr with exit status 1.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

import typer

if TYPE_CHECKING:
    from ..expect import Expect

#: ``hpx run``: the capture succeeded but its outputs did not match.
EXIT_MISMATCH = 3


@contextmanager
def _reported(verbose: int) -> Iterator[None]:
    """Report an error or Ctrl-C as one line, never as a traceback.

    Beyond ``HpxError`` (all ``hpx profile`` reports), ``ValueError`` and
    ``OSError`` are reported too: a contract the capture checks, or a file the
    host cannot read or write, is the user's to fix.
    """
    from ..console import HpxConsole
    from ..errors import HpxError

    try:
        yield
    except KeyboardInterrupt:
        HpxConsole(verbose).print_interrupted()
        sys.exit(130)
    except (HpxError, ValueError, OSError) as exc:
        HpxConsole(verbose).print_error(exc)
        sys.exit(1)


def _print(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))


def _cmd_freeze(
    *,
    model: Path | None,
    output: Path,
    config: Path | None,
    backend: str | None,
    verbose: int,
    **params: Any,
) -> None:
    from ..errors import ConfigError
    from ..session import Session
    from .profile_cmd import _build_cli_overrides

    overrides = _build_cli_overrides(
        model=model.expanduser().resolve() if model is not None else None, **params
    )
    if backend is not None:
        overrides.setdefault("engine", {})["backend"] = backend
    target = output.expanduser().resolve()
    with _reported(verbose):
        if target.is_dir():
            raise ConfigError(
                f"{output} is a directory", hint="Name the plan file, for example plan.json."
            )
        session = Session.from_yaml(config) if config is not None else Session()
        plan = session.with_overrides(overrides).freeze()
        if target == Path(plan.model.path).expanduser().resolve():
            raise ConfigError(
                f"{output} is the model the plan is frozen against",
                hint="Name another file for the plan, for example plan.json.",
            )
        path = plan.save(output)
    _print({"plan": str(path), "intent_hash": plan.intent_hash, "portable": plan.portable})


def _cmd_build(
    *,
    plan: Path,
    out: Path,
    inputs: list[Path],
    lifecycle: str,
    timing_scope: str,
    model: Path | None,
    work_dir: Path | None,
    verbose: int,
) -> None:
    from ..frozen_run import FrozenRun
    from ..lifecycle import Lifecycle
    from ..run_directory import build_into, build_summary

    with _reported(verbose):
        compiled = build_into(
            FrozenRun.load(plan),
            out,
            inputs=inputs,
            model=model,
            lifecycle=Lifecycle(lifecycle),
            timing_scope=timing_scope,
            work_dir=work_dir,
        )
    sources = (
        [str(path.expanduser().resolve()) for path in inputs]
        if inputs
        else ["zero-filled"] * len(compiled.build.inputs)
    )
    _print(build_summary(out, compiled, sources=sources))


def _cmd_validate_build(*, directory: Path, verbose: int) -> None:
    from ..run_directory import build_summary, load_from, run_directory_of

    directory = run_directory_of(directory)
    with _reported(verbose):
        compiled = load_from(directory)
    _print(build_summary(directory, compiled))


def _cmd_run(
    *,
    directory: Path,
    board: str,
    jlink_serial: str,
    expect: str | None,
    tolerance: float,
    references: list[Path],
    settle_seconds: float,
    evidence_dir: Path | None,
    verbose: int,
) -> None:
    from ..run_directory import fresh_evidence_dir, run_directory_of, run_from, run_summary
    from ..run_guard import OperatorGuard

    directory = run_directory_of(directory)
    expectation = _expectation(expect, tolerance, references)
    with _reported(verbose):
        guard = OperatorGuard(board, jlink_serial)
        evidence = evidence_dir if evidence_dir is not None else fresh_evidence_dir(directory)
        result = run_from(
            directory,
            guard=guard,
            jlink_serial=jlink_serial,
            evidence_dir=evidence,
            settle_seconds=settle_seconds,
            expect=expectation,
        )
    _print(run_summary(result, evidence, authority="operator"))
    if not result.succeeded:
        raise typer.Exit(1)
    if result.verification is not None and result.verification.matched is False:
        raise typer.Exit(EXIT_MISMATCH)


def _expectation(kind: str | None, tolerance: float, references: list[Path]) -> Expect | None:
    """The ``Expect`` that ``--expect``, ``--tolerance`` and ``--reference`` name."""
    from ..expect import Expect, Tolerance

    if references and kind is not None:
        raise typer.BadParameter(
            "--reference names the reference outputs; drop --expect", param_hint="--expect"
        )
    try:
        bound = Tolerance(max_abs_error=tolerance)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--tolerance") from exc
    if references:
        return Expect.from_files(*references, tolerance=bound)
    choices: dict[str, Callable[[], Expect | None]] = {
        "host-litert": lambda: Expect.host_litert(tolerance=bound),
        "none": lambda: None,
    }
    return choices[kind or "host-litert"]()
