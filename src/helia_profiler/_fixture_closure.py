"""Content closure of the installed package that fixed-fixture consumers verify."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from .errors import ConfigError

#: Package-relative listing of every shipped file a fixture build may load.
CLOSURE_LISTING = "fixture_closure.txt"


@dataclass(frozen=True)
class SourceClosure:
    """Hashes of the listed package files and one digest over listing plus hashes."""

    api_version: tuple[int, int]
    files: tuple[tuple[str, str], ...]
    digest: str


def closure_paths(listing: bytes) -> tuple[str, ...]:
    """Parse the listing: one package-relative POSIX path per line, sorted, unique."""
    paths = tuple(listing.decode("utf-8").splitlines())
    if not paths or any(not p or p.startswith("/") or ".." in p.split("/") for p in paths):
        raise ConfigError("Malformed fixture closure listing")
    if list(paths) != sorted(set(paths)):
        raise ConfigError("Fixture closure listing is not sorted and unique")
    return paths


def closure_digest(listing: bytes, files: tuple[tuple[str, str], ...]) -> str:
    """sha256 over the listing bytes, then ``path\\0sha256\\n`` per file in listing order."""
    digest = hashlib.sha256(listing)
    for path, sha256 in files:
        digest.update(f"{path}\0{sha256}\n".encode())
    return digest.hexdigest()


def source_closure(root: Path | None = None) -> SourceClosure:
    """Hash the installed package files named by the closure listing."""
    from .fixture import FIXTURE_API_VERSION

    root = Path(__file__).resolve().parent if root is None else root
    listing = (root / CLOSURE_LISTING).read_bytes()
    files = []
    for path in closure_paths(listing):
        target = root / path
        if not target.is_file():
            raise ConfigError(f"Fixture closure file missing: {path}")
        files.append((path, hashlib.sha256(target.read_bytes()).hexdigest()))
    frozen = tuple(files)
    return SourceClosure(FIXTURE_API_VERSION, frozen, closure_digest(listing, frozen))
