"""Flash a secondary NSX target image via ``JLinkExe``.

Split out of :mod:`.jlink` so that module stays under the package size
ceiling; this is the flashing responsibility, which has exactly one caller
(``stages.flash_power``) and its own NSX-recipe-versus-fallback policy.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ...errors import CaptureError
from .jlink import _DEFAULT_TIMEOUT_S, run_jlink_script

log = logging.getLogger("hpx")

# The recipe grammar below is NSX's, ported from ``validate_flash_recipe`` /
# ``_LOAD_FILE_RE`` in ``neuralspotx.operations._hardware`` rather than
# imported: that module is private and NSX is only optionally importable here
# (AGENTS.md "NSX as Build Backend"), but the recipe hpx runs verbatim is
# emitted by NSX's own ``flash_cmds.jlink.in`` template, so the two must agree.
# Handles the quoted form NSX generates and the unquoted form a hand-rolled or
# hand-edited recipe may use, plus hex or decimal addresses.
_LOAD_FILE_RE = re.compile(
    r'^\s*LoadFile\s+(?:"(?P<quoted>[^"]+)"|(?P<plain>.+?))\s*,\s*'
    r"(?P<address>0x[0-9a-fA-F]+|[0-9]+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# Tolerates J-Link Commander's ``//`` line comments after the directive.
_FAIL_FAST_RE = re.compile(r"^\s*ExitOnError\s+1\s*(?://.*)?$", re.IGNORECASE | re.MULTILINE)
# J-Link names the destination on every flash, in both confirmation shapes:
#   "J-Link: Flash download: Bank 0 @ 0x00410000: 1 range affected (761856 bytes)"
#   "J-Link: Flash download: Bank 0 @ 0x00018000: Skipped. Contents already match"
# A multi-range image prints one such line per bank, so collect them all.
_BANK_ADDR_RE = re.compile(r"bank\s+\d+\s*@\s*(0x[0-9a-fA-F]+)", re.IGNORECASE)


def _parse_addr(text: str) -> int:
    """Parse a commander-script address, which may be hex or bare decimal."""
    return int(text, 16) if text.lower().startswith("0x") else int(text, 10)


def _recipe_load_address(
    script: str,
    *,
    script_path: Path,
    bin_path: Path,
    target_name: str,
) -> int:
    """Validate an NSX-generated recipe and return the address it loads at.

    On the recipe path the *recipe* — not the caller's ``load_addr`` — is the
    authority on where the image lands, because hpx runs it verbatim and NSX
    baked the address from the target's own linker configuration.  So the
    expected address is read out of the recipe's ``LoadFile`` line, mirroring
    NSX's ``validate_flash_recipe``, and the two further checks NSX makes are
    adopted with it:

    * The ``LoadFile`` path must resolve to *this build's* ``.bin``.  Recipes
      bake ABSOLUTE paths, so a recipe left behind by an earlier build can
      still resolve happily and flash a stale image while hpx reports the new
      build's identity — the same silent mis-measurement a wrong address
      causes, by a different route.
    * ``ExitOnError 1`` must be present.  It is what makes JLinkExe's exit
      status trustworthy: without it J-Link can fail a command and still exit
      zero, demoting the output-text checks from corroboration to sole gate.
    """
    if _FAIL_FAST_RE.search(script) is None:
        raise CaptureError(
            f"NSX flash recipe for {target_name} ({script_path}) is missing "
            "`ExitOnError 1`; without it JLinkExe can fail a command and still "
            "exit successfully, so a failed flash would look like a success.",
            hint="Re-run the build so NSX regenerates the flash recipe.",
        )

    loaded: list[Path] = []
    for match in _LOAD_FILE_RE.finditer(script):
        quoted = match.group("quoted")
        candidate = Path(quoted if quoted is not None else match.group("plain").strip())
        if not candidate.is_absolute():
            candidate = script_path.parent / candidate
        if candidate.resolve() == bin_path.resolve():
            return _parse_addr(match.group("address"))
        loaded.append(candidate)

    if not loaded:
        raise CaptureError(
            f"NSX flash recipe for {target_name} ({script_path}) has no usable "
            "`LoadFile <image>, <address>` command, so there is nothing to "
            "verify the flash against and it may program nothing at all.",
            hint="Re-run the build so NSX regenerates the flash recipe.",
        )
    listed = ", ".join(str(path) for path in loaded)
    raise CaptureError(
        f"NSX flash recipe for {target_name} ({script_path}) loads {listed}, "
        f"not this build's image {bin_path}. Recipes bake absolute paths, so a "
        "stale recipe flashes an older image while hpx attributes the results "
        "to the current build.",
        hint="Delete the build directory and re-run the build so NSX "
        "regenerates the flash recipe against this build's artifacts.",
    )


def _verify_flash_address(
    output: str,
    *,
    expected_addr: int,
    target_name: str,
    source: str,
) -> None:
    """Require J-Link to report programming at the address we asked for.

    A flash to a wrong-but-writable address prints the same ``Total:`` summary
    as a correct one, so the summary alone proves only that *something* was
    programmed, never *where*.  The device then boots stale firmware from its
    real entry point while hpx publishes power numbers attributed to the new
    build — silent mis-measurement, the worst failure mode this tool has.

    When J-Link names no address at all this warns instead of raising: the
    address line is corroboration layered on top of the exit-status gate and
    the summary-marker tripwire, and turning a cosmetic J-Link rewording into a
    hard stop would block correct flashes without any evidence of a wrong one.
    """
    observed = [_parse_addr(addr) for addr in _BANK_ADDR_RE.findall(output)]
    if not observed:
        log.warning(
            "JLinkExe confirmed a flash of %s but named no bank address, so the "
            "destination could not be checked against the requested 0x%08X (%s).",
            target_name,
            expected_addr,
            source,
        )
        return
    if expected_addr in observed:
        return
    seen = ", ".join(f"0x{addr:08X}" for addr in observed)
    raise CaptureError(
        f"JLinkExe programmed {target_name} at {seen}, but {source} requested "
        f"0x{expected_addr:08X}. The flash succeeded at the wrong address: the "
        "device boots stale firmware from its real entry point while hpx would "
        "attribute the capture to this build.",
        hint="Re-run the build to regenerate the NSX flash recipe, and confirm "
        "the board's app flash load address matches the linker script; a build "
        "directory carried over from another board is the usual cause.",
    )


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

    Both paths verify afterwards that J-Link programmed the address that was
    asked for, but they learn that address differently.  The fallback builds
    the script itself, so *load_addr* is authoritative.  The recipe path runs
    NSX's script verbatim and *load_addr* is frequently ``None`` there, so the
    expected address is parsed out of the recipe itself; *load_addr* is neither
    consulted nor required on that path.  Running a recipe verbatim also means
    vetting it first the way NSX's own ``validate_flash_recipe`` does — see
    :func:`_recipe_load_address`.

    The target free-runs immediately after this returns, which is fine: a
    race-free reset happens again later, right before the gated capture window
    is armed (``capture.capture_power``'s ``_prepare_target_once``).
    """
    target_name = binary_path.stem if binary_path.suffix else binary_path.name
    script_path = binary_path.parent / "jlink" / target_name / "flash_cmds.jlink"
    # NSX writes both the recipe and the image next to the ELF in the build
    # dir, so both branches derive the same expected artifact the same way.
    bin_path = binary_path if binary_path.suffix == ".bin" else binary_path.with_suffix(".bin")
    if script_path.is_file():
        script = script_path.read_text()
        # Validate before programming: everything below is cheap and static,
        # and a bad recipe should be refused rather than run and then blamed.
        if not bin_path.is_file():
            raise CaptureError(
                f"NSX flash recipe for {target_name} ({script_path}) exists but "
                f"this build's image ({bin_path}) does not, so the recipe can "
                "only flash something other than what hpx just built.",
                hint="Re-run the build; the NSX build emits both per target.",
            )
        expected_addr = _recipe_load_address(
            script, script_path=script_path, bin_path=bin_path, target_name=target_name
        )
        expected_source = f"its NSX flash recipe ({script_path})"
        # The generated script ends with Exit; run it verbatim.
        log.info(
            "Flashing %s via NSX-generated JLink script %s at 0x%08X (serial=%s)",
            target_name,
            script_path,
            expected_addr,
            jlink_serial or "auto",
        )
    else:
        # Fallback mirrors the generated script: explicit .bin, quoted path
        # (spaces), load address.  A raw ELF loadfile is NOT safe (docstring).
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
        expected_addr = load_addr
        expected_source = "the resolved app flash load address"

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
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    output = combined.lower()
    if not any(m in output for m in ("flash download: total", "skipped. contents already match")):
        raise CaptureError(
            f"JLinkExe flash of {target_name} printed no recognized flash "
            "confirmation — either J-Link reworded its summary, or nothing was "
            "programmed and a power capture would measure stale firmware.",
            hint="Inspect the JLinkExe output; check the probe connection "
            "and that the .bin/base-address recipe matches the board.",
        )
    # ...and that the bytes landed where they were asked to land.
    _verify_flash_address(
        combined,
        expected_addr=expected_addr,
        target_name=target_name,
        source=expected_source,
    )
    log.info("Flash complete: %s at 0x%08X", target_name, expected_addr)
