# CPIB v0.1 — Clinical Prompt Injection Benchmark

Pilot benchmark testing whether LLM safety mitigations generalize to specialized
clinical domains, or whether domain-specific language creates bypass vectors that
general-purpose red-teaming misses.

## Research Question

Do LLM safety mitigations generalize to specialized clinical domains, or does
domain-specific language create bypass vectors that general-purpose red-teaming
misses?

## Status

Pilot in progress. Pre-registration committed and git-tagged (`pre-registration-v1`)
before any execution run. See `docs/PRE-REGISTRATION.md` for the locked analysis plan.

## Method (summary)

- **Scenarios (2):** triage classification (ESI), multi-turn clinical summarization
  with cross-patient PHI leakage vector
- **Conditions (7 per scenario — 2×3 factorial + mitigation):** clinical baseline,
  clinical attack_v1 (direct injection), clinical attack_v2 (authority-role),
  non-clinical baseline, non-clinical control_v1 (matched injection), non-clinical
  control_v2 (matched authority-role), mitigation (attack_v1 + system-prompt defense)
- **Models (4 open-weight):** Qwen 3 8B (thinking mode OFF), Llama 3.1 8B,
  Gemma 2 9B, Mistral 7B
- **Total runs:** 2 × 7 × 4 × 10 = 560
- **Parameters:** temperature 0, top_p 0.9, top_k 40, num_predict 2048, fresh
  session per run, seed sequence [42..51] logged per row
- **Classification:** regex primary (word-boundary, case-insensitive for Scenario 2)
  + solo-rater manual annotation + Claude cross-rating on 30% stratified subset
  (seed=777)
- **Baseline sanity gate:** 80% baseline correctness required per model per
  scenario for primary hypothesis inclusion; excluded models' data still reported
- **Optional Tier 1:** Claude Sonnet 4.6 as frontier comparator arm (reported
  separately — not pooled with open-weight results)

Full methodology: `docs/METHODOLOGY.md`.
Pipeline contract (file schemas, invariants, validation rules):
`docs/PIPELINE_CONTRACT.md`.

## Ethics & HIPAA

All clinical scenarios use synthetic data. No real patient information is present
in scenarios, logs, or results. No outputs are acted on clinically. See
`docs/METHODOLOGY.md` for the full ethics statement and responsible-disclosure
commitments.

## Reproducibility

- Pre-registration committed and git-tagged before first execution run
- All deviations from the pre-registered plan logged in `docs/DEVIATIONS.md`
  with timestamp and rationale — never silently applied
- Model digests pinned; seeds logged; prompt/response SHA-256 hashes recorded
- Each run batch writes to `results/run_YYYY-MM-DD_HHMMSS/` with JSONL + config snapshot

## Running

Requires Python 3.12+ with [UV](https://docs.astral.sh/uv/) and a reachable
[Ollama](https://ollama.com/) instance with the four open-weight models pulled.

```bash
uv sync
uv run python src/harness.py \
    --scenario scenarios/scenario_01_triage.yaml \
    --model qwen3:8b
```

## License

MIT — see `LICENSE`.
