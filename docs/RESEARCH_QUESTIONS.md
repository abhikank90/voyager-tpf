# Research Questions and Hypotheses — Voyager-TPF v1.5

**Status:** Pre-registered. Written before any v1.5 benchmark runs. 

**Baseline release:** v1.4.1 (JOSS submission). New conditions land in v1.5.0.

---

## Conditions

| Condition | Detection | Feedback | Re-execution |
|---|---|---|---|
| `baseline` | Rules (audit only) | None | None — refinement disabled, first hub routes straight to final audit [1] |
| `full_rerun` | Rules | Typed messages to all agents | Every agent re-runs |
| `broadcast` | Rules | Hub's natural-language critique to all agents, no typed routing | Every agent re-runs |
| `targeted` | Rules | Typed constraint to the responsible agent(s) | Only targeted agents re-run |
| `two_way` *(ablation)* | Rules | Typed messages to both agents in a conflict | Both agents re-run |

All conditions use identical deterministic detectors at final audit (routing disabled), so
final conflict counts are directly comparable across conditions [1]. Corollary: reported
counts come from the same predicates that triggered each correction. H2c exists to close
that circularity.

**Targeting caveat (pre-registered):** in `targeted`, when ≥3 of 5 agents are implicated or a
message is addressed to all, the whole set re-runs anyway [1]. The `targeted` vs `full_rerun`
comparison is therefore reported twice: overall, and restricted to queries where the targeted
set was <3 agents.

**Cut:** `peer` mode. Paliskara et al.'s coordinator-vs-peer result is cited in related work
instead of re-tested in a single-user setting.

---

## RQ1 — Effectiveness and cost

**Question:** Does deterministic typed detection with single-agent routing reduce final
cross-agent conflicts compared with no coordination, and does it cost less than the obvious
alternatives?

**Hypotheses:**
- **H1a (superiority):** `targeted` yields fewer final conflicts per query than `baseline`.
- **H1b (non-inferiority):** `targeted` is non-inferior to `full_rerun` on final conflicts,
  with a margin of **0.1 conflicts/query**, while using fewer agent invocations, lower cost,
  **and producing fewer post-refinement introductions and reopened conflicts**. The
  introductions term is what makes targeting a quality claim rather than a savings
  observation.
- **H1c (superiority, primary novelty claim):** `targeted` yields fewer final conflicts than
  `broadcast` at equal or lower cost. Typed routing beats prose feedback.
- **H1d (predicted null):** dollar overhead vs `baseline` stays within run-to-run noise
  (operationally: the 95% CI of the paired cost difference includes zero or lies below it).
  **No latency advantage is predicted** for `targeted` vs `full_rerun`: a refinement round
  runs its agents concurrently, so its wall time is set by the slowest agent re-run, not by
  how many re-run [1], and final option synthesis is expected to dominate end-to-end time.
  Per-node wall time is measured for both inventories so this explanation is checked, not
  assumed.

**Margin justification (H1b):** 0.1 conflicts/query is 5% of the ~2.0 conflicts/query that
single-pass `baseline` ships on synthetic inventory, i.e. at most one additional conflict per
ten queries — below the level a user of the planner would notice, and smaller than any
difference that would change a practitioner's choice between the two designs.

**Metrics:** final conflicts per query (primary); resolution rate; post-refinement
introductions; reopened conflicts (fingerprint-based); agent invocations; tokens per model;
estimated dollars (per-model pricing, reconciled against billed usage; a regression test
fails when per-model costs do not sum to the session total [1]); end-to-end and per-node wall
time.

**Analysis:**
- **H1a, H1c:** paired per-query Wilcoxon signed-rank on final conflicts (zero differences
  handled with the Pratt method); Holm correction across these two comparisons.
- **H1b:** one-sided test via bootstrap: non-inferiority holds if the upper bound of the 95%
  bootstrap CI on mean(targeted − full_rerun) final conflicts is **< 0.1**. Introductions and
  reopened conflicts compared with paired bootstrap CIs.
- **All:** bootstrap 95% CIs on resolution rate, invocations and cost; effect sizes reported
  alongside p-values. Secondary metrics are exploratory: CIs reported, no significance claims.

**Inventory:** synthetic fixtures, real captured inventory, and clean controls (§ Controls).

---

## RQ2 — Limits and failure attribution

**Question:** When the pattern leaves a conflict unresolved, can the conflict-lifecycle
metrics attribute the failure to the option space ("no valid option exists") rather than to
the coordination mechanism?

**Hypotheses:**
- **H2a:** On satisfiable synthetic inventory, resolution is ≥97% within three rounds, with
  zero post-refinement introductions.
