# Voyager-TPF Evaluation Protocol — v1.5

**Version:** 1.0 · **Date:** 2026-11-15 · **Pre-registration tag:** `prereg-v1.5`
**Status:** PRE-REGISTERED. The pre-registration set is committed together, pushed, and
tagged `prereg-v1.5` before any v1.5 benchmark run:

- `docs/EVALUATION_PROTOCOL.md` (this file)
- `docs/RESEARCH_QUESTIONS.md` (research questions and hypotheses)
- `docs/H2C_RATER_GUIDE.md` (coherence audit procedure)
- `results/v1.5/query_manifest.csv` and `results/v1.5/query_manifest.README.md`
- `results/v1.5/gold_crosscheck_log.md` and `scripts/label_synthetic_gold.py`
- `results/v1.5/gold_labeling/evidence.jsonl` and `results/v1.5/gold_labeling/evidence.csv` (labeling-run evidence)

Implementation work (§10, build steps) may start before this commit; **no benchmark run
may**. The only pre-commit run is the gold-labeling run (§5.3), recorded in §13. Any
deviation after the commit is logged in §13 with reason and commit hash, and disclosed in the
paper's threats to validity.

---

## 1. System under test

- Release under evaluation: **v1.5.0** (to be tagged). Baseline reference: **v1.4.1**
  (`d356ad2`, JOSS submission).
- System: FastAPI backend and React front end around a LangGraph state machine.
  Pipeline nodes: personalisation, intent parser, five research agents (flight, hotel,
  experience, visa/safety, weather), Collaboration Hub, budget guardrail, option generator,
  itinerary builder.
- **Scope conditions (claimed, not tested beyond them):** conflicts must be expressible as
  deterministic predicates over agent outputs, and resolution is bounded by what the option
  space contains.
- **No prior-run numbers are used as anchors in this protocol.** All reported values come from
  `results/v1.5/`. Earlier figures (v1.0 article, v1.4.x README) are cited only in the prior
  evidence table of the RQ file.

## 2. Model pins and environment

| Role | Model | Temperature (v1.4.1 default) |
|---|---|---|
| Intent parser | `claude-sonnet-4-6` | 0 |
| Experience agent | `claude-sonnet-4-6` | 0.3 |
| Collaboration Hub (narrative) | `claude-sonnet-4-6` | 0.3 |
| Option generator | `claude-sonnet-4-6` | 0.7 |
| Itinerary builder | `claude-sonnet-4-6` | 0.4 |
| Flight, hotel, visa/safety | `claude-haiku-4-5-20251001` | 0 |
| RQ4 detector (both levels) | `claude-sonnet-4-6` | 0 |

- **Pinning caveat (pre-registered):** `claude-sonnet-4-6` exists only as an undated
  canonical ID (verified via `GET /v1/models`, June 2026; `config/api_config.py`). The
  `/v1/models` entry for each model ID, including `created_at`, is saved to
  `results/v1.5/model_manifest.json` at the **start and end** of the benchmark window.
- **Canary prompts (adopted):** 5 fixed prompts per model, temperature 0, fixed `max_tokens`,
  run at window start and end; output hashes stored in the manifest. This catches silent
  behavioural drift that `created_at` would not. A hash change on any canary is declared as
  an RQ3 variance source.
- **Temperature logging:** every session logs the effective temperature per agent call.
  Replay mode pins all agents to 0, and `VOYAGER_TEMPERATURE_OVERRIDE` forces one value for
  all; either would silently remove the variance the synthetic repeats exist to measure.
- **Tracing disabled for every session** (`LANGCHAIN_TRACING_V2=false`,
  `LANGSMITH_TRACING=false`), as in `scripts/reproduce.sh`. Trace-upload retries can add to
  measured wall time.
- **Date drift:** in v1.4.1 the parser's year varied with the run date (e.g. SYN-02 "March
  2026" parsed as March 2027 in the labeling run). From v1.5 the year is resolved in code from
  a fixed anchor (§5.2), so trip dates no longer depend on the run date. The parser's own year
  is still logged, and any remaining effect on LLM output is an RQ3 variance source.
- **Date anchoring:** see §5.2. Real sessions anchor to the capture's `replay_anchor_date`,
  independent of machine timezone; synthetic and control sessions anchor to
  `MOCK_ANCHOR_DATE`.

