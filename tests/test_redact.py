"""Exhaustive, table-driven tests for helia_profiler.redact.

Covers the exact categories the field-diagnostics support bundle must
scrub by default: home/workspace absolute paths (POSIX/Windows/UNC),
credentialed and token-bearing URLs, common credential/token shapes, secret
env-style assignments, and device serial numbers — plus the explicit
``--raw-probe-ids`` opt-in and "nothing to redact" no-op behavior.
"""

from __future__ import annotations

import json

import pytest

from helia_profiler.redact import (
    RedactionCounts,
    RedactionPolicy,
    redact_serial,
    redact_text,
    redact_value,
)

# ---------------------------------------------------------------------------
# Absolute filesystem paths — home dir, workspace dirs, POSIX, Windows, UNC.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/Users/adam.page/models/foo.tflite", "<redacted-path>/foo.tflite"),
        ("/home/ci-runner/work/hpx/build", "<redacted-path>/build"),
        ("/root/.cache/hpx/workspace", "<redacted-path>/workspace"),
        ("/dev/ttyACM0", "<redacted-path>/ttyACM0"),
        (r"C:\Users\Ada\module", "<redacted-path>/module"),
        (r"C:\Program Files\SEGGER\JLink_V960\JLink.exe", "<redacted-path>/JLink.exe"),
        (r"\\build-server\share\hpx-workspace", "<redacted-path>/hpx-workspace"),
        ("/opt/toolchains/gcc-arm/bin/arm-none-eabi-gcc", "<redacted-path>/arm-none-eabi-gcc"),
        ("/Users/adam.page/", "<redacted-path>/adam.page"),
    ],
)
def test_redact_text_scrubs_absolute_paths_keeping_basename(raw: str, expected: str) -> None:
    redacted, counts = redact_text(raw)

    assert redacted == expected
    assert counts.paths == 1
    assert counts.total == 1


@pytest.mark.parametrize(
    "raw",
    [
        "relative/module/path",
        "modules/demo",
        "nsx.lock",
        "just some prose with no path in it",
    ],
)
def test_redact_text_leaves_relative_paths_and_plain_text_alone(raw: str) -> None:
    redacted, counts = redact_text(raw)

    assert redacted == raw
    assert counts.total == 0


def test_redact_text_paths_disabled_by_policy() -> None:
    raw = "/Users/adam.page/models/foo.tflite"

    redacted, counts = redact_text(raw, RedactionPolicy(redact_paths=False))

    assert redacted == raw
    assert counts.paths == 0


# ---------------------------------------------------------------------------
# URL credentials and token-shaped query parameters.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://user:hunter2@github.com/org/repo.git",
            "https://<redacted>@github.com/org/repo.git",
        ),
        (
            "https://api.example.com/v1?token=abcdef123456&x=1",
            "https://api.example.com/v1?token=<redacted>&x=1",
        ),
        (
            "https://example.com/download?api_key=SECRETVALUE",
            "https://example.com/download?api_key=<redacted>",
        ),
        (
            "https://example.com/download?access_token=abc123&other=keep",
            "https://example.com/download?access_token=<redacted>&other=keep",
        ),
    ],
)
def test_redact_text_scrubs_url_credentials_and_tokens(raw: str, expected: str) -> None:
    redacted, counts = redact_text(raw)

    assert redacted == expected
    assert counts.urls == 1
    assert counts.total == 1


def test_redact_text_url_path_segments_survive_intact() -> None:
    raw = "https://github.com/AmbiqAI/neuralspotx/releases/download/v0.7.10/asset.tar.gz"

    redacted, counts = redact_text(raw)

    assert redacted == raw
    assert counts.total == 0


def test_redact_text_plain_url_without_credentials_is_untouched() -> None:
    raw = "https://github.com/AmbiqAI/nsx-ambiq-sdk.git"

    redacted, counts = redact_text(raw)

    assert redacted == raw
    assert counts.total == 0


# ---------------------------------------------------------------------------
# Common credential/token shapes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "ghp_" + "a" * 36,
        "github_pat_" + "b" * 22,
        "AKIA" + "A" * 16,
        "xoxb-" + "c" * 12,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQ_abcXYZ123",
    ],
)
def test_redact_text_scrubs_known_token_shapes(raw: str) -> None:
    redacted, counts = redact_text(raw)

    assert redacted == "<redacted-token>"
    assert counts.tokens == 1
    assert counts.total == 1


def test_redact_text_scrubs_bearer_token_keeping_prefix() -> None:
    raw = "Authorization: Bearer " + "x" * 40

    redacted, counts = redact_text(raw)

    assert redacted == "Authorization: Bearer <redacted-token>"
    assert counts.tokens == 1


# ---------------------------------------------------------------------------
# KEY=VALUE / KEY: VALUE secret-shaped assignments ("env values").
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("API_KEY=supersecretvalue123", "API_KEY=<redacted>"),
        ("API_KEY: supersecretvalue123", "API_KEY: <redacted>"),
        ("token=abcdefghij", "token=<redacted>"),
        ("PASSWORD='correct horse battery staple'", "PASSWORD='<redacted>'"),
        ('secret: "abcdefg1234"', 'secret: "<redacted>"'),
        ("ACCESS_KEY=" + "A" * 16, "ACCESS_KEY=<redacted>"),
    ],
)
def test_redact_text_scrubs_secret_assignments(raw: str, expected: str) -> None:
    redacted, counts = redact_text(raw)

    assert redacted == expected
    assert counts.total >= 1


