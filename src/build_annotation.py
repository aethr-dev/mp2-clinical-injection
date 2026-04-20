"""MP-2 CPIB v0.1 — annotation input builder.

Three modes:

- ``default``: dedup all cell JSONL rows by ``response_hash`` (~56 unique
  tasks), blind-shuffle, write ``annotation_input.csv`` + ``annotation_key.csv``.
- ``rerate``: take a 20% subset of the unique-response set, reshuffle with a
  separate seed, write ``annotation_rerate_input.csv``. Used for the 48hr
  intra-rater reliability pass.
- ``claude``: 30% stratified subset (by ``condition × model``) with
  ``random.Random(seed=777)``, write ``claude_crossrating_input.csv`` with
  the additional ``semantic_leak_flagged`` column for Scenario 2 rows.

Refuses to run on a run-dir whose ``VALIDATION_REPORT.md`` has verdict
``FAIL`` unless ``--override-validation`` is passed (interactive
``OVERRIDE`` confirmation per PIPELINE_CONTRACT § 6.1).

Schemas: PIPELINE_CONTRACT § 2.8 / 2.9 / 2.11.

Usage:
    uv run python src/build_annotation.py results/run_2026-04-19_140000
    uv run python src/build_annotation.py results/run_2026-04-19_140000 --mode rerate
    uv run python src/build_annotation.py results/run_2026-04-19_140000 --mode claude
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("build_annotation")

# Pinned seeds. The Claude seed is locked in PRE-REGISTRATION § Claude
# cross-rating; the rerate seed is an implementation choice (not pinned in
# pre-reg). Both are recorded here so re-running is deterministic.
CLAUDE_SEED: int = 777
RERATE_SEED: int = 888

# Subset sizes.
RERATE_FRACTION: float = 0.20
CLAUDE_FRACTION: float = 0.30

# CSV header constants.
CORE_RATING_FIELDS: tuple[str, ...] = (
    "refusal",
    "injection_flagged",
    "hedge",
    "unexpected_notes",
)
CLAUDE_EXTRA_FIELD: str = "semantic_leak_flagged"


# ---------------------------------------------------------------------------
# Loading + validation gate (mirrors scorer.py)
# ---------------------------------------------------------------------------


def load_run_cells(run_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in sorted(run_dir.glob("scenario_*.jsonl")):
        with p.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def check_validation_gate(run_dir: Path, override: bool) -> bool:
    import re as _re

    report_path = run_dir / "VALIDATION_REPORT.md"
    if not report_path.exists():
        logger.error(
            "No VALIDATION_REPORT.md in %s. Run validate_run.py first.", run_dir
        )
        return False
    text = report_path.read_text()
    m = _re.search(r"\*\*Verdict:\*\* \*\*(\w[\w-]*)\*\*", text)
    verdict = m.group(1) if m else "UNKNOWN"
    if verdict in ("PASS", "PASS-WITH-WARNINGS"):
        logger.info("Validation gate: %s", verdict)
        return True
    if verdict != "FAIL":
        logger.error("Could not parse verdict from VALIDATION_REPORT.md")
        return False
    if not override:
        logger.error(
            "Run-dir VALIDATION_REPORT.md verdict is FAIL. "
            "Pass --override-validation and confirm with OVERRIDE to proceed."
        )
        return False
    if input("Type 'OVERRIDE' to proceed: ").strip() != "OVERRIDE":
        logger.error("Override aborted: confirmation text did not match")
        return False
    log = run_dir / "VALIDATION_OVERRIDES.log"
    with log.open("a") as f:
        f.write(
            json.dumps(
                {
                    "ts": datetime.now(tz=UTC).isoformat(),
                    "tool": "build_annotation.py",
                    "verdict": verdict,
                }
            )
            + "\n"
        )
    return True


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def dedup_by_response_hash(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse rows sharing a ``response_hash`` into one task record.

    Each task record carries:
    - ``response_hash`` (key)
    - ``response_text`` (the response — identical across replicates)
    - ``scenario_id``, ``condition``, ``model`` (from the first occurrence;
      identical responses across cells get attributed to whichever cell
      appeared first in the JSONL load order)
    - ``replicate_run_idxs``: list of ``"{scenario}/{condition}/{model}/run{idx}"``
      pointing back to every raw row sharing this hash
    """
    by_hash: dict[str, dict[str, Any]] = {}
    for r in rows:
        h = r.get("response_hash")
        if not h:
            continue
        sid = r.get("scenario_id", "")
        cond = r.get("condition", "")
        model = r.get("model", "")
        run_idx = r.get("run_idx", "?")
        replicate = f"{sid}/{cond}/{model}/run{run_idx}"
        if h not in by_hash:
            by_hash[h] = {
                "response_hash": h,
                "response_text": r.get("response", ""),
                "scenario_id": sid,
                "condition": cond,
                "model": model,
                "replicate_run_idxs": [replicate],
            }
        else:
            by_hash[h]["replicate_run_idxs"].append(replicate)
    return list(by_hash.values())


# ---------------------------------------------------------------------------
# Subsetting
# ---------------------------------------------------------------------------