## 3. Conditions

| Condition | Config |
|---|---|
| `baseline` | `enable_refinement` off; first hub routes straight to final audit |
| `full_rerun` | hub detection on; typed messages to all agents; every agent re-runs |
| `broadcast` | hub's natural-language critique (as written for the UI) to all agents; no typed routing; every agent re-runs |
| `targeted` | the pattern: typed detection, addressed routing, rounds bounded by graph shape |
| `two_way` (ablation) | typed messages to both agents in a conflict; both re-run |

All conditions use identical deterministic detectors at final audit (routing disabled), so
final conflict counts are directly comparable. In `targeted`, if ≥3 of 5 research agents are
implicated or a message is addressed to all, the whole set re-runs; the `targeted` vs
`full_rerun` comparison is reported overall and restricted to queries where the targeted set
was <3 agents.

## 4. Replay keying requirement (must be fixed before any run)

In replay mode, LLM-generated research outputs are served from fixtures. The experience
fixture key (`agents/experience_agent.py`) is built from destination, interests, duration,
activity budget and `location_focus`. **It does not include the content of the agent's
inbox.** Consequence: in `broadcast`, `full_rerun` and `two_way`, a re-run experience agent
that receives feedback which does not change `location_focus` (for example, prose critique)
would be served its Round-1 output again. Those re-runs would be silent no-ops, biasing every
comparison against `targeted` toward a false win.

**Required change (v1.5):** for any re-run, the fixture key includes a hash of the canonical
JSON of the inbox messages the agent consumed (empty for Round 1, so existing Round-1 fixtures
stay valid). The same rule applies to every LLM output recorded as a fixture (`intent`,
`experiences`, and any source added for the new modes). A unit test asserts that two
different inboxes produce two different keys. New keys are recorded once with
`VOYAGER_REPLAY_RECORD_MISSING=1`, then locked and hash-verified.

## 5. Inventory

| Set | n | Source | Labels |
|---|---|---|---|
| Synthetic | 25 | v1.4.1 mock fixtures; every query planted in conflict; satisfiable by construction | hand-derived gold (§5.3) |
| Real | 25 | one new live capture (SerpApi flights, Nuitee hotels, Open-Meteo monthly climatology) | rule-derived at R1 audit (**not gold**) |
| Controls | 10 | **new mock fixtures**: satisfiable, conflict-free trips | gold label "none" |
| RQ4 probes | ≥20 | hand-built states extending the 8-state pilot, with coordinates | gold, hand-labeled |

Full list, provenance and pairing: `results/v1.5/query_manifest.csv` and its README.

### 5.1 Real query set and pairing

- The real set is the 25 synthetic queries with years removed (§5.2). `pair_id` P01–P25
  links each SYN row to its REAL row; the pairs are the synthetic-vs-real design for RQ2.
- REAL-01 to REAL-12 are the queries in the v1.4.1 capture, re-captured rather than reused;
  their v1.4.1 intent-fixture keys are in the manifest. REAL-13 to REAL-25 are new.
- The list is committed **before** the capture. No query is removed after capture except
  under §8.
- **Labels on real inventory are rule outputs, not ground truth.** They support RQ1–RQ3
  only. RQ4 precision and recall are computed on synthetic, controls and probes.

### 5.2 Trip dates and weather (all inventory modes)

**v1.4.1 defect.** Inventory searches ignored the requested month. `base_agent._effective_dates`
returned July 1–14 of the parsed year in mock mode and capture date + 90 days in capture and
replay. Every v1.4.1 real query was therefore searched for early-December dates whatever it
asked for, and every synthetic trip was dated July. Live weather was a forecast taken at
capture time, not the requested month.

**v1.5 rule (one function, all modes):**
- The LLM intent parser chooses the **month** (including seasons and named events, e.g.
  "summer", "cherry blossom season"). Its year is ignored.
- Code resolves the **year**: the next occurrence of that month strictly after the anchor
  month. A month equal to the anchor month resolves to the following year.
- Departure is the **15th** of the resolved month; return is departure + `duration_days`
  (default 13, as in v1.4.1).
- "any month" (parsed month = None) keeps the v1.4.1 behaviour: anchor + 90 days. The
  resolved window is recorded.
