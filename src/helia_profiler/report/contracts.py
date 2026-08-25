"""Schema identities for HPX-owned report artifacts."""

RUN_SUMMARY_SCHEMA = "hpx.run-summary"
#: v2 (issue #24): ``binary.bss`` no longer includes linker-reserved NOBITS
#: regions -- chiefly the ``.heap`` fill NSX's AP5 scripts use to claim all
#: remaining DTCM. Those bytes moved to the new ``binary.reserved``. The
#: number therefore means something different than it did in v1, by a large
#: margin on AP5 (392 KB -> 248 B on a measured build), so this is a version
#: bump rather than an additive key: consumers comparing across the boundary
#: need to be able to SEE it rather than read it as a memory regression.
#: ``run_summary_schema_version`` is a comparability dimension, so bumping it
#: is what surfaces the difference in ``hpx compare``.
#: v3 (issue #133): the ``memory_plan`` block is now a pure decision
#: record -- ``free``/``overflow``/``has_overflow`` are GONE from it, and
#: the new measured ``memory_regions`` block (the linked ELF classified
#: into the verified per-SoC windows) owns region truth: per-region
#: used/reserved/free/load_image under the app-window contract of
#: ``platform.memory_map``. The plan's old free/overflow answered "does
#: what hpx placed fit the datasheet capacity" -- free was overstated and
#: overflow could not fire on real exhaustion because the plan only counts
#: what HPX placed (the #133 pathology). A consumer comparing across the
#: boundary must SEE the semantic change, and
#: ``run_summary_schema_version`` is a comparability dimension, so this is
#: a version bump per the v2 precedent above.
#:
#: v4 (#142/#181): the gate-duration verdict was re-sourced. A v4 artifact
#: can carry ``power.energy_per_inference_j`` alongside
#: ``gate_duration_integrity.valid: false`` (impossible at v3, where the
#: capture raised before such an artifact existed) -- the firmware's own
#: window clock arbitrates, and ``power.gated_window_reference_drift``
#: records the reclassification. ``gated_window_duration_suspect`` now keys
#: on that arbitration (observer/terminal-health/floor), not the est*count
#: band alone. A consumer applying v3 semantics to a v4 artifact fails
#: healthy drift runs (the validation runner did exactly that until taught
#: the drift field), so the boundary must be visible.
RUN_SUMMARY_SCHEMA_VERSION = 4

RUN_METADATA_SCHEMA = "hpx.run-metadata"
RUN_METADATA_SCHEMA_VERSION = 1

PROFILE_RESULTS_SCHEMA = "hpx.profile-results"
PROFILE_RESULTS_SCHEMA_VERSION = 1