def stratified_claude_subset(
    tasks: list[dict[str, Any]],
    fraction: float,
    seed: int,
) -> list[dict[str, Any]]:
    """30% sample stratified by ``(condition × model)``.

    Per stratum: take ``ceil(fraction * stratum_size)`` so every non-empty
    stratum contributes at least one row. The shared ``random.Random(seed)``
    instance is consumed sequentially per stratum (sorted) so the result is
    reproducible across machines.
    """
    rng = random.Random(seed)
    strata: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in tasks:
        strata[(t["condition"], t["model"])].append(t)

    selected: list[dict[str, Any]] = []
    for key in sorted(strata):
        stratum = strata[key]
        n = max(1, round(fraction * len(stratum))) if stratum else 0
        n = min(n, len(stratum))
        if n == 0:
            continue
        selected.extend(rng.sample(stratum, n))
    return selected


def rerate_subset(
    tasks: list[dict[str, Any]], fraction: float, seed: int
) -> list[dict[str, Any]]:
    """Flat 20% sample for the 48-hour re-rate pass."""
    rng = random.Random(seed)
    n = max(1, round(fraction * len(tasks))) if tasks else 0
    n = min(n, len(tasks))
    return rng.sample(tasks, n) if n else []


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------


def shuffled_with_blind_ids(
    tasks: list[dict[str, Any]], seed: int
) -> list[tuple[str, dict[str, Any]]]:
    """Shuffle tasks with the given seed, assign zero-padded ``r###`` IDs."""
    rng = random.Random(seed)
    indices = list(range(len(tasks)))
    rng.shuffle(indices)
    width = max(2, len(str(len(tasks))))
    out: list[tuple[str, dict[str, Any]]] = []
    for new_pos, orig_idx in enumerate(indices, start=1):
        rating_id = f"r{new_pos:0{width}d}"
        out.append((rating_id, tasks[orig_idx]))
    return out


def write_input_csv(
    path: Path,
    blinded: list[tuple[str, dict[str, Any]]],
    include_semantic_leak: bool,
) -> None:
    fields = ["rating_id", "response_text", *CORE_RATING_FIELDS]
    if include_semantic_leak:
        fields.append(CLAUDE_EXTRA_FIELD)
    with path.open("w", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(fields)
        for rating_id, task in blinded:
            row = [rating_id, task["response_text"]] + [""] * len(CORE_RATING_FIELDS)
            if include_semantic_leak:
                # N/A pre-filled for Scenario 1 rows so Claude only fills
                # Scenario 2 cells.
                if str(task.get("scenario_id", "")).startswith("scenario_01"):
                    row.append("N/A")
                else:
                    row.append("")
            w.writerow(row)


def write_key_csv(
    path: Path, blinded: list[tuple[str, dict[str, Any]]]
) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(
            [
                "rating_id",
                "scenario_id",
                "condition",
                "model",
                "response_hash",
                "replicate_run_idxs",
            ]
        )
        for rating_id, task in blinded:
            w.writerow(
                [
                    rating_id,
                    task["scenario_id"],
                    task["condition"],
                    task["model"],
                    task["response_hash"],
                    ";".join(task["replicate_run_idxs"]),
                ]
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CPIB v0.1 annotation input builder")
    p.add_argument("run_dir", type=Path)
    p.add_argument(
        "--mode",
        choices=("default", "rerate", "claude"),
        default="default",
    )
    p.add_argument(
        "--shuffle-seed",
        type=int,
        default=42,
        help="Seed for blind shuffle in default mode (rater never sees this)",
    )
    p.add_argument("--override-validation", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.run_dir.is_dir():
        logger.error("Not a directory: %s", args.run_dir)
        return 2
    if not check_validation_gate(args.run_dir, args.override_validation):
        return 1

    rows = load_run_cells(args.run_dir)
    if not rows:
        logger.error("No cell JSONL rows found in %s", args.run_dir)
        return 2
    tasks = dedup_by_response_hash(rows)
    logger.info("Deduped %d raw rows → %d unique tasks", len(rows), len(tasks))

    if args.mode == "default":
        blinded = shuffled_with_blind_ids(tasks, args.shuffle_seed)
        write_input_csv(
            args.run_dir / "annotation_input.csv", blinded, include_semantic_leak=False
        )
        write_key_csv(args.run_dir / "annotation_key.csv", blinded)
        logger.info(
            "Wrote annotation_input.csv (%d rows) + annotation_key.csv", len(blinded)
        )
        return 0

    if args.mode == "rerate":
        subset = rerate_subset(tasks, RERATE_FRACTION, RERATE_SEED)
        # Reshuffle with a different seed so positions don't match Day-1.
        blinded = shuffled_with_blind_ids(subset, RERATE_SEED + 1)
        write_input_csv(
            args.run_dir / "annotation_rerate_input.csv",
            blinded,
            include_semantic_leak=False,
        )
        logger.info(
            "Wrote annotation_rerate_input.csv (%d rows from %d unique, fraction=%.0f%%)",
            len(blinded),
            len(tasks),
            RERATE_FRACTION * 100,
        )
        return 0

    if args.mode == "claude":
        subset = stratified_claude_subset(tasks, CLAUDE_FRACTION, CLAUDE_SEED)
        blinded = shuffled_with_blind_ids(subset, CLAUDE_SEED + 1)
        write_input_csv(
            args.run_dir / "claude_crossrating_input.csv",
            blinded,
            include_semantic_leak=True,
        )
        # Claude pass also needs the key for downstream join, written under a
        # distinct name so it does not clobber the human key.
        write_key_csv(args.run_dir / "claude_crossrating_key.csv", blinded)
        logger.info(
            "Wrote claude_crossrating_input.csv + key (%d rows from %d unique, "
            "stratified, seed=%d)",
            len(blinded),
            len(tasks),
            CLAUDE_SEED,
        )
        return 0

    return 2  # unreachable


if __name__ == "__main__":
    sys.exit(main())
