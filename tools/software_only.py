"""Run pytest or a Python probe with the software-only device guard installed."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from software_guard.guard import install


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] not in {"pytest", "python"}:
        raise SystemExit("usage: software_only.py {pytest <args...>|python <probe.py> [args...]}")
    mode, *arguments = sys.argv[1:]
    install()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    if mode == "pytest":
        import pytest

        raise SystemExit(pytest.main(arguments))
    sys.argv = arguments
    sys.path.insert(1, str(Path(arguments[0]).resolve().parent))
    runpy.run_path(arguments[0], run_name="__main__")


if __name__ == "__main__":
    main()