- **Anchors:** capture uses the capture date, saved as `replay_anchor_date`; replay reads it
  from the manifest; mock uses the fixed constant `MOCK_ANCHOR_DATE = 2026-12-01`, committed
  in config. Synthetic dates therefore no longer drift with the run date.
- Year-stripping in the real query text (REAL rows) is kept so the parser is never handed a
  past year, but the resolved window does not depend on it.
- **Check:** every session asserts that departure is after its anchor; a failure is handled
  under §8.

**Weather for the resolved month (capture):** monthly climatology from the Open-Meteo
historical archive (`archive-api.open-meteo.com/v1/archive`, ERA5, non-commercial, no key),
for the destination's geocoded coordinates (OpenWeather `/geo/1.0/direct`, as in v1.4.1):
- `avg_temp_c` = mean of the daily maximum temperature over that month in 2016–2025, matching
  the scale of the mock climate table (daytime highs) and the 32°C heat-advisory threshold.
- `precipitation_mm` = mean monthly precipitation total over 2016–2025.
- `summary` = empty. The rain-keyword path of the weather rule therefore cannot fire on real
  inventory; only the numeric thresholds apply. This asymmetry with mock data is declared as
  a threat to validity.
- Fixture key: (destination, resolved month). Recorded and replayed like other sources.

**Mock weather** is unchanged (per-month climate table), so it now agrees with the resolved
month by construction.

- The v1.5 capture records every source (inventory, intent, experiences, weather) in one pass.

### 5.3 Synthetic gold labels

- Gold is **hand-derived from mock fixture data and recorded evidence, never from rule
  output**, because the final audit runs the same detectors that trigger corrections.
- **Labeling run (pre-commit, recorded in §13):** one baseline mock pass over the 25
  synthetic queries with `scripts/label_synthetic_gold.py`. The script records raw evidence
  only (parsed month, weather values, selected arrival time, hotel location, top experience
  locations, names and categories) plus the observed R1 conflict types. It does not
  re-implement any rule. Gold is assigned by hand from the evidence, and every disagreement
  with the observed types is resolved in `gold_crosscheck_log.md`.
- **Gold strength by type:**
  - `timing_inefficiency` and `weather_activity_mismatch`: full gold. They are fixed by the
    fixtures, the parsed month and the experience list.
  - `location_mismatch`: **construct gold**. The mock hotel's location is the string
    "Beachfront" with no coordinates, so the label records that the harness planted a
    mismatch, not geographic distance. It also depends on LLM-generated experience locations,
    so it is re-checked per session in the benchmark runs. Geographic gold for this rule
    comes from the RQ4 probe states.

## 6. Runs and sample size

- **Synthetic:** 5 live runs per condition, equal across conditions;
  `VOYAGER_INVENTORY_MODE=mock`, `VOYAGER_TEMPERATURE_OVERRIDE` unset.
- **Controls:** 3 live runs per condition, same settings.
- **Real:** **one capture, then every condition runs in replay against it.** All conditions
  therefore see identical inventory, identical parsed intent and identical Round-1
  experiences, which makes the per-query comparison truly paired. (In v1.4 the baseline and
  full runs hit the APIs separately, so their Round-1 states differed.) Replay pins
  temperature to 0, so one run per condition; run-to-run variance is measured on synthetic.
- **Replay determinism (RQ3):** 2 further full replays per condition on the real set.
- **Unit of paired analysis:** per-query median across runs (synthetic, controls); the single
  replay value (real).

## 7. Metric definitions (exact)

- **final_conflicts** *(primary endpoint)*: rule-detected conflicts in the final plan state
  (R1 audit state for `baseline`).
- **resolution_rate_conflict**: R1-detected conflicts whose lifecycle status is `resolved` at
  final audit / R1-detected conflicts. Primary for H2a.
- **resolution_rate_query**: conflicted queries with zero conflicts at final audit /
  conflicted queries. Reported alongside; this is the definition behind the v1.4 "11%".
- **introductions**: fingerprints first seen after the R1 audit (status `new` in a later
  audit).
