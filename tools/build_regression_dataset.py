"""Build static regression dashboard data from validation bundles."""

from __future__ import annotations

import argparse
from pathlib import Path

from helia_profiler.regression import build_regression_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundles", nargs="+", type=Path, help="Completed validation bundles")
    parser.add_argument("--output", required=True, type=Path, help="Dataset output directory")
    args = parser.parse_args()
    paths = build_regression_dataset(args.bundles, args.output)
    print(f"Wrote regression dataset with {len(args.bundles)} runs and {len(paths)} files")


if __name__ == "__main__":
    main()
