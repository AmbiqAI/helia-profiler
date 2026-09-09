"""Identity of the binary a run actually built (#291).

Source-side provenance already names the exact inputs — dependency locks,
module commits, and a recursive content digest for every path override. The
build side had no equivalent, so two firmware images differing only in
``-mcpu=cortex-m55`` vs ``-mcpu=cortex-m55+nomve`` produced bundles whose
metadata was byte-identical, and evidence had to be told apart by directory
name. Both facts recorded here come from artifacts the build already leaves
behind: the binary itself, and the ``compile_commands.json`` CMake writes.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path

from ..results import BuildImage, RunMetadata
from ..results.serde import sha256_file

log = logging.getLogger("hpx")

#: The flags that decide the instruction set. A build that changes any of
#: them is a different measurement, whatever its directory is called.
_ARCH_FLAG = re.compile(r"(?<![\w=])-m(?:cpu|arch|fpu|float-abi)=[^\s\"']+")

#: Deepest build tree worth searching for the compile database. NSX writes it
#: at the build root; the bound stops a runaway walk on an odd layout.
_COMPILE_DB_DEPTH = 3


def build_image(
    *,
    role: str,
    target_name: str,
    binary_path: Path,
    build_dir: Path,
) -> BuildImage | None:
    """Record which image was built and what the compiler was given.

    Best-effort, like ``binary_sections``: a missing compile database costs
    the flag set, not the run. Returns ``None`` only when the binary itself
    cannot be read, since an image with no digest states nothing.
    """
    try:
        digest = sha256_file(binary_path)
        size_bytes = binary_path.stat().st_size
    except OSError as exc:
        log.info("Build image provenance unavailable for %s: %s", binary_path, exc)
        return None

    flags, translation_units = _architecture_flags(build_dir)
    return BuildImage(
        role=role,
        target_name=target_name,
        binary_name=binary_path.name,
        sha256=digest,
        size_bytes=size_bytes,
        architecture_flags=flags,
        translation_units=translation_units,
    )


def record_build_image(
    metadata: RunMetadata,
    *,
    role: str,
    target_name: str,
    binary_path: Path,
    build_dir: Path,
) -> None:
    """Append one built target's identity to run metadata.

    Takes the metadata rather than the pipeline context because that is all
    it touches — a build stage is not a prerequisite for recording what a
    build produced.

    Replaces any earlier entry for the same role: a power run re-renders and
    rebuilds its target for a host-selected inference count, and the image
    that ran is the last one built, not the first.
    """
    image = build_image(
        role=role,
        target_name=target_name,
        binary_path=binary_path,
        build_dir=build_dir,
    )
    if image is None:
        return
    kept = tuple(existing for existing in metadata.build_images if existing.role != image.role)
    metadata.build_images = (*kept, image)
    log.info(
        "Build image (%s): sha256=%s%s",
        role,
        image.sha256[:12],
        f" · {', '.join(image.architecture_flags)}" if image.architecture_flags else "",
    )


def _architecture_flags(build_dir: Path) -> tuple[dict[str, int], int]:
    """Count the architecture flags the compiler actually received.

    Counts rather than a bare set: one flag over every translation unit is a
    uniform build, while two spellings of ``-mcpu`` state a genuinely mixed
    one instead of letting whichever appeared first speak for the image.

    The database covers the whole build tree — every module compiled into it,
    not only ``target_name`` — which is what makes it answer "was this tree
    built with Helium", the question the flags are recorded for.
    """
    database = _find_compile_database(build_dir)
    if database is None:
        log.info("No compile_commands.json under %s — architecture flags not recorded", build_dir)
        return {}, 0

    try:
        entries = json.loads(database.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.info("Could not read %s: %s", database, exc)
        return {}, 0
    if not isinstance(entries, list):
        log.info("%s is not a compile-command list — architecture flags not recorded", database)
        return {}, 0

    counts: Counter[str] = Counter()
    translation_units = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        translation_units += 1
        # Deduplicate within a unit so a flag repeated on one command line
        # cannot outvote a flag that genuinely differs between units.
        counts.update(set(_ARCH_FLAG.findall(_command_text(entry))))
    return dict(sorted(counts.items())), translation_units


def _command_text(entry: dict[str, object]) -> str:
    """Return one entry's compile command, in either database spelling."""
    command = entry.get("command")
    if isinstance(command, str):
        return command
    arguments = entry.get("arguments")
    if isinstance(arguments, list):
        return " ".join(str(argument) for argument in arguments)
    return ""


def _find_compile_database(build_dir: Path) -> Path | None:
    """Locate the compile database, shallowest match first."""
    for depth in range(_COMPILE_DB_DEPTH):
        pattern = "/".join(["*"] * depth + ["compile_commands.json"])
        matches = sorted(path for path in build_dir.glob(pattern) if path.is_file())
        if matches:
            return matches[0]
    return None