- **reopened**: fingerprints with lifecycle status `reopened`.
- **rounds_to_converge**: distribution over R1 / R2 / R3 / not converged.
- **invocations**: research-agent LLM calls; hub calls counted separately.
- **tokens_in / tokens_out**: per model, from API usage metadata.
- **cost_usd**: per-model pricing (`metrics/token_tracker.MODEL_PRICING`), reconciled against
  billed usage for the window; the conservation regression test must pass.
- **wall_s**: end-to-end and per node.
- **unresolved_reason** (every conflict not `resolved` at final audit gets exactly one):
  `no_qualifying_option` (emitted by the agent's fallback), `introduced_late`,
  `reopened`, `round_limit` (still persisting after R3 with a satisfiable constraint),
  `routing_miss` (no message reached an agent able to change the conflicting field).
  v1.4.1 emits `no_qualifying_option` and the lifecycle statuses `new`, `persisting`,
  `resolved`, `reopened`; **the other reasons are a v1.5 build item** (§10).

## 8. Exclusions and failure handling (pre-registered)

- API errors and timeouts: up to 3 retries with exponential backoff, inside the session.
- A session that still fails is logged to `results/v1.5/failures.csv` and re-run once.
- If the re-run also fails, that query is dropped **from every condition** in that inventory,
  so pairing is preserved. Dropped queries and their reasons are reported in the paper.
- No session is excluded for its outcome. Results are never re-run because they look wrong;
  a suspected bug is fixed, logged in §13, and the affected condition re-run in full.

## 9. Statistical analysis (pre-committed)

**Confirmatory family:** H1a and H1c on both the synthetic and real sets (four tests), Holm
correction across the four. Everything not listed as confirmatory is exploratory: CIs
reported, no significance claims.

| H | Test | Success criterion |
|---|---|---|
| H1a | Paired Wilcoxon signed-rank on final_conflicts, Pratt zero handling | `targeted` < `baseline`, Holm-adjusted p < 0.05, each inventory |
| H1c | Same | `targeted` < `broadcast`, Holm-adjusted p < 0.05, each inventory |
| H1b | One-sided bootstrap on mean(`targeted` − `full_rerun`) final_conflicts | upper bound of 95% CI < 0.1 |
| H1b quality | Paired bootstrap on (`targeted` − `full_rerun`) introductions and reopened | upper bound of 95% CI < 0 |
| H1b cost | Paired bootstrap on invocations and cost_usd | upper bound of 95% CI < 0 |
| H1d | Paired bootstrap on cost_usd (`targeted` − `baseline`) | CI includes 0 or lies below it; per-node timing reported to check the stated explanation |
| H2a | resolution_rate_conflict, synthetic, Wilson 95% CI | point estimate ≥ 97%; introductions = 0 |
| H2b | unresolved_reason distribution, real | 100% of unresolved conflicts carry a reason; Wilson CIs on each rate |
| H2c | See §11 | ≥ 90% fully coherent on assessable plans |
| H3a | Exact match of detection, routing, resolution and fingerprints across 2 replays | 100% |
| H3b | Per-agent CV of cost and latency across replays | ≥ 80% of across-replay cost variance comes from agents not involved in any conflict for that query |
| H4 | Precision / recall vs gold (synthetic, controls, probes), Wilson CIs; self-consistency at temperature 0 | rules: 0 spurious on controls; LLM recall gain and false-positive re-run cost both reported |
| H5 | Paired Wilcoxon on reopened and R3 usage, `two_way` vs `targeted` (exploratory) | `two_way` higher on both; final_conflicts not lower |

Bootstrap: 10,000 resamples, percentile intervals, fixed seed committed in the analysis
script. Effect sizes (median paired difference; matched-pairs rank-biserial for Wilcoxon) are
reported alongside every p-value.

## 10. Execution runbook (order is load-bearing)

**Build (may start before the pre-registration commit):**
1. Implement `full_rerun`, `broadcast`, `two_way`.
2. Replay keying fix (§4) with its unit test.
2a. Trip-window resolution (§5.2): one `resolve_trip_window(intent, anchor)` used by flight,
    hotel and weather agents in every mode; `MOCK_ANCHOR_DATE` in config; departure-after-
    anchor assertion. Unit tests: month-to-year resolution including the same-month case,
    the "any month" fallback, and that two different parsed months give two different
    windows and fixture keys.
2b. Climatology weather source for capture (§5.2), with its fixture key and a unit test on a
    recorded response.
3. Emit `unresolved_reason` for every non-resolved conflict (§7).
4. Per-node wall time and per-call effective-temperature logging.
5. Build and label the control fixtures; build the RQ4 probe states.
6. `model_manifest.json` snapshot and canary script.
7. `scripts/paper_numbers.py` (or a `make paper-numbers` target; no Makefile exists in
   v1.4.1) that regenerates every reported value from `results/v1.5/`.
8. Blinded-packet builder for the H2c audit.

**Pre-registration:**
8a. Gold-labeling run (§5.3); fill `gold_crosscheck_log.md`; copy final gold into the
    manifest.
9. Commit the full pre-registration set (header list) in **one commit**, push the branch,
   and tag it `prereg-v1.5`. Optionally deposit the set on Zenodo for an independent
   timestamp.

**Run (only after step 9):**
10. Smoke test: 2 queries × every condition, written to `results/v1.5/pilot/`, never analysed.
11. Model manifest and canaries, window start.
12. Real capture (one run, `capture` mode), then lock and hash-verify fixtures.
13. Synthetic 5×, controls 3×, real replay 1× per condition. Record missing keys once
    (`VOYAGER_REPLAY_RECORD_MISSING=1`), then lock.
14. Two further replays per condition on the real set.
15. Model manifest and canaries, window end.
16. Billing reconciliation for the window.
17. H2c audit (§11).
18. Statistics per §9 via `paper_numbers.py`.
19. Tag v1.5.0; CHANGELOG; README results section pointing at the exact files behind each
    table and figure; Zenodo DOI.

## 11. H2c coherence audit (procedure)

Full rater instructions in `docs/H2C_RATER_GUIDE.md`. Operational rules:

- **Sample:** 30 resolved outcomes, stratified by inventory × condition, drawn with a fixed,
  committed seed.
- **Blinding:** packets are built by the committed script from step 8, which strips condition
  labels, run IDs, round counts, fingerprints and detector output. **The author scores from
  the same blinded packets**, so both raters are blinded identically.
- **Double-scoring:** 10 of the 30, drawn with the same seed before either rater starts;
  second rater is the project co-contributor.
- **Calibration:** 2 plans outside the sample (one coherent, one with a clear D4 failure);
  reference scores written before the rater sees them.
- **U handling:** a plan with any `U` dimension is "not fully assessable". The ≥90% threshold
  is computed on assessable plans; the U rate and the U-as-coherent variant are reported too.
- **Agreement:** percent agreement is primary; Cohen's κ is secondary, reported with the
  caveat that κ is unstable at n = 10 with skewed marginals.
- **Contingency:** if percent agreement is below 80% on any dimension, rubric wording is
  clarified (logged in §13) and a further 10 plans are double-scored.
- **Declared threat:** the second rater is not fully independent of the project.

## 12. RQ4 detector evaluation (procedure)

1. **Isolation:** `scripts/eval_hybrid_detection.py`, `claude-sonnet-4-6`, temperature 0, on
   the ≥20 probe states plus controls. Findings from the 8-state pilot to re-test: full
   self-consistency at temperature 0; proposed conflict types the rules do not cover; a missed
   late-arrival timing conflict; a budget violation asserted on a control where none existed.
2. **As shipped:** the in-hub candidate detector, `VOYAGER_ENABLE_LLM_CONFLICT_CANDIDATES=1`
   (off by default in v1.4.1), `llm_detector_repetitions = 3`, union of candidates.
   Precision is measured on the union, as shipped, with single-call precision alongside. The
   rate of LLM candidates rejected by rule validation is reported.

## 13. Deviation log and policy

### Pre-registration activities

Runs made before the step-9 commit, for labeling only. Their outputs are never analysed as
results.

| Date | Activity | Purpose | Output |
|---|---|---|---|
| 2026-11-15 | Baseline mock run, 25 synthetic queries, `scripts/label_synthetic_gold.py`, on v1.4.1 code (~$4–6) | Evidence for hand-derived gold (§5.3) | `results/v1.5/gold_labeling/`, `gold_crosscheck_log.md` |

The labeling run predates the §5.2 date fix. Its gold is unaffected: in mock mode the arrival
hour, hotel location and per-month climate do not depend on the trip window, and the weather
label follows the parsed month, which the fix does not change. The step-10 smoke test
re-checks the synthetic gold on v1.5 code; any mismatch is logged here and resolved before
the main runs.

### Deviations

Any departure from this document after the step-9 commit is recorded here (date, deviation,
reason, commit hash) and disclosed in the paper's threats to validity. Deviations discovered
after the fact are still disclosed, marked as unlogged.

| Date | Deviation | Reason | Commit |
|---|---|---|---|
| 2026-11-15 | `broadcast` critique is the detected conflicts rendered as prose (no typed data, no addressing), not the UI narrative; flight and hotel interpret it with their own LLM, recorded as fixtures in capture/replay. | The UI narrative is built without flight arrival times or experience locations, so it cannot express two of the three conflict types; using it would confound H1c with an information difference rather than test typed routing. | 60ed0ed |
| 2026-11-15 | `two_way` re-runs exactly the agents it messages, without the ≥3-agent full re-run. | With location and timing conflicts it messages three agents, which would trigger a full re-run and make H5 differ from `targeted` by more than the second resolution direction. | 60ed0ed |
| 2026-11-16 | Stay length is the trip window's nights (13 by default) for hotel pricing, experiences, option costs and itinerary dates under the v1.5 month rule. | Hotel totals were priced for 14 nights against a 13-night check-in/out, and the itinerary always used July dates, so delivered plans contradicted the searched dates (relevant to H2c coherence). | a036695 |
| 2026-11-16 | Option-generator `max_tokens` raised from 3000 to 8192; template fallback itineraries are now marked (`itinerary_fallback`, `fallback_reason`) and logged per session (`itinerary_fallback_styles`). | At 3000, every 13-day itinerary was truncated and replaced by a placeholder template, so H2c would have rated templates; marking makes any remaining fallback visible and excludable from interpretation. Round-1 message and evidence logging and `feedback_by_round` were also corrected; these fix the instrumentation, not the metric definitions. | 9d36b12 |
| 2026-11-16 | Clarification (threat to validity, no code change): no condition carries constraints across rounds. Each re-run agent receives only that round's messages, so in `full_rerun` and `broadcast`, a Round-3 re-run of an agent whose Round-2 conflict was resolved can reintroduce it (logged as `reopened`). `targeted` avoids this only by not re-running that agent. | This is inherent to the pre-registered condition definitions (§3) and is part of what H1 compares; declared so that `reopened` counts are read with it in mind. | — |
| 2026-11-16 | Budget (§14) re-estimated. Mock smoke sessions now cost about $0.25–0.33 (mean $0.30, up from $0.20) because itineraries are actually generated. Revised LLM total is about $330–380 (synthetic ~$170–200, controls ~$40–50, real replays ~$75–95, other ~$40). | §14 assumed v1.4 per-query costs ($0.11–0.18), which included truncated itineraries. Actual spend is still reconciled against billing at step 16. | — |
| 2026-11-17 | The option generator's three itineraries are generated concurrently (the sync Anthropic client is run in worker threads and gathered), instead of three sequential calls. | Outputs, prompts, temperature and the token cap are unchanged; only the wall time of the `option_generator` node drops, which affects reported `wall_s` (and therefore latency comparisons). Declared so reported wall time is read with this change in mind. | 75485bd |

## 14. Budget estimate

| Item | Sessions | Approx. cost |
|---|---|---|
| Synthetic: 25 × 5 conditions × 5 runs | 625 | $130–160 (`full_rerun`, `broadcast` and `two_way` cost more than `targeted`) |
| Controls: ~10 × 5 × 3 | ~150 | ~$25 |
| Gold-labeling run: 25 × 1 | 25 | ~$4–6 |
| Real capture (LLM side) | 25 | ~$5, plus SerpApi and Nuitee quota |
| Real replays: 25 × 5 conditions × 3 | 375 | ~$45–60 |
| RQ4 detector, smoke tests, canaries | — | ~$10 |
| **Total (LLM)** | | **~$215–265** |

Session costs are based on the v1.4 averages ($0.11–0.18 per query). Actual spend is
reconciled against billing in step 16.

---

## Sources

[1] Voyager-TPF v1.4.1 source code and documentation, tag `v1.4.1` (`d356ad2`),
    github.com/abhikank90/voyager-tpf.
    