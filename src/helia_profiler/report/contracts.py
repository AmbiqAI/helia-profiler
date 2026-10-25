"""Schema identities for HPX-owned report artifacts.

The run-summary identity and version are owned by ``results.run_summary``
and re-exported here for report writers. Historical field and measurement
changes are documented in the Run-summary schema reference:
https://ambiqai.github.io/helia-profiler/reference/run-summary-schema/
"""

from ..results.run_summary import (  # noqa: F401
    RUN_SUMMARY_SCHEMA,
    RUN_SUMMARY_SCHEMA_VERSION,
)

RUN_METADATA_SCHEMA = "hpx.run-metadata"
RUN_METADATA_SCHEMA_VERSION = 1

PROFILE_RESULTS_SCHEMA = "hpx.profile-results"
PROFILE_RESULTS_SCHEMA_VERSION = 1
