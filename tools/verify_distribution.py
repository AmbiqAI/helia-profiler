#!/usr/bin/env python3
"""Verify release distributions before they cross a package-index boundary."""

from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path


PACKAGE_NAME = "helia-profiler"
REQUIRES_PYTHON = ">=3.11,<3.13"
REQUIRED_PACKAGE_FILES = {
    "helia_profiler/vendor/segger_rtt/LICENSE.md",
    "helia_profiler/vendor/segger_rtt/SOURCE.md",
    "helia_profiler/vendor/segger_rtt/Config/SEGGER_RTT_Conf.h",
    "helia_profiler/vendor/segger_rtt/RTT/SEGGER_RTT.c",
    "helia_profiler/vendor/segger_rtt/RTT/SEGGER_RTT.h",
    "helia_profiler/vendor/segger_rtt/RTT/SEGGER_RTT_ConfDefaults.h",
}
OBSOLETE_WHEEL_FILES = {
    "helia_profiler/artifacts.py",
    "helia_profiler/comparability.py",
    "helia_profiler/compare.py",
    "helia_profiler/comparison_profile.py",
    "helia_profiler/model_analysis.py",
    "helia_profiler/result_manifest.py",
    "helia_profiler/results.py",
    "helia_profiler/validity.py",
}


def _only(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {pattern!r} in {directory}, found {len(matches)}")
    return matches[0]


def _verify_metadata(metadata_bytes: bytes, expected_version: str) -> None:
    metadata = BytesParser(policy=default).parsebytes(metadata_bytes)
    expected = {
        "Name": PACKAGE_NAME,
        "Version": expected_version,
        "License-Expression": "Apache-2.0",
        "Description-Content-Type": "text/markdown",
    }
    for field, expected_value in expected.items():
        actual = metadata.get(field)
        if actual != expected_value:
            raise ValueError(f"METADATA {field} is {actual!r}, expected {expected_value!r}")

    requires_python = metadata.get("Requires-Python")
    if requires_python is None or set(requires_python.split(",")) != set(REQUIRES_PYTHON.split(",")):
        raise ValueError(
            f"METADATA Requires-Python is {requires_python!r}, expected bounds {REQUIRES_PYTHON!r}"
        )

    project_urls = set(metadata.get_all("Project-URL", []))
    required_urls = {
        "Documentation, https://ambiqai.github.io/helia-profiler/",
        "Issues, https://github.com/AmbiqAI/helia-profiler/issues",
        "Repository, https://github.com/AmbiqAI/helia-profiler",
    }
    missing_urls = required_urls - project_urls
    if missing_urls:
        raise ValueError(f"METADATA is missing project URLs: {sorted(missing_urls)}")


def _verify_wheel(wheel: Path, expected_version: str) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ValueError(f"Wheel contains {len(metadata_names)} METADATA files")
        _verify_metadata(archive.read(metadata_names[0]), expected_version)

    missing = REQUIRED_PACKAGE_FILES - names
    if missing:
        raise ValueError(f"Wheel is missing packaged resources: {sorted(missing)}")
    obsolete = OBSOLETE_WHEEL_FILES & names
    if obsolete:
        raise ValueError(f"Wheel contains obsolete compatibility modules: {sorted(obsolete)}")

    license_files = {name for name in names if ".dist-info/licenses/" in name}
    required_license_suffixes = {
        "/LICENSE",
        "/THIRD_PARTY_NOTICES.md",
        "/src/helia_profiler/vendor/segger_rtt/LICENSE.md",
    }
    missing_licenses = {
        suffix for suffix in required_license_suffixes if not any(name.endswith(suffix) for name in license_files)
    }
    if missing_licenses:
        raise ValueError(f"Wheel is missing license files: {sorted(missing_licenses)}")


def _verify_sdist(sdist: Path) -> None:
    with tarfile.open(sdist, mode="r:gz") as archive:
        names = {member.name for member in archive.getmembers() if member.isfile()}
    roots = {name.split("/", 1)[0] for name in names}
    if len(roots) != 1:
        raise ValueError(f"sdist has unexpected archive roots: {sorted(roots)}")
    root = next(iter(roots))
    required = {
        f"{root}/LICENSE",
        f"{root}/README.md",
        f"{root}/THIRD_PARTY_NOTICES.md",
        f"{root}/pyproject.toml",
        *(f"{root}/src/{name}" for name in REQUIRED_PACKAGE_FILES),
    }
    missing = required - names
    if missing:
        raise ValueError(f"sdist is missing release files: {sorted(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist_dir", type=Path)
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()

    try:
        wheel = _only(args.dist_dir, "*.whl")
        sdist = _only(args.dist_dir, "*.tar.gz")
        _verify_wheel(wheel, args.expected_version)
        _verify_sdist(sdist)
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"release distribution verification failed: {exc}", file=sys.stderr)
        return 1

    print(f"verified {wheel.name} and {sdist.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
