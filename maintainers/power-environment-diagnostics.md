# Proposed power environment diagnostics

[#228](https://github.com/AmbiqAI/helia-profiler/issues/228) records JS320 publish
timeouts in the Python 3.11 environment using the older pyjoulescope driver.
That historical failure is evidence for a diagnostic boundary, not a current
physical reproduction or a reason to reject every Joulescope device.

## Ownership and policy

The power compatibility owner must select the supported environment/version
policy and diagnostic severity. Preserve the recorded first-refusal ownership.
Keep that rule in the existing power compatibility or diagnostic layer and expose
it through real doctor/preflight consumers; do not duplicate version tables.
A stale Python shim and a discovered JS320 must be diagnosed from the same
resolved environment facts if both consumers are selected.

The message should identify the actual interpreter, driver and discovered device
combination, explain the known incompatibility and provide an owner-approved
remediation. Unknown metadata and no-device states need distinct messages; they
must not be described as a confirmed incompatible JS320. JS110 and JS220 retain
their supported behavior.

## Acceptance and limits

Exercise compatible, known incompatible, unknown and no-device metadata through
the selected real diagnostic consumers. Assert actionable hints and severity,
including unaffected JS110/220 controls. Tests must not install drivers, contact
devices or convert a historical stopped USB/driver purpose into a fake campaign.

This is a proposal for a diagnostic contract. It does not change Python support,
patch compiled drivers, edit benchmark workflows or prescribe USB recovery.
Implementation waits for owner policy and permitted named checks. Physical
validation and environment changes require their own explicit decision.
