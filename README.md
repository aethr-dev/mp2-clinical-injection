# CPIB v0.1 — Clinical Prompt Injection Benchmark

Pilot benchmark testing whether LLM safety mitigations generalize to specialized
clinical domains, or whether domain-specific language creates bypass vectors that
general-purpose red-teaming misses.

## Research Question

Do LLM safety mitigations generalize to specialized clinical domains, or does
domain-specific language create bypass vectors that general-purpose red-teaming
misses?

## Status

CPIB v0.1 complete. Full 560-run pilot sweep executed, validated, and
scored; pre-registered primary hypothesis tests (H1a, H1b, H2a, H2b, H3,
H4) computed via deterministic regex extraction with per-cell Wilson 95%
CIs and two-sided Fisher's exact tests. See `docs/WRITEUP.md` for results
and discussion. Pre-registration committed and git-tagged
(`pre-registration-v1`) before any execution run; all post-tag deviations
are logged in `docs/DEVIATIONS.md`. The locked analysis plan lives in
`docs/PRE-REGISTRATION.md`.

Secondary outcomes specified in the pre-registration — manual rubric
annotation, Claude cross-rating, and intra-rater Cohen's kappa — are
deferred to CPIB v0.2 with an independent multi-rater pipeline; rationale
in `docs/DEVIATIONS.md`.

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
  — deterministic, byte-reproducible at temperature 0 with pinned model digests.
  Pre-registered secondary outcomes (manual rubric annotation, Claude cross-rating,
  Cohen's kappa) deferred to v0.2 per `docs/DEVIATIONS.md`.
- **Baseline sanity gate:** 80% baseline correctness required per model per
  scenario for primary hypothesis inclusion; excluded models' data still reported
- **Frontier comparator:** Claude Sonnet 4.6 was specified as an optional
  Tier 1 arm but was not executed in v0.1; carried forward to v0.2.

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
