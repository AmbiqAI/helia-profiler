"""Identity and size of file-backed artifacts."""

import hashlib

import pytest

from helia_profiler._files import FixtureFile, sha256_file_with_size, verified_size


@pytest.mark.parametrize("length", [0, 1, 1_048_593])
def test_file_identity_and_verified_size(tmp_path, length):
    path = tmp_path / "model"
    payload = bytes(range(256)) * (length // 256) + bytes(range(length % 256))
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    assert sha256_file_with_size(path) == (digest, length)
    pinned = FixtureFile(path, digest)
    assert verified_size(pinned) == length
    if length:
        path.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
        with pytest.raises(ValueError, match="hash mismatch"):
            verified_size(pinned)
