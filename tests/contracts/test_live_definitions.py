"""Keep optional firmware helpers paired with their rendered callers."""

import re

import pytest

from .fixture_compile_cases import FIXTURE_ENGINES, render_fixture
from .test_firmware_render_snapshots import _all_combos, _render

_SYMBOLS = (
    "kSyncLockstep",
    "kStateGpioPin",
    "kGoGpioPin",
    "hpx_sync_not_ready",
    "hpx_sync_fault",
    "hpx_rtt_shorten_lossless_timeout",
)


def _assert_referenced_definitions(text):
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.DOTALL)
    for symbol in _SYMBOLS:
        occurrences = len(re.findall(rf"\b{symbol}\b", code))
        assert occurrences != 1, f"{symbol} has a definition without a reference"


@pytest.mark.parametrize("soc,transport,engine", _all_combos())
@pytest.mark.parametrize(
    "sync,lockstep,power_only",
    [
        (False, False, False),
        (True, False, False),
        (True, True, False),
        (False, False, True),
        (True, False, True),
        (True, True, True),
    ],
)
def test_profile_helpers_have_callers(soc, transport, engine, sync, lockstep, power_only):
    if engine == "executorch" and (sync or power_only):
        pytest.skip("ExecuTorch power is not a supported configuration")
    text = _render(
        soc,
        transport,
        engine,
        power_only=power_only,
        overrides={"power_sync_enabled": sync, "lockstep": lockstep},
    )
    _assert_referenced_definitions(text)
    assert ("static inline void hpx_sync_not_ready" in text) == (sync or power_only)
    assert ("static void hpx_rtt_shorten_lossless_timeout" in text) == (
        transport == "rtt" and sync and not power_only
    )
    assert "hpx_sync_fault" not in text


@pytest.mark.parametrize("engine", FIXTURE_ENGINES)
@pytest.mark.parametrize("kind", ["resource", "power-resource"])
def test_fixture_helpers_have_callers(engine, kind):
    text, _ = render_fixture(kind, engine)
    _assert_referenced_definitions(text)
    assert ("static inline void hpx_sync_not_ready" in text) == (kind == "power-resource")
    assert "hpx_sync_fault" not in text


@pytest.mark.parametrize("symbol", _SYMBOLS)
def test_unused_definition_check_rejects_reintroduced_helper(symbol):
    text = _render("apollo510", "rtt", "litert")
    with pytest.raises(AssertionError, match=symbol):
        _assert_referenced_definitions(text + f"\nstatic void {symbol}(void) {{}}\n")


@pytest.mark.host_integration
def test_compile_rejects_removed_live_sync_definition(tmp_path):
    from .test_render_compile import _compile_doctored

    result = _compile_doctored(
        tmp_path, lambda text: text.replace("static inline void hpx_sync_ready(void) {}", "")
    )
    assert result.returncode != 0
    assert "hpx_sync_ready" in result.stderr
