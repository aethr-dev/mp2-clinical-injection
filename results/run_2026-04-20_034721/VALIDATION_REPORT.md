# VALIDATION REPORT

**Run dir:** `results/run_2026-04-20_034721`
**Verdict:** **PASS-WITH-WARNINGS**

- FAIL: 0
- WARN: 1
- INFO: 35

## Summary table

| Rule | Severity | Message |
|---|---|---|
| `INV-S01` | INFO | Run dir name OK |
| `INV-S05` | INFO | Sweep manifest present |
| `INV-S02` | INFO | All 56 expected cells present |
| `INV-S03` | INFO | All 8 config snapshots present |
| `INV-S04` | INFO | No stray .tmp files |
| `INV-S06` | INFO | All run-dir files within 10MB bound |
| `INV-SC01` | INFO | All cell JSONLs parse cleanly |
| `INV-SC02` | INFO | All rows have required fields |
| `INV-SC03` | INFO | All field types OK |
| `INV-SC04` | INFO | All hashes match sha256:<64hex> |
| `INV-SC05` | INFO | All timestamps ISO 8601 with TZ |
| `INV-V01-V07` | INFO | All pinned values match locked spec |
| `INV-C01` | INFO | All cells have expected row count |
| `INV-C02` | INFO | All cells have complete run_idx 0..n-1 |
| `INV-C03` | INFO | All seeds match SEEDS[run_idx] |
| `INV-C04` | INFO | model_digest constant within each cell |
| `INV-C05` | INFO | prompt_hash constant within each cell |
| `INV-C06` | INFO | All rows num_predict=2048 |
| `INV-X01` | INFO | model_digest matches config for all rows |
| `INV-X02` | INFO | model_digest matches manifest for all configs |
| `INV-X03/X04` | INFO | Scenario YAML SHAs match live files |
| `INV-X05` | INFO | harness_git_sha matches HEAD (6fedb4ec) |
| `INV-X06` | INFO | Condition coverage YAML↔cells consistent |
| `INV-CS01` | INFO | Zero __HARNESS_ERROR__ rows |
| `INV-CS02` | INFO | All non-error rows have non-empty response |
| `INV-CS03` | WARN | 56 cells show response non-determinism (GPU-kernel residual) |
| `INV-CS03-detail` | INFO | Per-cell determinism |
| `INV-CS04` | INFO | Scenario-2 multi-turn turn counts OK |
| `INV-S1-01` | INFO | Scenario-1 multi-turn flags OK |
| `INV-S1-03` | INFO | All Scenario-1 extracted values within 1-5 |
| `INV-S1-02` | INFO | Scenario-1 regex extraction summary |
| `INV-S2-02/03` | INFO | Scenario-2 multi-turn structure OK |
| `INV-S2-01` | INFO | Scenario-2 anchor collision check passed |
| `INV-R01` | INFO | All baselines pass the 80% sanity gate |
| `INV-R01-detail` | INFO | Baseline parseable per cell |
| `INV-R02` | INFO | Unparseable counts per cell (Scenario 1) |

## Details

### `INV-CS03` — WARN

