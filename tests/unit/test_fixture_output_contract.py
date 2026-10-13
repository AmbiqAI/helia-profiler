"""Complete measurement IO need not contain a numerical reference answer."""

import hashlib
import json
from dataclasses import asdict, replace
from enum import StrEnum
from types import SimpleNamespace

import pytest

from helia_profiler.fixture import (
    FixtureFile,
    FixtureIO,
    FixtureOutput,
    FixtureRole,
    FixtureTensor,
    PerTensorQuantization,
    TypedFixture,
)


def _file(path, data):
    path.write_bytes(data)
    return FixtureFile(path, hashlib.sha256(data).hexdigest())


def _fixture(tmp_path):
    tensor = FixtureTensor("signal", 0, "int8", (1, 4), PerTensorQuantization(0.5, 0))
    return TypedFixture(
        _file(tmp_path / "model", b"model"),
        (FixtureIO(tensor, _file(tmp_path / "input", b"\x01\x02\x03\x04")),),
        (FixtureOutput(replace(tensor, name="score", index=1)),),
    )


def test_complete_outputs_without_reference_files(tmp_path):
    fixture = _fixture(tmp_path)
    fixture.verify()
    assert fixture.expected is None
    assert len(list(tmp_path.iterdir())) == 2
    changed = replace(
        fixture, outputs=(FixtureOutput(replace(fixture.outputs[0].tensor, shape=(1, 5))),)
    )
    assert fixture.identity != changed.identity
    fixture.inputs[0].data.path.write_bytes(b"\x00" * 4)
    with pytest.raises(ValueError, match="hash|sha256|digest"):
        fixture.verify()


def test_legacy_reference_identity_is_byte_compatible(tmp_path):
    fixture = _fixture(tmp_path)
    fixture = replace(
        fixture,
        outputs=(FixtureIO(fixture.outputs[0].tensor, _file(tmp_path / "expected", b"\x00" * 4)),),
    )
    previous = asdict(fixture)
    previous["model"].pop("path")
    for io in (*previous["inputs"], *previous["outputs"]):
        io["data"].pop("path")
    assert (
        fixture.identity
        == hashlib.sha256(
            json.dumps(previous, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
    )
    fixture.verify()


def test_mixed_reference_and_descriptor_outputs_refuse(tmp_path):
    fixture = _fixture(tmp_path)
    mixed = replace(
        fixture,
        outputs=(*fixture.outputs, FixtureIO(fixture.outputs[0].tensor, fixture.inputs[0].data)),
    )
    with pytest.raises(ValueError, match="Mixed"):
        mixed.verify()


def test_reference_removal_does_not_change_rendered_io(tmp_path):
    from helia_profiler.firmware.fixture import _fixture_io

    descriptors = _fixture(tmp_path)
    referenced = replace(
        descriptors,
        outputs=(
            FixtureIO(descriptors.outputs[0].tensor, _file(tmp_path / "reference", bytes(4))),
        ),
    )
    for inputs in (True, False):
        assert _fixture_io(SimpleNamespace(fixture=descriptors), inputs=inputs) == _fixture_io(
            SimpleNamespace(fixture=referenced), inputs=inputs
        )


class ConsumerRole(StrEnum):
    SIGNAL = "signal"


@pytest.mark.parametrize("role", [ConsumerRole.SIGNAL, "signal"])
def test_build_record_normalizes_valid_caller_role_without_changing_identity(tmp_path, role):
    from helia_profiler._fixture_build import _measurement_io

    original = _fixture(tmp_path)
    supplied = replace(original, inputs=(replace(original.inputs[0], role=role),))
    supplied.verify()
    assert supplied.identity == original.identity
    inputs, outputs = _measurement_io(supplied)
    assert inputs[0].role is FixtureRole.SIGNAL
    assert inputs[0].data == original.inputs[0].data
    assert outputs == tuple(io.tensor for io in supplied.outputs)
