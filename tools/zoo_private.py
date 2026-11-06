# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyrage>=1.4.0",
#     "pyyaml>=6.0",
# ]
# ///
"""Show or edit the private Zoo list; seal or open private results.

    uv run tools/zoo_private.py show          # plaintext to stdout
    uv run tools/zoo_private.py edit          # $EDITOR, check, re-encrypt
    uv run tools/zoo_private.py seal DIR OUT  # tar.gz DIR, encrypt to OUT
    uv run tools/zoo_private.py unseal IN DIR # decrypt IN, extract to DIR

Decrypts with ``ZOO_AGE_KEY``, ``ZOO_AGE_KEY_FILE`` or
``~/.config/helia-profiler/zoo-private.key`` and encrypts to every key in
``tools/zoo_private.recipients``. For readable ``git diff`` output:

    git config diff.age.textconv "uv run --quiet tools/zoo_private.py show"
"""

from __future__ import annotations

import argparse
import io
import os
import shlex
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import yaml
import zoo_registry


def _identity() -> str:
    identity = zoo_registry.age_identity()
    if not identity:
        raise ValueError("no age key: set ZOO_AGE_KEY or ZOO_AGE_KEY_FILE")
    return identity


def _edit(path: Path) -> int:
    before = zoo_registry.decrypt_list(path.read_bytes(), _identity())
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


def _skip_work(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
    return None if "work" in Path(member.name).parts else member


def seal(directory: Path, output: Path, recipients: Path = zoo_registry.RECIPIENTS) -> None:
    """Encrypt a tar.gz of ``directory`` to ``output``."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        # Build trees stay out, as in public artifacts.
        archive.add(directory, arcname=directory.name, filter=_skip_work)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(zoo_registry.encrypt(buffer.getvalue(), recipients))


def unseal(sealed: Path, directory: Path, identity: str) -> None:
    """Decrypt ``sealed`` and extract it under ``directory``."""
    data = zoo_registry.decrypt(sealed.read_bytes(), identity)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        archive.extractall(directory, filter="data")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("show", "edit"):
        command = commands.add_parser(name)
        command.add_argument("path", type=Path, nargs="?", default=zoo_registry.PRIVATE_LIST)
    command = commands.add_parser("seal")
    command.add_argument("directory", type=Path)
    command.add_argument("output", type=Path)
    command = commands.add_parser("unseal")
    command.add_argument("sealed", type=Path)
    command.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            sys.stdout.write(zoo_registry.decrypt_list(args.path.read_bytes(), _identity()))
        elif args.command == "edit":
            return _edit(args.path)
        elif args.command == "seal":
            seal(args.directory, args.output)
        else:
            unseal(args.sealed, args.directory, _identity())
    except (OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f"zoo_private: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
