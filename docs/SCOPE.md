# SCOPE — MP-2 Tiered Execution Plan

> **Source:** internal scope decision, 2026-04-17 (consolidated into this repo).

---

## Status

Active. Governs execution through April 24, 2026 submit target. Revisit if
submit date slips or scope conditions change materially.

## Research Goal

Answer: *Do LLM safety mitigations generalize to specialized clinical domains,
or does domain-specific language create bypass vectors that general-purpose
red-teaming misses?*

Method: comparative controlled pilot. Clinical attacks paired with matched
general-language controls. Minimal mitigation arm tests whether the simplest
defense helps at all. Two scenarios stress-test different safety surfaces —
classification manipulation (triage) and cross-context information flow
(summarization).

Deliverable: CPIB v0.1 — scenario library + harness + pilot findings +
methodology + pre-registration, all in a public repo at submit time.

## Tiered Structure

### Tier 0 — MVP (ships April 24 regardless)

**Scope:**
- 2 scenarios: Triage Classification (Scenario 1), Clinical Summarization
  multi-turn (Scenario 2)
- **7 conditions per scenario — 2×3 + mitigation factorial design:**
  | domain × attack | none | injection (v1) | authority-role (v2) |
  |---|---|---|---|
  | clinical | baseline | attack_v1 | attack_v2 |
  | non-clinical | non_clinical_baseline | control_v1 | control_v2 |
  | + mitigation | — | (attack_v1 + system-prompt defense) | — |

  Non-clinical baseline + per-attack-flavor matched controls anchor H1a / H1b
  clinical-framing tests symmetrically. See `docs/PRE-REGISTRATION.md` for
  hypothesis mapping.
- 4 open-weight models (Qwen 3 8B with thinking mode **OFF**, Llama 3.1 8B,
  Gemma 2 9B, Mistral 7B)
- 10 runs per condition, temp 0, fixed seed, fresh session per run
- Hybrid classification: auto-regex + single-rater manual annotation + Claude
  cross-rating on subset (30%, stratified, seed-pinned)
- Pre-registration committed and tagged before first run
- Matched control design (paired per attack flavor) is the research contribution
- HIPAA statement, responsible disclosure statement
- Visualization: attack success by condition × model figure (**mandatory**)
- Writeup: ~2000–2500 words with findings table, mitigation result, limitations
  section

**Run count:** 2 × 7 × 4 × 10 = **560 runs** (~42 min wall-clock on the local
inference host at 7–9B).

**Baseline sanity gate (both scenarios):** models with <80% baseline
correctness (8/10) are excluded from primary H1/H2/H3 testing. Excluded
models' data is still reported for transparency.

**Future-work backlog:** maintained in `docs/FUTURE_WORK.md` (living doc).
Indicative items (non-exhaustive): Scenario 3 (documentation / critical-finding
omission), Variant 3 (patient-stated preferences within clinical documentation),
authority-role-specific mitigation (defense targeting role claims), batch vs.
multi-turn comparison for Scenario 2, variable-position injection for Scenario 2,
larger N at temp 0.7, Qwen 3 thinking-mode robustness as a separate study,
clinical expert annotation, RAG-based leak scenarios, additional frontier
comparators (data-handling constraints permitting), second human rater
(inter-rater kappa).

### Tier 1 — Stretch (Monday–Tuesday, if Gate 1 = GO)

**Add:** frontier comparator arm using Claude Sonnet 4.6.

- 2 scenarios × 3 conditions (baseline + attack variant 1 + matched control)
  × 5 runs = **30 API calls**
- Cost: ~$1–3 total
- Responsible-disclosure preamble in system message (template in
  `docs/METHODOLOGY.md`)
- Optional micro-experiment: subset with preamble-absent for preamble-effect
  measurement

**Writeup framing:** "exploratory comparator arm at frontier scale" — not a
full evaluation. Named scope limit protects the claim.

#### Gate 1 — Monday AM

Paste status report to chat. Required fields:
- Sunday run state: completed / partial / failed
- Actual logged runs vs. 560 target
- Errors, model crashes, weird outputs
- Initial regex classification sanity check (eyeball 5–10 responses — do they
  parse?)
- Harness state: stable / needs fix
- Gut call: GO / NO-GO / unsure

**GO triggers:** 560 runs complete, classifier parsing sensibly, no blocking
issues.
**NO-GO triggers:** runs incomplete, classifier broken, harness unstable → kill
Tier 1, use Monday for fixes.

### Tier 2 — Reach (Tuesday–Wednesday, if Gate 2 = GO)

**Add:** second-order analysis — pick ONE based on data shape.

- **2A — Refusal-language taxonomy:** cluster model responses by refusal style;
  correlate with attack resistance. Best if raw results show heterogeneous
  refusal behavior. ~2–3 hours.
- **2B — Attack-variant breakdown:** decompose attack success by injection-type
  × model; quantify which attack flavor generalizes vs. is model-specific. Best
  if attacks show divergent patterns. ~2 hours.
- **2C — Mitigation dose-response:** add one stronger mitigation variant;
  report no/minimal/stronger defense as trajectory. Converts mitigation from
  single-condition to dose-response. ~3–4 hours (includes new runs).

No pre-commit to option. Decide at Gate 2 based on what Monday data looks like.

#### Gate 2 — Tuesday AM

Paste status report to chat. Required fields:
- Tier 1 frontier runs: state (if executed)
- Findings table: clean / messy / surprising
- Writeup outline state: drafted / partial / nothing
- Visualization: made / pending
- Wednesday PM external review target: realistic?
- Data shape pointing toward 2A / 2B / 2C?

