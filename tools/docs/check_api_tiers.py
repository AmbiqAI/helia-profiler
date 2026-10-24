"""Compare the source-derived documentation tier map with runtime package metadata."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def check_tiers(document: dict, exports: list[str], stability: dict[str, str]) -> None:
    if document.get("all") != exports:
        raise ValueError("Documentation export order/membership differs from runtime __all__")
    if document.get("stability") != stability:
        raise ValueError("Documentation stability tiers differ from runtime __api_stability__")
    if document.get("tiers") != dict(Counter(stability.values())):
        raise ValueError("Documentation tier counts differ from runtime metadata")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tiers", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    import helia_profiler

    check_tiers(
        json.loads(args.tiers.read_text(encoding="utf-8")),
        helia_profiler.__all__,
        helia_profiler.__api_stability__,
    )
    print("Documentation export order and stability tiers match runtime metadata.")


if __name__ == "__main__":
    main()
