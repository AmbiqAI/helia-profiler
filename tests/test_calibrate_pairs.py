"""The pairing digest on real captures: one KWS session with the weights in MRAM, PSRAM and TCM.

Capture recipe: three ``hpx profile --engine helia-aot`` runs of one KWS model
in one board session, identical but for ``model.weights_location`` (``mram``,
``psram``, ``tcm``), every one of them with
``engine.config.aot_args.memory.allocate_arenas: false``. hpx accepts PSRAM
weights only in that mode, and the setting is held by the pairing digest, so
a TCM or MRAM capture left at the default does not pair with the PSRAM one.
The three bundles are committed under
``tests/fixtures/calibration/pairs/<weights_location>/``, and a missing one
fails the real-data test. The retained bundles keep their original summary contract and are read with
the producing HPX release. Current-format pairing is exercised by the synthetic
pair below. Reading a bundle needs heliaAOT, as ``test_calibrate.py`` does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from helia_profiler.calibration import calibrate
from tests.calibration_bundles import pair, schema_checked_writes  # noqa: F401

pytest.importorskip("helia_aot")
pytestmark = pytest.mark.usefixtures("schema_checked_writes")

PAIRS = Path(__file__).parent / "fixtures" / "calibration" / "pairs"
WEIGHTS_LOCATIONS = ("mram", "psram", "tcm")


def _pairs(slow: dict[str, Any], fast: dict[str, Any]) -> bool:
    """heliaOptimus's pairing check (``pricing._check_pair``), on a slow and a fast ``dtcm`` file.

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


def test_retained_real_captures_keep_their_producing_contract(tmp_path: Path) -> None:
    from helia_profiler.errors import ReportError
    from helia_profiler.results import load_result_manifest

    for name in WEIGHTS_LOCATIONS:
        path = PAIRS / name
        load_result_manifest(path / "result_manifest.json", verify=True)
        summary = json.loads((path / "summary.json").read_text())
        assert summary["schema_version"] == 20
        with pytest.raises(ReportError, match="read the result with the HPX release that wrote it"):
            calibrate([path], output=tmp_path / f"{name}.json")
        assert not (tmp_path / f"{name}.json").exists()


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
