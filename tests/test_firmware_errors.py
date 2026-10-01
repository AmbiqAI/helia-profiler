from __future__ import annotations

import pytest

from helia_profiler.errors import CaptureError
from helia_profiler.transport.firmware_errors import firmware_error_kind, raise_on_firmware_error


def test_no_error_returns_none():
    lines = [
        "--- HPX_START ---",
        "HPX_VERSION=1",
        "--- HPX_END ---",
    ]
    raise_on_firmware_error(lines)


def test_unsupported_op_is_classified():
    lines = [
        "--- HPX_START ---",
        "HPX_ERROR=unsupported_op kind=builtin builtin=42 name=FOO index=3",
        "HPX_ERROR=missing_ops count=1 hint=rebuild_with_op_registration",
    ]
    with pytest.raises(CaptureError) as exc_info:
        raise_on_firmware_error(lines)
    msg = str(exc_info.value)
    assert "unsupported_op" in msg
    # Hint mentions the resolver fix, not the arena.
    assert "resolver" in msg.lower()
    assert "arena" not in msg.lower()


def test_alloc_tensors_failed_mentions_both_possibilities():
    """Must not tell the user it is definitely arena size."""
    lines = [
        "--- HPX_START ---",
        "HPX_ERROR=alloc_tensors_failed arena=65536 status=2 "
        "hint=arena_too_small_or_kernel_prepare_failed",
    ]
    with pytest.raises(CaptureError) as exc_info:
        raise_on_firmware_error(lines)
    hint = exc_info.value.hint or ""
    assert "arena" in hint.lower()
    assert "kernel" in hint.lower() or "prepare" in hint.lower()


def test_schema_mismatch_payload_with_colon():
    lines = [
        "--- HPX_START ---",
        "HPX_ERROR=schema_mismatch:5_vs_3",
    ]
    with pytest.raises(CaptureError) as exc_info:
        raise_on_firmware_error(lines)
    assert "schema" in str(exc_info.value).lower()


def test_unknown_kind_still_raises():
    lines = [
        "HPX_ERROR=brand_new_error_kind detail=foo",
    ]
    with pytest.raises(CaptureError):
        raise_on_firmware_error(lines)


def test_only_first_error_is_raised():
    lines = [
        "HPX_ERROR=unsupported_op kind=builtin builtin=99 name=FOO index=0",
        "HPX_ERROR=alloc_tensors_failed arena=1024",
    ]
    with pytest.raises(CaptureError) as exc_info:
        raise_on_firmware_error(lines)
    assert "unsupported_op" in str(exc_info.value)


class TestStimerDeadSeverity:
    def test_fatal_when_power_is_enabled(self):
        with pytest.raises(CaptureError, match="stimer_dead"):
            raise_on_firmware_error(
                ["HPX_ERROR=stimer_dead settle_us=1000000 last_ticks=0"],
                power_enabled=True,
            )

    def test_warns_and_continues_without_power(self, caplog):
        with caplog.at_level("WARNING", logger="hpx"):
            raise_on_firmware_error(
                ["HPX_ERROR=stimer_dead settle_us=1000000 last_ticks=0"],
                power_enabled=False,
            )
        assert any("stimer_dead" in r.message for r in caplog.records)

    def test_downgrade_does_not_swallow_later_errors(self):
        with pytest.raises(CaptureError, match="schema_mismatch"):
            raise_on_firmware_error(
                [
                    "HPX_ERROR=stimer_dead settle_us=1000000 last_ticks=0",
                    "HPX_ERROR=schema_mismatch:1_vs_2",
                ],
                power_enabled=False,
            )


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("HPX_ERROR=unsupported_op kind=builtin builtin=42", "unsupported_op"),
        ("HPX_ERROR=schema_mismatch:5_vs_3", "schema_mismatch"),
        ("HPX_ERROR=bind_arena_failed:-2:region=1 extra", "bind_arena_failed"),
        ("  HPX_ERROR=psram_init_failed\r\n", "psram_init_failed"),
    ],
)
def test_firmware_error_kind_is_the_first_space_or_colon_token(line, kind):
    assert firmware_error_kind(line) == kind
