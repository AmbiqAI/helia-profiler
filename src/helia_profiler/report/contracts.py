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
RUN_SUMMARY_SCHEMA_VERSION = 3

RUN_METADATA_SCHEMA = "hpx.run-metadata"
RUN_METADATA_SCHEMA_VERSION = 1

PROFILE_RESULTS_SCHEMA = "hpx.profile-results"
PROFILE_RESULTS_SCHEMA_VERSION = 1