@pytest.mark.parametrize(
    "raw",
    [
        "iterations=100",
        "board=apollo510_evb",
        "arena_size=65536",
        "channel: stable",
    ],
)
def test_redact_text_does_not_flag_ordinary_assignments(raw: str) -> None:
    redacted, counts = redact_text(raw)

    assert redacted == raw
    assert counts.total == 0


# ---------------------------------------------------------------------------
# Device serial numbers — structural (by field name), not digit-pattern.
# ---------------------------------------------------------------------------


def test_redact_serial_replaces_with_stable_hash_preview() -> None:
    redacted, counts = redact_serial("1160002204")

    assert redacted != "1160002204"
    assert redacted.startswith("<redacted-serial:")
    assert counts.serials == 1
    assert counts.total == 1


def test_redact_serial_is_deterministic_for_the_same_value() -> None:
    first, _ = redact_serial("1160002204")
    second, _ = redact_serial("1160002204")

    assert first == second


def test_redact_serial_differs_for_different_values() -> None:
    first, _ = redact_serial("1160002204")
    second, _ = redact_serial("1160002205")

    assert first != second


def test_redact_serial_empty_value_is_a_no_op() -> None:
    redacted, counts = redact_serial("")

    assert redacted == ""
    assert counts.total == 0


def test_redact_serial_raw_probe_ids_opt_out_leaves_value_untouched() -> None:
    redacted, counts = redact_serial("1160002204", RedactionPolicy(redact_probe_serials=False))

    assert redacted == "1160002204"
    assert counts.total == 0


def test_redact_value_routes_serial_shaped_keys_through_redact_serial() -> None:
    value = {
        "serial": "1160002204",
        "serial_number": "HPX-000123",
        "board_id": "1160002204",
    }

    redacted, counts = redact_value(value)

    assert redacted["serial"].startswith("<redacted-serial:")
    assert redacted["serial_number"].startswith("<redacted-serial:")
    # A key with no "serial" substring is treated as plain text: 10 raw
    # digits alone match no path/url/token/secret-assignment pattern, so
    # it is left untouched.
    assert redacted["board_id"] == "1160002204"
    assert counts.serials == 2


def test_redact_value_raw_probe_ids_opt_in_keeps_probe_serials_readable() -> None:
    value = {"serial": "1160002204"}

    redacted, counts = redact_value(value, RedactionPolicy(redact_probe_serials=False))

    assert redacted["serial"] == "1160002204"
    assert counts.total == 0


# ---------------------------------------------------------------------------
# Recursive structure handling and no-op / idempotence guarantees.
# ---------------------------------------------------------------------------


def test_redact_value_recurses_through_nested_dicts_and_lists() -> None:
    value = {
        "path": "/Users/adam.page/models/foo.tflite",
        "nested": {
            "urls": ["https://user:pass@example.com/repo.git", "https://example.com/plain"],
            "serial_number": "ABC123",
        },
        "list_of_dicts": [{"token": "ghp_" + "z" * 36}],
    }

    redacted, counts = redact_value(value)

    assert redacted["path"] == "<redacted-path>/foo.tflite"
    assert redacted["nested"]["urls"][0] == "https://<redacted>@example.com/repo.git"
    assert redacted["nested"]["urls"][1] == "https://example.com/plain"
    assert redacted["nested"]["serial_number"].startswith("<redacted-serial:")
    assert redacted["list_of_dicts"][0]["token"] == "<redacted-token>"
    assert counts.paths == 1
    assert counts.urls == 1
    assert counts.serials == 1
    assert counts.tokens == 1


def test_redact_value_preserves_tuple_type() -> None:
    value = ("relative/path", "plain text")

    redacted, _ = redact_value(value)

    assert isinstance(redacted, tuple)
    assert redacted == value


def test_redact_value_passes_through_non_string_scalars_untouched() -> None:
    value = {"count": 5, "enabled": True, "ratio": 1.5, "missing": None}

    redacted, counts = redact_value(value)

    assert redacted == value
    assert counts.total == 0


def test_redact_value_result_is_json_serializable() -> None:
    value = {
        "path": "/Users/adam.page/x.tflite",
        "url": "https://user:pass@example.com/repo.git",
        "count": 3,
        "nested": [1, 2, {"serial": "999"}],
    }

    redacted, _ = redact_value(value)

    # Must round-trip cleanly: the collector writes this straight to JSON.
    assert json.loads(json.dumps(redacted, sort_keys=True)) == redacted


def test_redact_text_empty_string_is_a_no_op() -> None:
    redacted, counts = redact_text("")

    assert redacted == ""
    assert counts == RedactionCounts()


def test_redact_text_never_raises_on_arbitrary_bytes_like_text() -> None:
    # Adversarial/garbage input must never raise — redaction always degrades
    # to "leave it alone", never to an exception.
    weird = "\x00\x01 not/a a:b://c \\ /// mixed \\\\ separators"

    redacted, counts = redact_text(weird)

    assert isinstance(redacted, str)
    assert isinstance(counts, RedactionCounts)


def test_redaction_counts_to_dict_includes_total() -> None:
    counts = RedactionCounts(paths=1, urls=2, tokens=3, serials=4, env_values=5)

    assert counts.to_dict() == {
        "paths": 1,
        "urls": 2,
        "tokens": 3,
        "serials": 4,
        "env_values": 5,
        "total": 15,
    }


def test_redaction_counts_combined_sums_componentwise() -> None:
    a = RedactionCounts(paths=1, urls=1)
    b = RedactionCounts(paths=2, tokens=3)

    combined = a.combined(b)

    assert combined == RedactionCounts(paths=3, urls=1, tokens=3)
