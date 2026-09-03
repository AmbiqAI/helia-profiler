#!/usr/bin/env python3
"""Run gitleaks against staged changes, or explain how to install it.

`.pre-commit-config.yaml` shells out to a system-installed `gitleaks`
(`language: system`) rather than pre-commit's `golang` hook language, so a
missing binary must fail loudly instead of silently skipping the scan.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

GITLEAKS_VERSION = "8.29.1"

INSTALL_MESSAGE = f"""
gitleaks was not found on PATH. helia-profiler's pre-commit hooks scan
staged changes for secrets with gitleaks v{GITLEAKS_VERSION}.

Install it with one of:
  Nix (devShell or profile): nix profile install nixpkgs#gitleaks
  Homebrew:                  brew install gitleaks
  Manual release download:   https://github.com/gitleaks/gitleaks/releases/tag/v{GITLEAKS_VERSION}

Then re-run the commit.
""".strip()


def main() -> int:
    gitleaks = shutil.which("gitleaks")
    if gitleaks is None:
        print(INSTALL_MESSAGE, file=sys.stderr)
        return 1
    result = subprocess.run([gitleaks, "git", "--pre-commit", "--redact", "--staged", "--verbose"])
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
