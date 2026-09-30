"""Shared result-document coercion and ``None`` stripping."""

from __future__ import annotations

import math

import pytest

from helia_profiler.results.serde import strip_none, to_float, to_int


@pytest.mark.parametrize("value", [None, True, False, "x", [], {}, 10**400])
def test_to_float_rejects_unconvertible_values(value):
    assert to_float(value) is None


@pytest.mark.parametrize(("value", "expected"), [(3, 3.0), (2.5, 2.5), ("1.5", 1.5)])
def test_to_float_converts_numbers_and_numeric_strings(value, expected):
    assert to_float(value) == expected


def test_to_float_keeps_non_finite_values_unless_asked_not_to():
    assert to_float("inf") == math.inf
    nan = to_float(math.nan)
    assert nan is not None and math.isnan(nan)
    assert to_float("inf", finite=True) is None
    assert to_float(math.nan, finite=True) is None
    assert to_float(4, finite=True) == 4.0


@pytest.mark.parametrize("value", [None, True, "1.5", "x", math.inf, math.nan, []])
def test_to_int_rejects_unconvertible_values(value):
    assert to_int(value) is None


@pytest.mark.parametrize(("value", "expected"), [(7, 7), ("12", 12), (3.9, 3)])
def test_to_int_converts_counts(value, expected):
    assert to_int(value) == expected


def test_strip_none_recurses_through_dicts_and_lists_but_keeps_list_positions():
    value = {
        "a": None,
        "b": {"c": None, "d": 1},
        "e": [{"f": None, "g": 2}, None, [{"h": None}]],
        "t": (None,),
    }
    assert strip_none(value) == {"b": {"d": 1}, "e": [{"g": 2}, None, [{}]], "t": (None,)}
