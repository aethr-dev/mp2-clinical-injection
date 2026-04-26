# Deviations from Pre-Registration

This file records any deviation from the pre-registered analysis plan
(`PRE-REGISTRATION.md`) encountered during execution. **Deviations are logged,
never silently applied.**

## Entry Format

Each entry:

- **Date** (ISO 8601 with timezone offset)
- **Deviation** — what changed vs. the pre-reg
- **Rationale** — why the deviation was necessary
- **Affected runs** — which scenario / condition / model / run indices
- **Reporting impact** — what the writeup will say

## Template

```
### YYYY-MM-DD — [One-line title]

**Deviation:** [what changed]

**Rationale:** [why]

**Affected runs:** [scenario / condition / model / run indices — or "all
subsequent runs"]

**Reporting impact:** [how this appears in the writeup limitations section]
```

---

## Deviations

### 2026-04-20 — validate_run.py cell-filename parser fix

**Deviation:** Fixed a greedy-regex bug in `src/validate_run.py:92`
(`CELL_NAME_RE = r"^(scenario_\d{2}_[a-z_]+)_(.+)_([a-z_0-9]+)\.jsonl$"`).
The scenario group's `[a-z_]+` greedy-consumed model-name fragments
("mistral") when parsing filenames like
`scenario_01_triage_mistral_7b_attack_v1.jsonl`, causing the validator's
INV-S02 check to falsely flag all 14 mistral cells as "missing on disk"
(first validation attempt → verdict FAIL). Fix: trust the row's
`scenario_id` field when available rather than the regex's group 1 —
mirroring the existing pattern in the same function where `model` and
`condition` are read from row contents rather than the filename.

**Rationale:** The bug is in a post-hoc validation tool, not in the
harness or the execution protocol. Without the fix, validate_run.py
cannot correctly validate any run containing the mistral model, which
blocks the pipeline contract's "validate before scoring" gate. The
harness itself wrote mistral cell files correctly (all 14 verified
present on disk, 10 rows each, all fields valid); only the validator's
filename parsing was defective.

**Affected runs:** `results/run_2026-04-20_034721` — all 14 mistral
cells (7 conditions × 2 scenarios). After the fix, INV-S02 correctly
recognizes all 56 cells; overall verdict moved FAIL → PASS-WITH-WARNINGS
with the sole WARN being INV-CS03 (GPU-kernel residual non-determinism,
already anticipated in the pre-reg and measured as a secondary outcome).

**Reporting impact:** None on primary analysis — the sweep data is
unchanged; validator behavior does not affect any inference outputs or
classification results. Limitations section will note that a post-hoc
validator bug was discovered and corrected during analysis, with the
fix committed in a post-sweep cleanup commit that advances HEAD past
`pre-registration-v1`. The tag continues to reference the pre-commit
state of the harness and all files used to produce the sweep data.

### 2026-04-21 — Inadvertent brief `annotation_key.csv` exposure (pre-annotation)

**Deviation:** During pre-annotation setup, the study author briefly
(~10 seconds) opened
`results/run_2026-04-20_034721/annotation_key.csv` while organizing
files for upload to external storage. Author reports seeing model
names only (one or more of `qwen3:8b`, `llama3.1:8b`, `gemma2:9b`,
`mistral:7b`) and not condition or scenario labels, with no retained
understanding of specific `rating_id → (model, condition, scenario)`
mappings. Annotation had not yet begun for any subset at time of
exposure.

**Rationale:** File opened in error. Duration too brief and content
too limited (model names only, no condition data) to plausibly bias
a 78-row rating task on a rubric that evaluates response content
(refusal pattern, injection flagged, hedge language, unexpected notes)
rather than outcome expectations. Model is a blocking variable in the
study design, not a primary-outcome variable. The pre-registration's
blinding protocol was technically violated in letter (rater briefly
viewed the deblinding file before rating) but preserved in spirit
(no actionable information retained, no primary-outcome-relevant
content viewed).

**Affected ratings:** Main annotation set (78 responses). Does NOT
affect the 48-hour re-rate subset (`annotation_rerate_input.csv`,
seed 888) or the Claude cross-rating subset
(`claude_crossrating_input.csv`, seed 777). Those key files were
not exposed.

**Corrective action considered and declined:** Re-running
`build_annotation.py` in default mode with a different internal
seed would regenerate `annotation_input.csv` and
`annotation_key.csv` with a different `rating_id` shuffle, rendering
any retained associations meaningless. Declined because (a) the
practical risk of memory-based bias from a 10-second glance at 78
rows is vanishingly small, (b) re-randomizing would discard the
already-generated printable forms (`annotation_forms.html`) and
any annotation already begun, and (c) the pre-registered seeds for
the rerate and Claude subsets remain intact and unexposed, so the
reliability measurements that actually drive secondary-outcome
conclusions are unaffected.

