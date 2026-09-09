"""#291 — a bundle must be able to say which binary produced it.

The failure these guard against is not a crash: it is two runs whose metadata
is byte-identical because the only thing that differed was ``-mcpu``, leaving
the directory name as the sole record of which was which.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from helia_profiler.firmware.image import build_image, record_build_image
from helia_profiler.results import BuildImage, RunMetadata
from helia_profiler.results.dimensions import derive_architecture_flags

MVE = "-mcpu=cortex-m55"
NOMVE = "-mcpu=cortex-m55+nomve"


def _build_tree(
    root: Path, flags: list[str], *, units: int = 3, nest: int = 0
) -> tuple[Path, Path]:
    """Write a fake build tree: one binary and one compile database."""
    build_dir = root / "build" / "apollo510_evb"
    build_dir.mkdir(parents=True)
    binary = build_dir / "hpx_profiler.elf"
    binary.write_bytes(b"\x7fELF" + b"".join(f.encode() for f in flags))

    entries = [
        {
            "directory": str(build_dir),
            "file": f"src/unit{index}.c",
            "command": f"arm-none-eabi-gcc {flags[index % len(flags)]} -O3 -c src/unit{index}.c",
        }
        for index in range(units)
    ]
    db_dir = build_dir.joinpath(*[f"nested{n}" for n in range(nest)])
    db_dir.mkdir(parents=True, exist_ok=True)
    (db_dir / "compile_commands.json").write_text(json.dumps(entries))
    return build_dir, binary


def _image(root: Path, flags: list[str], **kwargs) -> BuildImage:
    build_dir, binary = _build_tree(root, flags, **kwargs)
    image = build_image(
        role="profile", target_name="hpx_profiler", binary_path=binary, build_dir=build_dir
    )
    assert image is not None
    return image


def test_the_image_is_identified_by_the_digest_of_the_binary_itself(tmp_path):
    build_dir, binary = _build_tree(tmp_path, [MVE])
    image = build_image(
        role="profile", target_name="hpx_profiler", binary_path=binary, build_dir=build_dir
    )

    assert image is not None
    assert image.sha256 == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert image.size_bytes == binary.stat().st_size
    assert image.binary_name == "hpx_profiler.elf"
    assert image.role == "profile"


def test_a_uniform_build_reports_one_cpu_flag_over_every_translation_unit(tmp_path):
    image = _image(tmp_path, [MVE], units=5)

    assert image.architecture_flags == {MVE: 5}
    assert image.translation_units == 5


def test_the_two_builds_of_an_mve_ab_are_distinguishable_without_their_directory_names(tmp_path):
    """The #488 acceptance criterion, stated as the pair it failed on."""
    with_mve = _image(tmp_path / "a", [MVE])
    without_mve = _image(tmp_path / "b", [NOMVE])

    assert with_mve.architecture_flags != without_mve.architecture_flags
    assert with_mve.sha256 != without_mve.sha256


def test_a_mixed_build_states_both_spellings_rather_than_letting_one_speak(tmp_path):
    image = _image(tmp_path, [MVE, NOMVE, NOMVE], units=6)

    assert image.architecture_flags == {MVE: 2, NOMVE: 4}


def test_a_flag_repeated_within_one_unit_cannot_outvote_a_differing_unit(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    binary = build_dir / "hpx_profiler.elf"
    binary.write_bytes(b"\x7fELF")
    (build_dir / "compile_commands.json").write_text(
        json.dumps(
            [
                {"file": "a.c", "command": f"gcc {MVE} {MVE} {MVE} -c a.c"},
                {"file": "b.c", "command": f"gcc {NOMVE} -c b.c"},
            ]
        )
    )

    image = build_image(
        role="profile", target_name="hpx_profiler", binary_path=binary, build_dir=build_dir
    )

    assert image is not None
    assert image.architecture_flags == {MVE: 1, NOMVE: 1}


def test_the_arguments_form_of_the_compile_database_is_read_too(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    binary = build_dir / "hpx_profiler.elf"
    binary.write_bytes(b"\x7fELF")
    (build_dir / "compile_commands.json").write_text(
        json.dumps([{"file": "a.c", "arguments": ["gcc", NOMVE, "-mfloat-abi=hard", "-c", "a.c"]}])
    )

    image = build_image(
        role="profile", target_name="hpx_profiler", binary_path=binary, build_dir=build_dir
    )

    assert image is not None
    assert image.architecture_flags == {NOMVE: 1, "-mfloat-abi=hard": 1}


@pytest.mark.parametrize("nest", [0, 1, 2])
def test_the_compile_database_is_found_down_to_the_declared_depth(tmp_path, nest):
    image = _image(tmp_path, [MVE], nest=nest)

    assert image.architecture_flags == {MVE: 3}


def test_a_database_deeper_than_the_bound_costs_the_flags_not_the_digest(tmp_path):
    """Pins where the search stops, so the bound is a decision, not a surprise."""
    image = _image(tmp_path, [MVE], nest=3)

    assert image.sha256
    assert image.architecture_flags == {}


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(lambda p: p.write_text("{not json"), id="unparseable"),
        pytest.param(lambda p: p.write_text('{"entries": []}'), id="not_a_list"),
        pytest.param(lambda p: p.unlink(), id="absent"),
    ],
)
def test_an_unusable_compile_database_costs_the_flags_not_the_digest(tmp_path, corrupt):
    build_dir, binary = _build_tree(tmp_path, [MVE])
    corrupt(build_dir / "compile_commands.json")

    image = build_image(
        role="profile", target_name="hpx_profiler", binary_path=binary, build_dir=build_dir
    )

    assert image is not None
    assert image.sha256 == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert image.architecture_flags == {}