```
  scenario_01_triage/gemma2:9b/attack_v1: ratio=0.10
  scenario_01_triage/gemma2:9b/attack_v2: ratio=0.20
  scenario_01_triage/gemma2:9b/baseline: ratio=0.10
  scenario_01_triage/gemma2:9b/control_v1: ratio=0.10
  scenario_01_triage/gemma2:9b/control_v2: ratio=0.10
  scenario_01_triage/gemma2:9b/mitigation: ratio=0.20
  scenario_01_triage/gemma2:9b/non_clinical_baseline: ratio=0.20
  scenario_01_triage/llama3.1:8b/attack_v1: ratio=0.20
  scenario_01_triage/llama3.1:8b/attack_v2: ratio=0.20
  scenario_01_triage/llama3.1:8b/baseline: ratio=0.10
  scenario_01_triage/llama3.1:8b/control_v1: ratio=0.20
  scenario_01_triage/llama3.1:8b/control_v2: ratio=0.20
  scenario_01_triage/llama3.1:8b/mitigation: ratio=0.10
  scenario_01_triage/llama3.1:8b/non_clinical_baseline: ratio=0.10
  scenario_01_triage/mistral:7b/attack_v1: ratio=0.10
  scenario_01_triage/mistral:7b/attack_v2: ratio=0.10
  scenario_01_triage/mistral:7b/baseline: ratio=0.10
  scenario_01_triage/mistral:7b/control_v1: ratio=0.20
  scenario_01_triage/mistral:7b/control_v2: ratio=0.10
  scenario_01_triage/mistral:7b/mitigation: ratio=0.20
  scenario_01_triage/mistral:7b/non_clinical_baseline: ratio=0.20
  scenario_01_triage/qwen3:8b/attack_v1: ratio=0.20
  scenario_01_triage/qwen3:8b/attack_v2: ratio=0.10
  scenario_01_triage/qwen3:8b/baseline: ratio=0.10
  scenario_01_triage/qwen3:8b/control_v1: ratio=0.20
  scenario_01_triage/qwen3:8b/control_v2: ratio=0.10
  scenario_01_triage/qwen3:8b/mitigation: ratio=0.10
  scenario_01_triage/qwen3:8b/non_clinical_baseline: ratio=0.10
  scenario_02_summarization/gemma2:9b/attack_v1: ratio=0.10
  scenario_02_summarization/gemma2:9b/attack_v2: ratio=0.10
  scenario_02_summarization/gemma2:9b/baseline: ratio=0.20
  scenario_02_summarization/gemma2:9b/control_v1: ratio=0.10
  scenario_02_summarization/gemma2:9b/control_v2: ratio=0.10
  scenario_02_summarization/gemma2:9b/mitigation: ratio=0.20
  scenario_02_summarization/gemma2:9b/non_clinical_baseline: ratio=0.20
  scenario_02_summarization/llama3.1:8b/attack_v1: ratio=0.10
  scenario_02_summarization/llama3.1:8b/attack_v2: ratio=0.10
  scenario_02_summarization/llama3.1:8b/baseline: ratio=0.20
  scenario_02_summarization/llama3.1:8b/control_v1: ratio=0.10
  scenario_02_summarization/llama3.1:8b/control_v2: ratio=0.10
  scenario_02_summarization/llama3.1:8b/mitigation: ratio=0.20
  scenario_02_summarization/llama3.1:8b/non_clinical_baseline: ratio=0.20
  scenario_02_summarization/mistral:7b/attack_v1: ratio=0.10
  scenario_02_summarization/mistral:7b/attack_v2: ratio=0.10
  scenario_02_summarization/mistral:7b/baseline: ratio=0.20
  scenario_02_summarization/mistral:7b/control_v1: ratio=0.10
  scenario_02_summarization/mistral:7b/control_v2: ratio=0.10
  scenario_02_summarization/mistral:7b/mitigation: ratio=0.10
  scenario_02_summarization/mistral:7b/non_clinical_baseline: ratio=0.20
  scenario_02_summarization/qwen3:8b/attack_v1: ratio=0.10
  scenario_02_summarization/qwen3:8b/attack_v2: ratio=0.10
  scenario_02_summarization/qwen3:8b/baseline: ratio=0.10
  scenario_02_summarization/qwen3:8b/control_v1: ratio=0.10
  scenario_02_summarization/qwen3:8b/control_v2: ratio=0.10
  scenario_02_summarization/qwen3:8b/mitigation: ratio=0.20
  scenario_02_summarization/qwen3:8b/non_clinical_baseline: ratio=0.20
```

### `INV-CS03-detail` — INFO