**Reporting impact:** Limitations section will acknowledge the brief
exposure as a potential (though likely not practical) blinding
integrity concern, distinguishing strict blinding protocol
(technically violated) from practical blinding integrity (likely
intact due to brief, non-examining nature of the exposure and the
narrow scope of what was viewed). No corrective action taken.

*Note (2026-04-26):* the secondary annotation pass was subsequently
deferred to CPIB v0.2 in full (see the 2026-04-26 entry below); this
exposure therefore had no effect on any reported v0.1 outcome. The
record is retained as part of the append-only integrity log.

### 2026-04-26 — analyze.py figure errorbar clamp (post-tag tooling fix)

**Deviation:** Added `max(0.0, ...)` clamp to the per-cell Wilson-CI
errorbar deltas (`err_lo`, `err_hi`) in `make_primary_figure`
(`src/analyze.py`). Newer matplotlib raises `ValueError: 'yerr' must
not contain negative values` when fed a `-0.0`, which the existing
code produced for cells where the per-cell success rate was exactly
1.0 (k = n_valid = 10): the Wilson upper bound clamps to 1.0 via
`min(1.0, ...)`, and `hi - p = 1.0 - 1.0` evaluates as `-0.0` under
IEEE-754 floating point. The clamp converts `-0.0` to `0.0` and
otherwise leaves all values unchanged.

**Rationale:** Pure plotting fix. The Wilson 95% CIs themselves are
computed and written to `proportions_table.csv` correctly; the issue
exists only in the figure's errorbar input arrays, where matplotlib
rejects negative-zero. Without the clamp, the primary figure cannot
render at all. The fix does not modify any computed proportion,
confidence interval, p-value, or aggregated count; it only ensures
matplotlib accepts the plotting input.

**Affected runs:** None. The fix is in the analysis-time figure
renderer, not the harness or any computation feeding into the
proportions table or Fisher's exact tests. All 25 cells where the
clamp activates are k=n=10 cells whose `proportion_success`,
`wilson_ci_lower`, and `wilson_ci_upper` columns in
`proportions_table.csv` were already correct prior to the fix.

**Reporting impact:** Limitations section will note the post-tag
tooling fix alongside the validate_run.py fix from 2026-04-20: both
are post-hoc analysis-tool corrections with no effect on the sweep
data or any reported primary-outcome statistic. Tag
`pre-registration-v1` continues to reference the pre-sweep state of
the harness; both fixes will land in the same post-sweep cleanup
commit.

### 2026-04-26 — Behavioral rubric annotation and intra-rater re-rate not completed in v0.1; deferred to v0.2

**Deviation:** The pre-registered secondary outcomes — four-field manual
rubric annotation (refusal type R/C/P, injection-flagged Y/N, hedge Y/N,
free-text notes), 30%-stratified Claude cross-rating subset, and 20%
intra-rater 48-hour re-rate for Cohen's kappa — were not executed within
the v0.1 reporting window. Primary regex-based outcomes (H1a, H1b, H2a,
H2b, H3, H4 hypothesis tests; per-cell Wilson 95% CIs; Fisher's exact
two-sided tests) are complete and unaffected. Secondary annotation
outcomes are deferred in full to CPIB v0.2 with an independent
multi-rater pipeline.

**Rationale:** A single-rater pass executed under time pressure would
not meet the inter-rater reliability bar appropriate for the secondary
characterization. The pre-registration specifies intra-rater (48-hour
re-rate) and human↔Claude inter-rater Cohen's kappa as the reliability
measures; producing those metrics from a rushed single-session annotation
would yield numbers that do not represent the reliability the pre-reg
intends to measure. Better to defer the secondary outcomes to a v0.2
replication-and-extension scope with adequate rater bandwidth than to
report low-quality reliability statistics in v0.1.

**Affected outcomes:** Secondary outcomes only. Specifically deferred:
- Refusal-type breakdown (R/C/P) per (condition × model)
- Injection-flagged proportion per (condition × model)
- Hedge-language proportion per (condition × model)
- Human↔Claude inter-rater Cohen's kappa per rubric field
- Intra-rater (self) Cohen's kappa per rubric field
- Semantic-paraphrase leak detection (Scenario 2 sixth column)

Primary outcomes (regex-extracted from the full 560-run sweep) are
complete: per-cell `proportions_table.csv`, pre-registered Fisher's
exact tests in `fisher_exact_table.csv`, Scenario 2 per-turn /
per-direction secondary outcomes in `scenario_2_secondary_table.csv`,
and the primary figure (`primary_figure.svg`/`.png`).

**Reporting impact:** Writeup §5 (Limitations) names the deferral
explicitly as a quality-bar decision; §6 (Conclusions) notes secondary
outcomes as a v0.2 scope item alongside larger N per cell, frontier
model comparator, and additional attack-vector coverage. The behavioral
characterization that the rubric was designed to produce (whether models
*refuse*, *comply*, or *hedge* under attack, and whether the regex-success
binary maps cleanly onto these qualitative behaviors) remains an open
question for v0.2 — and is itself one of the strongest reasons to run
v0.2.
