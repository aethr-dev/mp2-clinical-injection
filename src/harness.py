"""MP-2 CPIB v0.1 — benchmark harness.

Hardened sweep runner. Two modes:

(a) Single-cell mode (smoke-test): one ``--scenario`` × one ``--model`` ×
    one ``--condition`` for K runs. Writes a config snapshot but no sweep
    manifest. Useful for smoke-testing or one-off verification.

(b) Sweep mode (``--sweep``): full 4-models × 2-scenarios × 7-conditions ×
    10-runs cartesian product (560 runs). Writes the canonical sweep
    manifest with drift checks, cell-level resumability, circuit breaker,
    atomic writes, and the full PIPELINE_CONTRACT § 2.5 config snapshot
    fields.

See ``docs/METHODOLOGY.md`` for run parameters and logged fields,
``docs/PIPELINE_CONTRACT.md`` for the file/schema/invariants spec, and
``docs/PRE-REGISTRATION.md`` for the locked classification rubric.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("harness")

# Locked run parameters — must match PIPELINE_CONTRACT § 2.5 / METHODOLOGY.
SEEDS: list[int] = [42, 43, 44, 45, 46, 47, 48, 49, 50, 51]
TEMPERATURE: float = 0.0
TOP_P: float = 0.9
TOP_K: int = 40
NUM_PREDICT: int = 2048
REQUEST_TIMEOUT_S: float = 300.0

# Hardening parameters.
MAX_RETRIES: int = 3
BACKOFF_SCHEDULE_S: tuple[float, ...] = (1.0, 2.0, 4.0)
CIRCUIT_BREAKER_THRESHOLD: int = 3

# Canonical sweep targets.
DEFAULT_SWEEP_MODELS: tuple[str, ...] = (
    "qwen3:8b",
    "llama3.1:8b",
    "gemma2:9b",
    "mistral:7b",
)
DEFAULT_SWEEP_SCENARIOS: tuple[str, ...] = (
    "scenarios/scenario_01_triage.yaml",
    "scenarios/scenario_02_summarization.yaml",
)

CONDITIONS: tuple[str, ...] = (
    "baseline",
    "attack_v1",
    "attack_v2",
    "non_clinical_baseline",
    "control_v1",
    "control_v2",
    "mitigation",
)

QWEN3_MODEL: str = "qwen3:8b"
GEMMA2_MODEL: str = "gemma2:9b"

ERROR_PREFIX: str = "__HARNESS_ERROR__"
TURN_BREAK: str = "\n\n---TURN_BREAK---\n\n"


class HarnessFatalError(Exception):
    """Non-recoverable error — abort sweep."""


# ---------------------------------------------------------------------------
# Hashing, git, transient-error detection
# ---------------------------------------------------------------------------


def sha256_hex(text: str) -> str:
    """Return the SHA-256 hex digest of a string, prefixed with ``sha256:``."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's bytes, prefixed."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def git_rev_parse_head() -> str:
    """Return current HEAD SHA. Returns ``uncommitted`` if no commit exists."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, OSError):
        pass
    return "uncommitted"


def is_transient_error(exc: BaseException) -> bool:
    """Identify transient infra errors that are retry-eligible.

    Transient: connection / timeout errors, HTTP 5xx, HTTP 429.
    Permanent: HTTP 4xx (except 429) and everything else.
    """
    if isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.RemoteProtocolError,
        ),
    ):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code >= 500 or code == 429
    return False


def model_slug(model: str) -> str:
    """Convert ``qwen3:8b`` → ``qwen3_8b`` for safe filename use."""
    return model.replace(":", "_").replace("/", "_")


# ---------------------------------------------------------------------------
# Ollama API wrappers
# ---------------------------------------------------------------------------


def ollama_version(host: str) -> str:
    """Fetch Ollama server version via ``/api/version``."""
    r = httpx.get(f"http://{host}/api/version", timeout=10.0)
    r.raise_for_status()
    return r.json().get("version", "unknown")


def ollama_tags(host: str) -> list[str]:
    """List model tags currently available on the Ollama host (``/api/tags``)."""
    r = httpx.get(f"http://{host}/api/tags", timeout=10.0)
    r.raise_for_status()
    return [m["name"] for m in r.json().get("models", [])]


def ollama_tag_metadata(host: str) -> dict[str, dict[str, Any]]:
    """Return ``{name: full_model_dict}`` from ``/api/tags``.

    The per-model dict carries ``digest``, ``size``, ``modified_at``, etc.
    Used by preflight to capture the model SHA-256 digest reliably across
    Ollama versions (recent versions stopped returning ``digest`` at the top
    level of ``/api/show``).
    """
    r = httpx.get(f"http://{host}/api/tags", timeout=10.0)
    r.raise_for_status()
    return {m["name"]: m for m in r.json().get("models", [])}


def ollama_show(host: str, model: str) -> dict[str, Any]:
    """Fetch model metadata (incl. SHA-256 digest) via ``/api/show``."""
    r = httpx.post(
        f"http://{host}/api/show",
        json={"name": model},
        timeout=60.0,
    )
    r.raise_for_status()
    return r.json()


def _ollama_options(seed: int) -> dict[str, Any]:
    """Standard option dict per locked params."""
    return {
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "top_k": TOP_K,
        "num_predict": NUM_PREDICT,
        "seed": seed,
    }


def _add_qwen3_thinking_off(payload: dict[str, Any], model: str) -> None:
    """For Qwen 3, disable thinking mode (locked OFF for cross-model parity)."""
    if model == QWEN3_MODEL:
        payload["think"] = False


def ollama_generate_raw(
    host: str,
    model: str,
    prompt: str,
    seed: int,
    system: str | None,
) -> dict[str, Any]:
    """Single-turn ``/api/generate`` call. Returns the full JSON response."""
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": _ollama_options(seed),
    }
    _add_qwen3_thinking_off(payload, model)
    if system:
        payload["system"] = system
    r = httpx.post(
        f"http://{host}/api/generate",
        json=payload,
        timeout=REQUEST_TIMEOUT_S,
    )
    r.raise_for_status()
    return r.json()


def ollama_chat_raw(
    host: str,
    model: str,
    messages: list[dict[str, str]],
    seed: int,
    system: str | None,
) -> dict[str, Any]:
    """Single ``/api/chat`` call with the given message history."""
    full_messages: list[dict[str, str]] = []
    if system:
        full_messages.append({"role": "system", "content": system})
    full_messages.extend(messages)
    payload: dict[str, Any] = {
        "model": model,
        "messages": full_messages,
        "stream": False,
        "options": _ollama_options(seed),
    }
    _add_qwen3_thinking_off(payload, model)
    r = httpx.post(
        f"http://{host}/api/chat",
        json=payload,
        timeout=REQUEST_TIMEOUT_S,
    )
    r.raise_for_status()
    return r.json()


def with_retry(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call ``fn`` with up to ``MAX_RETRIES`` attempts on transient errors.

    Backoff schedule: ``BACKOFF_SCHEDULE_S`` (1s, 2s, 4s). Permanent errors
    (HTTP 4xx other than 429, value errors, etc.) raise immediately without
    retry.
    """
    last_exc: BaseException | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if is_transient_error(exc) and attempt < MAX_RETRIES - 1:
                wait = BACKOFF_SCHEDULE_S[attempt]
                logger.warning(
                    "Transient error (attempt %d/%d): %s — retrying in %.1fs",
                    attempt + 1,
                    MAX_RETRIES,
                    exc,
                    wait,
                )
                time.sleep(wait)
                continue
            raise
    assert last_exc is not None  # unreachable: loop either returns or raises
    raise last_exc


