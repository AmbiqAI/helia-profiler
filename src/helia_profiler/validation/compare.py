"""Offline comparison orchestration for completed validation bundles."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from ..compare import CompareResult, compare_runs, write_compare_artifacts
from ..errors import HpxError, ReportError
from .bundle import (
    ValidationBundle,
    ValidationBundleCase,
    ValidationCaseIdentity,
    load_validation_bundle,
    resolve_artifact,
)

_REQUIRED_ARTIFACTS = ("case_dir", "summary", "run_metadata", "profile_results")


class CaseOutcome(str, Enum):
    COMPARED = "compared"
    BASELINE_ONLY = "baseline_only"
    CANDIDATE_ONLY = "candidate_only"
    FAILED = "failed"
    SKIPPED = "skipped"
    INELIGIBLE = "ineligible"
    COMPARE_ERROR = "compare_error"


@dataclass(frozen=True)
class ValidationCaseCompare:
    identity: ValidationCaseIdentity
    outcome: CaseOutcome
    baseline_case: ValidationBundleCase | None
    candidate_case: ValidationBundleCase | None
    warnings: tuple[str, ...] = ()
    reason: str | None = None
    compare_result: CompareResult | None = None


@dataclass(frozen=True)
class ValidationCompareResult:
    baseline: ValidationBundle
    candidate: ValidationBundle
    cases: tuple[ValidationCaseCompare, ...]
    warnings: tuple[str, ...]

    @property
    def summary(self) -> dict[str, int]:
        counts = {outcome.value: 0 for outcome in CaseOutcome}
        for case in self.cases:
            counts[case.outcome.value] += 1
        return {"total": len(self.cases), **counts}


def compare_validation_bundles(baseline_dir: Path, candidate_dir: Path) -> ValidationCompareResult:
    """Load, match, and compare two validation bundles without hardware."""

    baseline = load_validation_bundle(baseline_dir)
    candidate = load_validation_bundle(candidate_dir)
    base_by_id = {case.identity: case for case in baseline.cases}
    cand_by_id = {case.identity: case for case in candidate.cases}
    cases: list[ValidationCaseCompare] = []

    for identity in sorted(set(base_by_id) | set(cand_by_id)):
        base_case = base_by_id.get(identity)
        cand_case = cand_by_id.get(identity)
        if base_case is None:
            cases.append(
                ValidationCaseCompare(
                    identity,
                    CaseOutcome.CANDIDATE_ONLY,
                    None,
                    cand_case,
                    reason="No matching baseline case.",
                )
            )
            continue
        if cand_case is None:
            cases.append(
                ValidationCaseCompare(
                    identity,
                    CaseOutcome.BASELINE_ONLY,
                    base_case,
                    None,
                    reason="No matching candidate case.",
                )
            )
            continue

        health_warnings = tuple(
            [f"Baseline health: {issue}" for issue in base_case.health_issues]
            + [f"Candidate health: {issue}" for issue in cand_case.health_issues]
        )
        statuses = {base_case.status, cand_case.status}
        if "fail" in statuses:
            cases.append(
                ValidationCaseCompare(
                    identity,
                    CaseOutcome.FAILED,
                    base_case,
                    cand_case,
                    warnings=health_warnings,
                    reason="At least one validation case failed.",
                )
            )
            continue
        if "skip" in statuses:
            cases.append(
                ValidationCaseCompare(
                    identity,
                    CaseOutcome.SKIPPED,
                    base_case,
                    cand_case,
                    warnings=health_warnings,
                    reason="At least one validation case was skipped.",
                )
            )
            continue

        missing = _missing_required(baseline, base_case, "baseline") + _missing_required(
            candidate, cand_case, "candidate"
        )
        if missing:
            cases.append(
                ValidationCaseCompare(
                    identity,
                    CaseOutcome.INELIGIBLE,
                    base_case,
                    cand_case,
                    warnings=health_warnings,
                    reason="Missing required artifacts: " + ", ".join(missing),
                )
            )
            continue

        try:
            base_dir = _case_dir(baseline, base_case)
            cand_dir = _case_dir(candidate, cand_case)
            run_result = compare_runs(base_dir, cand_dir)
        except (HpxError, OSError, ValueError) as exc:
            cases.append(
                ValidationCaseCompare(
                    identity,
                    CaseOutcome.COMPARE_ERROR,
                    base_case,
                    cand_case,
                    warnings=health_warnings,
                    reason=str(exc),
                )
            )
            continue
        cases.append(
            ValidationCaseCompare(
                identity,
                CaseOutcome.COMPARED,
                base_case,
                cand_case,
                warnings=health_warnings + tuple(run_result.warnings),
                compare_result=run_result,
            )
        )

    return ValidationCompareResult(
        baseline=baseline,
        candidate=candidate,
        cases=tuple(cases),
        warnings=baseline.warnings + candidate.warnings,
    )


def write_validation_compare_artifacts(
    result: ValidationCompareResult, output_dir: Path
) -> list[Path]:
    """Write the stable JSON, concise Markdown, and per-case artifacts."""

    out = output_dir.expanduser().resolve()
    for bundle in (result.baseline, result.candidate):
        try:
            out.relative_to(bundle.root)
        except ValueError:
            continue
        raise ReportError(f"Validation comparison output must be outside input bundles: {out}")
    if out.exists():
        if not out.is_dir():
            raise ReportError(f"Validation comparison output is not a directory: {out}")
        if any(out.iterdir()):
            raise ReportError(f"Validation comparison output directory must be empty: {out}")
    else:
        out.mkdir(parents=True)

    case_documents: list[dict[str, object]] = []
    written: list[Path] = []
    case_root = out / "case_compares"
    for case in result.cases:
        artifact_refs: dict[str, str] = {}
        if case.compare_result is not None and case.baseline_case and case.candidate_case:
            slug = _identity_slug(case.identity)
            destination = case_root / slug
            base_ref = case.baseline_case.artifact("case_dir")
            cand_ref = case.candidate_case.artifact("case_dir")
            assert base_ref is not None and cand_ref is not None
            paths = write_compare_artifacts(
                case.compare_result,
                destination,
                source_dirs=(f"baseline/{base_ref.path}", f"candidate/{cand_ref.path}"),
                omit_empty_layers=True,
            )
            written.extend(paths)
            artifact_refs = {path.name: path.relative_to(out).as_posix() for path in paths}
        case_documents.append(_case_document(case, artifact_refs))

    json_path = out / "validation_compare.json"
    markdown_path = out / "validation_compare.md"
    document = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "baseline": _bundle_document(result.baseline),
        "candidate": _bundle_document(result.candidate),
        "warnings": list(result.warnings),
        "summary": result.summary,
        "cases": case_documents,
    }
    json_path.write_text(json.dumps(document, indent=2, default=str) + "\n")
    markdown_path.write_text(render_validation_compare_markdown(result))
    return [json_path, markdown_path, *written]


def render_validation_compare_markdown(result: ValidationCompareResult) -> str:
    summary = result.summary
    lines = [
        "# heliaPROFILER Validation Comparison",
        "",
        f"- total: **{summary['total']}**",
        f"- compared: **{summary['compared']}**",
        f"- incomplete: **{summary['total'] - summary['compared']}**",
        "",
        "| Case | Attempt | Outcome | Baseline | Candidate | Notes |",
        "|------|--------:|---------|----------|-----------|-------|",
    ]
    for case in result.cases:
        notes = case.reason or "; ".join(case.warnings)
        lines.append(
            "| {case} | {attempt} | {outcome} | {baseline} | {candidate} | {notes} |".format(
                case=_display_identity(case.identity),
                attempt=case.identity.attempt,
                outcome=case.outcome.value,
                baseline=case.baseline_case.status if case.baseline_case else "-",
                candidate=case.candidate_case.status if case.candidate_case else "-",
                notes=notes.replace("|", r"\|") if notes else "",
            )
        )
    return "\n".join(lines) + "\n"


def _missing_required(bundle: ValidationBundle, case: ValidationBundleCase, side: str) -> list[str]:
    missing: list[str] = []
    for name in _REQUIRED_ARTIFACTS:
        artifact = case.artifact(name)
        if artifact is None or not resolve_artifact(bundle, artifact).exists():
            missing.append(f"{side}.{name}")
    return missing


def _case_dir(bundle: ValidationBundle, case: ValidationBundleCase) -> Path:
    artifact = case.artifact("case_dir")
    if artifact is None:
        raise ReportError(f"Case {case.case_id!r} has no case_dir artifact")
    return resolve_artifact(bundle, artifact)


def _bundle_document(bundle: ValidationBundle) -> dict[str, object]:
    return {
        "schema_version": bundle.schema_version,
        "hpx_version": bundle.metadata.hpx_version,
        "generated_at": bundle.metadata.generated_at,
        "repo": {
            "sha": bundle.metadata.repo_sha,
            "branch": bundle.metadata.repo_branch,
            "dirty": bundle.metadata.repo_dirty,
        },
        "warnings": list(bundle.warnings),
    }


def _case_document(case: ValidationCaseCompare, artifact_refs: dict[str, str]) -> dict[str, object]:
    return {
        "identity": case.identity.to_dict(),
        "outcome": case.outcome.value,
        "reason": case.reason,
        "baseline_case_id": case.baseline_case.case_id if case.baseline_case else None,
        "baseline_status": case.baseline_case.status if case.baseline_case else None,
        "baseline_provenance": (
            dict(case.baseline_case.provenance) if case.baseline_case else None
        ),
        "candidate_case_id": case.candidate_case.case_id if case.candidate_case else None,
        "candidate_status": case.candidate_case.status if case.candidate_case else None,
        "candidate_provenance": (
            dict(case.candidate_case.provenance) if case.candidate_case else None
        ),
        "warnings": list(case.warnings),
        "artifacts": artifact_refs,
    }


def _identity_slug(identity: ValidationCaseIdentity) -> str:
    body = "-".join(
        [identity.board, identity.model_id, identity.engine, identity.toolchain, identity.transport]
    )
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", body).strip("-.") or "case"
    digest = hashlib.sha256(json.dumps(identity.to_dict(), sort_keys=True).encode()).hexdigest()[:8]
    return f"{safe[:96]}-{digest}-attempt{identity.attempt:02d}"


def _display_identity(identity: ValidationCaseIdentity) -> str:
    return f"{identity.board}/{identity.model_id}/{identity.engine}/{identity.toolchain}/{identity.transport}"