- **H2b:** On real inventory, every unresolved conflict carries a structured reason,
  predominantly `no_qualifying_option`, with the residue explained by fingerprint-tracked
  introductions rather than reopened conflicts. Success criterion: 100% of unresolved
  conflicts classifiable. (Descriptive; the claim is that the instrumentation separates the
  two causes.)
- **H2c (coherence audit):** "Resolved" is verified independently of the detectors. A
  stratified sample of 30 resolved outcomes (across inventories and conditions) is scored
  against a fixed rubric: temporal consistency, budget consistency, preference consistency,
  logistical feasibility; binary per dimension. Pre-registered threshold: **≥90% fully
  coherent**.
  - **Judge:** manual, scored by the author with the detector verdict hidden. A second rater
    (project co-contributor) independently scores 10 of the 30 under
    `docs/H2C_RATER_GUIDE.md`; percent agreement and Cohen's κ reported. No LLM judge, to
    avoid reintroducing model-on-model circularity. The second rater is not fully independent
    of the project; this is declared as a threat to validity.

**Metrics:** resolution rate with Wilson 95% CI, by inventory; classification distribution of
unresolved conflicts; introductions; reopened conflicts; convergence round distribution
(R1/R2/R3); audit coherence rate and inter-rater agreement.

---

## RQ3 — Reproducibility

**Question:** Can a multi-agent LLM benchmark be made replay-deterministic, and which sources
of variance remain?

**Hypotheses:**
- **H3a:** Conflict outcomes (detection, routing, resolution, fingerprints) are identical
  across replay runs. Target: 100% exact match.
- **H3b:** Cost and latency vary across replays, and that variance is attributable to
  generation-length nondeterminism in non-conflict agents.

**Metrics:** exact-match rate of conflict outcomes; coefficient of variation for cost and
latency, broken down by agent; fixture hash-verification pass rate.

**Comparisons:** capture vs replay; replay vs replay; author run vs independent reproduction.

---

## RQ4 — Rules vs LLM detection *(optional, word budget permitting)*

**Question:** What does an LLM-based detector add over the deterministic rules, and what does
it get wrong?

**Hypotheses:**
- **H4a:** Rules produce zero spurious conflicts on clean control queries; the LLM detector's
  false positives would trigger spurious paid re-runs under this pattern.
- **H4b:** The LLM detector catches conflict types the rules do not enumerate. "LLM proposes,
  rules validate" is therefore the defensible hybrid.

**Two levels of evaluation:**
- **Detector in isolation:** `scripts/eval_hybrid_detection.py` (default `claude-sonnet-4-6`,
  temperature 0), scored against hand-labeled probe states. Extends the 8-state pilot.
- **Detector as shipped:** the in-hub candidate detector, enabled with
  `VOYAGER_ENABLE_LLM_CONFLICT_CANDIDATES=1` (off by default in v1.4.1). It calls the model
  `llm_detector_repetitions` times (default 3) and takes the **union** of candidates, which
  raises recall and false positives together. Precision is therefore measured on the union,
  as shipped, with single-call precision reported alongside for comparison.

**Metrics:** per-type precision and recall against hand-labeled ground truth (planted
conflicts and clean controls); self-consistency across repeated calls at temperature 0;
projected cost of spurious re-runs; in the shipped configuration, the rate of LLM candidates
rejected by rule validation.

**Fallback:** if the word budget is too tight, this becomes two sentences in future work.

---

## Ablation — one-way vs two-way resolution

**Hypothesis:**
- **H5:** `two_way` increases oscillation relative to `targeted` (one resolution direction
  per conflict type): more reopened conflicts and more Round-3 usage, with no improvement in
  final conflicts.

**Metrics:** reopened conflicts; convergence round; final conflicts per query.

---

## Controls

10 clean queries (CTL-01 to CTL-10), no planted conflict, gold label "none". Used for spurious-conflict counts
in RQ1 (per condition) and detector precision in RQ4.

**New work required:** the existing synthetic fixtures are constructed so every query starts in
conflict, so the controls need new fixtures built and labeled for satisfiable, conflict-free
trips. Listed with gold labels in `results/v1.5/query_manifest.csv` alongside the synthetic
and real sets.

---

## Runs and sample size

- **Synthetic:** **5 runs per condition, equal across all conditions**, normal temperature,
  live (repeats are the variance measurement, not replays). Run with
  `VOYAGER_INVENTORY_MODE=mock` and **`VOYAGER_TEMPERATURE_OVERRIDE` unset**: replay mode
  pins every agent to temperature 0, and the override forces a single value, so either would
  make the repeats identical. Each run logs the effective temperature per agent so this can
  be checked. Agent defaults (v1.4.1): hub narrative 0.3, option generator 0.7, flight and
  visa/safety 0.
