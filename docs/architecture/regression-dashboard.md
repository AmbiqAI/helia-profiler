# Regression Dashboard

The regression dashboard is a static analytics client for completed HPX validation bundles. It
does not run hardware, build firmware, or replace the validation and comparison commands.

## Data flow

1. GitHub Actions runs a complete validation suite.
2. HPX writes an immutable portable validation bundle.
3. `tools/build_regression_dataset.py` normalizes one or more bundles into a versioned static
   dataset.
4. The dashboard loads run summaries eagerly and layer measurements only when a case is opened.

Raw bundles remain the audit record. The regression dataset is a rebuildable query and display
format.

## Dataset v1

`catalog.json` discovers available runs. Each entry links to a run document under `runs/`. Run
documents contain structured case identity, provenance, health, and run-level metrics. Case
documents link to optional layer files under `layers/<run-id>/`.

The case identity preserves every validation dimension:

- model;
- engine;
- board;
- normalized toolchain;
- transport;
- requested memory configuration;
- requested power configuration;
- repeat attempt.

The dashboard matches cases by the canonical serialized identity. Therefore a larger suite, new
model, additional board, or another engine requires no dashboard schema change.

## Comparison semantics

Dashboard deltas are candidate minus baseline. Layer comparison is available only when layer index
and operation sequence match exactly. A graph change remains visible at run level but must not be
silently aligned by position.

The browser computes interactive deltas from normalized measurements. HPX remains authoritative
for generated CLI comparison artifacts and future regression policy.

## Deferred

- permanent object storage and scheduled publishing;
- statistical aggregation across repeats;
- regression thresholds and notifications;
- authentication and annotations;
- Parquet compaction and browser-side analytical SQL.