# ---------------------------------------------------------------------------
# Scenarios, message preparation, warmup
# ---------------------------------------------------------------------------


def load_scenario(path: Path) -> dict[str, Any]:
    """Load and minimally validate a scenario YAML."""
    with path.open() as f:
        scenario = yaml.safe_load(f)
    required_top = {"scenario", "conditions", "classification"}
    missing = required_top - scenario.keys()
    if missing:
        raise ValueError(f"Scenario {path} missing keys: {missing}")
    return scenario


def build_messages(
    condition_block: dict[str, Any],
) -> tuple[str | None, list[dict[str, str]], bool]:
    """Return ``(system, messages, is_multi_turn)`` for a condition block."""
    system = condition_block.get("system")
    if "turns" in condition_block:
        return system, list(condition_block["turns"]), True
    if "user" in condition_block:
        return system, [{"role": "user", "content": condition_block["user"]}], False
    raise ValueError(
        f"Condition block has neither 'user' nor 'turns': {condition_block}"
    )


def prepare_for_model(
    model: str,
    gemma2_mitigation_mode: str,
    system: str | None,
    messages: list[dict[str, str]],
) -> tuple[str | None, list[dict[str, str]]]:
    """Apply per-model adjustments.

    Currently only handles Gemma 2 ``prefix_fallback`` mode for the mitigation
    condition: if Gemma 2's chat template silently drops system messages, we
    fall back to prepending the system text to the first user turn so the
    defense actually reaches the model. Logged via ``gemma2_mitigation_mode``
    in the config snapshot.
    """
    if (
        model == GEMMA2_MODEL
        and gemma2_mitigation_mode == "prefix_fallback"
        and system
        and messages
        and messages[0].get("role") == "user"
    ):
        adjusted = list(messages)
        adjusted[0] = {
            "role": "user",
            "content": f"[SYSTEM PREFIX] {system}\n\n[USER MESSAGE] {adjusted[0]['content']}",
        }
        return None, adjusted
    return system, messages


