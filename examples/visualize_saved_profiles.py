"""Experimental saved-profile export; see examples/saved-visualization.md."""

import argparse
from pathlib import Path

from helia_profiler._profile_visualization import export_visualization, read_visual_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", nargs=2, action="append", required=True, metavar=("LABEL", "BUNDLE")
    )
    parser.add_argument(
        "--mapping", type=Path, help="Explicit manifest-bound graph attribution JSON"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="New directory outside source bundles"
    )
    args = parser.parse_args()
    runs = [read_visual_run(Path(path), label) for label, path in args.run]
    print(export_visualization(runs, args.output, mapping_path=args.mapping))


if __name__ == "__main__":
    main()
