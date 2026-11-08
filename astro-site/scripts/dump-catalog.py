"""Read the resolved board and engine registries as the Home page's JSON input.

Run through the project's locked environment, as with the CLI reference. Reading
platform views loads catalog facts but invokes no build tools or device operations.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Path to src/helia_profiler.")
    args = parser.parse_args()
    root = Path(args.source).resolve()
    if not (root / "__init__.py").is_file():
        raise SystemExit(f"{root} is not a package source directory.")
    sys.path.insert(0, str(root.parent))

    from helia_profiler.engines import EngineType
    from helia_profiler.platform import list_boards

    json.dump(
        {
            "boards": [
                {"id": b.name, "soc": b.soc, "channel": b.channel, "isFpga": b.is_fpga}
                for b in list_boards()
            ],
            "engines": [{"id": e.value} for e in EngineType],
        },
        sys.stdout,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
