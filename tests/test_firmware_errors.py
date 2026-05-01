"""Tests for the firmware error classifier in capture.__init__."""

from __future__ import annotations

import pytest

from helia_profiler.capture import _raise_on_firmware_error
from helia_profiler.errors import CaptureError


def test_no_error_returns_none():
    lines = [
        "--- HPX_START ---",
        "HPX_VERSION=1",
        "--- HPX_END ---",
    ]
    # Should simply return without raising.
    _raise_on_firmware_error(lines)


def test_unsupported_op_is_classified():
    lines = [
        "--- HPX_START ---",
        "HPX_ERROR=unsupported_op kind=builtin builtin=42 name=FOO index=3",
        "HPX_ERROR=missing_ops count=1 hint=rebuild_with_op_registration",
    ]
    with pytest.raises(CaptureError) as exc_info:
        _raise_on_firmware_error(lines)
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
        _raise_on_firmware_error(lines)
    hint = exc_info.value.hint or ""
    assert "arena" in hint.lower()
    assert "kernel" in hint.lower() or "prepare" in hint.lower()


def test_schema_mismatch_payload_with_colon():
    lines = [
        "--- HPX_START ---",
        "HPX_ERROR=schema_mismatch:5_vs_3",
    ]
    with pytest.raises(CaptureError) as exc_info:
        _raise_on_firmware_error(lines)
    assert "schema" in str(exc_info.value).lower()


def test_unknown_kind_still_raises():
    lines = [
        "HPX_ERROR=brand_new_error_kind detail=foo",
    ]
    with pytest.raises(CaptureError):
        _raise_on_firmware_error(lines)


def test_only_first_error_is_raised():
    lines = [
        "HPX_ERROR=unsupported_op kind=builtin builtin=99 name=FOO index=0",
        "HPX_ERROR=alloc_tensors_failed arena=1024",
    ]
    with pytest.raises(CaptureError) as exc_info:
        _raise_on_firmware_error(lines)
    assert "unsupported_op" in str(exc_info.value)
