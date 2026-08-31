"""Field-diagnostics support bundle: collection and deterministic archiving.

``hpx doctor --bundle`` calls into this module to assemble a sanitized,
offline-safe snapshot of the host environment for troubleshooting — never a
model, firmware source, ELF/binary, or raw credential. See
``docs/architecture/field-diagnostics.md`` for the full design rationale.

Collection is best-effort per section: a missing workspace, an absent
optional tool, or a config that fails to resolve degrades exactly that one
section to ``available=False`` with a human-readable reason instead of
failing the whole bundle (see :class:`SupportBundleSection`).
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .._version import __version__ as _hpx_version
from ..deps.compatibility import CompatibilityBaseline, load_compatibility_baseline
from ..config import Toolchain, Transport, load_config
from ..deps.dependencies import read_dependency_lock_provenance
from ..hostenv.doctor import inspect_environment
from ..engines import EngineType
from ..errors import DependencyError, HpxError, ReportError
from .redact import RedactionCounts, RedactionPolicy, redact_known_serial, redact_text, redact_value
from ..results import DependencyLockProvenance, ResultArtifact
from ..results.support_bundle import (
    SUPPORT_BUNDLE_SCHEMA,
    SUPPORT_BUNDLE_SCHEMA_VERSION,
    SupportBundleManifest,
    SupportBundleSection,
)

# ---------------------------------------------------------------------------
# Options and in-memory collection result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SupportBundleOptions:
    """What ``hpx doctor --bundle`` should collect.

    Every section beyond the always-available doctor checks and
    compatibility baseline is optional and independently toggleable so a
    bundle can be built entirely offline with no attached hardware.
    """

    workspace: Path | None = None
    config_path: Path | None = None
    toolchain: Toolchain = Toolchain.ARM_NONE_EABI_GCC
    transport: Transport = Transport.RTT
    engine: EngineType = EngineType.HELIA_RT
    include_probes: bool = True
    include_ports: bool = True
    raw_probe_ids: bool = False


@dataclass(frozen=True)
class SupportBundleCollection:
    """Collected, already-redacted members plus their manifest, in memory."""

    manifest: SupportBundleManifest
    members: dict[str, bytes]


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def collect_support_bundle(options: SupportBundleOptions = SupportBundleOptions()) -> SupportBundleCollection:
    """Gather every diagnostic section, redact it, and build the manifest.

    Never raises for a missing optional dependency, tool, or workspace —
    every section catches its own typed failures and records a skip reason
    instead. Only truly unexpected internal errors propagate.
    """
    policy = RedactionPolicy(redact_probe_serials=not options.raw_probe_ids)
    members: dict[str, bytes] = {}
    sections: list[SupportBundleSection] = []
    counts = RedactionCounts()

    counts = _collect_checks(options, policy, members, sections, counts)
    baseline, counts = _collect_compatibility(policy, members, sections, counts)
    provenance, counts = _collect_dependencies(options, policy, members, sections, counts)
    counts = _collect_modules(baseline, provenance, policy, members, sections, counts)
    counts = _collect_config(options, policy, members, sections, counts)
    counts = _collect_probes(options, policy, members, sections, counts)
    counts = _collect_ports(options, policy, members, sections, counts)

    host, counts = _collect_host(policy, counts)
    manifest = _build_manifest(
        members=members,
        sections=sections,
        counts=counts,
        host=host,
        raw_probe_ids=options.raw_probe_ids,
        policy=policy,
    )
    return SupportBundleCollection(manifest=manifest, members=members)


def _dump_json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")


def _add_json_member(
    members: dict[str, bytes], name: str, value: Any, policy: RedactionPolicy
) -> RedactionCounts:
    redacted, counts = redact_value(value, policy)
    members[name] = _dump_json(redacted)
    return counts


def _collect_checks(
    options: SupportBundleOptions,
    policy: RedactionPolicy,
    members: dict[str, bytes],
    sections: list[SupportBundleSection],
    counts: RedactionCounts,
) -> RedactionCounts:
    doctor_result = inspect_environment(
        toolchain=options.toolchain,
        transport=options.transport,
        engine=options.engine,
        include_versions=True,
    )
    item_counts = _add_json_member(members, "checks.json", doctor_result.to_dict(), policy)
    sections.append(SupportBundleSection("checks", True))
    return counts.combined(item_counts)


def _collect_compatibility(
    policy: RedactionPolicy,
    members: dict[str, bytes],
    sections: list[SupportBundleSection],
    counts: RedactionCounts,
) -> tuple[CompatibilityBaseline | None, RedactionCounts]:
    try:
        baseline = load_compatibility_baseline()
    except HpxError as exc:
        sections.append(SupportBundleSection("compatibility", False, reason=str(exc)))
        return None, counts
    item_counts = _add_json_member(members, "compatibility.json", baseline.to_dict(), policy)
    sections.append(SupportBundleSection("compatibility", True))
    return baseline, counts.combined(item_counts)


def _collect_dependencies(
    options: SupportBundleOptions,
    policy: RedactionPolicy,
    members: dict[str, bytes],
    sections: list[SupportBundleSection],
    counts: RedactionCounts,
) -> tuple[DependencyLockProvenance | None, RedactionCounts]:
    if options.workspace is None:
        reason = "no --workspace given"
        sections.append(SupportBundleSection("dependencies", False, reason=reason))
        sections.append(SupportBundleSection("nsx.lock", False, reason=reason))
        return None, counts

    try:
        provenance = read_dependency_lock_provenance(options.workspace)
    except (DependencyError, OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError (a ValueError, not an OSError): the recorded
        # provenance state file (hpx-dependencies.json) is read as UTF-8
        # text internally by read_dependency_lock_provenance() -- a
        # corrupted/truncated state file must degrade this section like
        # every other best-effort failure here, not crash the collector.
        sections.append(SupportBundleSection("dependencies", False, reason=str(exc)))
        sections.append(SupportBundleSection("nsx.lock", False, reason=str(exc)))
        return None, counts

    item_counts = _add_json_member(
        members, "dependencies.json", _lock_provenance_to_dict(provenance), policy
    )
    counts = counts.combined(item_counts)
    sections.append(SupportBundleSection("dependencies", True))

    try:
        lock_text = provenance.lock_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError (a ValueError, not an OSError) is just as
        # possible here as a read failure -- nsx.lock is user/tool-written
        # and nothing guarantees it is valid UTF-8. Either way this is a
        # best-effort section like every other one in this collector, not
        # a reason to abort the whole bundle.
        sections.append(SupportBundleSection("nsx.lock", False, reason=str(exc)))
        return provenance, counts

    redacted_lock_text, lock_counts = redact_text(lock_text, policy)
    members["nsx.lock"] = redacted_lock_text.encode("utf-8")
    sections.append(SupportBundleSection("nsx.lock", True))
    return provenance, counts.combined(lock_counts)


def _lock_provenance_to_dict(provenance: DependencyLockProvenance) -> dict[str, Any]:
    return {
        "lock_path": str(provenance.lock_path),
        "lock_sha256": provenance.lock_sha256,
        "registry_hash": provenance.registry_hash,
        "requested_refs": [
            {
                "scope": item.scope,
                "name": item.name,
                "requested_ref": item.requested_ref,
                "requested_tag": item.requested_tag,
            }
            for item in provenance.requested_refs
        ],
        "resolved": [
            {
                "name": module.name,
                "project": module.project,
                "kind": module.kind,
                "requested_ref": module.requested_ref,
                "requested_tag": module.requested_tag,
                "peeled_commit": module.peeled_commit,
                "content_hash": module.content_hash.to_dict(),
                "url": module.url,
                "vendored_at": module.vendored_at,
            }
            for module in provenance.resolved
        ],
        "overrides": [
            {
                "scope": item.scope,
                "name": item.name,
                "mode": item.mode,
                "requested": item.requested,
                "content_hash": item.content_hash.to_dict() if item.content_hash else None,
            }
            for item in provenance.overrides
        ],
        "qualification": provenance.qualification.value,
        "baseline_fingerprint": provenance.baseline_fingerprint,
        "workspace_fingerprint": provenance.workspace_fingerprint,
        "lock_mode": provenance.lock_mode.value,
        "update_requested": provenance.update_requested,
    }


def _collect_modules(
    baseline: CompatibilityBaseline | None,
    provenance: DependencyLockProvenance | None,
    policy: RedactionPolicy,
    members: dict[str, bytes],
    sections: list[SupportBundleSection],
    counts: RedactionCounts,
) -> RedactionCounts:
    payload = {
        "baseline": (
            {module.name: {"project": module.project, "ref": module.ref} for module in baseline.modules}
            if baseline is not None
            else {}
        ),
        "resolved": (
            {
                module.name: {
                    "project": module.project,
                    "kind": module.kind,
                    "requested_ref": module.requested_ref,
                    "requested_tag": module.requested_tag,
                    "peeled_commit": module.peeled_commit,
                    "content_hash": module.content_hash.to_dict(),
                    "url": module.url,
                    "vendored_at": module.vendored_at,
                }
                for module in provenance.resolved
            }
            if provenance is not None
            else {}
        ),
    }
    item_counts = _add_json_member(members, "modules.json", payload, policy)
    sections.append(SupportBundleSection("modules", True))
    return counts.combined(item_counts)


def _collect_config(
    options: SupportBundleOptions,
    policy: RedactionPolicy,
    members: dict[str, bytes],
    sections: list[SupportBundleSection],
    counts: RedactionCounts,
) -> RedactionCounts:
    if options.config_path is None:
        sections.append(SupportBundleSection("config", False, reason="no --config given"))
        return counts
    try:
        config = load_config(options.config_path, {})
    except (HpxError, OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError (a ValueError, not an OSError): load_config()
        # opens the YAML file as text with no explicit encoding, so a
        # hand-authored config with a non-UTF-8 byte (or any file read
        # under a non-UTF-8 locale) must degrade this section like every
        # other best-effort failure here, not crash the collector.
        sections.append(SupportBundleSection("config", False, reason=str(exc)))
        return counts

    from ..pipeline import _serialize_config

    item_counts = _add_json_member(members, "config.json", _serialize_config(config), policy)
    sections.append(SupportBundleSection("config", True))
    return counts.combined(item_counts)


def _collect_probes(
    options: SupportBundleOptions,
    policy: RedactionPolicy,
    members: dict[str, bytes],
    sections: list[SupportBundleSection],
    counts: RedactionCounts,
) -> RedactionCounts:
    if not options.include_probes:
        sections.append(SupportBundleSection("probes", False, reason="--no-probes"))
        return counts
    try:
        from ..target.probe.jlink import list_connected_probes

        probes = list_connected_probes()
    except (HpxError, ImportError, OSError) as exc:
        sections.append(SupportBundleSection("probes", False, reason=str(exc)))
        return counts

    payload = [
        {"serial": probe.serial, "product": probe.product, "connection": probe.connection}
        for probe in probes
    ]
    item_counts = _add_json_member(members, "probes.json", payload, policy)
    sections.append(SupportBundleSection("probes", True))
    return counts.combined(item_counts)


def _collect_ports(
    options: SupportBundleOptions,
    policy: RedactionPolicy,
    members: dict[str, bytes],
    sections: list[SupportBundleSection],
    counts: RedactionCounts,
) -> RedactionCounts:
    if not options.include_ports:
        sections.append(SupportBundleSection("ports", False, reason="--no-ports"))
        return counts
    try:
        from ..transport.ports import list_serial_ports

        ports = list_serial_ports(include_all=False)
    except (HpxError, ImportError, OSError) as exc:
        sections.append(SupportBundleSection("ports", False, reason=str(exc)))
        return counts

    counts_and_records = [_sanitize_port_record(port, policy) for port in ports]
    payload = [record for record, _ in counts_and_records]
    for _, record_counts in counts_and_records:
        counts = counts.combined(record_counts)
    item_counts = _add_json_member(members, "ports.json", payload, policy)
    sections.append(SupportBundleSection("ports", True))
    return counts.combined(item_counts)


def _sanitize_port_record(
    port: Any, policy: RedactionPolicy
) -> tuple[dict[str, Any], RedactionCounts]:
    """Scrub a known serial number out of every sibling field before JSON.

    ``redact_value``'s key-based routing only redacts the field literally
    *named* like a serial (``serial_number``); it does not catch the same
    value recurring inside an unrelated field such as pyserial's ``hwid``
    (``... SER=<serial> ...``) or a device path whose basename embeds it
    (macOS ``/dev/tty.usbmodem<serial>...``). Substitute those first, so the
    later generic redaction pass never sees the raw value at all.
    """
    record = {
        "device": port.device,
        "kind": port.kind,
        "description": port.description,
        "manufacturer": port.manufacturer,
        "product": port.product,
        "serial_number": port.serial_number,
        "interface": port.interface,
        "hwid": port.hwid,
    }
    serial = port.serial_number or ""
    record_counts = RedactionCounts()
    if serial:
        for field in ("device", "description", "manufacturer", "product", "interface", "hwid"):
            record[field], field_counts = redact_known_serial(record[field], serial, policy)
            record_counts = record_counts.combined(field_counts)
    return record, record_counts


def _collect_host(
    policy: RedactionPolicy, counts: RedactionCounts
) -> tuple[dict[str, Any], RedactionCounts]:
    # Deliberately excludes platform.node() (hostname) and any username —
    # neither is needed to diagnose a toolchain/build problem, and both are
    # more identifying than the redaction policy below is designed to catch.
    host = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "platform": sys.platform,
    }
    redacted, item_counts = redact_value(host, policy)
    return redacted, counts.combined(item_counts)


def _redact_sections(
    sections: list[SupportBundleSection], policy: RedactionPolicy, counts: RedactionCounts
) -> tuple[tuple[SupportBundleSection, ...], RedactionCounts]:
    """Redact every section's ``reason`` before it reaches the manifest.

    A skip reason is almost always a formatted exception message (a missing
    workspace, an unreadable config, ...), which routinely embeds the exact
    absolute path or account name that triggered it -- this is otherwise
    the one place in the collector where free-form text reaches the
    archive without going through :func:`~helia_profiler.redact.redact_value`.
    """
    redacted_sections: list[SupportBundleSection] = []
    for section in sections:
        if section.reason is None:
            redacted_sections.append(section)
            continue
        redacted_reason, reason_counts = redact_text(section.reason, policy)
        counts = counts.combined(reason_counts)
        redacted_sections.append(
            SupportBundleSection(section.name, section.available, reason=redacted_reason)
        )
    return tuple(redacted_sections), counts


def _build_manifest(
    *,
    members: dict[str, bytes],
    sections: list[SupportBundleSection],
    counts: RedactionCounts,
    host: dict[str, Any],
    raw_probe_ids: bool,
    policy: RedactionPolicy,
) -> SupportBundleManifest:
    redacted_sections, counts = _redact_sections(sections, policy, counts)
    artifacts = tuple(
        ResultArtifact(
            path=name,
            media_type="application/yaml" if name == "nsx.lock" else "application/json",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            role="diagnostic",
            producer="hpx.support-bundle",
            optional=False,
        )
        for name, data in sorted(members.items())
    )
    return SupportBundleManifest(
        schema=SUPPORT_BUNDLE_SCHEMA,
        schema_version=SUPPORT_BUNDLE_SCHEMA_VERSION,
        hpx_version=_hpx_version,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        host=host,
        sections=redacted_sections,
        redaction={**counts.to_dict(), "raw_probe_ids": raw_probe_ids},
        artifacts=artifacts,
    )


# ---------------------------------------------------------------------------
# Deterministic archive writing and verification
# ---------------------------------------------------------------------------

_ZIP_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
_UNSAFE_VERSION_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")
_ALLOWED_MEMBER_SUFFIXES = frozenset({".json"})
_ALLOWED_EXACT_MEMBER_NAMES = frozenset({"nsx.lock", "manifest.json"})
# A Windows drive-absolute path ("C:/...") is absolute on Windows even
# though it contains no leading "/" and PurePosixPath doesn't recognize a
# drive letter as a root -- this module only ever writes this archive
# itself, but a hostile/corrupted one could smuggle a member name that a
# Windows extractor would treat as an absolute path outside the bundle.
# Backslash forms are already rejected above by the plain "\\" in name
# check; this only needs to close the forward-slash form.
_WINDOWS_DRIVE_ABS_RE = re.compile(r"(?i)^[a-z]:[/\\]")


def content_fingerprint(members: dict[str, bytes]) -> str:
    """Deterministic identity of *members*, independent of wall-clock time.

    Used both for the archive filename and to prove two bundles built from
    identical inputs are identical: ``manifest.json`` is excluded by the
    caller (it embeds ``generated_at``), so this only ever reflects content.
    """
    digest = hashlib.sha256()
    for name in sorted(members):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(members[name]).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def write_support_bundle(collection: SupportBundleCollection, output: Path) -> Path:
    """Write *collection* as a deterministic ZIP archive and return its path.

    *output* names the exact archive file when it ends in ``.zip``;
    otherwise it is treated as a directory (created if needed) and the
    filename is derived from :func:`content_fingerprint` plus the HPX
    version, so identical inputs always produce the same file name and the
    same member bytes for every entry except ``manifest.json`` (only its
    ``generated_at`` timestamp differs run to run).

    Raises :class:`~helia_profiler.errors.ReportError` (not a raw
    :class:`OSError`) if the destination cannot be created or written to
    (permission denied, no space left, a path component that is itself a
    file, ...), so CLI callers only ever need to catch ``HpxError``.
    """
    fingerprint = content_fingerprint(collection.members)
    all_members = dict(collection.members)
    all_members["manifest.json"] = _dump_json(collection.manifest.to_dict())

    is_explicit_file = output.suffix.lower() == ".zip"
    if is_explicit_file:
        archive_path = output
    else:
        safe_version = _UNSAFE_VERSION_CHARS.sub("_", collection.manifest.hpx_version) or "0"
        archive_path = output / f"hpx-support-bundle-{safe_version}-{fingerprint[:16]}.zip"

    try:
        if is_explicit_file:
            archive_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            output.mkdir(parents=True, exist_ok=True)
        _write_deterministic_zip(archive_path, all_members)
    except OSError as exc:
        raise ReportError(
            f"Cannot write support bundle to {archive_path}: {exc}",
            hint="Check that the destination directory is writable and has free space.",
        ) from exc
    return archive_path


def _write_deterministic_zip(path: Path, members: dict[str, bytes]) -> None:
    ordered_names = sorted(name for name in members if name != "manifest.json")
    ordered_names.append("manifest.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    # tempfile.mkstemp opens with O_CREAT|O_EXCL (and O_NOFOLLOW where the
    # platform supports it) at an unpredictable name, unlike a name derived
    # from the PID alone — that would let another local user on a shared
    # host pre-create the exact path (e.g. as a symlink) and win a race.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            # ZIP_STORED (no compression), not ZIP_DEFLATED: DEFLATE's exact
            # output bytes are a function of the zlib version/build doing
            # the compressing, which is not something this module controls
            # or pins, so two hosts with different zlib builds could
            # otherwise produce different bytes for identical input despite
            # every other normalization here. Storing uncompressed makes
            # the "byte-identical members" guarantee true independent of
            # host/zlib — an acceptable trade-off since bundle members are
            # small JSON/text, not something worth compressing.
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_STORED) as archive:
                for name in ordered_names:
                    info = zipfile.ZipInfo(name, date_time=_ZIP_FIXED_DATE_TIME)
                    info.compress_type = zipfile.ZIP_STORED
                    info.external_attr = 0o644 << 16
                    info.create_system = 0  # normalize across platforms for byte-determinism
                    archive.writestr(info, members[name])
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)


def verify_support_bundle(path: Path) -> SupportBundleManifest:
    """Verify a support-bundle archive's structure, contents, and digests.

    Rejects absolute member paths (POSIX, and Windows drive-letter paths in
    either ``C:\\...`` or ``C:/...`` form), ``..``/empty path segments,
    backslashes, NUL bytes, duplicate entries, and any file extension other
    than ``.json``/exactly ``nsx.lock`` — defense in depth against a
    malformed or hostile archive (zip-slip, disguised binary payloads) even
    though this module only ever writes archives matching that shape
    itself.
    """
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        _validate_member_names(names)
        if "manifest.json" not in names:
            raise ReportError(f"Support bundle is missing manifest.json: {path}")
        try:
            manifest_data = json.loads(archive.read("manifest.json"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            # A corrupted/hand-edited archive can carry a manifest.json
            # that isn't valid JSON or valid UTF-8 -- surface this as the
            # same typed ReportError every other verification failure uses
            # instead of letting a raw JSONDecodeError/UnicodeDecodeError
            # escape and break the "verify never raises anything but
            # ReportError" contract. SupportBundleManifest.from_dict()'s
            # own ReportError (bad schema/shape) still propagates as-is.
            raise ReportError(
                f"Support bundle manifest.json is not valid JSON: {path}: {exc}"
            ) from exc
        manifest = SupportBundleManifest.from_dict(manifest_data)

        declared = {artifact.path for artifact in manifest.artifacts} | {"manifest.json"}
        actual = set(names)
        extra = sorted(actual - declared)
        if extra:
            raise ReportError(f"Support bundle contains undeclared members: {extra}")
        missing = sorted(declared - actual)
        if missing:
            raise ReportError(f"Support bundle is missing declared members: {missing}")

        for artifact in manifest.artifacts:
            data = archive.read(artifact.path)
            if len(data) != artifact.size_bytes:
                raise ReportError(f"Support bundle artifact size mismatch: {artifact.path}")
            if hashlib.sha256(data).hexdigest() != artifact.sha256:
                raise ReportError(f"Support bundle artifact digest mismatch: {artifact.path}")
    return manifest


def _validate_member_names(names: list[str]) -> None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise ReportError(f"Support bundle contains a duplicate member: {name}")
        seen.add(name)
        if not name or "\x00" in name or "\\" in name:
            raise ReportError(f"Support bundle member has an unsafe path: {name!r}")
        parts = name.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ReportError(f"Support bundle member has an unsafe path: {name!r}")
        pure = PurePosixPath(name)
        if pure.is_absolute() or _WINDOWS_DRIVE_ABS_RE.match(name):
            raise ReportError(f"Support bundle member path must be relative: {name!r}")
        if name not in _ALLOWED_EXACT_MEMBER_NAMES and pure.suffix not in _ALLOWED_MEMBER_SUFFIXES:
            raise ReportError(f"Support bundle member has a disallowed type: {name!r}")


__all__ = [
    "SupportBundleCollection",
    "SupportBundleOptions",
    "collect_support_bundle",
    "content_fingerprint",
    "verify_support_bundle",
    "write_support_bundle",
]
