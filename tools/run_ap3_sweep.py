#!/usr/bin/env python3
"""Run an AP3 hardware validation sweep one HPX case at a time.

This is intentionally a thin harness around ``hpx validate``.  It uses HPX's
own matrix expansion to decide which cases exist, then runs each case as an
independent command so every step has a clear PASS/FAIL line and a dedicated
log file.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from helia_profiler.validation.matrix import CaseSpec, build_matrix  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_dir = args.output_dir or _default_output_dir(args.board)
    output_dir = output_dir.resolve()
    logs_dir = output_dir / "logs"
    cases_dir = output_dir / "cases"
    logs_dir.mkdir(parents=True, exist_ok=True)
    cases_dir.mkdir(parents=True, exist_ok=True)

    cases = _build_cases(args)
    if args.limit is not None:
        cases = cases[: args.limit]

    status_path = output_dir / "status.jsonl"
    summary_path = output_dir / "summary.json"
    prior = _load_status(status_path) if args.resume else {}

    print(f"HPX AP3 sweep")
    print(f"  output: {output_dir}")
    print(f"  logs:   {logs_dir}")
    print(f"  cases:  {len(cases)}")
    if args.dry_run:
        for case in cases:
            print(case.case_id)
        return 0

    started = time.monotonic()
    results: list[dict[str, Any]] = []
    pass_count = 0
    fail_count = 0
    skip_count = 0

    for index, case in enumerate(cases, 1):
        if args.resume and prior.get(case.case_id, {}).get("status") == "pass":
            skip_count += 1
            print(f"[{index:03d}/{len(cases):03d}] SKIP {case.case_id} (prior pass)")
            continue

        log_path = logs_dir / f"{case.case_id}.log"
        case_output_dir = cases_dir / case.case_id
        cmd = _hpx_validate_command(args, case, case_output_dir)

        print(f"[{index:03d}/{len(cases):03d}] RUN  {case.case_id}")
        print(f"          log: {log_path}")
        rc, duration_s = _run_logged(cmd, log_path, stream=args.stream)
        status = "pass" if rc == 0 else "fail"
        if status == "pass":
            pass_count += 1
        else:
            fail_count += 1

        record = {
            "case_id": case.case_id,
            "status": status,
            "returncode": rc,
            "duration_s": round(duration_s, 3),
            "log": str(log_path),
            "output_dir": str(case_output_dir),
            "command": cmd,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        results.append(record)
        with status_path.open("a", encoding="utf-8") as status_file:
            status_file.write(json.dumps(record) + "\n")

        print(f"[{index:03d}/{len(cases):03d}] {status.upper():4} {case.case_id} ({duration_s:.1f}s)")
        if status == "fail":
            print(_tail(log_path, lines=40))
            if args.stop_on_fail:
                break

    summary = {
        "board": args.board,
        "jlink_serial": args.jlink_serial,
        "output_dir": str(output_dir),
        "status_path": str(status_path),
        "total_selected": len(cases),
        "pass": pass_count,
        "fail": fail_count,
        "skip": skip_count,
        "elapsed_s": round(time.monotonic() - started, 3),
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("Summary:")
    print(json.dumps(summary, indent=2))
    return 1 if fail_count else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", default="apollo3p_evb")
    parser.add_argument("--jlink-serial", default="1160000174")
    parser.add_argument("--models", default="kws,vww,ic,ad")
    parser.add_argument("--engines", default="helia-rt,helia-aot")
    parser.add_argument("--toolchains", default="arm-none-eabi-gcc,armclang,atfe")
    parser.add_argument("--interfaces", "--transports", dest="transports", default="rtt,swo,uart")
    parser.add_argument("--memories", default="auto,tcm,sram,mram,psram")
    parser.add_argument("--power", choices=("off", "on", "both"), default="off")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--limit", type=int, help="Run only the first N selected cases")
    parser.add_argument("--resume", action="store_true", help="Skip cases already recorded as pass")
    parser.add_argument("--stop-on-fail", action="store_true")
    parser.add_argument("--stream", action="store_true", help="Also print child hpx output live")
    parser.add_argument("--dry-run", action="store_true", help="List selected case IDs and exit")
    return parser.parse_args(argv)


def _build_cases(args: argparse.Namespace) -> list[CaseSpec]:
    return build_matrix(
        models=_split_csv(args.models),
        engines=_split_csv(args.engines),
        power=args.power,
        boards=[args.board],
        toolchains=_split_csv(args.toolchains),
        transports=_split_csv(args.transports),
        memories=_split_csv(args.memories),
        jlink_serials={args.board: args.jlink_serial},
        repeat=args.repeat,
    )


def _split_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _default_output_dir(board: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return REPO_ROOT / "results" / "validation" / f"{board}-full-sweep-{stamp}"


def _hpx_validate_command(args: argparse.Namespace, case: CaseSpec, output_dir: Path) -> list[str]:
    return [
        "uv",
        "--directory",
        str(REPO_ROOT),
        "run",
        "hpx",
        "validate",
        "--boards",
        case.board.id,
        "--models",
        case.model.id,
        "--engines",
        case.engine.value,
        "--power",
        "on" if case.power else "off",
        "--toolchains",
        case.toolchain.value,
        "--interfaces",
        case.transport.value,
        "--memories",
        case.memory.value,
        "--jlink-serials",
        f"{case.board.id}={args.jlink_serial}",
        "--repeat",
        "1",
        "--timeout",
        str(args.timeout),
        "--output-dir",
        str(output_dir),
        "-v",
    ]


def _run_logged(cmd: list[str], log_path: Path, *, stream: bool) -> tuple[int, float]:
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        log_file.write("$ " + " ".join(cmd) + "\n\n")
        log_file.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log_file.write(line)
            if stream:
                print(line, end="")
        rc = proc.wait()
    return rc, time.monotonic() - started


def _load_status(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        out[str(record["case_id"])] = record
    return out


def _tail(path: Path, *, lines: int) -> str:
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = "\n".join(text[-lines:])
    return f"--- tail {path} ---\n{tail}\n--- end tail ---"


if __name__ == "__main__":
    raise SystemExit(main())