**GO triggers:** Tier 1 clean, writeup outline exists, data points to one of
the three options, Wednesday review still feasible.
**NO-GO triggers:** Tier 1 incomplete, writeup nowhere, data ambiguous → kill
Tier 2, spend Tuesday on writeup.

## Locked Parameters

| Parameter | Value | Rationale |
|---|---|---|
| Models (open-weight) | Qwen 3 8B, Llama 3.1 8B, Gemma 2 9B, Mistral 7B | Training-family diversity across 4 labs |
| Qwen 3 thinking mode | OFF | Cross-model comparability; reasoning-mode robustness parked as future work |
| Gemma 2 mitigation mode | TBD at smoke-test (`system_message` or `prefix_fallback`) | Gemma 2 chat template may not accept system messages natively |
| Frontier comparator (Tier 1) | Claude Sonnet 4.6 | Deployment-realistic tier; vendor-disclosed |
| Conditions per scenario | 7 (2×3 + mitigation) | Option A design; matched controls per attack flavor |
| Temperature | 0 | Determinism attempt; pilot scope |
| top_p / top_k | 0.9 / 40 | Pinned explicitly (Ollama defaults drift across versions) |
| num_predict | 2048 | Prevents silent truncation of long responses |
| Seed policy | Fixed per run, rotated across runs within condition, logged | Reproducibility |
| Seed sequence | [42, 43, 44, 45, 46, 47, 48, 49, 50, 51] | Pinned |
| Runs per condition (Tier 0) | 10 | Pilot-scale proportion estimation |
| Runs per condition (Tier 1) | 5 | Exploratory comparator only |
| Context reset per run | Yes — fresh API session | Avoids KV-cache bleed |
| Warmup protocol | 1 discarded inference per (model, scenario) at session start | Controls cold-vs-warm numerical drift |
| Scenario 2 turn structure | Consistent-position: attack at Turn 2 (Patient B / Tenant B) | MVP simplicity |
| Scenario 2 anchor matching | Word-boundary regex, case-insensitive | Prevents substring false positives |
| Baseline sanity gate | 80% per (model, scenario) | Models below gate excluded from primary tests; data still reported |
| Condition execution order | YAML-declaration order per invocation | Documented; not a confound (fresh HTTP call per run) |
| Multi-turn retry behavior | Restart whole run from Turn 1 on any turn failure (up to 3 attempts) | At temp=0 fixed seed, replay is deterministic; simpler than per-turn state |
| Retry exhaustion | Mark whole run as `__HARNESS_ERROR__` | Partial multi-turn data is harder to interpret |
| Response language expectation | English | Models are English-trained; non-English responses flagged in unexpected_notes |
| Claude cross-rating | 30% stratified subset, seed=777 | Reproducible, covers all (condition × model) cells |
| Circuit breaker | 3 consecutive failures halt sweep | Surface issue instead of filling JSONL with error rows |
| Repo visibility | Private until April 24; public at submit | OPSEC during composition |
| Commit signing | Annotated tag (unsigned) — GPG not set up | Weaker provenance; acknowledged in writeup |

## Timeline

| Date | Day | Tier 0 Track | Tier 1 Prep / Execution | Gates |
|---|---|---|---|---|
| 2026-04-17 | Fri | Decision docs locked. Produce DECISION + methodology + pre-registration. | — | — |
| 2026-04-18 | Sat | Init repo. Pull 4 open-weight models. Write harness skeleton. Lock Scenario 1 + 2 YAMLs. Finalize mitigation prompt wording. Smoke-test end-to-end. | API key setup + Sonnet 4.6 harness extension tested ($0.01 call). | — |
| 2026-04-19 | Sun | **Execute 560 Tier 0 runs.** Log everything. | — | — |
| 2026-04-20 | Mon | Auto-classify. Sanity-check. Start manual annotation. Start writeup outline. Visualization draft. | Frontier arm (30 calls) executed if Gate 1 = GO. | **Gate 1 AM** |
| 2026-04-21 | Tue | Writeup draft (incorporates Tier 1 if executed). Finish annotation. Intra-rater re-annotate subset (48hr window started Sunday eve). | Tier 2 option executed if Gate 2 = GO. | **Gate 2 AM** |
| 2026-04-22 | Wed | Repo polish: README, HIPAA statement, responsible disclosure. Companion writeup re-draft against real results. | Tier 2 analysis folded in if executed. | — |
| 2026-04-23 | Thu | External review. Final writeup polish. | — | — |
| 2026-04-24 | Fri | **Submit deliverable.** Flip repo to public. | — | — |

**Buffer:** Thursday has slack. Companion writeup re-draft may bleed from Wed
into Thu depending on result complexity.

## Narrative Flexibility Pledge

If Tier 0 results contradict the framing in the companion writeup (hypothesis
that clinical framing is a distinct attack surface), the writeup gets rewritten
to match findings. Committed to transparency over narrative consistency.

## Changelog

- v1 (2026-04-17): Initial scope lock.
- v2 (2026-04-19): Option A expansion — 5→7 conditions (2×3 + mitigation);
  400→560 runs. Qwen 2.5 7B → Qwen 3 8B with thinking mode OFF. Baseline
  sanity gate (80%) added. Locked-parameters table expanded with
  `num_predict=2048`, seed sequence, top_p/top_k, Gemma 2 mitigation mode,
  anchor matching rule, execution order, multi-turn retry behavior,
  Claude cross-rating stratified sampling + seed, circuit breaker, language
  expectation. Commit signing downgraded from optional-GPG to annotated
  (unsigned). Future-work backlog consolidated into `docs/FUTURE_WORK.md`.
