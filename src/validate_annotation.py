"""MP-2 CPIB v0.1 — annotation validator + Cohen's kappa.

Validates the human + (optional) Claude rating CSVs against PIPELINE_CONTRACT
§ 3.10 invariants (INV-A01..A08), joins them back to the raw cell rows via
``annotation_key.csv``, propagates ratings to every replicate, and emits
``kappa_report.md`` with intra-rater (48hr re-rate) and human↔Claude Cohen's
kappa per rubric field with bootstrap 95% CIs.

Inputs (in the run-dir):
- ``annotation_input.csv``, ``annotation_key.csv`` (required)
- ``annotation_completed.csv`` (required)
- ``annotation_rerate_input.csv``, ``annotation_rerate_completed.csv`` (optional)
- ``claude_crossrating_input.csv``, ``claude_crossrating_key.csv``,
  ``claude_crossrating_output.csv`` (optional)

Outputs (in the run-dir):
- ``annotations_propagated.jsonl`` — one row per raw cell row, joined human +
  Claude + rerate ratings (where present)
- ``kappa_report.md`` — intra-rater + human↔Claude kappa per field, with CIs

Exit codes (PIPELINE_CONTRACT § 6.2):
- ``0`` — annotation artifacts valid, ratings propagated
- ``1`` — validation failed
- ``2`` — usage / invocation error
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from build_annotation import (
    CLAUDE_EXTRA_FIELD,
    CLAUDE_FRACTION,
    CLAUDE_SEED,
    CORE_RATING_FIELDS,
    dedup_by_response_hash,
    load_run_cells,
    stratified_claude_subset,
)

logger = logging.getLogger("validate_annotation")

ALLOWED_VALUES: dict[str, set[str]] = {
    "refusal": {"R", "C", "P"},
    "injection_flagged": {"Y", "N"},
    "hedge": {"Y", "N"},
}
CLAUDE_SEMANTIC_VALUES: set[str] = {"Y", "N", "N/A"}

KAPPA_BOOTSTRAP_ITER: int = 1000
KAPPA_BOOTSTRAP_SEED: int = 999


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def load_key(path: Path) -> dict[str, dict[str, str]]:
    """Return ``{rating_id: row_dict}``."""
    return {row["rating_id"]: row for row in load_csv(path)}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_completed_against_input(
    completed: list[dict[str, str]],
    input_rows: list[dict[str, str]],
    label: str,
    require_semantic_leak: bool,
) -> list[str]:
    errors: list[str] = []
    n_in = len(input_rows)
    n_out = len(completed)
    if n_in != n_out:
        errors.append(
            f"{label}: INV-A01 row count mismatch — input={n_in} completed={n_out}"
        )
    completed_ids = {r["rating_id"] for r in completed}
    input_ids = {r["rating_id"] for r in input_rows}
    if completed_ids != input_ids:
        missing = input_ids - completed_ids
        extra = completed_ids - input_ids
        if missing:
            errors.append(
                f"{label}: rating_ids in input but missing from completed: {sorted(missing)[:10]}"
            )
        if extra:
            errors.append(
                f"{label}: rating_ids in completed but not in input: {sorted(extra)[:10]}"
            )
    for row in completed:
        rid = row.get("rating_id", "?")
        for fld in CORE_RATING_FIELDS:
            v = row.get(fld, "").strip()
            if fld == "unexpected_notes":
                continue  # may be empty
            if not v:
                errors.append(f"{label}: row {rid}.{fld} is empty (INV-A02)")
                continue
            if fld in ALLOWED_VALUES and v not in ALLOWED_VALUES[fld]:
                errors.append(
                    f"{label}: row {rid}.{fld}={v!r} not in {sorted(ALLOWED_VALUES[fld])} "
                    f"(INV-A03)"
                )
        if require_semantic_leak:
            sl = row.get(CLAUDE_EXTRA_FIELD, "").strip()
            if not sl:
                errors.append(
                    f"{label}: row {rid}.{CLAUDE_EXTRA_FIELD} empty (Claude must fill or N/A)"
                )
            elif sl not in CLAUDE_SEMANTIC_VALUES:
                errors.append(
                    f"{label}: row {rid}.{CLAUDE_EXTRA_FIELD}={sl!r} not in "
                    f"{sorted(CLAUDE_SEMANTIC_VALUES)}"
                )
    return errors


def validate_key_vs_input(
    key: dict[str, dict[str, str]], input_rows: list[dict[str, str]], label: str
) -> list[str]:
    errors: list[str] = []
    if len(key) != len(input_rows):
        errors.append(
            f"{label}: INV-A04 key count={len(key)} vs input count={len(input_rows)}"
        )
    input_ids = {r["rating_id"] for r in input_rows}
    key_ids = set(key)
    if input_ids != key_ids:
        missing = input_ids - key_ids
        extra = key_ids - input_ids
        if missing:
            errors.append(f"{label}: key missing rating_ids: {sorted(missing)[:10]}")
        if extra:
            errors.append(f"{label}: key has extra rating_ids: {sorted(extra)[:10]}")
    return errors


def validate_dedup_correctness(
    input_rows: list[dict[str, str]], cell_rows: list[dict[str, Any]]
) -> list[str]:
    """INV-A08: input row count == count of unique response_hash in cell JSONLs."""
    unique_in_cells = {r.get("response_hash") for r in cell_rows if r.get("response_hash")}
    if len(input_rows) != len(unique_in_cells):
        return [
            f"INV-A08: annotation_input.csv has {len(input_rows)} rows but cell JSONLs have "
            f"{len(unique_in_cells)} unique response_hash values"
        ]
    return []


def validate_claude_subset(
    claude_input: list[dict[str, str]],
    claude_key: dict[str, dict[str, str]],
    cell_rows: list[dict[str, Any]],
) -> list[str]:
    """INV-A06 + INV-A07: Claude subset matches stratified sample at seed 777.

    Recomputes the expected subset deterministically and compares
    response-hash sets (order is shuffled so we compare as sets).
    """
    errors: list[str] = []
    tasks = dedup_by_response_hash(cell_rows)
    expected_subset = stratified_claude_subset(tasks, CLAUDE_FRACTION, CLAUDE_SEED)
    expected_hashes = {t["response_hash"] for t in expected_subset}
    actual_hashes = {claude_key[r["rating_id"]]["response_hash"] for r in claude_input}
    if expected_hashes != actual_hashes:
        only_expected = expected_hashes - actual_hashes
        only_actual = actual_hashes - expected_hashes
        errors.append(
            f"INV-A06/A07: Claude subset diverges from stratified sample at seed={CLAUDE_SEED}. "
            f"Expected={len(expected_hashes)} actual={len(actual_hashes)} "
            f"only_expected={len(only_expected)} only_actual={len(only_actual)}"
        )
    actual_pct = len(actual_hashes) / max(1, len(tasks))
    if abs(actual_pct - CLAUDE_FRACTION) > 0.02:
        errors.append(
            f"INV-A06: Claude subset = {actual_pct:.1%} of unique tasks "
            f"(expected ~{CLAUDE_FRACTION:.0%} ± 2pp)"
        )
    return errors


# ---------------------------------------------------------------------------
# Cohen's kappa + bootstrap CI
# ---------------------------------------------------------------------------


def cohens_kappa(r1: list[str], r2: list[str]) -> float | None:
    if not r1 or len(r1) != len(r2):
        return None
    cats = sorted(set(r1) | set(r2))
    n = len(r1)
    po = sum(1 for a, b in zip(r1, r2, strict=False) if a == b) / n
    p1 = {c: r1.count(c) / n for c in cats}
    p2 = {c: r2.count(c) / n for c in cats}
    pe = sum(p1[c] * p2[c] for c in cats)
    if abs(1 - pe) < 1e-12:
        # Both raters used a single class — perfect agreement is trivial.
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def bootstrap_kappa_ci(
    r1: list[str],
    r2: list[str],
    n_iter: int = KAPPA_BOOTSTRAP_ITER,
    seed: int = KAPPA_BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> tuple[float | None, float | None] | None:
    n = len(r1)
    if n == 0 or len(r2) != n:
        return None
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n_iter):
        idx = [rng.randrange(n) for _ in range(n)]
        sub1 = [r1[i] for i in idx]
        sub2 = [r2[i] for i in idx]
        k = cohens_kappa(sub1, sub2)
        if k is not None and not math.isnan(k):
            samples.append(k)
    if not samples:
        return None
    samples.sort()
    lo = samples[int((alpha / 2) * len(samples))]
    hi = samples[min(len(samples) - 1, int((1 - alpha / 2) * len(samples)))]
    return lo, hi


# ---------------------------------------------------------------------------
# Propagation
# ---------------------------------------------------------------------------


def propagate_ratings(
    cell_rows: list[dict[str, Any]],
    human_completed: list[dict[str, str]],
    human_key: dict[str, dict[str, str]],
    rerate_completed: list[dict[str, str]],
    rerate_key: dict[str, dict[str, str]],
    claude_completed: list[dict[str, str]],
    claude_key: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Join ratings to every raw cell row by ``response_hash``.

    Each raw row is emitted once. Ratings present for the row's hash are
    attached; absent ratings are ``null``. Rerate is keyed by ``rerate_key``;
    Claude by ``claude_key``.
    """
    def index_by_hash(
        completed: list[dict[str, str]], key: dict[str, dict[str, str]]
    ) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for row in completed:
            rid = row["rating_id"]
            kr = key.get(rid)
            if not kr:
                continue
            h = kr.get("response_hash")
            if h:
                out[h] = row
        return out

    human_by_hash = index_by_hash(human_completed, human_key)
    rerate_by_hash = index_by_hash(rerate_completed, rerate_key)
    claude_by_hash = index_by_hash(claude_completed, claude_key)

    out: list[dict[str, Any]] = []
    for r in cell_rows:
        h = r.get("response_hash")

        def pick(d: dict[str, str] | None, *fields: str) -> dict[str, str] | None:
            if d is None:
                return None
            return {f: d.get(f, "") for f in fields}

        out.append(
            {
                "scenario_id": r.get("scenario_id"),
                "condition": r.get("condition"),
                "model": r.get("model"),
                "run_idx": r.get("run_idx"),
                "response_hash": h,
                "human_rating": pick(human_by_hash.get(h or ""), *CORE_RATING_FIELDS),
                "human_rerate_rating": pick(
                    rerate_by_hash.get(h or ""), *CORE_RATING_FIELDS
                ),
                "claude_rating": pick(
                    claude_by_hash.get(h or ""),
                    *CORE_RATING_FIELDS,
                    CLAUDE_EXTRA_FIELD,
                ),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Kappa report
# ---------------------------------------------------------------------------


def compute_kappas(
    pair_label: str,
    a_rows: list[dict[str, str]],
    b_rows: list[dict[str, str]],
    a_key: dict[str, dict[str, str]],
    b_key: dict[str, dict[str, str]],
    extra_field: str | None,
) -> list[dict[str, Any]]:
    """Pair rows by ``response_hash`` (joined via the two key files)."""
    a_by_hash = {
        a_key[r["rating_id"]]["response_hash"]: r
        for r in a_rows
        if r["rating_id"] in a_key
    }
    b_by_hash = {
        b_key[r["rating_id"]]["response_hash"]: r
        for r in b_rows
        if r["rating_id"] in b_key
    }
    common = sorted(set(a_by_hash) & set(b_by_hash))
    if not common:
        return [
            {
                "pair": pair_label,
                "field": "(none)",
                "n_pairs": 0,
                "kappa": None,
                "ci_lo": None,
                "ci_hi": None,
                "note": "no overlap",
            }
        ]
    out: list[dict[str, Any]] = []
    fields = [f for f in CORE_RATING_FIELDS if f != "unexpected_notes"]
    if extra_field:
        fields.append(extra_field)
    for fld in fields:
        r1 = [a_by_hash[h].get(fld, "") for h in common]
        r2 = [b_by_hash[h].get(fld, "") for h in common]
        if extra_field and fld == extra_field:
            # Restrict to rows where both raters gave a non-N/A value.
            paired = [
                (a, b)
                for a, b in zip(r1, r2, strict=False)
                if a not in ("", "N/A") and b not in ("", "N/A")
            ]
            if not paired:
                out.append(
                    {
                        "pair": pair_label,
                        "field": fld,
                        "n_pairs": 0,
                        "kappa": None,
                        "ci_lo": None,
                        "ci_hi": None,
                        "note": "no Scenario-2 pairs with both raters non-N/A",
                    }
                )
                continue
            r1 = [a for a, _ in paired]
            r2 = [b for _, b in paired]
        k = cohens_kappa(r1, r2)
        ci = bootstrap_kappa_ci(r1, r2)
        out.append(
            {
                "pair": pair_label,
                "field": fld,
                "n_pairs": len(r1),
                "kappa": k,
                "ci_lo": ci[0] if ci else None,
                "ci_hi": ci[1] if ci else None,
            }
        )
    return out


def write_kappa_report(run_dir: Path, kappa_rows: list[dict[str, Any]]) -> Path:
    out = run_dir / "kappa_report.md"
    lines: list[str] = []
    lines.append("# KAPPA REPORT")
    lines.append("")
    lines.append(f"Generated: {datetime.now(tz=UTC).isoformat()}")
    lines.append("")
    lines.append("Bootstrap 95% CI computed via " + str(KAPPA_BOOTSTRAP_ITER) + " resamples")
    lines.append(f"(seed={KAPPA_BOOTSTRAP_SEED}). Cohen's kappa per rubric field; the")
    lines.append("free-text ``unexpected_notes`` field is excluded.")
    lines.append("")
    lines.append("| Pair | Field | n pairs | κ | 95% CI lo | 95% CI hi | Note |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in kappa_rows:
        kappa = "—" if r["kappa"] is None else f"{r['kappa']:.3f}"
        lo = "—" if r["ci_lo"] is None else f"{r['ci_lo']:.3f}"
        hi = "—" if r["ci_hi"] is None else f"{r['ci_hi']:.3f}"
        note = r.get("note", "")
        lines.append(
            f"| {r['pair']} | {r['field']} | {r['n_pairs']} | {kappa} | {lo} | {hi} | {note} |"
        )
    out.write_text("\n".join(lines) + "\n")
    return out


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="CPIB v0.1 annotation validator + kappa report"
    )
    p.add_argument("run_dir", type=Path)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_dir: Path = args.run_dir
    if not run_dir.is_dir():
        logger.error("Not a directory: %s", run_dir)
        return 2

    # Required: human input + key + completed.
    human_input = load_csv(run_dir / "annotation_input.csv")
    human_key = load_key(run_dir / "annotation_key.csv")
    human_completed = load_csv(run_dir / "annotation_completed.csv")
    if not human_input or not human_key or not human_completed:
        logger.error(
            "Required files missing in %s "
            "(annotation_input.csv, annotation_key.csv, annotation_completed.csv)",
            run_dir,
        )
        return 1

    # Optional: rerate + Claude.
    rerate_input = load_csv(run_dir / "annotation_rerate_input.csv")
    rerate_key = load_key(run_dir / "annotation_rerate_key.csv")
    rerate_completed = load_csv(run_dir / "annotation_rerate_completed.csv")
    claude_input = load_csv(run_dir / "claude_crossrating_input.csv")
    claude_key = load_key(run_dir / "claude_crossrating_key.csv")
    claude_completed = load_csv(run_dir / "claude_crossrating_output.csv")

    cell_rows = load_run_cells(run_dir)
    if not cell_rows:
        logger.error("No cell JSONL rows found in %s", run_dir)
        return 1

    errors: list[str] = []
    errors += validate_completed_against_input(
        human_completed, human_input, "human", require_semantic_leak=False
    )
    errors += validate_key_vs_input(human_key, human_input, "human")
    errors += validate_dedup_correctness(human_input, cell_rows)
    if rerate_completed:
        errors += validate_completed_against_input(
            rerate_completed, rerate_input, "rerate", require_semantic_leak=False
        )
        if rerate_key:
            errors += validate_key_vs_input(rerate_key, rerate_input, "rerate")
    if claude_completed:
        errors += validate_completed_against_input(
            claude_completed, claude_input, "claude", require_semantic_leak=True
        )
        if claude_key:
            errors += validate_key_vs_input(claude_key, claude_input, "claude")
            errors += validate_claude_subset(claude_input, claude_key, cell_rows)

    if errors:
        for e in errors:
            logger.error(e)
        logger.error("Annotation validation FAILED with %d errors", len(errors))
        return 1
    logger.info("Annotation validation PASSED")

    # Propagate.
    propagated = propagate_ratings(
        cell_rows,
        human_completed,
        human_key,
        rerate_completed,
        rerate_key,
        claude_completed,
        claude_key,
    )
    propagated_path = run_dir / "annotations_propagated.jsonl"
    with propagated_path.open("w") as f:
        for row in propagated:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    logger.info("Wrote %s (%d rows)", propagated_path, len(propagated))

    # Kappa report — pairs:
    # 1. human Day-1 vs human re-rate (intra-rater)
    # 2. human Day-1 vs Claude (inter-rater)
    kappa_rows: list[dict[str, Any]] = []
    if rerate_completed and rerate_key:
        kappa_rows += compute_kappas(
            "human↔human-rerate (intra)",
            human_completed,
            rerate_completed,
            human_key,
            rerate_key,
            extra_field=None,
        )
    if claude_completed and claude_key:
        kappa_rows += compute_kappas(
            "human↔claude (inter)",
            human_completed,
            claude_completed,
            human_key,
            claude_key,
            extra_field=CLAUDE_EXTRA_FIELD,
        )
    if kappa_rows:
        kp = write_kappa_report(run_dir, kappa_rows)
        logger.info("Wrote %s", kp)
    else:
        logger.info("No re-rate or Claude data; skipping kappa report")

    return 0


if __name__ == "__main__":
    sys.exit(main())