```
  scenario_01_triage/gemma2:9b/attack_v1: 1/10 unique = 0.10
  scenario_01_triage/gemma2:9b/attack_v2: 2/10 unique = 0.20
  scenario_01_triage/gemma2:9b/baseline: 1/10 unique = 0.10
  scenario_01_triage/gemma2:9b/control_v1: 1/10 unique = 0.10
  scenario_01_triage/gemma2:9b/control_v2: 1/10 unique = 0.10
  scenario_01_triage/gemma2:9b/mitigation: 2/10 unique = 0.20
  scenario_01_triage/gemma2:9b/non_clinical_baseline: 2/10 unique = 0.20
  scenario_01_triage/llama3.1:8b/attack_v1: 2/10 unique = 0.20
  scenario_01_triage/llama3.1:8b/attack_v2: 2/10 unique = 0.20
  scenario_01_triage/llama3.1:8b/baseline: 1/10 unique = 0.10
  scenario_01_triage/llama3.1:8b/control_v1: 2/10 unique = 0.20
  scenario_01_triage/llama3.1:8b/control_v2: 2/10 unique = 0.20
  scenario_01_triage/llama3.1:8b/mitigation: 1/10 unique = 0.10
  scenario_01_triage/llama3.1:8b/non_clinical_baseline: 1/10 unique = 0.10
  scenario_01_triage/mistral:7b/attack_v1: 1/10 unique = 0.10
  scenario_01_triage/mistral:7b/attack_v2: 1/10 unique = 0.10
  scenario_01_triage/mistral:7b/baseline: 1/10 unique = 0.10
  scenario_01_triage/mistral:7b/control_v1: 2/10 unique = 0.20
  scenario_01_triage/mistral:7b/control_v2: 1/10 unique = 0.10
  scenario_01_triage/mistral:7b/mitigation: 2/10 unique = 0.20
  scenario_01_triage/mistral:7b/non_clinical_baseline: 2/10 unique = 0.20
  scenario_01_triage/qwen3:8b/attack_v1: 2/10 unique = 0.20
  scenario_01_triage/qwen3:8b/attack_v2: 1/10 unique = 0.10
  scenario_01_triage/qwen3:8b/baseline: 1/10 unique = 0.10
  scenario_01_triage/qwen3:8b/control_v1: 2/10 unique = 0.20
  scenario_01_triage/qwen3:8b/control_v2: 1/10 unique = 0.10
  scenario_01_triage/qwen3:8b/mitigation: 1/10 unique = 0.10
  scenario_01_triage/qwen3:8b/non_clinical_baseline: 1/10 unique = 0.10
  scenario_02_summarization/gemma2:9b/attack_v1: 1/10 unique = 0.10
  scenario_02_summarization/gemma2:9b/attack_v2: 1/10 unique = 0.10
  scenario_02_summarization/gemma2:9b/baseline: 2/10 unique = 0.20
  scenario_02_summarization/gemma2:9b/control_v1: 1/10 unique = 0.10
  scenario_02_summarization/gemma2:9b/control_v2: 1/10 unique = 0.10
  scenario_02_summarization/gemma2:9b/mitigation: 2/10 unique = 0.20
  scenario_02_summarization/gemma2:9b/non_clinical_baseline: 2/10 unique = 0.20
  scenario_02_summarization/llama3.1:8b/attack_v1: 1/10 unique = 0.10
  scenario_02_summarization/llama3.1:8b/attack_v2: 1/10 unique = 0.10
  scenario_02_summarization/llama3.1:8b/baseline: 2/10 unique = 0.20
  scenario_02_summarization/llama3.1:8b/control_v1: 1/10 unique = 0.10
  scenario_02_summarization/llama3.1:8b/control_v2: 1/10 unique = 0.10
  scenario_02_summarization/llama3.1:8b/mitigation: 2/10 unique = 0.20
  scenario_02_summarization/llama3.1:8b/non_clinical_baseline: 2/10 unique = 0.20
  scenario_02_summarization/mistral:7b/attack_v1: 1/10 unique = 0.10
  scenario_02_summarization/mistral:7b/attack_v2: 1/10 unique = 0.10
  scenario_02_summarization/mistral:7b/baseline: 2/10 unique = 0.20
  scenario_02_summarization/mistral:7b/control_v1: 1/10 unique = 0.10
  scenario_02_summarization/mistral:7b/control_v2: 1/10 unique = 0.10
  scenario_02_summarization/mistral:7b/mitigation: 1/10 unique = 0.10
  scenario_02_summarization/mistral:7b/non_clinical_baseline: 2/10 unique = 0.20
  scenario_02_summarization/qwen3:8b/attack_v1: 1/10 unique = 0.10
  scenario_02_summarization/qwen3:8b/attack_v2: 1/10 unique = 0.10
  scenario_02_summarization/qwen3:8b/baseline: 1/10 unique = 0.10
  scenario_02_summarization/qwen3:8b/control_v1: 1/10 unique = 0.10
  scenario_02_summarization/qwen3:8b/control_v2: 1/10 unique = 0.10
  scenario_02_summarization/qwen3:8b/mitigation: 2/10 unique = 0.20
  scenario_02_summarization/qwen3:8b/non_clinical_baseline: 2/10 unique = 0.20
```

### `INV-S1-02` — INFO

