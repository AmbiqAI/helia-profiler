"""Schema identities for HPX-owned report artifacts.

The run-summary pair lives on the typed model (#202: the shape and its
version belong together; ``results/run_summary.py`` carries the full
version history) and is re-exported here for the long-standing import
sites. Detailed v1→v4 semantics rationale: see the git history of this
file and the model's docstrings.

v2 (#24): ``binary.bss`` excludes linker-reserved NOBITS (moved to
``binary.reserved``). v3 (#133): measured ``memory_regions`` owns region
truth; ``memory_plan`` is a pure decision record. v4 (#142/#181):
``energy_per_inference_j`` can coexist with
``gate_duration_integrity.valid: false``; ``gated_window_duration_suspect``
keys on the observer arbitration; ``gated_window_reference_drift`` added.
Each was a semantic change a cross-boundary consumer must SEE —
``run_summary_schema_version`` is a comparability dimension.
"""

from ..results.run_summary import (  # noqa: F401
    RUN_SUMMARY_SCHEMA,
    RUN_SUMMARY_SCHEMA_VERSION,
)

RUN_METADATA_SCHEMA = "hpx.run-metadata"
RUN_METADATA_SCHEMA_VERSION = 1

PROFILE_RESULTS_SCHEMA = "hpx.profile-results"
PROFILE_RESULTS_SCHEMA_VERSION = 1
