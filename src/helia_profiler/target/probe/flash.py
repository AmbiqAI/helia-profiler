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

# Every pre-flight refusal in this module has to tell the user the board was
# left alone: "refused" and "failed halfway through programming" call for
# opposite next steps.  ``TestFlashRecipeValidation._refuse`` asserts that
# invariant across all of them by substring, so the phrase is pinned here
# rather than respelled at each site -- eight sites had drifted into three
# spellings (em-dash, colon, "this" versus "the recipe"), any of which a future
# reword could push out from under the assertion without failing a test.
_NOTHING_PROGRAMMED_RECIPE = "Nothing was programmed — the recipe was refused before JLinkExe ran."
# The fallback branch has no recipe to name: it is reached precisely because
# the recipe is missing, so "the recipe was refused" would be a lie there.
_NOTHING_PROGRAMMED = "Nothing was programmed — this was refused before JLinkExe ran."

# The recipe grammar below is NSX's, ported from ``validate_flash_recipe`` /
# ``_LOAD_FILE_RE`` in ``neuralspotx.operations._hardware`` rather than
# imported: that module is private and NSX is only optionally importable here
# (AGENTS.md "NSX as Build Backend"), but the recipe hpx runs verbatim is
# emitted by NSX's own ``flash_cmds.jlink.in`` template, so the two must agree.
# Handles the quoted form NSX generates and the unquoted form a hand-rolled or
# hand-edited recipe may use.
#
# DELIBERATE DIVERGENCE FROM NSX: NSX's regex anchors the address at
# end-of-line, which refuses three forms JLinkExe itself accepts.  Its embedded
# help reads ``loadfile <filename> [, <Addr>] [, <noreset | reset>]``, and the
# commander strips trailing ``//`` comments from every command line, so
# ``LoadFile "x.bin", 0x410000, noreset`` and ``LoadFile "x.bin", 0x410000 //
# the app`` are both valid and both flashed fine before this check existed.
# NSX only ever reads its own generated recipes; hpx's docstring puts
# hand-edited recipes in scope, which is exactly where those forms appear, so a
# hard refusal here would be a regression NSX never risks.  The optional
# reset/comment tail below closes that gap; keep it when re-syncing with NSX.
#
# A bare decimal address is tolerated because hpx has no reason to refuse one,
# NOT because J-Link is known to read it as decimal: J-Link's numeric
# convention is per-command and unverified for ``LoadFile``.  Every real NSX
# recipe uses the ``0x`` form, so this branch is tolerance, not a contract.
#
# Named for the property that separates it from ``_ANY_LOAD_FILE_RE`` below:
# this one requires an ADDRESS, and is the only one whose matches hpx will
# verify a flash against.  (NSX's own regex, referenced above, is the bare
# ``_LOAD_FILE_RE``; the names diverge deliberately because the grammars do.)
_ADDRESSED_LOAD_FILE_RE = re.compile(
    r'^\s*LoadFile\s+(?:"(?P<quoted>[^"]+)"|(?P<plain>.+?))\s*,\s*'
    r"(?P<address>0x[0-9a-fA-F]+|[0-9]+)"
    r"(?:\s*,\s*(?:no)?reset)?\s*(?://.*)?$",
    re.IGNORECASE | re.MULTILINE,
)
# Tolerates J-Link Commander's ``//`` line comments after the directive.
_FAIL_FAST_RE = re.compile(r"^\s*ExitOnError\s+1\s*(?://.*)?$", re.IGNORECASE | re.MULTILINE)
# Deliberately looser than ``_ADDRESSED_LOAD_FILE_RE``: this one answers "has
# anything been programmed yet?" for the ordering check below, not "is this the
# line whose address hpx verifies?", so it must also see a ``LoadFile`` that
# ``_ADDRESSED_LOAD_FILE_RE`` rejects (no address, odd quoting) — such a line
# still programs flash, and fail-fast must already be on when it runs.  Keeping
# the two regexes separate also stops a future widening of the addressed one
# from quietly loosening the ordering guard along with it.
#
# The gap between the two is also what tells the two "no address to verify"
# refusals apart below: matched here but not there means a ``LoadFile`` that
# programs flash at a destination hpx cannot read.
_ANY_LOAD_FILE_RE = re.compile(r"^\s*LoadFile\b", re.IGNORECASE | re.MULTILINE)
# This is a flash-BANK IDENTITY check, NOT a destination check.  Read that
# before trusting it for anything.
#
# ``Bank N @ 0x…`` is the base of the flash bank J-Link programmed into, not
# the address the image landed at.  J-Link's own format string is
# ``Bank %d @ 0x%.8X: %d range%s affected`` — one address for N ranges — so it
# cannot be a per-range start, and J-Link reports no exact destination at all.
#
# What that DOES catch: any requested address that falls in a bank J-Link never
# touched.  That covers the wrong-board case #150 was filed for, because every
# registered part's ``app_flash_load_addr`` is exactly its bank base (verified
# against J-Link's device database: apollo3p 0xC000, apollo4p/apollo4l
# 0x18000, apollo510/apollo510b/apollo5b 0x410000, plus all 23 NSX recipes on
# the bench host), so requested-address equality and bank equality coincide
# there.  apollo330P uses a custom Ambiq device entry absent from the stock
# database and could not be verified this way.
#
# What it does NOT catch: a wrong address INSIDE a bank J-Link did program.
# apollo3p is a four-bank part (0xC000, 0x80000, 0x100000, 0x180000), so a
# recipe baking 0x00080000 passes this check while the device boots stale
# firmware from 0xC000.  Making the check address-exact is not possible from
# J-Link's output; do not "tighten" this regex into pretending otherwise.
#
# Anchored on ``Flash download:`` so only a programming confirmation counts.
# Three other J-Link strings carry the same ``Bank %d @ …`` shape and are not
# confirmations — ``Start of determining flash info (Bank %d @ …)``, ``Error
# while determining flash info (Bank %d @ …)``, and the ``Switched from sector
# erase to chip erase`` notice.  Unanchored, an *error* naming the right bank
# would satisfy the check while a different bank was actually programmed.  If
# J-Link ever drops the prefix from a real confirmation the cost is the
# fail-open warning in ``_verify_flash_address``, not a refused flash, so the
# anchor errs in the safe direction.
# Both confirmation shapes keep the prefix:
#   "J-Link: Flash download: Bank 0 @ 0x00410000: 1 range affected (761856 bytes)"
#   "J-Link: Flash download: Bank 0 @ 0x00018000: Skipped. Contents already match"
# A multi-bank image prints one such line per bank, so collect them all.
_BANK_ADDR_RE = re.compile(r"flash\s+download:\s*bank\s+\d+\s*@\s*(0x[0-9a-fA-F]+)", re.IGNORECASE)


