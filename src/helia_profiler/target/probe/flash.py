"""Flash a secondary NSX target image via ``JLinkExe``.

Split out of :mod:`.jlink` so that module stays under the package size
ceiling; this is the flashing responsibility, which has exactly one caller
(``stages.flash_power``) and its own NSX-recipe-versus-fallback policy.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ...errors import CaptureError
from .jlink import _DEFAULT_TIMEOUT_S, run_jlink_script

log = logging.getLogger("hpx")


def flash_binary(
    binary_path: Path,
    *,
    device: str,
    load_addr: int | None,
    jlink_serial: str | None = None,
    speed_khz: int = 4000,
    interface: str = "SWD",
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> None:
    """Flash a second NSX target's image via its NSX-generated JLink script.

    This exists for the dedicated power binary (``hpx_profiler_power``): a
    *second* executable in the same NSX/CMake project as the transport binary,
    with no ``nsx flash`` entry point of its own (``nsx flash`` /
    :class:`JLinkFlashBackend` always target the project's primary executable).

    The NSX build generates a ready-made commander script per target at
    ``<build_dir>/jlink/<target>/flash_cmds.jlink`` containing the exact proven
    recipe (``LoadFile <target>.bin, <load_addr>`` -- the address-explicit
    ``.bin`` form the primary flash path uses); prefer running it verbatim.
    Hand-rolling ``loadfile`` on the extension-less ELF was tried first and
    SILENTLY programmed nothing on Apollo510 (measured window current stayed
    byte-identical to the previous firmware), so this must stay on that recipe.

    Falls back to an explicit ``.bin``-sibling load only if the script is
    missing, and raises if neither is available -- a silent no-op flash is
    the worst possible failure mode for a power measurement (the wrong,
    transport-enabled firmware gets measured while metadata claims
    "dedicated").  That fallback's *load_addr* (the SoC's app-image flash
    address, ``MemoryCapabilities.app_flash_load_addr``) differs per family, so
    it is the caller's resolved value; ``None`` raises rather than guesses.

    The target free-runs immediately after this returns, which is fine: a
    race-free reset happens again later, right before the gated capture window
    is armed (``capture.capture_power``'s ``_prepare_target_once``).
    """
    target_name = binary_path.stem if binary_path.suffix else binary_path.name
    script_path = binary_path.parent / "jlink" / target_name / "flash_cmds.jlink"
    if script_path.is_file():
        script = script_path.read_text()
        # The generated script ends with Exit; run it verbatim.
        log.info(
            "Flashing %s via NSX-generated JLink script %s (serial=%s)",
            target_name,
            script_path,
            jlink_serial or "auto",
        )
    else:
        # Fallback mirrors the generated script: explicit .bin, quoted path
        # (spaces), load address.  A raw ELF loadfile is NOT safe (docstring).
        bin_path = binary_path if binary_path.suffix == ".bin" else binary_path.with_suffix(".bin")
        if not bin_path.is_file():
            raise CaptureError(
                f"No flashable image for {target_name}: neither the NSX flash "
                f"script ({script_path}) nor a .bin sibling ({bin_path}) exists.",
                hint="Re-run the build; the NSX build emits both per target.",
            )
        if load_addr is None:
            raise CaptureError(
                f"Cannot flash {target_name}: the NSX flash script ({script_path}) is "
                f"missing and no app flash load address is known for device {device} "
                "to fall back to.",
                hint="Re-run the build to regenerate the flash script; flashing this "
                "SoC without it needs MemoryCapabilities.app_flash_load_addr set.",
            )
        log.warning(
            "NSX flash script missing for %s; falling back to .bin load of %s at 0x%08X",
            target_name,
            bin_path,
            load_addr,
        )
        script = f'ExitOnError 1\nReset\nLoadFile "{bin_path}", 0x{load_addr:08X}\nReset\nGo\nExit\n'

    proc = run_jlink_script(
        script,
        device=device,
        jlink_serial=jlink_serial,
        speed_khz=speed_khz,
        interface=interface,
        timeout_s=timeout_s,
        op_label="JLinkExe flash",
    )
    # Exit status is the primary gate: every recipe (NSX-generated and the
    # fallback above) starts with ``ExitOnError 1``, and run_jlink_script
    # raises CaptureError on nonzero rc — so the marker check below is a
    # tripwire for J-Link wording drift, not the gate.  Exactly two markers
    # confirm a flash: the "Flash download: Total" summary, or "Skipped.
    # Contents already match" when re-flashing a byte-identical image
    # (AP4-class parts skip; secure Apollo5 parts always erase+reprogram).
    # A bare connection "O.K." is printed before flashing and must NOT count.
    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).lower()
    if not any(m in output for m in ("flash download: total", "skipped. contents already match")):
        raise CaptureError(
            f"JLinkExe flash of {target_name} printed no recognized flash "
            "confirmation — either J-Link reworded its summary, or nothing was "
            "programmed and a power capture would measure stale firmware.",
            hint="Inspect the JLinkExe output; check the probe connection "
            "and that the .bin/base-address recipe matches the board.",
        )
    log.info("Flash complete: %s", target_name)
