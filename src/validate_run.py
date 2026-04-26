"""MP-2 CPIB v0.1 — pipeline contract enforcer.

Reads a completed (or in-progress) run-dir, runs every PIPELINE_CONTRACT § 3
invariant check against it, and emits ``VALIDATION_REPORT.md``. Downstream
analysis (``scorer.py``, ``analyze.py``) refuses to run on a run-dir whose
report's verdict is ``FAIL``.

Severity levels (PIPELINE_CONTRACT § 5):
- ``FAIL``: halts downstream analysis; verdict = ``FAIL``
- ``WARN``: reports but allows continuation; verdict = ``PASS-WITH-WARNINGS``
- ``INFO``: pure reporting (metrics, counts); does not affect verdict

Exit codes (PIPELINE_CONTRACT § 6.1):
- ``0`` — PASS or PASS-WITH-WARNINGS
- ``1`` — FAIL
- ``2`` — usage / invocation error

Usage:
    uv run python src/validate_run.py results/run_2026-04-19_140000
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from harness import (
    NUM_PREDICT,
    QWEN3_MODEL,
    SEEDS,
    TEMPERATURE,
    TOP_K,
    TOP_P,
    cell_filename,
    git_rev_parse_head,
    model_slug,
    sha256_file,
)

logger = logging.getLogger("validate_run")


class Severity(StrEnum):
    FAIL = "FAIL"
    WARN = "WARN"
    INFO = "INFO"


@dataclass
class CheckResult:
    rule: str
    severity: Severity
    message: str
    details: list[str] = field(default_factory=list)


@dataclass
class RunContext:
    """Loaded view of a run-dir for validation passes."""

    run_dir: Path
    manifest: dict[str, Any] | None
    configs: dict[tuple[str, str], dict[str, Any]]  # (model, scenario_id) -> snapshot
    cells: dict[tuple[str, str, str], list[dict[str, Any]]]  # (sid, model, cond) -> rows
    cell_paths: dict[tuple[str, str, str], Path]
    scenarios_dir: Path
    scenarios: dict[str, dict[str, Any]]  # scenario_id -> live YAML
    scenario_paths: dict[str, Path]  # scenario_id -> live path
    expected_n_runs: int


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


RUN_DIR_NAME_RE = re.compile(r"^run_\d{4}-\d{2}-\d{2}_\d{6}$")
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ISO_TZ_RE = re.compile(r"[+\-]\d{2}:\d{2}$|Z$")
CONFIG_NAME_RE = re.compile(r"^config_(.+)_(scenario_\d{2}_[a-z_]+)\.json$")
CELL_NAME_RE = re.compile(r"^(scenario_\d{2}_[a-z_]+)_(.+)_([a-z_0-9]+)\.jsonl$")


def load_run_context(run_dir: Path, scenarios_dir: Path) -> RunContext:
    """Load all artifacts in the run-dir + the live scenarios YAMLs."""
    manifest_path = run_dir / ".sweep_manifest.json"
    manifest: dict[str, Any] | None = None
    if manifest_path.exists():
        with manifest_path.open() as f:
            manifest = json.load(f)

    configs: dict[tuple[str, str], dict[str, Any]] = {}
    for p in sorted(run_dir.glob("config_*.json")):
        m = CONFIG_NAME_RE.match(p.name)
        if not m:
            continue
        model_slug_str, sid = m.group(1), m.group(2)
        with p.open() as f:
            snap = json.load(f)
        # Snapshot's own ``model`` field is more authoritative than parsed slug.
        model = snap.get("model") or model_slug_str
        configs[(model, sid)] = snap

    cells: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    cell_paths: dict[tuple[str, str, str], Path] = {}
    for p in sorted(run_dir.glob("scenario_*.jsonl")):
        m = CELL_NAME_RE.match(p.name)
        if not m:
            continue
        # Fallback scenario_id from the regex in case the file is
        # unparseable/empty. This may be wrong for names like
        # ``scenario_01_triage_mistral_7b_*`` where CELL_NAME_RE's greedy
        # ``[a-z_]+`` gobbles model-name fragments (e.g., "mistral",
        # "gemma") into the scenario group. Row fields (below) are the
        # authoritative source when available.
        sid = m.group(1)
        # Recover scenario_id, model, condition from the rows themselves to
        # be unambiguous — CELL_NAME_RE is inherently ambiguous for model
        # slugs containing common tokens (mistral, gemma) or dots
        # (llama3.1). Trust the row fields instead.
        rows: list[dict[str, Any]] = []
        try:
            with p.open() as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
        except json.JSONDecodeError:
            cells[(sid, "<unparseable>", p.name)] = []
            cell_paths[(sid, "<unparseable>", p.name)] = p
            continue
        if not rows:
            cells[(sid, "<empty>", p.name)] = []
            cell_paths[(sid, "<empty>", p.name)] = p
            continue
        # Override regex-parsed sid with authoritative row value.
        sid = rows[0].get("scenario_id", sid)
        model = rows[0].get("model", "<unknown>")
        condition = rows[0].get("condition", "<unknown>")
        cells[(sid, model, condition)] = rows
        cell_paths[(sid, model, condition)] = p

    scenarios: dict[str, dict[str, Any]] = {}
    scenario_paths: dict[str, Path] = {}
    for p in sorted(scenarios_dir.glob("scenario_*.yaml")):
        with p.open() as f:
            scn = yaml.safe_load(f)
        sid = scn.get("scenario", {}).get("id")
        if sid:
            scenarios[sid] = scn
            scenario_paths[sid] = p

    expected_n_runs = len(SEEDS)
    if manifest and isinstance(manifest.get("pinned"), dict):
        seeds = manifest["pinned"].get("seeds")
        if isinstance(seeds, list):
            expected_n_runs = len(seeds)

    return RunContext(
        run_dir=run_dir,
        manifest=manifest,
        configs=configs,
        cells=cells,
        cell_paths=cell_paths,
        scenarios_dir=scenarios_dir,
        scenarios=scenarios,
        scenario_paths=scenario_paths,
        expected_n_runs=expected_n_runs,
    )


# ---------------------------------------------------------------------------
# Structural (INV-S)
# ---------------------------------------------------------------------------


def check_run_dir_naming(ctx: RunContext) -> list[CheckResult]:
    if RUN_DIR_NAME_RE.match(ctx.run_dir.name):
        return [CheckResult("INV-S01", Severity.INFO, "Run dir name OK")]
    return [
        CheckResult(
            "INV-S01",
            Severity.FAIL,
            f"Run dir name {ctx.run_dir.name!r} does not match run_YYYY-MM-DD_HHMMSS",
        )
    ]


def check_sweep_manifest_exists(ctx: RunContext) -> list[CheckResult]:
    if ctx.manifest is None:
        # Single-cell smoke-test runs intentionally do not write a manifest;
        # downgrade to WARN. Sweep mode always writes one — its absence in
        # a sweep run-dir is itself a contract violation but we surface it
        # via the cell-coverage check (INV-S02), not here.
        return [
            CheckResult(
                "INV-S05",
                Severity.WARN,
                "No .sweep_manifest.json (single-cell mode or partial run)",
            )
        ]
    return [CheckResult("INV-S05", Severity.INFO, "Sweep manifest present")]


def check_expected_cell_files_exist(ctx: RunContext) -> list[CheckResult]:
    if ctx.manifest is None:
        return [
            CheckResult(
                "INV-S02",
                Severity.WARN,
                "Cannot verify cell coverage: no manifest",
            )
        ]
    expected = {
        (c["scenario"], c["model"], c["condition"])
        for c in ctx.manifest.get("cells_completed", [])
    }
    actual = set(ctx.cells.keys())
    missing = expected - actual
    if missing:
        details = [f"  {sid}/{m}/{cond}" for sid, m, cond in sorted(missing)]
        return [
            CheckResult(
                "INV-S02",
                Severity.FAIL,
                f"{len(missing)} cells declared completed in manifest but missing on disk",
                details,
            )
        ]
    return [CheckResult("INV-S02", Severity.INFO, f"All {len(expected)} expected cells present")]


def check_expected_config_files_exist(ctx: RunContext) -> list[CheckResult]:
    if ctx.manifest is None:
        return [
            CheckResult(
                "INV-S03",
                Severity.WARN,
                "Cannot verify config coverage: no manifest",
            )
        ]
    expected = {
        (m, sid)
        for m in ctx.manifest.get("models", [])
        for sid in ctx.manifest.get("scenarios", {})
    }
    actual = set(ctx.configs.keys())
    missing = expected - actual
    if missing:
        details = [f"  config_{model_slug(m)}_{sid}.json" for m, sid in sorted(missing)]
        return [
            CheckResult(
                "INV-S03",
                Severity.FAIL,
                f"{len(missing)} config snapshots missing",
                details,
            )
        ]
    return [
        CheckResult("INV-S03", Severity.INFO, f"All {len(expected)} config snapshots present")
    ]


def check_no_stray_tmp_files(ctx: RunContext) -> list[CheckResult]:
    tmps = list(ctx.run_dir.glob("*.tmp"))
    if tmps:
        return [
            CheckResult(
                "INV-S04",
                Severity.WARN,
                f"{len(tmps)} stray .tmp files (atomic-write residue)",
                [f"  {p.name}" for p in tmps],
            )
        ]
    return [CheckResult("INV-S04", Severity.INFO, "No stray .tmp files")]


def check_file_size_bounds(ctx: RunContext) -> list[CheckResult]:
    huge: list[str] = []
    for p in ctx.run_dir.iterdir():
        if p.is_file() and p.stat().st_size > 10 * 1024 * 1024:
            huge.append(f"  {p.name}: {p.stat().st_size // (1024 * 1024)}MB")
    if huge:
        return [
            CheckResult(
                "INV-S06",
                Severity.WARN,
                f"{len(huge)} files exceed 10MB",
                huge,
            )
        ]
    return [CheckResult("INV-S06", Severity.INFO, "All run-dir files within 10MB bound")]


# ---------------------------------------------------------------------------
# Schema (INV-SC)
# ---------------------------------------------------------------------------


def check_jsonl_rows_parseable(ctx: RunContext) -> list[CheckResult]:
    """INV-SC01: every JSONL cell file parsed as valid JSON.

    Loader (``load_run_context``) catches ``JSONDecodeError`` and registers
    affected cells with a sentinel model key (``<unparseable>`` /
    ``<empty>``). This check surfaces those sentinels as a named FAIL so
    the violation appears in the contract-rule table.
    """
    bad: list[str] = []
    for (_sid, model, _cond), path in ctx.cell_paths.items():
        if model in ("<unparseable>", "<empty>"):
            bad.append(f"  {path.name}: {model}")
    if bad:
        return [
            CheckResult(
                "INV-SC01",
                Severity.FAIL,
                f"{len(bad)} cell JSONL files unparseable or empty",
                bad,
            )
        ]
    return [CheckResult("INV-SC01", Severity.INFO, "All cell JSONLs parse cleanly")]


REQUIRED_ROW_FIELDS: tuple[str, ...] = (
    "scenario_id",
    "condition",
    "model",
    "model_digest",
    "run_idx",
    "seed",
    "is_multi_turn",
    "system",
    "messages",
    "response",
    "response_turns",
    "response_turn_hashes",
    "timestamp",
    "prompt_hash",
    "response_hash",
    "run_fingerprint",
    "ollama_eval_count",
    "ollama_total_duration",
    "num_predict",
)


def check_jsonl_required_fields(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        for i, row in enumerate(rows):
            missing = [f for f in REQUIRED_ROW_FIELDS if f not in row]
            if missing:
                failures.append(
                    f"  {sid}/{model}/{cond} row[{i}]: missing {missing}"
                )
    if failures:
        return [
            CheckResult(
                "INV-SC02",
                Severity.FAIL,
                f"{len(failures)} rows missing required fields",
                failures,
            )
        ]
    return [CheckResult("INV-SC02", Severity.INFO, "All rows have required fields")]


def check_jsonl_field_types(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        for i, row in enumerate(rows):
            if not isinstance(row.get("scenario_id"), str):
                failures.append(f"  {sid}/{model}/{cond} row[{i}]: scenario_id not str")
            if not isinstance(row.get("condition"), str):
                failures.append(f"  {sid}/{model}/{cond} row[{i}]: condition not str")
            if not isinstance(row.get("model"), str):
                failures.append(f"  {sid}/{model}/{cond} row[{i}]: model not str")
            if not isinstance(row.get("run_idx"), int):
                failures.append(f"  {sid}/{model}/{cond} row[{i}]: run_idx not int")
            if not isinstance(row.get("seed"), int):
                failures.append(f"  {sid}/{model}/{cond} row[{i}]: seed not int")
            if not isinstance(row.get("is_multi_turn"), bool):
                failures.append(f"  {sid}/{model}/{cond} row[{i}]: is_multi_turn not bool")
            if not isinstance(row.get("messages"), list):
                failures.append(f"  {sid}/{model}/{cond} row[{i}]: messages not list")
            if not isinstance(row.get("response"), str):
                failures.append(f"  {sid}/{model}/{cond} row[{i}]: response not str")
    if failures:
        return [
            CheckResult(
                "INV-SC03",
                Severity.FAIL,
                f"{len(failures)} type violations",
                failures,
            )
        ]
    return [CheckResult("INV-SC03", Severity.INFO, "All field types OK")]


def check_hash_format(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        for i, row in enumerate(rows):
            for fld in ("prompt_hash", "response_hash", "run_fingerprint", "model_digest"):
                v = row.get(fld)
                if isinstance(v, str) and not HASH_RE.match(v):
                    failures.append(
                        f"  {sid}/{model}/{cond} row[{i}].{fld}: bad format {v!r}"
                    )
            turn_hashes = row.get("response_turn_hashes")
            if isinstance(turn_hashes, list):
                for j, h in enumerate(turn_hashes):
                    if not isinstance(h, str) or not HASH_RE.match(h):
                        failures.append(
                            f"  {sid}/{model}/{cond} row[{i}].response_turn_hashes[{j}]: bad format"
                        )
    if failures:
        return [
            CheckResult(
                "INV-SC04",
                Severity.FAIL,
                f"{len(failures)} bad hash formats",
                failures,
            )
        ]
    return [CheckResult("INV-SC04", Severity.INFO, "All hashes match sha256:<64hex>")]


def check_timestamp_format(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        for i, row in enumerate(rows):
            ts = row.get("timestamp")
            if not isinstance(ts, str) or not ISO_TZ_RE.search(ts):
                failures.append(
                    f"  {sid}/{model}/{cond} row[{i}].timestamp: missing TZ suffix in {ts!r}"
                )
                continue
            try:
                datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                failures.append(
                    f"  {sid}/{model}/{cond} row[{i}].timestamp: unparseable {ts!r}"
                )
    if failures:
        return [
            CheckResult(
                "INV-SC05",
                Severity.FAIL,
                f"{len(failures)} bad timestamps",
                failures,
            )
        ]
    return [CheckResult("INV-SC05", Severity.INFO, "All timestamps ISO 8601 with TZ")]


# ---------------------------------------------------------------------------
# Pinned values (INV-V)
# ---------------------------------------------------------------------------


def check_pinned_values(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for (model, sid), snap in ctx.configs.items():
        prefix = f"  config[{model}/{sid}]"
        if snap.get("temperature") != TEMPERATURE:
            failures.append(
                f"{prefix}.temperature={snap.get('temperature')!r} (expected {TEMPERATURE})"
            )
        if snap.get("top_p") != TOP_P:
            failures.append(f"{prefix}.top_p={snap.get('top_p')!r} (expected {TOP_P})")
        if snap.get("top_k") != TOP_K:
            failures.append(f"{prefix}.top_k={snap.get('top_k')!r} (expected {TOP_K})")
        if snap.get("num_predict") != NUM_PREDICT:
            failures.append(
                f"{prefix}.num_predict={snap.get('num_predict')!r} (expected {NUM_PREDICT})"
            )
        n = snap.get("n_runs", len(SEEDS))
        expected_seeds = SEEDS[:n]
        if snap.get("seeds") != expected_seeds:
            failures.append(f"{prefix}.seeds={snap.get('seeds')!r} (expected {expected_seeds})")
        if snap.get("ollama_host") != "REDACTED":
            failures.append(
                f"{prefix}.ollama_host={snap.get('ollama_host')!r} (expected 'REDACTED')"
            )
        if model == QWEN3_MODEL and snap.get("qwen3_thinking_mode") is not False:
            failures.append(
                f"{prefix}.qwen3_thinking_mode={snap.get('qwen3_thinking_mode')!r} (expected False)"
            )
    if failures:
        return [
            CheckResult(
                "INV-V01-V07",
                Severity.FAIL,
                f"{len(failures)} pinned-value violations",
                failures,
            )
        ]
    return [CheckResult("INV-V01-V07", Severity.INFO, "All pinned values match locked spec")]


# ---------------------------------------------------------------------------
# Cross-row consistency (INV-C)
# ---------------------------------------------------------------------------


def _cell_expected_n(ctx: RunContext, sid: str, model: str) -> int:
    """Per-cell expected row count: prefer config.n_runs, then manifest, then SEEDS."""
    snap = ctx.configs.get((model, sid))
    if snap and isinstance(snap.get("n_runs"), int):
        return snap["n_runs"]
    return ctx.expected_n_runs


def check_n_rows_per_cell(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for key, rows in ctx.cells.items():
        sid, model, cond = key
        n = _cell_expected_n(ctx, sid, model)
        if len(rows) != n:
            failures.append(f"  {sid}/{model}/{cond}: {len(rows)} rows (expected {n})")
    if failures:
        return [
            CheckResult(
                "INV-C01",
                Severity.FAIL,
                f"{len(failures)} cells have wrong row count",
                failures,
            )
        ]
    return [CheckResult("INV-C01", Severity.INFO, "All cells have expected row count")]


def check_run_idx_complete(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        n = _cell_expected_n(ctx, sid, model)
        expected = set(range(n))
        idxs = [r.get("run_idx") for r in rows]
        if set(idxs) != expected or len(idxs) != len(set(idxs)):
            failures.append(f"  {sid}/{model}/{cond}: run_idx={idxs} (expected 0..{n - 1})")
    if failures:
        return [
            CheckResult(
                "INV-C02",
                Severity.FAIL,
                f"{len(failures)} cells have run_idx gaps/dupes",
                failures,
            )
        ]
    return [CheckResult("INV-C02", Severity.INFO, "All cells have complete run_idx 0..n-1")]


def check_seed_sequence(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        for r in rows:
            ri = r.get("run_idx")
            seed = r.get("seed")
            if isinstance(ri, int) and 0 <= ri < len(SEEDS) and seed != SEEDS[ri]:
                failures.append(
                    f"  {sid}/{model}/{cond} run_idx={ri}: seed={seed} expected={SEEDS[ri]}"
                )
    if failures:
        return [
            CheckResult(
                "INV-C03",
                Severity.FAIL,
                f"{len(failures)} seed mismatches",
                failures,
            )
        ]
    return [CheckResult("INV-C03", Severity.INFO, "All seeds match SEEDS[run_idx]")]


def check_model_digest_within_cell(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        digests = {r.get("model_digest") for r in rows}
        if len(digests) > 1:
            failures.append(
                f"  {sid}/{model}/{cond}: {len(digests)} distinct model_digests within cell"
            )
    if failures:
        return [
            CheckResult(
                "INV-C04",
                Severity.FAIL,
                f"{len(failures)} cells with mixed model_digests",
                failures,
            )
        ]
    return [CheckResult("INV-C04", Severity.INFO, "model_digest constant within each cell")]


def check_prompt_hash_within_cell(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        hashes = {r.get("prompt_hash") for r in rows}
        if len(hashes) > 1:
            failures.append(
                f"  {sid}/{model}/{cond}: {len(hashes)} distinct prompt_hashes within cell"
            )
    if failures:
        return [
            CheckResult(
                "INV-C05",
                Severity.FAIL,
                f"{len(failures)} cells with mixed prompt_hashes",
                failures,
            )
        ]
    return [CheckResult("INV-C05", Severity.INFO, "prompt_hash constant within each cell")]


def check_num_predict_constant(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        for r in rows:
            if r.get("num_predict") != NUM_PREDICT:
                failures.append(
                    f"  {sid}/{model}/{cond} run_idx={r.get('run_idx')}: "
                    f"num_predict={r.get('num_predict')!r}"
                )
    if failures:
        return [
            CheckResult(
                "INV-C06",
                Severity.FAIL,
                f"{len(failures)} rows with wrong num_predict",
                failures,
            )
        ]
    return [CheckResult("INV-C06", Severity.INFO, f"All rows num_predict={NUM_PREDICT}")]


# ---------------------------------------------------------------------------
# Cross-file (INV-X)
# ---------------------------------------------------------------------------


def check_digest_jsonl_vs_config(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        snap = ctx.configs.get((model, sid))
        if not snap:
            continue
        config_digest = snap.get("model_digest")
        for r in rows:
            if r.get("model_digest") != config_digest:
                failures.append(
                    f"  {sid}/{model}/{cond} row[{r.get('run_idx')}]: "
                    f"model_digest={r.get('model_digest')!r} config={config_digest!r}"
                )
    if failures:
        return [
            CheckResult(
                "INV-X01",
                Severity.FAIL,
                f"{len(failures)} model_digest mismatches between rows and config",
                failures,
            )
        ]
    return [CheckResult("INV-X01", Severity.INFO, "model_digest matches config for all rows")]


def check_digest_config_vs_manifest(ctx: RunContext) -> list[CheckResult]:
    if ctx.manifest is None:
        return [
            CheckResult("INV-X02", Severity.WARN, "Cannot verify: no manifest")
        ]
    failures: list[str] = []
    manifest_digests = ctx.manifest.get("model_digests", {})
    for (model, sid), snap in ctx.configs.items():
        m_digest = manifest_digests.get(model)
        if m_digest and snap.get("model_digest") != m_digest:
            failures.append(
                f"  config[{model}/{sid}]={snap.get('model_digest')!r} "
                f"manifest={m_digest!r}"
            )
    if failures:
        return [
            CheckResult(
                "INV-X02",
                Severity.FAIL,
                f"{len(failures)} model_digest mismatches between config and manifest",
                failures,
            )
        ]
    return [CheckResult("INV-X02", Severity.INFO, "model_digest matches manifest for all configs")]


def check_scenario_yaml_sha(ctx: RunContext) -> list[CheckResult]:
    """INV-X03 + INV-X04 collapsed: snapshot SHA + manifest SHA both vs live YAML."""
    failures: list[str] = []
    live_shas = {sid: sha256_file(p) for sid, p in ctx.scenario_paths.items()}
    for (model, sid), snap in ctx.configs.items():
        live = live_shas.get(sid)
        snap_sha = snap.get("scenario_yaml_sha256")
        if live and snap_sha and snap_sha != live:
            failures.append(
                f"  config[{model}/{sid}]={snap_sha!r} live={live!r}"
            )
    if ctx.manifest:
        for sid, sblock in ctx.manifest.get("scenarios", {}).items():
            live = live_shas.get(sid)
            m_sha = sblock.get("sha256")
            if live and m_sha and m_sha != live:
                failures.append(f"  manifest[{sid}]={m_sha!r} live={live!r}")
    if failures:
        return [
            CheckResult(
                "INV-X03/X04",
                Severity.FAIL,
                f"{len(failures)} scenario_yaml_sha256 drifts",
                failures,
            )
        ]
    return [CheckResult("INV-X03/X04", Severity.INFO, "Scenario YAML SHAs match live files")]


def check_harness_git_sha(ctx: RunContext) -> list[CheckResult]:
    if ctx.manifest is None:
        return [CheckResult("INV-X05", Severity.WARN, "Cannot verify: no manifest")]
    live = git_rev_parse_head()
    manifest_sha = ctx.manifest.get("harness_git_sha")
    if manifest_sha == "uncommitted":
        return [
            CheckResult(
                "INV-X05",
                Severity.WARN,
                "Manifest harness_git_sha=uncommitted (sweep ran on dirty/untagged tree)",
            )
        ]
    if live != manifest_sha:
        return [
            CheckResult(
                "INV-X05",
                Severity.FAIL,
                f"harness_git_sha drift: manifest={manifest_sha!r} live={live!r}",
            )
        ]
    return [CheckResult("INV-X05", Severity.INFO, f"harness_git_sha matches HEAD ({live[:8]})")]


def check_condition_coverage(ctx: RunContext) -> list[CheckResult]:
    """INV-X06: every condition in scenario YAML appears in a cell, and vice versa.

    Cells→YAML direction (every observed condition exists in the YAML) is
    always required. YAML→cells direction (every YAML condition observed in
    cells) only applies in sweep mode — single-cell smoke-tests legitimately
    target a subset, so we downgrade that arm to a WARN when no manifest
    exists.
    """
    failures: list[str] = []
    sweep_warns: list[str] = []
    for sid, scn in ctx.scenarios.items():
        yaml_conds = set(scn.get("conditions", {}).keys())
        cell_conds = {cond for (s, _m, cond) in ctx.cells if s == sid}
        unknown = cell_conds - yaml_conds
        if unknown:
            failures.append(f"  {sid}: cells use conditions not in YAML: {sorted(unknown)}")
        if cell_conds:
            absent = yaml_conds - cell_conds
            if absent:
                line = f"  {sid}: YAML conditions never observed in cells: {sorted(absent)}"
                if ctx.manifest is None:
                    sweep_warns.append(line)
                else:
                    failures.append(line)
    results: list[CheckResult] = []
    if failures:
        results.append(
            CheckResult(
                "INV-X06",
                Severity.FAIL,
                f"{len(failures)} condition coverage mismatches",
                failures,
            )
        )
    if sweep_warns:
        results.append(
            CheckResult(
                "INV-X06",
                Severity.WARN,
                f"{len(sweep_warns)} YAML conditions absent from cells "
                f"(expected in single-cell mode; would FAIL under sweep)",
                sweep_warns,
            )
        )
    if not results:
        results.append(
            CheckResult(
                "INV-X06", Severity.INFO, "Condition coverage YAML↔cells consistent"
            )
        )
    return results


# ---------------------------------------------------------------------------
# Content sanity (INV-CS)
# ---------------------------------------------------------------------------


def check_no_harness_errors(ctx: RunContext) -> list[CheckResult]:
    error_rows: list[str] = []
    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for (sid, model, cond), rows in ctx.cells.items():
        for r in rows:
            resp = r.get("response", "")
            if isinstance(resp, str) and resp.startswith("__HARNESS_ERROR__"):
                error_rows.append(
                    f"  {sid}/{model}/{cond} run_idx={r.get('run_idx')}: {resp[:120]}"
                )
                counts[(sid, model, cond)] += 1
    if error_rows:
        return [
            CheckResult(
                "INV-CS01",
                Severity.WARN,
                f"{len(error_rows)} __HARNESS_ERROR__ rows across {len(counts)} cells",
                error_rows,
            )
        ]
    return [CheckResult("INV-CS01", Severity.INFO, "Zero __HARNESS_ERROR__ rows")]


def check_response_nonempty(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        for r in rows:
            resp = r.get("response", "")
            if isinstance(resp, str) and resp.startswith("__HARNESS_ERROR__"):
                continue  # excluded — covered by INV-CS01
            if not resp:
                failures.append(
                    f"  {sid}/{model}/{cond} run_idx={r.get('run_idx')}: empty response"
                )
    if failures:
        return [
            CheckResult(
                "INV-CS02",
                Severity.FAIL,
                f"{len(failures)} non-error rows have empty response",
                failures,
            )
        ]
    return [CheckResult("INV-CS02", Severity.INFO, "All non-error rows have non-empty response")]


def check_response_determinism(ctx: RunContext) -> list[CheckResult]:
    """INV-CS03: report unique-response ratio per cell. WARN if <1.0 at temp=0."""
    nondet: list[str] = []
    info_lines: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        if not rows:
            continue
        hashes = {r.get("response_hash") for r in rows}
        ratio = len(hashes) / len(rows)
        info_lines.append(
            f"  {sid}/{model}/{cond}: {len(hashes)}/{len(rows)} unique = {ratio:.2f}"
        )
        if ratio < 1.0:
            nondet.append(f"  {sid}/{model}/{cond}: ratio={ratio:.2f}")
    results: list[CheckResult] = []
    if nondet:
        results.append(
            CheckResult(
                "INV-CS03",
                Severity.WARN,
                f"{len(nondet)} cells show response non-determinism (GPU-kernel residual)",
                nondet,
            )
        )
    else:
        results.append(
            CheckResult(
                "INV-CS03",
                Severity.INFO,
                "All cells fully deterministic (ratio=1.0)",
            )
        )
    results.append(
        CheckResult("INV-CS03-detail", Severity.INFO, "Per-cell determinism", info_lines)
    )
    return results


def check_scenario_2_turn_count(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        if not sid.startswith("scenario_02"):
            continue
        for r in rows:
            resp = r.get("response", "")
            if isinstance(resp, str) and resp.startswith("__HARNESS_ERROR__"):
                continue
            turns = r.get("response_turns")
            if not isinstance(turns, list) or len(turns) not in (1, 2, 3):
                failures.append(
                    f"  {sid}/{model}/{cond} run_idx={r.get('run_idx')}: "
                    f"response_turns={turns!r} (expected 3, or 1/2 if incomplete)"
                )
    if failures:
        return [
            CheckResult(
                "INV-CS04",
                Severity.FAIL,
                f"{len(failures)} Scenario-2 rows with bad turn count",
                failures,
            )
        ]
    return [CheckResult("INV-CS04", Severity.INFO, "Scenario-2 multi-turn turn counts OK")]


# ---------------------------------------------------------------------------
# Scenario-specific (INV-S1, INV-S2)
# ---------------------------------------------------------------------------


def check_scenario_1_single_turn_flags(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        if not sid.startswith("scenario_01"):
            continue
        for r in rows:
            if r.get("is_multi_turn") is not False:
                failures.append(
                    f"  {sid}/{model}/{cond} run_idx={r.get('run_idx')}: "
                    f"is_multi_turn={r.get('is_multi_turn')!r}"
                )
            if r.get("response_turns") is not None:
                failures.append(
                    f"  {sid}/{model}/{cond} run_idx={r.get('run_idx')}: "
                    f"response_turns not None"
                )
    if failures:
        return [
            CheckResult(
                "INV-S1-01",
                Severity.FAIL,
                f"{len(failures)} Scenario-1 rows with wrong multi-turn flags",
                failures,
            )
        ]
    return [CheckResult("INV-S1-01", Severity.INFO, "Scenario-1 multi-turn flags OK")]


def check_scenario_1_regex_extracts(ctx: RunContext) -> list[CheckResult]:
    """INV-S1-02 + INV-S1-03 combined: regex matches extract a 1-5 integer."""
    info_lines: list[str] = []
    out_of_range: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        if not sid.startswith("scenario_01"):
            continue
        scn = ctx.scenarios.get(sid)
        if not scn:
            continue
        per_cond_regex = (
            scn.get("classification", {}).get("per_condition_regex", {}).get(cond)
        )
        if not per_cond_regex:
            info_lines.append(f"  {sid}/{model}/{cond}: no per_condition_regex")
            continue
        pat = re.compile(per_cond_regex)
        parsed = 0
        for r in rows:
            resp = r.get("response", "")
            if isinstance(resp, str) and resp.startswith("__HARNESS_ERROR__"):
                continue
            matches = pat.findall(resp) if isinstance(resp, str) else []
            if matches:
                # multiple_matches_rule: take_final
                try:
                    val = int(matches[-1])
                except (TypeError, ValueError):
                    continue
                if val < 1 or val > 5:
                    out_of_range.append(
                        f"  {sid}/{model}/{cond} run_idx={r.get('run_idx')}: extracted={val}"
                    )
                else:
                    parsed += 1
        info_lines.append(
            f"  {sid}/{model}/{cond}: {parsed}/{len(rows)} rows parsed in 1-5 range"
        )
    results: list[CheckResult] = []
    if out_of_range:
        results.append(
            CheckResult(
                "INV-S1-03",
                Severity.WARN,
                f"{len(out_of_range)} Scenario-1 rows had extracted value outside 1-5",
                out_of_range,
            )
        )
    else:
        results.append(
            CheckResult(
                "INV-S1-03",
                Severity.INFO,
                "All Scenario-1 extracted values within 1-5",
            )
        )
    results.append(
        CheckResult("INV-S1-02", Severity.INFO, "Scenario-1 regex extraction summary", info_lines)
    )
    return results


def check_scenario_2_multi_turn(ctx: RunContext) -> list[CheckResult]:
    failures: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        if not sid.startswith("scenario_02"):
            continue
        for r in rows:
            if r.get("is_multi_turn") is not True:
                failures.append(
                    f"  {sid}/{model}/{cond} run_idx={r.get('run_idx')}: "
                    f"is_multi_turn={r.get('is_multi_turn')!r}"
                )
            resp = r.get("response", "")
            if isinstance(resp, str) and resp.startswith("__HARNESS_ERROR__"):
                continue
            hashes = r.get("response_turn_hashes")
            if not isinstance(hashes, list):
                failures.append(
                    f"  {sid}/{model}/{cond} run_idx={r.get('run_idx')}: "
                    f"response_turn_hashes not a list"
                )
                continue
            if len(hashes) not in (1, 2, 3):
                failures.append(
                    f"  {sid}/{model}/{cond} run_idx={r.get('run_idx')}: "
                    f"{len(hashes)} hashes (expected 1-3)"
                )
    if failures:
        return [
            CheckResult(
                "INV-S2-02/03",
                Severity.FAIL,
                f"{len(failures)} Scenario-2 multi-turn structural violations",
                failures,
            )
        ]
    return [
        CheckResult("INV-S2-02/03", Severity.INFO, "Scenario-2 multi-turn structure OK")
    ]


def check_scenario_2_anchor_collision(ctx: RunContext) -> list[CheckResult]:
    """INV-S2-01 (preflight): no anchor from entity N appears in entity M≠N's content.

    Examines the scenario YAML's anchors against the YAML's chart/ticket text
    blocks. Run pre-sweep (per spec) and again post-sweep here as a sanity
    re-check against drift.
    """
    failures: list[str] = []
    sid = "scenario_02_summarization"
    scn = ctx.scenarios.get(sid)
    if not scn:
        return [CheckResult("INV-S2-01", Severity.INFO, "Scenario 2 not loaded; skipping")]
    classification = scn.get("classification", {})
    domain_to_anchors = {
        "clinical": classification.get("anchors_clinical", {}),
        "non_clinical": classification.get("anchors_non_clinical", {}),
    }
    # The YAML's ``turn_structure.entity_per_turn`` only carries the clinical
    # entity IDs (with the non-clinical IDs mentioned in inline comments).
    # Derive a per-domain turn→entity map from the ordered keys of each
    # anchors block so non-clinical haystacks key against A0001/A0002/A0003
    # (not 10042/10158/10229).
    domain_entity_per_turn: dict[str, dict[int, str]] = {
        "clinical": {
            i + 1: str(eid)
            for i, eid in enumerate(classification.get("anchors_clinical", {}))
        },
        "non_clinical": {
            i + 1: str(eid)
            for i, eid in enumerate(classification.get("anchors_non_clinical", {}))
        },
    }
    # Build a haystack per (domain, entity_id) by serialising every condition's
    # turns; we then check entity N's anchors against entity M's haystack.
    domain_assignment = classification.get("domain_assignment", {})
    haystacks: dict[tuple[str, str], str] = {}
    for cond_name, cond_block in scn.get("conditions", {}).items():
        domain = domain_assignment.get(cond_name)
        if domain not in ("clinical", "non_clinical"):
            continue
        turns = cond_block.get("turns") or []
        entity_per_turn = domain_entity_per_turn[domain]
        for i, turn in enumerate(turns, start=1):
            content = turn.get("content", "")
            entity_id = entity_per_turn.get(i)
            if entity_id:
                haystacks.setdefault((domain, entity_id), "")
                haystacks[(domain, entity_id)] += "\n" + content
    for domain, anchors_by_entity in domain_to_anchors.items():
        for entity_n, anchors in anchors_by_entity.items():
            for entity_m, hay in haystacks.items():
                if entity_m[0] != domain or entity_m[1] == entity_n:
                    continue
                hay_lower = hay.lower()
                for a in anchors:
                    pat = re.compile(rf"\b{re.escape(a)}\b", re.IGNORECASE)
                    if pat.search(hay_lower):
                        failures.append(
                            f"  {domain}: anchor {a!r} from entity {entity_n} "
                            f"found in entity {entity_m[1]}'s content"
                        )
    if failures:
        return [
            CheckResult(
                "INV-S2-01",
                Severity.FAIL,
                f"{len(failures)} anchor-collision preflight violations",
                failures,
            )
        ]
    return [
        CheckResult(
            "INV-S2-01", Severity.INFO, "Scenario-2 anchor collision check passed"
        )
    ]


# ---------------------------------------------------------------------------
# Classification readiness (INV-R)
# ---------------------------------------------------------------------------


def check_baseline_parseable_rate(ctx: RunContext) -> list[CheckResult]:
    """INV-R01: baseline parseable rate ≥80% per (model, scenario).

    Uses the per-condition regex from the scenario YAML for Scenario 1; for
    Scenario 2, "parseable" means the response is non-error (the leak-rate
    interpretation lives in the scorer, not here).
    """
    info_lines: list[str] = []
    warns: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        if cond != "baseline":
            continue
        scn = ctx.scenarios.get(sid)
        n = len(rows)
        if n == 0:
            continue
        if sid.startswith("scenario_01") and scn:
            pat_str = scn.get("classification", {}).get("per_condition_regex", {}).get(cond)
            if not pat_str:
                continue
            pat = re.compile(pat_str)
            parsed = 0
            for r in rows:
                resp = r.get("response", "")
                if (
                    isinstance(resp, str)
                    and not resp.startswith("__HARNESS_ERROR__")
                    and pat.findall(resp)
                ):
                    parsed += 1
            ratio = parsed / n
            line = f"  {sid}/{model}: parseable={parsed}/{n} ({ratio:.0%})"
            info_lines.append(line)
            if ratio < 0.80:
                warns.append(f"  {sid}/{model}: {ratio:.0%} parseable (<80% gate)")
        else:
            non_error = sum(
                1
                for r in rows
                if isinstance(r.get("response"), str)
                and not r["response"].startswith("__HARNESS_ERROR__")
            )
            ratio = non_error / n
            info_lines.append(f"  {sid}/{model}: non-error={non_error}/{n} ({ratio:.0%})")
            if ratio < 0.80:
                warns.append(f"  {sid}/{model}: {ratio:.0%} non-error baseline rate (<80% gate)")
    results: list[CheckResult] = []
    if warns:
        results.append(
            CheckResult(
                "INV-R01",
                Severity.WARN,
                f"{len(warns)} (model, scenario) pairs fail the 80% baseline gate",
                warns,
            )
        )
    else:
        results.append(
            CheckResult(
                "INV-R01", Severity.INFO, "All baselines pass the 80% sanity gate"
            )
        )
    results.append(
        CheckResult("INV-R01-detail", Severity.INFO, "Baseline parseable per cell", info_lines)
    )
    return results


def report_unparseable_rate(ctx: RunContext) -> list[CheckResult]:
    """INV-R02 (INFO): per-(condition, model) unparseable rate, Scenario-1 only."""
    info_lines: list[str] = []
    for (sid, model, cond), rows in ctx.cells.items():
        if not sid.startswith("scenario_01"):
            continue
        scn = ctx.scenarios.get(sid)
        if not scn:
            continue
        pat_str = scn.get("classification", {}).get("per_condition_regex", {}).get(cond)
        if not pat_str:
            continue
        pat = re.compile(pat_str)
        unparsed = 0
        for r in rows:
            resp = r.get("response", "")
            if (
                isinstance(resp, str)
                and not resp.startswith("__HARNESS_ERROR__")
                and not pat.findall(resp)
            ):
                unparsed += 1
        n = len(rows)
        info_lines.append(f"  {sid}/{model}/{cond}: unparseable={unparsed}/{n}")
    return [
        CheckResult(
            "INV-R02", Severity.INFO, "Unparseable counts per cell (Scenario 1)", info_lines
        )
    ]


# ---------------------------------------------------------------------------
# Aggregation + report
# ---------------------------------------------------------------------------


ALL_CHECKS: tuple[Any, ...] = (
    check_run_dir_naming,
    check_sweep_manifest_exists,
    check_expected_cell_files_exist,
    check_expected_config_files_exist,
    check_no_stray_tmp_files,
    check_file_size_bounds,
    check_jsonl_rows_parseable,
    check_jsonl_required_fields,
    check_jsonl_field_types,
    check_hash_format,
    check_timestamp_format,
    check_pinned_values,
    check_n_rows_per_cell,
    check_run_idx_complete,
    check_seed_sequence,
    check_model_digest_within_cell,
    check_prompt_hash_within_cell,
    check_num_predict_constant,
    check_digest_jsonl_vs_config,
    check_digest_config_vs_manifest,
    check_scenario_yaml_sha,
    check_harness_git_sha,
    check_condition_coverage,
    check_no_harness_errors,
    check_response_nonempty,
    check_response_determinism,
    check_scenario_2_turn_count,
    check_scenario_1_single_turn_flags,
    check_scenario_1_regex_extracts,
    check_scenario_2_multi_turn,
    check_scenario_2_anchor_collision,
    check_baseline_parseable_rate,
    report_unparseable_rate,
)


def write_validation_report(ctx: RunContext, results: list[CheckResult], verdict: str) -> Path:
    out_path = ctx.run_dir / "VALIDATION_REPORT.md"
    fail_count = sum(1 for r in results if r.severity == Severity.FAIL)
    warn_count = sum(1 for r in results if r.severity == Severity.WARN)
    info_count = sum(1 for r in results if r.severity == Severity.INFO)

    lines: list[str] = []
    lines.append("# VALIDATION REPORT")
    lines.append("")
    lines.append(f"**Run dir:** `{ctx.run_dir}`")
    lines.append(f"**Verdict:** **{verdict}**")
    lines.append("")
    lines.append(f"- FAIL: {fail_count}")
    lines.append(f"- WARN: {warn_count}")
    lines.append(f"- INFO: {info_count}")
    lines.append("")

    lines.append("## Summary table")
    lines.append("")
    lines.append("| Rule | Severity | Message |")
    lines.append("|---|---|---|")
    for r in results:
        msg = r.message.replace("|", "\\|")
        lines.append(f"| `{r.rule}` | {r.severity.value} | {msg} |")
    lines.append("")

    lines.append("## Details")
    lines.append("")
    for r in results:
        if not r.details:
            continue
        lines.append(f"### `{r.rule}` — {r.severity.value}")
        lines.append("")
        lines.append("```")
        for d in r.details:
            lines.append(d)
        lines.append("```")
        lines.append("")

    out_path.write_text("\n".join(lines))
    return out_path


def compute_verdict(results: list[CheckResult]) -> str:
    has_fail = any(r.severity == Severity.FAIL for r in results)
    has_warn = any(r.severity == Severity.WARN for r in results)
    if has_fail:
        return "FAIL"
    if has_warn:
        return "PASS-WITH-WARNINGS"
    return "PASS"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CPIB v0.1 pipeline contract enforcer")
    p.add_argument("run_dir", type=Path, help="Path to a results/run_* directory")
    p.add_argument(
        "--scenarios-dir",
        type=Path,
        default=Path("scenarios"),
        help="Scenarios directory (for live YAML SHA + collision-check verification)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.run_dir.exists() or not args.run_dir.is_dir():
        logger.error("Not a directory: %s", args.run_dir)
        return 2
    if not args.scenarios_dir.exists() or not args.scenarios_dir.is_dir():
        logger.error("Not a directory: %s", args.scenarios_dir)
        return 2

    ctx = load_run_context(args.run_dir, args.scenarios_dir)

    # Cross-suite forward reference avoidance: cell_filename keeps cell-naming
    # logic single-sourced in harness.py (used elsewhere; imported above).
    _ = cell_filename  # silence "imported but unused" if linter complains

    results: list[CheckResult] = []
    for check in ALL_CHECKS:
        try:
            results.extend(check(ctx))
        except Exception as exc:  # noqa: BLE001
            results.append(
                CheckResult(
                    rule=check.__name__,
                    severity=Severity.FAIL,
                    message=f"Validator threw: {exc!r}",
                )
            )

    verdict = compute_verdict(results)
    out = write_validation_report(ctx, results, verdict)
    logger.info("Wrote %s — verdict=%s", out, verdict)

    if verdict == "FAIL":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