def _parse_addr(text: str) -> int:
    """Parse a commander-script address, which may be hex or bare decimal."""
    return int(text, 16) if text.lower().startswith("0x") else int(text, 10)


def _names_this_builds_image(candidate: Path, expected: Path) -> bool:
    """Does *candidate* name the same file on disk as *expected*?

    Path equality is kept as the fast path — it needs no filesystem access and
    settles every recipe NSX generates — but on its own it compares path TEXT,
    and that answer is platform-dependent.  ``Path.resolve()`` does NOT
    case-fold on POSIX, while on Windows it canonicalises the case of a path
    that exists, so a recipe whose baked path differs from this build's
    ``.bin`` only in case is refused as stale on macOS/Linux and accepted on
    Windows: a spurious hard refusal of a correct flash on one platform, and a
    gate that disagrees with itself across the two.

    ``samefile`` settles it on stat identity instead — device plus inode, and
    the volume-serial plus file-index equivalent on Windows — so it is case-
    correct wherever the volume is, and equally correct where it is not: on a
    case-SENSITIVE volume the two names really are different files and it says
    so.  That is the invariant worth asserting here, and unlike ``resolve()``
    its semantics do not shift between platforms or Python versions.

    Widening only: every pair the text comparison already accepted is still
    accepted, and ``samefile`` is true solely on a same-file answer from the
    OS, so the stale-recipe refusal cannot be loosened by this.
    """
    if candidate == expected:
        return True
    try:
        return candidate.samefile(expected)
    except (OSError, ValueError):
        # ``samefile`` stats BOTH paths and raises if either is unreadable.
        # *expected* is this build's own image, which ``flash_binary`` has
        # already confirmed is a file, so in practice this is the recipe
        # naming something that is not there — a stale recipe, the case the
        # caller must keep refusing with its own message.  A path the OS will
        # not stat at all lands here too (an embedded NUL raises ValueError,
        # which ``ntpath.realpath`` waves through on Windows 3.13+).  None of
        # those is this build's image, so ``False`` is the answer for all of
        # them and the refusal below stays exactly as it was.
        return False


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

    * The ``LoadFile`` path must name *this build's* ``.bin`` (see
      :func:`_names_this_builds_image` for why that is a stat-identity
      question rather than a string one).  Recipes bake ABSOLUTE paths, so a
      recipe left behind by an earlier build can still resolve happily and
      flash a stale image while hpx reports the new build's identity — the
      same silent mis-measurement a wrong address causes, by a different
      route.
    * ``ExitOnError 1`` must be present *and armed before the first*
      ``LoadFile``.  It is what makes JLinkExe's exit status trustworthy:
      without it J-Link can fail a command and still exit zero, demoting the
      output-text checks from corroboration to sole gate.
    """
    fail_fast = _FAIL_FAST_RE.search(script)
    if fail_fast is None:
        raise CaptureError(
            f"NSX flash recipe for {target_name} ({script_path}) is missing "
            "`ExitOnError 1`; without it JLinkExe can fail a command and still "
            "exit successfully, so a failed flash would look like a success. "
            f"{_NOTHING_PROGRAMMED_RECIPE}",
            hint="Add `ExitOnError 1` as the recipe's first line if it was "
            "hand-edited deliberately; re-running the build regenerates the "
            "recipe from NSX but discards any such edits.",
        )
    # ORDER, not just presence.  ``search`` is position-independent and J-Link
    # runs a script strictly top to bottom, so ``LoadFile … / ExitOnError 1 /
    # Exit`` satisfies a presence-only check while giving the flash NO
    # fail-fast protection whatsoever — precisely the failure the directive is
    # demanded for, passing the check meant to demand it.
    #
    # The rule is "before the first LoadFile" rather than the stricter "the
    # recipe's first directive".  Both accept every recipe that exists in
    # practice — NSX's ``flash_cmds.jlink.in`` emits ``ExitOnError 1`` first,
    # and all 53 generated recipes on the bench host lead with it — so the two
    # differ only in what they refuse of a HAND-EDITED recipe, which is the
    # case this module widened ``_ADDRESSED_LOAD_FILE_RE`` for one round earlier.
    # "First directive" additionally refuses a ``Reset`` / ``Halt`` /
    # ``SelectInterface`` preamble that JLinkExe accepts and that leaves the
    # LoadFile fully protected: a hard refusal of a working recipe, the same
    # regression this module already declines to risk.  "Before the first
    # LoadFile" refuses exactly the recipes that program something with
    # fail-fast still off, and nothing else.
    #
    # A later ``ExitOnError 0`` could disarm it again; that is not modelled,
    # because nothing generates one and the check would stop being readable.
    first_load = _ANY_LOAD_FILE_RE.search(script)
    if first_load is not None and fail_fast.start() > first_load.start():
        raise CaptureError(
            f"NSX flash recipe for {target_name} ({script_path}) puts "
            "`ExitOnError 1` after its first `LoadFile`, so the flash itself "
            "runs with fail-fast still off. J-Link executes the recipe in "
            "order, so enabling it afterwards protects nothing: JLinkExe can "
            "fail the `LoadFile` and still exit successfully, and a failed "
            f"flash would look like a success. {_NOTHING_PROGRAMMED_RECIPE}",
            hint="Move `ExitOnError 1` above the first `LoadFile` line — NSX's "
            "own recipes open with it. Re-running the build regenerates the "
            "recipe from NSX, but discards any deliberate hand-edits.",
        )

    # Resolved once: it is hpx's own build path and ``flash_binary`` has
    # already confirmed it is a file, so unlike the recipe's paths below it
    # cannot be the one that blows up.
    expected_bin = bin_path.resolve()
    loaded: list[Path] = []
    for match in _ADDRESSED_LOAD_FILE_RE.finditer(script):
        quoted = match.group("quoted")
        candidate = Path(quoted if quoted is not None else match.group("plain").strip())
        if not candidate.is_absolute():
            candidate = script_path.parent / candidate
        # Recipe text is arbitrary: a NUL inside the quotes raises ValueError
        # here, and a Windows-illegal character raises OSError.  This module's
        # contract with its caller is CaptureError, so convert rather than let
        # an untyped exception escape a flash helper as an internal error.
        try:
            resolved = candidate.resolve()
        except (OSError, ValueError) as exc:
            raise CaptureError(
                f"NSX flash recipe for {target_name} ({script_path}) names a "
                f"`LoadFile` path this host cannot resolve ({candidate!r}): {exc}. "
                f"{_NOTHING_PROGRAMMED_RECIPE}",
                hint="Inspect the recipe's LoadFile lines for stray quoting or "
                "control characters, or re-run the build so NSX regenerates it.",
            ) from exc
        if _names_this_builds_image(resolved, expected_bin):
            return _parse_addr(match.group("address"))
        loaded.append(candidate)

    if not loaded:
        # TWO different recipes land here and they are not the same fault, so
        # they must not share one message.  ``loaded`` is empty either because
        # the recipe has no ``LoadFile`` at all, or because it has one that
        # ``_ADDRESSED_LOAD_FILE_RE`` could not read an address out of — most
        # plainly ``LoadFile "x.bin"``, which J-Link accepts and which DOES
        # program flash (the destination comes from the image format).  Telling
        # that second user "a recipe that loads nothing programs nothing" is
        # simply false, and it contradicts what ``_ANY_LOAD_FILE_RE`` above says
        # about the very same line.  ``first_load`` already distinguishes them
        # for the ordering check, so reuse it rather than guess.
        if first_load is None:
            raise CaptureError(
                f"NSX flash recipe for {target_name} ({script_path}) has no "
                "`LoadFile` command at all, so it programs nothing and there is "
                "no address to verify a flash against. "
                f"{_NOTHING_PROGRAMMED_RECIPE}",
                hint="Re-run the build so NSX regenerates the flash recipe.",
            )
        raise CaptureError(
            f"NSX flash recipe for {target_name} ({script_path}) has a `LoadFile` "
            "command, but none in the `LoadFile <image>, <address>` form hpx can "
            "read a destination out of. J-Link accepts an addressless `LoadFile` "
            "— it takes the destination from the image format — so this recipe "
            "would program flash somewhere hpx cannot check, which is the one "
            "thing this gate exists to refuse. "
            f"{_NOTHING_PROGRAMMED_RECIPE}",
            hint="Give the `LoadFile` line an explicit address "
            '(`LoadFile "<image>", 0x…`) if the recipe was hand-edited '
            "deliberately; re-running the build regenerates it from NSX, which "
            "always bakes the address in, but discards any such edits.",
        )
    listed = ", ".join(str(path) for path in loaded)
    raise CaptureError(
        f"NSX flash recipe for {target_name} ({script_path}) loads {listed}, "
        f"not this build's image {bin_path}. Recipes bake absolute paths, so a "
        "stale recipe flashes an older image while hpx attributes the results "
        f"to the current build. {_NOTHING_PROGRAMMED_RECIPE}",
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
    """Require J-Link to name the flash bank the requested address lives in.

    A flash to a wrong-but-writable address prints the same ``Total:`` summary
    as a correct one, so the summary alone proves only that *something* was
    programmed, never *where*.  The device then boots stale firmware from its
    real entry point while hpx publishes power numbers attributed to the new
    build — silent mis-measurement, the worst failure mode this tool has.

    This narrows that to the BANK, which is all J-Link reports — see the
    ``_BANK_ADDR_RE`` comment for what that does and does not catch, and for
    why it nonetheless covers the wrong-board case on every registered part.

    When J-Link names no bank at all this warns instead of raising: the bank
    line is corroboration layered on top of the exit-status gate and the
    summary-marker tripwire, and turning a cosmetic J-Link rewording into a
    hard stop would block correct flashes without any evidence of a wrong one.
    """
    observed = [_parse_addr(addr) for addr in _BANK_ADDR_RE.findall(output)]
    if not observed:
        # Deliberately not an exception (see above), but this is the one path
        # where the guard against silent mis-measurement has itself gone
        # silent, so say that outright rather than reporting a parse miss.
        log.warning(
            "UNVERIFIED FLASH DESTINATION: JLinkExe confirmed a flash of %s but "
            "named no bank address, so hpx could not check where the image landed "
            "against the requested 0x%08X (%s). The flash was allowed to proceed; "
            "a wrong-address flash would look exactly like this, so treat any "
            "power numbers from this run as unconfirmed until the destination is "
            "checked by hand.",
            target_name,
            expected_addr,
            source,
        )
        return
    if expected_addr in observed:
        return
    seen = ", ".join(f"0x{addr:08X}" for addr in observed)
    raise CaptureError(
        f"JLinkExe programmed {target_name} into the flash bank(s) based at "
        f"{seen}, but {source} requested 0x{expected_addr:08X}, which is in "
        "none of them. J-Link names the bank it programmed, not the exact "
        "destination, so the image landed somewhere other than the requested "
        "address: the device boots stale firmware from its real entry point "
        "while hpx would attribute the capture to this build.",
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

    Both paths verify afterwards that J-Link programmed the flash bank the
    requested address lives in (all J-Link reports — see ``_BANK_ADDR_RE``),
    but they learn that address differently.  The fallback builds
    the script itself, so *load_addr* is authoritative.  The recipe path runs
    NSX's script verbatim and *load_addr* is frequently ``None`` there, so the
    expected address is parsed out of the recipe itself; *load_addr* neither
    sets it nor is required on that path.  It is not ignored outright, though:
    a non-``None`` value that DISAGREES with the recipe is warned about, because
    two conflicting statements about where the part boots are evidence the build
    was configured for a different part.  The recipe still wins.  Running a
    recipe verbatim also means vetting it first the way NSX's own
    ``validate_flash_recipe`` does — see :func:`_recipe_load_address`.

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
        # Explicit utf-8, never the locale codec: NSX writes the recipe as
        # utf-8 and reads it back the same way, but Python's default here is
        # cp1252 on Windows, where a build path with a non-ASCII character
        # decodes to mojibake.  Before this check that only corrupted the text
        # piped to JLinkExe; now it decides whether the flash runs at all, so a
        # mis-decode would refuse a perfectly correct flash.
        #
        # Stating the codec also makes a mis-encoded recipe RAISE rather than
        # mojibake, and ``UnicodeDecodeError`` is not this module's contract
        # with its caller: ``stages.flash_power`` catches ``CaptureError`` only,
        # so an untyped escape is reported as "likely a bug in heliaPROFILER"
        # for what is a user's mis-encoded file.  Converted the same way the
        # unresolvable-path case below is, and for the same reason.
        try:
            script = script_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CaptureError(
                f"NSX flash recipe for {target_name} ({script_path}) is not valid "
                f"UTF-8 ({exc}), so hpx cannot read the recipe it would have run. "
                "NSX writes it as UTF-8, so this one was written or re-saved by "
                "something using a different codec. "
                f"{_NOTHING_PROGRAMMED_RECIPE}",
                hint="Re-save the recipe as UTF-8 if it was hand-edited, or "
                "re-run the build so NSX regenerates it.",
            ) from exc
        # Validate before programming: everything below is cheap and static,
        # and a bad recipe should be refused rather than run and then blamed.
        if not bin_path.is_file():
            raise CaptureError(
                f"NSX flash recipe for {target_name} ({script_path}) exists but "
                f"this build's image ({bin_path}) does not, so the recipe can "
                "only flash something other than what hpx just built. "
                f"{_NOTHING_PROGRAMMED_RECIPE}",
                hint="Re-run the build; the NSX build emits both per target.",
            )
        expected_addr = _recipe_load_address(
            script, script_path=script_path, bin_path=bin_path, target_name=target_name
        )
        expected_source = f"its NSX flash recipe ({script_path})"
        # The recipe still WINS (that is this path's whole contract, and the
        # divergence is legitimate often enough that hard-refusing it would
        # break working configs), but a declared address that disagrees with it
        # must not be discarded in silence.  hpx holds two authoritative-looking
        # statements about where this part boots and quietly picks one; the
        # disagreement itself is free, high-quality evidence that the build's
        # linker configuration is not for the part the user declared.  If the
        # DECLARATION is the correct one, the image lands at the wrong offset
        # and hpx publishes power numbers for stale firmware — #150's own
        # failure mode, arriving through the one door this path leaves open.
        #
        # Warn, never raise: ``load_addr`` is frequently a legitimately
        # different (or ``None``) value here.  #153 both makes a custom SoC's
        # ``app_flash_load_addr`` declarable and documents ``based_on:
        # apollo510`` alongside a declared address as a WORKING configuration,
        # stating that the recipe is used verbatim and the value ignored — so
        # refusing this outright would break a shape the guide recommends.
        if load_addr is not None and load_addr != expected_addr:
            log.warning(
                "DECLARED FLASH ADDRESS IGNORED: %s declares an app flash load "
                "address of 0x%08X, but its NSX flash recipe (%s) loads at "
                "0x%08X. The recipe wins — hpx runs it verbatim and NSX baked "
                "that address from the build's own linker configuration — so "
                "the image is going to 0x%08X. If the declared address is the "
                "right one for this part, then this build was configured for a "
                "different part and the flash is landing at the wrong offset; "
                "power numbers from this run would describe stale firmware.",
                target_name,
                load_addr,
                script_path,
                expected_addr,
                expected_addr,
            )
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
                f"script ({script_path}) nor a .bin sibling ({bin_path}) exists. "
                f"{_NOTHING_PROGRAMMED}",
                hint="Re-run the build; the NSX build emits both per target.",
            )
        if load_addr is None:
            # The hint has to name the YAML a user can actually type.  The
            # address reaches here as MemoryCapabilities.app_flash_load_addr,
            # but that is an internal dataclass attribute: naming it sends the
            # reader looking for a setting that does not exist.  Config first,
            # because for a custom SoC that reached this branch the build is
            # not what is broken — nothing declared the address.
            #
            # ``device`` can be EMPTY here, and interpolating it blind leaves
            # the sentence with a hole where the part belongs ("...known for
            # device  to fall back to").  ``platform.custom`` defaults
            # ``jlink_device`` to "" for a custom SoC entry that declares
            # neither the device string nor a ``based_on`` to inherit one from
            # — the same under-specified entry that leaves the address unknown.
            # So an empty device is not a formatting nuisance to paper over; it
            # is a second symptom of the same cause, worth saying out loud.
            named = f"device {device}" if device else "this target's SoC"
            also_unnamed = (
                ""
                if device
                else " That SoC declares no J-Link device string either, so the "
                "entry is under-specified in both fields rather than just this one."
            )
            raise CaptureError(
                f"Cannot flash {target_name}: the NSX flash script ({script_path}) is "
                f"missing and no app flash load address is known for {named} to fall "
                f"back to.{also_unnamed} {_NOTHING_PROGRAMMED}",
                hint="Declare the address for this SoC in your profile config: "
                "`target.custom_socs.<name>.app_flash_load_addr: 0x…`, or give that "
                "entry a `based_on:` naming the part whose address it should "
                "inherit. If the SoC is a built-in one, the flash script is what is "
                "missing instead — re-run the build so NSX regenerates it (the "
                "recipe carries its own address and this fallback is skipped).",
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
    # Exit status is the primary gate: every recipe that reaches here arms
    # ``ExitOnError 1`` before its first ``LoadFile`` (the fallback above
    # opens with it; a recipe is vetted for it), and run_jlink_script
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
