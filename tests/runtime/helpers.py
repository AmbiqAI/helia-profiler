"""Synthetic archive and manifest for runtime ownership tests."""

import json
from hashlib import sha256

from helia_profiler import FixtureFile, PreparedUpstreamRuntime


def runtime(tmp_path, mutate=None):
    archive = tmp_path / "runtime.a"
    archive.write_bytes(b"!<arch>\nfixture-member")
    header = tmp_path / "header.h"
    header.write_bytes(b"header")
    data = {
        "schema_version": 1,
        "archive_sha256": sha256(archive.read_bytes()).hexdigest(),
        "providers": {
            "tflite-micro": {
                "url": "https://github.com/tensorflow/tflite-micro",
                "revision": "a" * 40,
            },
            "cmsis-nn": {"url": "https://github.com/ARM-software/CMSIS-NN", "revision": "b" * 40},
        },
        "abi": {"toolchain": "atfe", "cpu": "cortex-m55", "float_abi": "hard", "short_enums": True},
        "headers": {"header.h": sha256(header.read_bytes()).hexdigest()},
        "include_dirs": ["."],
    }
    if mutate:
        mutate(data)
    manifest = tmp_path / "runtime.json"
    manifest.write_text(json.dumps(data))
    return PreparedUpstreamRuntime(
        FixtureFile(archive, sha256(archive.read_bytes()).hexdigest()),
        tmp_path,
        FixtureFile(manifest, sha256(manifest.read_bytes()).hexdigest()),
    )
