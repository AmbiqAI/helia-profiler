"""Regenerate or verify ``src/helia_profiler/fixture_closure.txt``.

The closure is every file the wheel ships (tracked ``*.py`` plus the
``[tool.setuptools.package-data]`` globs) minus ``EXCLUDED``. New shipped files
join it by default; an exclusion needs a reason here and must stay unreachable
from a fixture build (``tests/contracts/test_fixture_closure.py``).

    uv run python tools/gen_fixture_closure.py          # write
    uv run python tools/gen_fixture_closure.py --check  # fail on drift
"""

from __future__ import annotations

import argparse
import glob
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "helia_profiler"
LISTING = PACKAGE / "fixture_closure.txt"

#: Shipped files outside the closure, each with why no fixture build reads it.
EXCLUDED = {
    "data/models/**": "bundled example models, read only by examples.py",
}


def _matches(patterns: list[str] | tuple[str, ...]) -> set[str]:
    found: set[str] = set()
    for pattern in patterns:
        found.update(
            Path(p).as_posix()
            for p in glob.glob(pattern, root_dir=PACKAGE, recursive=True)
            if (PACKAGE / p).is_file()
        )
    return found


def tracked_files() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "src/helia_profiler"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode()
    prefix = "src/helia_profiler/"
    return {p.removeprefix(prefix) for p in out.split("\0") if p}


def shipped_files() -> set[str]:
    tracked = tracked_files()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    globs = pyproject["tool"]["setuptools"]["package-data"]["helia_profiler"]
    return {p for p in tracked if p.endswith(".py")} | (_matches(globs) & tracked)


def excluded_files() -> set[str]:
    return _matches(tuple(EXCLUDED))


def closure() -> list[str]:
    return sorted(shipped_files() - excluded_files() - {LISTING.name})


def render() -> bytes:
    return "".join(f"{path}\n" for path in closure()).encode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the listing is stale")
    args = parser.parse_args()
    expected = render()
    if not args.check:
        LISTING.write_bytes(expected)
        return 0
    current = LISTING.read_bytes() if LISTING.exists() else b""
    if current == expected:
        return 0
    have = set(current.decode().splitlines())
    want = set(expected.decode().splitlines())
    for path in sorted(want - have):
        print(f"missing from {LISTING.name}: {path}", file=sys.stderr)
    for path in sorted(have - want):
        print(f"stale in {LISTING.name}: {path}", file=sys.stderr)
    print("Run: uv run python tools/gen_fixture_closure.py", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
