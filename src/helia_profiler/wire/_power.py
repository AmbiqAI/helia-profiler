"""Specs for the dedicated power binary's terminal record."""

from __future__ import annotations

from ._model import (
    GATE_POWER_INA228,
    GATE_POWER_ONLY,
    POWER_BINARY_ENGINES,
    PowerTerminalKey,
    WireBinary,
    WireConsumer,
    WireCriticality,
    WireKind,
    WireSpec,
    _spec,
)

_TERMINAL_FIELDS: tuple[tuple[PowerTerminalKey, bool | None, str, str], ...] = (
    (
        PowerTerminalKey.TERMINAL_VERSION,
        True,
        "Envelope version; anything but 1 is refused.",
        "int",
    ),
    (PowerTerminalKey.STATUS, True, "Whether the power run completed.", "ok | error"),
    (PowerTerminalKey.REQUESTED_COUNT, True, "Inferences the host asked for.", "int"),
    (
        PowerTerminalKey.COMPLETED_COUNT,
        True,
        "Inferences actually completed inside the gate.",
        "int",
    ),
    (
        PowerTerminalKey.ELAPSED_US,
        True,
        "Device-measured duration of the gated window.",
        "microseconds",
    ),
    (
        PowerTerminalKey.FINAL_PHASE,
        True,
        "Last phase the firmware reached (names the failure on error).",
        "string",
    ),
    (PowerTerminalKey.ERROR_CODE, True, "Non-zero iff status is error.", "int"),
    (PowerTerminalKey.GATE_ASSERTED, True, "The firmware raised the GPIO gate.", "0 | 1"),
    (
        PowerTerminalKey.GATE_LOWERED,
        True,
        "The firmware lowered the gate again — the capture is bounded.",
        "0 | 1",
    ),
    (
        PowerTerminalKey.MEASUREMENT_SOURCE,
        False,
        "Which on-device monitor produced the measurement.",
        "ina228",
    ),
    (
        PowerTerminalKey.MEASUREMENT_SCOPE,
        False,
        "What the measurement covers.",
        "fixed_n_inference",
    ),
    (
        PowerTerminalKey.ENERGY_NJ,
        False,
        "Energy accumulated across the gated window.",
        "nanojoules",
    ),
    (
        PowerTerminalKey.MEASUREMENT_DURATION_US,
        False,
        "Measurement duration; must equal ELAPSED_US.",
        "microseconds",
    ),
    (
        PowerTerminalKey.MEASUREMENT_COUNT,
        False,
        "Inferences covered; must equal COMPLETED_COUNT.",
        "int",
    ),
    (
        PowerTerminalKey.MEASUREMENT_OVERFLOW,
        False,
        "The accumulator overflowed and the energy is not trustworthy.",
        "0 | 1",
    ),
    (PowerTerminalKey.CHARGE_NC, False, "Charge accumulated across the window.", "nanocoulombs"),
    (
        PowerTerminalKey.BUS_VOLTAGE_UV,
        False,
        "Bus voltage sampled during the window.",
        "microvolts",
    ),
    (
        PowerTerminalKey.CALIBRATION_ID,
        False,
        "Identity of the shunt/current calibration used.",
        "string",
    ),
)


TERMINAL_SPECS: tuple[WireSpec, ...] = tuple(
    _spec(
        key.value,
        WireKind.TERMINAL,
        description,
        WireConsumer.POWER_TERMINAL,
        WireCriticality.PROTOCOL,
        engines=POWER_BINARY_ENGINES,
        binary=WireBinary.POWER,
        condition=GATE_POWER_ONLY if required else GATE_POWER_INA228,
        runtime_gate=(
            None
            if required
            # One `if` guards the whole optional block, which is why the group
            # is all-or-none on the wire rather than by convention.
            else "success && g_hpx_ina228_ok && the envelope written so far "
            "still fits the record buffer"
        ),
        value_shape=value_shape,
        required=required,
        note=(
            "Optional measurement payload: all of these appear together "
            "or not at all, and only for a successful window with valid "
            "accumulator reads."
            if required is False
            else "Missing or malformed required fields raise PowerError."
        ),
    )
    for key, required, description, value_shape in _TERMINAL_FIELDS
) + (
    _spec(
        PowerTerminalKey.INA228_DIAG.value,
        WireKind.TERMINAL,
        "INA228 register dump printed ahead of the envelope.",
        WireConsumer.DIAGNOSTIC,
        WireCriticality.DIAGNOSTIC,
        engines=POWER_BINARY_ENGINES,
        binary=WireBinary.POWER,
        condition=GATE_POWER_INA228,
        value_shape="0x<diag> CFG=0x<n> ADCCFG=0x<n> SHUNTCAL=<n>",
        required=None,
        note="Deliberately outside the start marker: the envelope parser "
        "ignores pre-record lines, so monitor diagnostics can change without "
        "widening the wire contract. The host logs them at INFO.",
    ),
    _spec(
        PowerTerminalKey.INA228_BYSTANDER_FAILED.value,
        WireKind.TERMINAL,
        "A bystander INA228 failed and was dropped; the run continued.",
        WireConsumer.DIAGNOSTIC,
        WireCriticality.DIAGNOSTIC,
        engines=POWER_BINARY_ENGINES,
        binary=WireBinary.POWER,
        condition=GATE_POWER_INA228,
        runtime_gate="not ina228_required and the bystander monitor failed",
        value_shape="<phase>:<rc>",
        required=None,
        note="Logged as a warning: the external capture is unaffected. The "
        "fail-phase global it prints is assigned only in the "
        "`not ina228_required` branches of the setup/arm/read sites, so a "
        "build where the INA228 is itself the measurement calls "
        "hpx_power_terminal_fail() instead and never reaches this line.",
    ),
)