- **Real:** **25 queries** (the synthetic set with years removed, paired P01–P25), one new
  capture, then every condition runs in replay against it, so all conditions see identical
  inventory, intent and Round-1 experiences. 1 run per condition (replay pins temperature
  to 0).
- **Controls:** 3 runs per condition.
- **Replay determinism:** 2 full replays per condition under frozen fixtures; new fixture keys
  for `full_rerun` / `broadcast` / `two_way` recorded once with
  `VOYAGER_REPLAY_RECORD_MISSING=1`, then locked.
- **Unit of paired analysis:** per-query median across runs.
- **Model pins:**
  - `claude-sonnet-4-6`: intent parser, experience, hub, option generator, itinerary
    builder, and the RQ4 detector (temperature 0). Same model as the hub, because RQ4 asks
    whether the hub's own model should take over detection.
  - `claude-haiku-4-5-20251001`: flight, hotel, visa/safety.
  - **Pinning caveat:** Anthropic publishes Sonnet 4.6 under the undated canonical ID
    `claude-sonnet-4-6` (verified via `GET /v1/models`, June 2026; see
    `config/api_config.py`). No dated snapshot exists, so a date-suffixed ID cannot be used.
    To compensate, the `/v1/models` entry for each model ID (including its `created_at`) is
    saved to `results/v1.5/model_manifest.json` at the start and end of the benchmark window.
    A change between the two is declared as an RQ3 variance source. Haiku is pinned by its
    dated snapshot.

---

## Prior evidence (v1.4.x)

| Hypothesis | Evidence before v1.5 | What v1.5 adds |
|---|---|---|
| H1a | Strong: 100% vs 0% resolution on synthetic; 11% on real | Repeats, CIs, Wilcoxon test |
| H1b | Untested (`full_rerun` mode does not exist yet) | New result; margin and introductions pre-registered |
| H1c | Untested; primary novelty claim | New result; highest risk |
| H1d | Partial: hub cheaper and faster than baseline on real inventory (−2.8% cost, −4.7% latency) | Per-node timing confirms or refutes the explanation |
| H2a/b | Strong: 7 of 11 real queries tagged `no_qualifying_option`; fingerprints exposed introductions | Re-run on the larger real set |
| H2c | None; audit circularity unaddressed | New; independent human coherence check |
| H3 | Strong: v1.4 replay reproduced outcomes; ~12% cost/latency drift from capture | Re-verified on the new modes |
| H4 | Pilot only (8 probe states, `claude-sonnet-4-6`, temperature 0); in-hub candidate detector shipped behind a flag in v1.4.1, not yet benchmarked | Expanded probes, controls, and the shipped union-of-3 configuration |
| H5 | Anecdotal (first design oscillated) | Turned into a measurement |

---

## Reporting commitment

Every hypothesis is reported whatever the outcome, at the pre-registered thresholds:

- If **H1c** is not supported (broadcast matches targeted on final conflicts), the
  contribution claim narrows to "typed routing achieves equivalent resolution at lower cost
  and makes conflicts measurable."
- If **H1b** shows no introductions difference, the targeting claim narrows to cost and
  invocation savings, and the "targeting avoids new conflicts" argument is dropped.
- If **H1d**'s per-node timing contradicts the stated explanation, the explanation is
  withdrawn and the observed breakdown reported instead.
- If **H2c** falls below 90%, resolution numbers are reported as detector-relative, not
  absolute, and the audit result is presented as a threat to validity.
- A negative or null result on any primary hypothesis is reported in the results section,
  not moved to future work.

Support and failure thresholds are restated in `docs/EVALUATION_PROTOCOL.md` §9.

---

## Decisions log

- [x] Real-inventory capture: 25 queries, paired with the synthetic set (budget confirmed).
- [x] Second rater for H2c: project co-contributor, per `docs/H2C_RATER_GUIDE.md`.
- [x] Detector LLM for RQ4: `claude-sonnet-4-6`, temperature 0. Verified against v1.4.1
      (`d356ad2`): the eval script's default and the hub model already match. No dated
      Sonnet 4.6 snapshot exists; pinning handled via `model_manifest.json`.
- [x] Synthetic run count: 5 per condition, `mock` mode, no temperature override.

---

## Sources

[1] Voyager-TPF v1.4.1 source code and documentation, tag `v1.4.1` (`d356ad2`),
    github.com/abhikank90/voyager-tpf.
