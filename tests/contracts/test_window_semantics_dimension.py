"""Comparability must see what the measured window actually does.

`hpx compare` treats two runs as power-comparable when a fixed list of
dimensions matches. That list was hand-maintained, and it did not include the
clean-window probe -- so an `infer` baseline and a `busy_loop` candidate
compared as fully comparable, and the delta between a model inference and a
calibrated CPU spin was reported as a regression (#125 item 4).

Adding `clean_window_probe` to the list would have fixed that one cell and
left `window_mode`, both window timers, and the peripheral-shutdown flags
equally invisible -- the cell-by-cell pattern that cost three review rounds on
#136. So the dimension is a digest of `PowerWindowContext`'s WHOLE field set,
and the test that matters here is the last one: every field participates, so a
field added to that dataclass extends the dimension without anyone
remembering to.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from helia_profiler.engines.base import EngineArtifacts
from helia_profiler.firmware.context import FirmwareRenderContext, PowerWindowContext

from .conftest import make_pmu_ctx


def _window(tmp_path: Path, **profiling) -> PowerWindowContext:
    tmp_path.mkdir(parents=True, exist_ok=True)
    ctx = make_pmu_ctx(
        tmp_path,
        board="apollo4p_blue_kbr_evb",
        power_enabled=True,
        extra={"profiling": profiling} if profiling else None,
    )
    ctx.engine_artifacts = EngineArtifacts()
    return FirmwareRenderContext.from_pipeline_context(ctx).power_window


def test_the_same_configuration_fingerprints_the_same(tmp_path: Path):
    """Two runs of one setup must stay comparable.

    A fingerprint that moved between runs would block every comparison, which
    is worse than the blindness it replaces -- so this pins that nothing
    run-derived leaks in. `clean_iters` here is `profiling.iterations`, NOT
    the power plan's N: the plan's N is injected by `render_power_source()`
    after `to_template_vars()`, and it varies with live timing.
    """
    first = _window(tmp_path / "a")
    second = _window(tmp_path / "b")

    assert first.fingerprint == second.fingerprint


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("clean_window_probe", "busy_loop"),
        ("window_mode", "fixed"),
        ("window_target_ms", 9000),
        ("window_min", 25),
        ("iterations", 250),
    ],
)
def test_a_change_to_what_the_window_does_changes_the_fingerprint(
    tmp_path: Path, field: str, value
):
    """The probe is the case #125 names; the rest come free from the design."""
    baseline = _window(tmp_path / "base")
    changed = _window(tmp_path / "changed", **{field: value})

    assert baseline.fingerprint != changed.fingerprint, (
        f"changing {field} left the window fingerprint unchanged"
    )


def test_every_window_field_participates_in_the_fingerprint(tmp_path: Path):
    """The property that stops this becoming a hand-maintained list again.

    Mutating ANY field of PowerWindowContext must move the digest. If a future
    field does not, it is invisible to comparability exactly the way
    `clean_window_probe` was -- so this fails rather than letting that ship.
    """
    window = _window(tmp_path)
    unchanged = []

    for f in dataclasses.fields(window):
        current = getattr(window, f.name)
        if isinstance(current, bool):
            other = not current
        elif isinstance(current, int):
            other = current + 1
        elif isinstance(current, str):
            other = current + "-x"
        else:
            other = 7  # None-valued fields (ble_reset_gpio_pin)
        if dataclasses.replace(window, **{f.name: other}).fingerprint == window.fingerprint:
            unchanged.append(f.name)

    assert not unchanged, f"fields invisible to the fingerprint: {unchanged}"


def test_the_fields_are_carried_so_a_mismatch_can_name_what_differed(tmp_path: Path):
    """A digest pair tells a user nothing; the field set is what explains it."""
    window = _window(tmp_path)

    assert window.semantics["clean_window_probe"] == "infer"
    assert set(window.semantics) == {
        f.name for f in dataclasses.fields(PowerWindowContext)
    }


def test_the_manifest_actually_carries_the_dimension_and_the_fields(tmp_path: Path):
    """The dimension is worthless if it never reaches the stored artifact.

    The report golden cannot see this: its hand-built context never runs the
    generate-firmware stage, so `window_semantics` is None and the recorded
    value is null whether or not the manifest records it at all. Found by
    mutation -- deleting the manifest line left the golden green.
    """
    from helia_profiler.report.manifest import _comparability, _provenance
    from helia_profiler.results.models import WindowSemantics

    ctx = make_pmu_ctx(
        tmp_path, board="apollo4p_blue_kbr_evb", power_enabled=True
    )
    ctx.engine_artifacts = EngineArtifacts()
    window = FirmwareRenderContext.from_pipeline_context(ctx).power_window
    ctx.run_metadata.window_semantics = WindowSemantics(
        fingerprint=window.fingerprint, fields=window.semantics
    )

    dimensions = _comparability(ctx)
    provenance = _provenance(ctx)

    assert dimensions["power_window_semantics"] == window.fingerprint
    assert provenance["power_window"]["clean_window_probe"] == "infer"


def test_a_run_without_a_generated_firmware_records_no_window_dimension(
    tmp_path: Path,
):
    """And a context that never rendered firmware must not invent one.

    A fabricated fingerprint would be worse than none: it would compare as
    equal to another fabricated one and silently restore the blindness.
    """
    from helia_profiler.report.manifest import _comparability, _provenance

    ctx = make_pmu_ctx(
        tmp_path, board="apollo4p_blue_kbr_evb", power_enabled=True
    )

    assert _comparability(ctx)["power_window_semantics"] is None
    assert "power_window" not in _provenance(ctx)
