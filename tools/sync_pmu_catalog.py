from __future__ import annotations

import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    ROOT.parents[1]
    / "neuralspotx"
    / "nsx-modules"
    / "nsx-pmu-armv8m"
    / "data"
    / "armv8m_pmu_events.json"
)
DESTINATION = ROOT / "src" / "helia_profiler" / "data" / "armv8m_pmu_events.json"


def main(argv: list[str]) -> int:
    source = Path(argv[1]).resolve() if len(argv) > 1 else DEFAULT_SOURCE
    if not source.is_file():
        print(
            "PMU catalog source not found. Pass the upstream armv8m_pmu_events.json path explicitly.",
            file=sys.stderr,
        )
        return 1
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, DESTINATION)
    print(f"Synced PMU catalog from {source} to {DESTINATION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))