```
  scenario_01_triage/gemma2:9b/attack_v1: 10/10 rows parsed in 1-5 range
  scenario_01_triage/gemma2:9b/attack_v2: 10/10 rows parsed in 1-5 range
  scenario_01_triage/gemma2:9b/baseline: 10/10 rows parsed in 1-5 range
  scenario_01_triage/gemma2:9b/control_v1: 10/10 rows parsed in 1-5 range
  scenario_01_triage/gemma2:9b/control_v2: 10/10 rows parsed in 1-5 range
  scenario_01_triage/gemma2:9b/mitigation: 10/10 rows parsed in 1-5 range
  scenario_01_triage/gemma2:9b/non_clinical_baseline: 10/10 rows parsed in 1-5 range
  scenario_01_triage/llama3.1:8b/attack_v1: 10/10 rows parsed in 1-5 range
  scenario_01_triage/llama3.1:8b/attack_v2: 10/10 rows parsed in 1-5 range
  scenario_01_triage/llama3.1:8b/baseline: 10/10 rows parsed in 1-5 range
  scenario_01_triage/llama3.1:8b/control_v1: 10/10 rows parsed in 1-5 range
  scenario_01_triage/llama3.1:8b/control_v2: 10/10 rows parsed in 1-5 range
  scenario_01_triage/llama3.1:8b/mitigation: 10/10 rows parsed in 1-5 range
  scenario_01_triage/llama3.1:8b/non_clinical_baseline: 10/10 rows parsed in 1-5 range
  scenario_01_triage/mistral:7b/attack_v1: 10/10 rows parsed in 1-5 range
  scenario_01_triage/mistral:7b/attack_v2: 10/10 rows parsed in 1-5 range
  scenario_01_triage/mistral:7b/baseline: 10/10 rows parsed in 1-5 range
  scenario_01_triage/mistral:7b/control_v1: 10/10 rows parsed in 1-5 range
  scenario_01_triage/mistral:7b/control_v2: 10/10 rows parsed in 1-5 range
  scenario_01_triage/mistral:7b/mitigation: 10/10 rows parsed in 1-5 range
  scenario_01_triage/mistral:7b/non_clinical_baseline: 10/10 rows parsed in 1-5 range
  scenario_01_triage/qwen3:8b/attack_v1: 10/10 rows parsed in 1-5 range
  scenario_01_triage/qwen3:8b/attack_v2: 10/10 rows parsed in 1-5 range
  scenario_01_triage/qwen3:8b/baseline: 10/10 rows parsed in 1-5 range
  scenario_01_triage/qwen3:8b/control_v1: 10/10 rows parsed in 1-5 range
  scenario_01_triage/qwen3:8b/control_v2: 10/10 rows parsed in 1-5 range
  scenario_01_triage/qwen3:8b/mitigation: 10/10 rows parsed in 1-5 range
  scenario_01_triage/qwen3:8b/non_clinical_baseline: 10/10 rows parsed in 1-5 range
```

### `INV-R01-detail` — INFO

```
  scenario_01_triage/gemma2:9b: parseable=10/10 (100%)
  scenario_01_triage/llama3.1:8b: parseable=10/10 (100%)
  scenario_01_triage/mistral:7b: parseable=10/10 (100%)
  scenario_01_triage/qwen3:8b: parseable=10/10 (100%)
  scenario_02_summarization/gemma2:9b: non-error=10/10 (100%)
  scenario_02_summarization/llama3.1:8b: non-error=10/10 (100%)
  scenario_02_summarization/mistral:7b: non-error=10/10 (100%)
  scenario_02_summarization/qwen3:8b: non-error=10/10 (100%)
```

### `INV-R02` — INFO

```
  scenario_01_triage/gemma2:9b/attack_v1: unparseable=0/10
  scenario_01_triage/gemma2:9b/attack_v2: unparseable=0/10
  scenario_01_triage/gemma2:9b/baseline: unparseable=0/10
  scenario_01_triage/gemma2:9b/control_v1: unparseable=0/10
  scenario_01_triage/gemma2:9b/control_v2: unparseable=0/10
  scenario_01_triage/gemma2:9b/mitigation: unparseable=0/10
  scenario_01_triage/gemma2:9b/non_clinical_baseline: unparseable=0/10
  scenario_01_triage/llama3.1:8b/attack_v1: unparseable=0/10
  scenario_01_triage/llama3.1:8b/attack_v2: unparseable=0/10
  scenario_01_triage/llama3.1:8b/baseline: unparseable=0/10
  scenario_01_triage/llama3.1:8b/control_v1: unparseable=0/10
  scenario_01_triage/llama3.1:8b/control_v2: unparseable=0/10
  scenario_01_triage/llama3.1:8b/mitigation: unparseable=0/10
  scenario_01_triage/llama3.1:8b/non_clinical_baseline: unparseable=0/10
  scenario_01_triage/mistral:7b/attack_v1: unparseable=0/10
  scenario_01_triage/mistral:7b/attack_v2: unparseable=0/10
  scenario_01_triage/mistral:7b/baseline: unparseable=0/10
  scenario_01_triage/mistral:7b/control_v1: unparseable=0/10
  scenario_01_triage/mistral:7b/control_v2: unparseable=0/10
  scenario_01_triage/mistral:7b/mitigation: unparseable=0/10
  scenario_01_triage/mistral:7b/non_clinical_baseline: unparseable=0/10
  scenario_01_triage/qwen3:8b/attack_v1: unparseable=0/10
  scenario_01_triage/qwen3:8b/attack_v2: unparseable=0/10
  scenario_01_triage/qwen3:8b/baseline: unparseable=0/10
  scenario_01_triage/qwen3:8b/control_v1: unparseable=0/10
  scenario_01_triage/qwen3:8b/control_v2: unparseable=0/10
  scenario_01_triage/qwen3:8b/mitigation: unparseable=0/10
  scenario_01_triage/qwen3:8b/non_clinical_baseline: unparseable=0/10
```
