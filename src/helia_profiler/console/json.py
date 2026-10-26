"""Machine-readable command output, written directly to stdout."""

import json
from typing import Any


def print_json(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