def test_an_unreadable_binary_records_nothing_rather_than_a_digestless_image(tmp_path):
    build_dir, binary = _build_tree(tmp_path, [MVE])
    binary.unlink()

    assert (
        build_image(
            role="profile", target_name="hpx_profiler", binary_path=binary, build_dir=build_dir
        )
        is None
    )


# ---------------------------------------------------------------------------
# Recording into run metadata
# ---------------------------------------------------------------------------


def _record(metadata: RunMetadata, root: Path, flags: list[str], *, role: str) -> None:
    build_dir, binary = _build_tree(root, flags)
    record_build_image(
        metadata, role=role, target_name=f"hpx_{role}", binary_path=binary, build_dir=build_dir
    )


def test_a_power_run_records_both_of_its_images(tmp_path):
    meta = RunMetadata()
    _record(meta, tmp_path / "profile", [MVE], role="profile")
    _record(meta, tmp_path / "power", [MVE], role="power")

    assert [image.role for image in meta.build_images] == ["profile", "power"]


def test_rebuilding_a_target_records_the_image_that_ran_not_the_first_one(tmp_path):
    """The power target is rebuilt for a host-selected N; the last one flashes."""
    meta = RunMetadata()
    _record(meta, tmp_path / "profile", [MVE], role="profile")
    _record(meta, tmp_path / "first", [MVE], role="power")
    first = meta.build_images[-1].sha256
    _record(meta, tmp_path / "second", [NOMVE], role="power")

    roles = [image.role for image in meta.build_images]
    assert roles == ["profile", "power"]
    assert meta.build_images[-1].sha256 != first


def test_an_unreadable_binary_leaves_run_metadata_untouched(tmp_path):
    meta = RunMetadata()
    build_dir, binary = _build_tree(tmp_path, [MVE])
    binary.unlink()

    record_build_image(
        meta, role="profile", target_name="hpx_profiler", binary_path=binary, build_dir=build_dir
    )

    assert meta.build_images == ()


# ---------------------------------------------------------------------------
# The comparison dimension
# ---------------------------------------------------------------------------


def _record_dict(role: str, flags: dict[str, int]) -> dict:
    return {"role": role, "architecture_flags": flags}


def test_the_dimension_summarizes_the_profile_image():
    # Power listed first on purpose: a summary that simply took the first
    # image would pass with the natural ordering and still be wrong.
    metadata = {
        "build_images": [
            _record_dict("power", {NOMVE: 40}),
            _record_dict("profile", {MVE: 40, "-mfloat-abi=hard": 40}),
        ]
    }

    assert derive_architecture_flags(metadata) == f"{MVE} -mfloat-abi=hard"


def test_a_power_only_run_falls_back_to_the_image_it_does_have():
    metadata = {"build_images": [_record_dict("power", {NOMVE: 12})]}

    assert derive_architecture_flags(metadata) == NOMVE


def test_the_mve_ab_differs_on_the_dimension_a_comparison_reads():
    mve = {"build_images": [_record_dict("profile", {MVE: 40})]}
    nomve = {"build_images": [_record_dict("profile", {NOMVE: 40})]}

    assert derive_architecture_flags(mve) != derive_architecture_flags(nomve)


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param({}, id="predates_291"),
        pytest.param({"build_images": []}, id="nothing_built"),
        pytest.param({"build_images": "profile"}, id="not_a_list"),
        pytest.param({"build_images": [{"role": "profile"}]}, id="flags_unrecorded"),
        pytest.param({"build_images": [_record_dict("profile", {})]}, id="no_flags_found"),
    ],
)
def test_an_absent_record_is_none_so_the_comparator_skips_it(metadata):
    assert derive_architecture_flags(metadata) is None
