"""Print the resolved identities of the Nix environment as JSON.

The flake passes the identities fixed at evaluation (flake and nixpkgs
revisions, the heliaAOT selection, the compatibility baseline pins) in
HPX_NIX_IDENTITIES. This script adds what the installed tools report.
"""

import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys


def first_line(*command: str) -> str | None:
    if shutil.which(command[0]) is None and not os.path.isabs(command[0]):
        return None
    try:
        out = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except OSError:
        return None
    text = (out.stdout or out.stderr).strip()
    return text.splitlines()[0] if text else None


def dist_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def main() -> None:
    identities = json.loads(os.environ["HPX_NIX_IDENTITIES"])
    atfe_root = os.environ.get("ATFE_ROOT", "")
    identities["installed"] = {
        "python": platform.python_version(),
        "helia-aot": dist_version("helia-aot"),
        "helia-profiler": dist_version("helia-profiler"),
        "neuralspotx": dist_version("neuralspotx"),
        "ai-edge-litert": dist_version("ai-edge-litert"),
        "uv": first_line("uv", "--version"),
        "arm-none-eabi-gcc": first_line("arm-none-eabi-gcc", "--version"),
        "atfe-clang": first_line(os.path.join(atfe_root, "bin", "clang"), "--version")
        if atfe_root
        else None,
        "cmake": first_line("cmake", "--version"),
        "ninja": first_line("ninja", "--version"),
    }
    json.dump(identities, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
