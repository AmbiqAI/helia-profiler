"""The pairing digest on real captures: one KWS session with the weights in MRAM, PSRAM and TCM.

The three result bundles come from one board session and are committed under
``tests/fixtures/calibration/pairs/<weights_location>/``; until they are, the
real-data test skips and the synthetic pairs in ``test_calibrate.py`` are the
check. Reading a bundle needs heliaAOT, as ``test_calibrate.py`` does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from helia_profiler.calibration import calibrate
from tests.calibration_bundles import pair

pytest.importorskip("helia_aot")

PAIRS = Path(__file__).parent / "fixtures" / "calibration" / "pairs"
WEIGHTS_LOCATIONS = ("mram", "psram", "tcm")


def _committed() -> bool:
    return all((PAIRS / name / "result_manifest.json").is_file() for name in WEIGHTS_LOCATIONS)


def _pairs(slow: dict[str, Any], fast: dict[str, Any]) -> bool:
    """heliaOptimus's P7, on two calibration files: a slow one and a fast ``dtcm`` one.

    The pairing digests are equal and not null, and the conditions are equal
    in every field but ``weights_memory``, and but ``external_memory_clock``
    when the slow file's weights sit in PSRAM; a field absent on one side
    equals a null one.
    """
    digest = slow["capture"]["setup"].get("pairing_digest")
    if digest is None or digest != fast["capture"]["setup"].get("pairing_digest"):
        return False
    skipped = {"weights_memory"}
    if slow["conditions"].get("weights_memory") == "psram":
        skipped.add("external_memory_clock")
    names = (set(slow["conditions"]) | set(fast["conditions"])) - skipped
    return all(slow["conditions"].get(name) == fast["conditions"].get(name) for name in names)


def _check(bundles: dict[str, Path], tmp_path: Path) -> None:
    files = {}
    for name, path in bundles.items():
        output = tmp_path / f"{name}.json"
        calibrate([path], output=output)
        files[name] = json.loads(output.read_text(encoding="utf-8"))
    fast = files["tcm"]
    assert fast["conditions"]["weights_memory"] == "dtcm"
    assert "external_memory_clock" not in fast["conditions"]
    assert files["psram"]["conditions"]["external_memory_clock"] == 48
    for slow in ("mram", "psram"):
        setup = files[slow]["capture"]["setup"]
        assert setup["pairing_digest"] is not None, slow
        assert setup["pairing_digest"] == fast["capture"]["setup"]["pairing_digest"], slow
        assert setup["digest"] != fast["capture"]["setup"]["digest"], slow
        assert _pairs(files[slow], fast), slow


@pytest.mark.skipif(
    not _committed(),
    reason=(
        "the three KWS result bundles of one board session (weights_location mram, psram "
        "and tcm) are not yet committed under tests/fixtures/calibration/pairs/"
    ),
)
def test_real_captures_pair_across_the_weights_memory(tmp_path: Path) -> None:
    _check({name: PAIRS / name for name in WEIGHTS_LOCATIONS}, tmp_path)


def test_the_check_holds_for_the_synthetic_pairs(tmp_path: Path) -> None:
    mram, tcm = pair(tmp_path, "mram")
    psram, _ = pair(tmp_path, "psram")
    _check({"mram": mram, "psram": psram, "tcm": tcm}, tmp_path)

    # It refuses a held condition that differs, and two null digests.
    def file(cpu_hz: int, digest: str | None) -> dict[str, Any]:
        return {"conditions": {"cpu_hz": cpu_hz}, "capture": {"setup": {"pairing_digest": digest}}}

    assert _pairs(file(1, "9" * 64), file(1, "9" * 64))
    assert not _pairs(file(1, "9" * 64), file(2, "9" * 64))
    assert not _pairs(file(1, None), file(1, None))
