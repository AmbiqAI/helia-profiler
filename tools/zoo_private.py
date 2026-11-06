# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyrage>=1.4.0",
#     "pyyaml>=6.0",
# ]
# ///
"""Show or edit the age-encrypted private Zoo list.

    uv run tools/zoo_private.py show          # plaintext to stdout
    uv run tools/zoo_private.py edit          # $EDITOR, check, re-encrypt

Decrypts with ``ZOO_AGE_KEY``, ``ZOO_AGE_KEY_FILE`` or
``~/.config/helia-profiler/zoo-private.key`` and encrypts to every key in
``tools/zoo_private.recipients``. For readable ``git diff`` output:

    git config diff.age.textconv "uv run --quiet tools/zoo_private.py show"
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
import zoo_registry


def _decrypt(path: Path) -> str:
    identity = zoo_registry.age_identity()
    if not identity:
        raise ValueError("no age key: set ZOO_AGE_KEY or ZOO_AGE_KEY_FILE")
    return zoo_registry.decrypt_list(path.read_bytes(), identity)


def _edit(path: Path) -> int:
    before = _decrypt(path)
    with tempfile.TemporaryDirectory(prefix="zoo-private-") as scratch:
        draft = Path(scratch) / "zoo_private.yaml"
        draft.write_text(before, encoding="utf-8")
        editor = shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi")
        while True:
            subprocess.run([*editor, str(draft)], check=True)
            after = draft.read_text(encoding="utf-8")
            try:
                zoo_registry.parse_private_list(after)
                break
            except (ValueError, yaml.YAMLError) as error:
                print(f"zoo_private: {error}", file=sys.stderr)
                input("Enter to fix, Ctrl-C to drop the edit: ")
    if after == before:
        print("No change.")
        return 0
    path.write_bytes(zoo_registry.encrypt_list(after))
    print(f"Encrypted {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("show", "edit"))
    parser.add_argument("path", type=Path, nargs="?", default=zoo_registry.PRIVATE_LIST)
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            sys.stdout.write(_decrypt(args.path))
            return 0
        return _edit(args.path)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"zoo_private: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
