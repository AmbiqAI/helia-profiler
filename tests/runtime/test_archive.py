"""Prepared runtime ingress rejects malformed records and changed sources."""

import json
from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest

from helia_profiler._files import FixtureFile
from helia_profiler.runtime.archive import (
    PreparedUpstreamRuntime,
    RuntimeManifest,
    prepare_runtime_module,
)
from tests.runtime.helpers import runtime


def test_verified_records_are_frozen(tmp_path):
    verified = runtime(tmp_path).verify()
    assert isinstance(verified.record, RuntimeManifest)
    assert verified.record.headers[0].name == "header.h"
    with pytest.raises(FrozenInstanceError):
        verified.record.abi.short_enums = False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema_version=True),
        lambda d: d.update(unrecognized=True),
        lambda d: d.update(providers=[]),
        lambda d: d["providers"]["cmsis-nn"].update(revision=123),
        lambda d: d["abi"].update(short_enums=1),
        lambda d: d.update(headers=[]),
        lambda d: d.update(headers={"header.h": 42}),
        lambda d: d.update(include_dirs="."),
        lambda d: d.update(include_dirs=[1]),
        lambda d: d.update(include_dirs=["../"]),
    ],
)
def test_strict_manifest_types(tmp_path, mutate):
    with pytest.raises(ValueError):
        runtime(tmp_path, mutate).verify()


@pytest.mark.parametrize("name", ["runtime.a", "header.h"])
def test_stage_rechecks_content_after_ingress(tmp_path, name):
    verified = runtime(tmp_path).verify()
    (tmp_path / name).write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        prepare_runtime_module(verified, tmp_path / "work")


def test_stage_rechecks_header_containment(tmp_path):
    verified = runtime(tmp_path).verify()
    outside = tmp_path.parent / (tmp_path.name + "-outside.h")
    outside.write_bytes(b"header")
    header = tmp_path / "header.h"
    header.unlink()
    header.symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        prepare_runtime_module(verified, tmp_path / "work")


def test_duplicate_manifest_keys_rejected(tmp_path):
    source = runtime(tmp_path)
    raw = source.manifest.path.read_text().replace(
        '"schema_version": 1', '"schema_version": 1, "schema_version": 1'
    )
    source.manifest.path.write_text(raw)
    source = PreparedUpstreamRuntime(
        source.archive,
        source.header_root,
        FixtureFile(source.manifest.path, sha256(raw.encode()).hexdigest()),
    )
    with pytest.raises(ValueError, match="Duplicate"):
        source.verify()


def test_non_archive_rejected_even_with_matching_manifest_hash(tmp_path):
    candidate = runtime(tmp_path)
    candidate.archive.path.write_bytes(b"not-an-archive")
    data = json.loads(candidate.manifest.path.read_text())
    data["archive_sha256"] = sha256(candidate.archive.path.read_bytes()).hexdigest()
    candidate.manifest.path.write_text(json.dumps(data))
    candidate = PreparedUpstreamRuntime(
        FixtureFile(candidate.archive.path, data["archive_sha256"]),
        candidate.header_root,
        FixtureFile(
            candidate.manifest.path, sha256(candidate.manifest.path.read_bytes()).hexdigest()
        ),
    )
    with pytest.raises(ValueError, match="archive"):
        candidate.verify()