def warmup(host: str, model: str, scenario_id: str) -> None:
    """Fire one discarded inference per (model, scenario) per invocation."""
    logger.info("Warmup [%s/%s]: discarded inference", model, scenario_id)
    try:
        with_retry(ollama_generate_raw, host, model, "ping", 0, None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Warmup failed for %s/%s: %s", model, scenario_id, exc)


# ---------------------------------------------------------------------------
# Single-run execution (with retry + multi-turn turn-restart)
# ---------------------------------------------------------------------------


def execute_single_turn(
    host: str,
    model: str,
    messages: list[dict[str, str]],
    seed: int,
    system: str | None,
) -> tuple[str, dict[str, Any]]:
    """Run a single-turn request. Returns ``(response_text, raw_response)``."""
    raw = ollama_generate_raw(host, model, messages[0]["content"], seed=seed, system=system)
    return raw.get("response", ""), raw


def execute_multi_turn(
    host: str,
    model: str,
    user_turns: list[dict[str, str]],
    seed: int,
    system: str | None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Turn-by-turn chat orchestration. Returns ``(per_turn_responses, raws)``.

    Each user turn is sent in a separate ``/api/chat`` call with an
    incrementally-grown message history (prior user + assistant turns).
    Any turn's exception propagates so the caller can restart the entire run
    from Turn 1 per METHODOLOGY § Multi-turn retry.
    """
    response_turns: list[str] = []
    raw_responses: list[dict[str, Any]] = []
    msg_history: list[dict[str, str]] = []
    for turn in user_turns:
        msg_history.append(turn)
        raw = ollama_chat_raw(host, model, msg_history, seed=seed, system=system)
        content = raw.get("message", {}).get("content", "")
        msg_history.append({"role": "assistant", "content": content})
        response_turns.append(content)
        raw_responses.append(raw)
    return response_turns, raw_responses


def run_one_record(
    *,
    host: str,
    model: str,
    model_digest: str,
    scenario: dict[str, Any],
    condition_name: str,
    condition_block: dict[str, Any],
    run_idx: int,
    seed: int,
    gemma2_mitigation_mode: str,
) -> dict[str, Any]:
    """Execute one run (single-turn or multi-turn). Always returns a record.

    Retries the whole call (or whole multi-turn sequence) up to
    ``MAX_RETRIES`` times on transient errors. On retries-exhausted failure,
    returns a record whose ``response`` starts with ``__HARNESS_ERROR__``
    per METHODOLOGY § Retry exhaustion (the whole run is marked, never a
    per-turn partial).
    """
    raw_system, raw_messages, is_multi_turn = build_messages(condition_block)
    system, messages = prepare_for_model(
        model, gemma2_mitigation_mode, raw_system, raw_messages
    )

    scenario_id = scenario["scenario"]["id"]
    prompt_dump = json.dumps(
        {"system": system, "messages": messages}, ensure_ascii=False
    )
    prompt_hash = sha256_hex(prompt_dump)
    timestamp = datetime.now(tz=UTC).isoformat()

    response_text: str
    response_turns: list[str] | None = None
    response_turn_hashes: list[str] | None = None
    eval_count: int | None = None
    total_duration: int | None = None

    try:
        if is_multi_turn:
            turns, raws = with_retry(
                execute_multi_turn, host, model, messages, seed, system
            )
            response_turns = turns
            response_turn_hashes = [sha256_hex(t) for t in turns]
            response_text = TURN_BREAK.join(turns)
            eval_count = sum((r.get("eval_count") or 0) for r in raws) or None
            total_duration = (
                sum((r.get("total_duration") or 0) for r in raws) or None
            )
        else:
            response_text, raw = with_retry(
                execute_single_turn, host, model, messages, seed, system
            )
            eval_count = raw.get("eval_count")
            total_duration = raw.get("total_duration")
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Run failed after retries [%s/%s/%s seed=%d]: %s",
            scenario_id,
            condition_name,
            model,
            seed,
            exc,
        )
        response_text = f"{ERROR_PREFIX}: {exc!r}"
        response_turns = None
        response_turn_hashes = None

    response_hash = sha256_hex(response_text)
    fingerprint_input = "|".join(
        [
            scenario_id,
            condition_name,
            model,
            model_digest,
            str(run_idx),
            str(seed),
            prompt_hash,
            response_hash,
        ]
    )
    run_fingerprint = sha256_hex(fingerprint_input)

    return {
        "scenario_id": scenario_id,
        "condition": condition_name,
        "model": model,
        "model_digest": model_digest,
        "run_idx": run_idx,
        "seed": seed,
        "is_multi_turn": is_multi_turn,
        "system": system,
        "messages": messages,
        "response": response_text,
        "response_turns": response_turns,
        "response_turn_hashes": response_turn_hashes,
        "timestamp": timestamp,
        "prompt_hash": prompt_hash,
        "response_hash": response_hash,
        "run_fingerprint": run_fingerprint,
        "ollama_eval_count": eval_count,
        "ollama_total_duration": total_duration,
        "num_predict": NUM_PREDICT,
    }


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------


def atomic_write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Write all records to ``{path}.tmp``, then ``os.replace`` to ``{path}``.

    Per RESUME / PIPELINE_CONTRACT: never append to the final file. Either
    the cell file exists with the complete record set, or it does not.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: Any) -> None:
    """Write JSON to ``{path}.tmp``, then ``os.replace`` to ``{path}``."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Cell execution + circuit breaker + resumability
# ---------------------------------------------------------------------------


def cell_filename(scenario_id: str, model: str, condition: str) -> str:
    return f"{scenario_id}_{model_slug(model)}_{condition}.jsonl"


def cell_is_complete(jsonl_path: Path, n_runs: int) -> tuple[bool, str]:
    """Quick structural check: file exists, parseable, ``n_runs`` rows.

    Returns ``(is_complete, reason)``. Used to decide skip-vs-abort per
    RESUME's "Cell exists + valid → skip; cell exists + invalid → hard abort"
    rule.

    NOTE: this is a fast presence check, not a substitute for
    ``src/validate_run.py`` which enforces the full PIPELINE_CONTRACT.
    """
    if not jsonl_path.exists():
        return False, "missing"
    rows: list[dict[str, Any]] = []
    try:
        with jsonl_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except json.JSONDecodeError as exc:
        return False, f"invalid JSON: {exc}"
    if len(rows) != n_runs:
        return False, f"expected {n_runs} rows, found {len(rows)}"
    required = {
        "scenario_id",
        "condition",
        "model",
        "run_idx",
        "response_hash",
        "run_fingerprint",
    }
    for i, r in enumerate(rows):
        missing = required - r.keys()
        if missing:
            return False, f"row {i} missing fields: {missing}"
    return True, ""


def run_cell(
    *,
    host: str,
    model: str,
    model_digest: str,
    scenario: dict[str, Any],
    condition_name: str,
    n_runs: int,
    jsonl_path: Path,
    gemma2_mitigation_mode: str,
    dry_run: bool,
) -> str:
    """Execute one ``(scenario, model, condition)`` cell.

    Returns one of: ``completed`` | ``skipped`` | ``dry-run``. Raises
    ``HarnessFatalError`` on:
    - existing cell file present but invalid (refuse to overwrite or repair)
    - circuit-breaker trip (3 consecutive failures inside this cell)
    """
    scenario_id = scenario["scenario"]["id"]
    is_complete, reason = cell_is_complete(jsonl_path, n_runs)
    if jsonl_path.exists() and not is_complete:
        raise HarnessFatalError(
            f"Cell file exists but invalid ({reason}): {jsonl_path}. "
            f"Remove the file manually or move the run-dir aside before retrying."
        )
    if is_complete:
        logger.info("SKIP cell (already complete): %s", jsonl_path.name)
        return "skipped"

    if dry_run:
        logger.info("DRY-RUN cell: would execute %d runs → %s", n_runs, jsonl_path)
        return "dry-run"

    condition_block = scenario["conditions"][condition_name]
    consecutive_failures = 0
    records: list[dict[str, Any]] = []
    for run_idx in range(n_runs):
        seed = SEEDS[run_idx]
        record = run_one_record(
            host=host,
            model=model,
            model_digest=model_digest,
            scenario=scenario,
            condition_name=condition_name,
            condition_block=condition_block,
            run_idx=run_idx,
            seed=seed,
            gemma2_mitigation_mode=gemma2_mitigation_mode,
        )
        records.append(record)
        is_error = record["response"].startswith(ERROR_PREFIX)
        consecutive_failures = consecutive_failures + 1 if is_error else 0
        logger.info(
            "Run %d/%d [%s/%s/%s seed=%d] %s",
            run_idx + 1,
            n_runs,
            scenario_id,
            condition_name,
            model,
            seed,
            "ERROR" if is_error else "ok",
        )
        if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            atomic_write_jsonl(jsonl_path, records)
            raise HarnessFatalError(
                f"Circuit breaker tripped: {CIRCUIT_BREAKER_THRESHOLD} consecutive "
                f"failures in cell {scenario_id}/{condition_name}/{model}. "
                f"Wrote {len(records)} rows then halted."
            )

    atomic_write_jsonl(jsonl_path, records)
    return "completed"


# ---------------------------------------------------------------------------
# Pre-flight check
# ---------------------------------------------------------------------------


def preflight_check(host: str, models: list[str]) -> dict[str, str]:
    """Verify all models present and respond. Returns ``{model: digest}``.

    Steps per model:
    1. Confirm tag present in ``/api/tags``
    2. One ``/api/generate`` test call (with retry) to ensure model loads
    3. Capture SHA-256 digest from ``/api/show``
    """
    metadata = ollama_tag_metadata(host)
    missing = [m for m in models if m not in metadata]
    if missing:
        raise HarnessFatalError(
            f"Models not pulled on Ollama host: {missing}. "
            f"Available: {sorted(metadata)}"
        )

    digests: dict[str, str] = {}
    for model in models:
        try:
            with_retry(ollama_generate_raw, host, model, "ping", 0, None)
        except Exception as exc:  # noqa: BLE001
            raise HarnessFatalError(
                f"Pre-flight test-generate failed for {model}: {exc}"
            ) from exc
        digest = metadata[model].get("digest", "")
        if digest and not digest.startswith("sha256:"):
            digest = "sha256:" + digest
        if not digest:
            raise HarnessFatalError(
                f"No digest in /api/tags entry for {model}"
            )
        digests[model] = digest
        logger.info("Pre-flight ok: %s (digest=%s)", model, digest)
    return digests


# ---------------------------------------------------------------------------
# Sweep manifest
# ---------------------------------------------------------------------------


def make_or_load_manifest(
    *,
    run_dir: Path,
    models: list[str],
    scenarios_block: dict[str, dict[str, str]],
    model_digests: dict[str, str],
    ollama_ver: str,
    harness_git_sha: str,
) -> dict[str, Any]:
    """Create a sweep manifest if absent; on resume, load and run drift checks.

    Drift checks (any mismatch → ``HarnessFatalError``):
    - harness git SHA must equal manifest's recorded SHA
    - each scenario YAML SHA-256 must equal manifest's recorded SHA
    - each model digest must equal manifest's recorded digest

    Ollama version drift is a warning, not a hard abort, but is logged.
    """
    manifest_path = run_dir / ".sweep_manifest.json"
    new_manifest = {
        "run_dir": str(run_dir),
        "created_ts": datetime.now(tz=UTC).isoformat(),
        "harness_git_sha": harness_git_sha,
        "scenarios": scenarios_block,
        "models": models,
        "model_digests": model_digests,
        "ollama_version": ollama_ver,
        "pinned": {
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "top_k": TOP_K,
            "num_predict": NUM_PREDICT,
            "seeds": SEEDS,
        },
        "cells_completed": [],
    }

    if not manifest_path.exists():
        atomic_write_json(manifest_path, new_manifest)
        logger.info("Wrote new sweep manifest: %s", manifest_path)
        return new_manifest

    with manifest_path.open() as f:
        existing = json.load(f)

    if existing.get("harness_git_sha") != harness_git_sha:
        raise HarnessFatalError(
            f"Harness git SHA drift: manifest={existing.get('harness_git_sha')} "
            f"live={harness_git_sha}. Refusing to resume a sweep on a modified harness."
        )
    for sid, sblock in scenarios_block.items():
        existing_block = existing.get("scenarios", {}).get(sid)
        if not existing_block:
            raise HarnessFatalError(
                f"Scenario {sid} not declared in existing manifest."
            )
        if existing_block.get("sha256") != sblock["sha256"]:
            raise HarnessFatalError(
                f"Scenario YAML drift on {sid}: "
                f"manifest={existing_block.get('sha256')} live={sblock['sha256']}."
            )
    for m, d in model_digests.items():
        existing_digest = existing.get("model_digests", {}).get(m)
        if existing_digest and existing_digest != d:
            raise HarnessFatalError(
                f"Model digest drift on {m}: manifest={existing_digest} live={d}."
            )
    if existing.get("ollama_version") != ollama_ver:
        logger.warning(
            "Ollama version drift: manifest=%s live=%s (continuing; logged)",
            existing.get("ollama_version"),
            ollama_ver,
        )

    logger.info(
        "Resumed sweep manifest: %s (%d cells already completed)",
        manifest_path,
        len(existing.get("cells_completed", [])),
    )
    return existing


def update_manifest_completed_cell(
    run_dir: Path, scenario_id: str, model: str, condition: str
) -> None:
    """Append a completed-cell record to ``.sweep_manifest.json`` atomically."""
    manifest_path = run_dir / ".sweep_manifest.json"
    with manifest_path.open() as f:
        manifest = json.load(f)
    cell = {"scenario": scenario_id, "model": model, "condition": condition}
    if cell not in manifest.get("cells_completed", []):
        manifest.setdefault("cells_completed", []).append(cell)
        atomic_write_json(manifest_path, manifest)


# ---------------------------------------------------------------------------
# Config snapshot (PIPELINE_CONTRACT § 2.5)
# ---------------------------------------------------------------------------


def make_config_snapshot(
    *,
    scenario_path: Path,
    scenario_id: str,
    scenario_yaml_sha: str,
    model: str,
    model_digest: str,
    conditions: list[str],
    n_runs: int,
    ollama_ver: str,
    harness_git_sha: str,
    uv_lock_sha: str,
    gemma2_mitigation_mode: str,
) -> dict[str, Any]:
    """Build the full config snapshot dict per PIPELINE_CONTRACT § 2.5."""
    return {
        "scenario_path": str(scenario_path),
        "scenario_id": scenario_id,
        "scenario_yaml_sha256": scenario_yaml_sha,
        "model": model,
        "model_digest": model_digest,
        "conditions": conditions,
        "n_runs": n_runs,
        "seeds": SEEDS[:n_runs],
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "top_k": TOP_K,
        "num_predict": NUM_PREDICT,
        "ollama_host": "REDACTED",
        "ollama_version": ollama_ver,
        "qwen3_thinking_mode": False,
        "harness_git_sha": harness_git_sha,
        "harness_invocation_ts": datetime.now(tz=UTC).isoformat(),
        "python_version": (
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        "uv_lock_sha256": uv_lock_sha,
        "gemma2_mitigation_mode": gemma2_mitigation_mode,
    }


# Fields that must match between an existing snapshot and a re-invocation.
# The full snapshot is immutable after first write per RESUME ("first write wins;
# second invocation verifies match, refuses to rewrite") — this is the field
# subset used for the verification.
_LOCKED_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "scenario_yaml_sha256",
    "model_digest",
    "num_predict",
    "seeds",
    "temperature",
    "top_p",
    "top_k",
    "harness_git_sha",
    "qwen3_thinking_mode",
)


def write_config_snapshot_if_absent(
    run_dir: Path, model: str, scenario_id: str, snapshot: dict[str, Any]
) -> Path:
    """Write the snapshot if absent; if present, verify locked fields match."""
    path = run_dir / f"config_{model_slug(model)}_{scenario_id}.json"
    if path.exists():
        with path.open() as f:
            existing = json.load(f)
        for k in _LOCKED_SNAPSHOT_FIELDS:
            if existing.get(k) != snapshot.get(k):
                raise HarnessFatalError(
                    f"Config snapshot drift on {path.name} field {k!r}: "
                    f"existing={existing.get(k)!r} new={snapshot.get(k)!r}"
                )
        return path
    atomic_write_json(path, snapshot)
    return path


# ---------------------------------------------------------------------------
# CLI / mode dispatch
# ---------------------------------------------------------------------------


def make_run_dir(root: Path) -> Path:
    """Create a timestamped run directory and return its path."""
    stamp = datetime.now(tz=UTC).strftime("%Y-%m-%d_%H%M%S")
    path = root / f"run_{stamp}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CPIB v0.1 harness")
    p.add_argument(
        "--sweep",
        action="store_true",
        help="Full sweep mode (cartesian product over --models × --scenarios × CONDITIONS)",
    )
    p.add_argument("--scenario", type=Path, help="Single-cell mode: scenario YAML path")
    p.add_argument("--model", help="Single-cell mode: Ollama model tag (e.g. qwen3:8b)")
    p.add_argument(
        "--condition",
        default="all",
        help=f"Single-cell mode: condition name or 'all'. Valid: {'|'.join(CONDITIONS)}|all",
    )
    p.add_argument("--runs", type=int, default=10, help="Runs per condition (max len(SEEDS)=10)")
    p.add_argument(
        "--results-root",
        type=Path,
        default=Path("results"),
        help="Results root directory (a timestamped run-dir is created underneath)",
    )
    p.add_argument(
        "--run-dir",
        type=Path,
        help=(
            "Reuse an existing run-dir (resume mode). "
            "For sweep, requires .sweep_manifest.json present."
        ),
    )
    p.add_argument(
        "--models",
        nargs="+",
        default=list(DEFAULT_SWEEP_MODELS),
        help="Sweep mode: list of model tags",
    )
    p.add_argument(
        "--scenarios",
        nargs="+",
        type=Path,
        default=[Path(s) for s in DEFAULT_SWEEP_SCENARIOS],
        help="Sweep mode: list of scenario YAML paths",
    )
    p.add_argument(
        "--gemma2-mitigation-mode",
        choices=("system_message", "prefix_fallback"),
        default="system_message",
        help="Gemma 2 mitigation handling — set per smoke-test outcome",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and skip Ollama calls (no run-dir state changes)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.runs < 1 or args.runs > len(SEEDS):
        logger.error("--runs must be between 1 and %d (len(SEEDS))", len(SEEDS))
        return 2

    host = os.environ.get("OLLAMA_HOST")
    if not host:
        logger.error("OLLAMA_HOST not set. Copy .env.example to .env and fill it in.")
        return 2

    if args.sweep:
        return run_sweep_mode(args, host)
    if not args.scenario or not args.model:
        logger.error(
            "Single-cell mode requires --scenario and --model. "
            "Use --sweep for the full cartesian sweep."
        )
        return 2
    return run_single_cell_mode(args, host)


def _uv_lock_sha() -> str:
    p = Path("uv.lock")
    if p.exists():
        return sha256_file(p)
    logger.warning("uv.lock not found — using placeholder in config snapshot")
    return "uv.lock_missing"


def run_single_cell_mode(args: argparse.Namespace, host: str) -> int:
    """One scenario × one model × {one condition | all conditions} for K runs."""
    scenario = load_scenario(args.scenario)
    scenario_id = scenario["scenario"]["id"]
    scenario_yaml_sha = sha256_file(args.scenario)

    if args.condition == "all":
        conditions = [c for c in CONDITIONS if c in scenario["conditions"]]
        missing = [c for c in CONDITIONS if c not in scenario["conditions"]]
        if missing:
            logger.warning(
                "Scenario %s missing conditions: %s", scenario_id, missing
            )
    else:
        if args.condition not in CONDITIONS:
            logger.error(
                "Unknown condition %r. Valid: %s|all",
                args.condition,
                "|".join(CONDITIONS),
            )
            return 2
        if args.condition not in scenario["conditions"]:
            logger.error(
                "Scenario %s has no condition %r", scenario_id, args.condition
            )
            return 2
        conditions = [args.condition]

    if args.dry_run:
        logger.info(
            "DRY-RUN single-cell: %s × %s × %d conditions × %d runs",
            args.model,
            scenario_id,
            len(conditions),
            args.runs,
        )
        digests = {args.model: "sha256:" + "0" * 64}
        ollama_ver = "DRY_RUN_VERSION"
    else:
        ollama_ver = ollama_version(host)
        try:
            digests = preflight_check(host, [args.model])
        except HarnessFatalError as exc:
            logger.error("FATAL: %s", exc)
            return 1
        warmup(host, args.model, scenario_id)

    model_digest = digests[args.model]
    if args.dry_run:
        run_dir = args.run_dir or (
            args.results_root / f"DRY_RUN_{datetime.now(tz=UTC).strftime('%Y-%m-%d_%H%M%S')}"
        )
    else:
        run_dir = args.run_dir or make_run_dir(args.results_root)
        run_dir.mkdir(parents=True, exist_ok=True)

    snapshot = make_config_snapshot(
        scenario_path=args.scenario,
        scenario_id=scenario_id,
        scenario_yaml_sha=scenario_yaml_sha,
        model=args.model,
        model_digest=model_digest,
        conditions=conditions,
        n_runs=args.runs,
        ollama_ver=ollama_ver,
        harness_git_sha=git_rev_parse_head(),
        uv_lock_sha=_uv_lock_sha(),
        gemma2_mitigation_mode=args.gemma2_mitigation_mode,
    )
    if not args.dry_run:
        try:
            write_config_snapshot_if_absent(run_dir, args.model, scenario_id, snapshot)
        except HarnessFatalError as exc:
            logger.error("FATAL: %s", exc)
            return 1

    logger.info("Run dir: %s", run_dir)

    for condition_name in conditions:
        jsonl_path = run_dir / cell_filename(scenario_id, args.model, condition_name)
        try:
            run_cell(
                host=host,
                model=args.model,
                model_digest=model_digest,
                scenario=scenario,
                condition_name=condition_name,
                n_runs=args.runs,
                jsonl_path=jsonl_path,
                gemma2_mitigation_mode=args.gemma2_mitigation_mode,
                dry_run=args.dry_run,
            )
        except HarnessFatalError as exc:
            logger.error("FATAL: %s", exc)
            return 1

    logger.info(
        "Done. %d conditions × %d runs → %s", len(conditions), args.runs, run_dir
    )
    return 0


def run_sweep_mode(args: argparse.Namespace, host: str) -> int:
    """Full cartesian sweep over models × scenarios × conditions."""
    scenario_paths: list[Path] = list(args.scenarios)
    scenarios: dict[str, dict[str, Any]] = {}
    scenarios_block: dict[str, dict[str, str]] = {}
    for sp in scenario_paths:
        scn = load_scenario(sp)
        sid = scn["scenario"]["id"]
        scenarios[sid] = scn
        scenarios_block[sid] = {"path": str(sp), "sha256": sha256_file(sp)}

    if args.dry_run:
        n_conds_per_scn = {
            sid: sum(1 for c in CONDITIONS if c in scn["conditions"])
            for sid, scn in scenarios.items()
        }
        total_cells = sum(n_conds_per_scn.values()) * len(args.models)
        logger.info(
            "DRY-RUN sweep: %d models × %d scenarios → %d cells × %d runs (max)",
            len(args.models),
            len(scenarios),
            total_cells,
            args.runs,
        )
        digests = {m: "sha256:" + "0" * 64 for m in args.models}
        ollama_ver = "DRY_RUN_VERSION"
    else:
        ollama_ver = ollama_version(host)
        try:
            digests = preflight_check(host, args.models)
        except HarnessFatalError as exc:
            logger.error("FATAL: %s", exc)
            return 1

    harness_git_sha = git_rev_parse_head()

    if args.run_dir:
        run_dir = args.run_dir
        if not (run_dir / ".sweep_manifest.json").exists() and not args.dry_run:
            logger.error(
                "FATAL: --run-dir %s has no .sweep_manifest.json. "
                "Resume requires an existing manifest from a prior sweep run.",
                run_dir,
            )
            return 1
    elif args.dry_run:
        run_dir = args.results_root / (
            f"DRY_RUN_{datetime.now(tz=UTC).strftime('%Y-%m-%d_%H%M%S')}"
        )
    else:
        run_dir = make_run_dir(args.results_root)
    if not args.dry_run:
        run_dir.mkdir(parents=True, exist_ok=True)

    if not args.dry_run:
        try:
            make_or_load_manifest(
                run_dir=run_dir,
                models=args.models,
                scenarios_block=scenarios_block,
                model_digests=digests,
                ollama_ver=ollama_ver,
                harness_git_sha=harness_git_sha,
            )
        except HarnessFatalError as exc:
            logger.error("FATAL: %s", exc)
            return 1

    uv_lock_sha = _uv_lock_sha()

    if not args.dry_run:
        for sid, scn in scenarios.items():
            sp = Path(scenarios_block[sid]["path"])
            scn_conditions = [c for c in CONDITIONS if c in scn["conditions"]]
            for model in args.models:
                snapshot = make_config_snapshot(
                    scenario_path=sp,
                    scenario_id=sid,
                    scenario_yaml_sha=scenarios_block[sid]["sha256"],
                    model=model,
                    model_digest=digests[model],
                    conditions=scn_conditions,
                    n_runs=args.runs,
                    ollama_ver=ollama_ver,
                    harness_git_sha=harness_git_sha,
                    uv_lock_sha=uv_lock_sha,
                    gemma2_mitigation_mode=args.gemma2_mitigation_mode,
                )
                try:
                    write_config_snapshot_if_absent(run_dir, model, sid, snapshot)
                except HarnessFatalError as exc:
                    logger.error("FATAL: %s", exc)
                    return 1

    logger.info("Run dir: %s", run_dir)

    for sid, scn in scenarios.items():
        scn_conditions = [c for c in CONDITIONS if c in scn["conditions"]]
        for model in args.models:
            if not args.dry_run:
                warmup(host, model, sid)
            for condition_name in scn_conditions:
                jsonl_path = run_dir / cell_filename(sid, model, condition_name)
                try:
                    status = run_cell(
                        host=host,
                        model=model,
                        model_digest=digests[model],
                        scenario=scn,
                        condition_name=condition_name,
                        n_runs=args.runs,
                        jsonl_path=jsonl_path,
                        gemma2_mitigation_mode=args.gemma2_mitigation_mode,
                        dry_run=args.dry_run,
                    )
                    if status == "completed" and not args.dry_run:
                        update_manifest_completed_cell(
                            run_dir, sid, model, condition_name
                        )
                except HarnessFatalError as exc:
                    logger.error("FATAL: %s", exc)
                    return 1

    logger.info("Sweep complete → %s", run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
