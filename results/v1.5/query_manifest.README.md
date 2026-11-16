# query_manifest.csv — provenance and procedures

Pre-registered 2026-11-15 (tag `prereg-v1.5`) in one commit with `docs/EVALUATION_PROTOCOL.md`,
`docs/RESEARCH_QUESTIONS.md` and `docs/H2C_RATER_GUIDE.md`. The CSV is kept strictly
parseable (no comment lines); all context lives here.

## 1. Provenance and v1.4.1 continuity

- Real set = first 12 entries of `scripts/benchmark_queries.py` @ tag v1.4.1 (`d356ad2`),
  re-captured for v1.5; 13 new queries added by trip type.
- v1.4.1 fixture traceability: `fixtures/live_inventory/manifest.json`,
  `replay_anchor_date` 2026-09-04. Fixture identity is a content hash of
  (source, canonical query params). The `v1_4_1_intent_fixture` column gives the
  16-character key of each kept query's intent fixture
  (`fixtures/live_inventory/<key>_intent.json`), matched on `raw_query`. The v1.4.1 capture
  contains exactly one intent fixture per kept query.
- v1.4.1 inventory (SerpApi, Nuitee, OpenWeather) was captured 2026-09-04/05; the intent and
  experience fixtures were backfilled 2026-10-14/15 with `VOYAGER_REPLAY_RECORD_MISSING`.
  The v1.5 capture records every source in one pass.
- **Known v1.4.1 defect motivating re-capture.** Against the 2026-09-04 anchor:
  - By query text, 8 of 12 kept queries name a month already past.
  - By the recorded intent (what the agents actually used), **6 of 12 were past-dated**
    (REAL-01, 03, 06, 07, 08, 09), 1 fell in the anchor month (REAL-05), and 5 were future.
    Of the future ones, the parser rolled two past-named months forward to 2027 (REAL-02,
    REAL-12), and resolved "winter 2026" to December 2026 (REAL-10). REAL-04 and REAL-11
    were future as written.
  - The parser's inconsistency (rolling some past months forward but not others) is why v1.5
    resolves the year in code (§2).
  - More important: v1.4.1 inventory searches ignored the month altogether, so the past-dated
    intents affected only the LLM agents. Flights and hotels for all 12 queries were searched
    for capture date + 90 days (early December 2026).

## 2. Trip dates (protocol §5.2)

REAL rows are the corresponding SYN rows (same `pair_id`) with the year removed; the query
text otherwise matches word for word. The intent parser chooses the month (including seasons
and named events); code resolves the year as the next occurrence after the anchor month (a
month equal to the anchor month goes to the following year) and searches the 15th of that
month for `duration_days`. "any month" falls back to anchor + 90 days. Every session asserts
that departure is after the anchor.

v1.4.1 never used the month for inventory: mock trips were dated July 1–14 and captured
trips capture date + 90 days. The month-by-month notes in the CSV describe the expected
resolution under the v1.5 rule.

## 3. `v1_4_1_intent_fixture` column

Filled for REAL-01 to REAL-12. Blank means the query was not in the v1.4.1 capture. To
re-derive a key, use `inventory_query_id("intent", ...)` with the parser's canonical
parameters (`agents/intent_parser.py`, `agents/inventory.py`), or match `raw_query` inside the
intent fixtures.

## 4. Synthetic gold labels (RQ4 ground truth)

Gold is **hand-derived from the mock fixture data, never from rule output.** The final audit
runs the same deterministic detectors that trigger corrections, so rule output cannot
validate the rules [1].

Procedure per row, with one line of evidence recorded per label:
(a) hotel location vs. the activity cluster;
(b) arrival time vs. the 20:00 cutoff;
(c) fixture month vs. fixture weather.

Labeled 2026-11-15: all 25 rows are `location_mismatch;timing_inefficiency`, matching the
observed R1 types with no disagreements. `weather_activity_mismatch` has no positive case in
the synthetic set (SYN-01 was parsed as June), so weather recall for RQ4 comes from probe
states.

**Cross-check:** one baseline mock run (`--mode baseline --inventory mock`; inventory is
mocked but LLM calls are live, about $4–6) compared against the hand labels. Every
disagreement is logged and resolved by hand in `gold_crosscheck_log.md`, committed alongside.
The baseline run is a labeling aid, not a benchmark result, and is recorded under "Pre-registration activities" in protocol §13.

Why independent gold matters, from the 8-state pilot [1]: the LLM detector **missed** a
late-arrival timing conflict that the rules catch, and **asserted** a budget violation on a
control where none existed. Only labels independent of both detectors can score both kinds of
error.

## 5. Controls

10 conflict-free mock fixtures to be built: hotel within the activity cluster, arrival before
14:00, forecast below the heat-advisory threshold with no rain on outdoor days, and dates
matching the stated month. Gold label "none". CTL-06 (Sydney, December) is southern-hemisphere
summer; its fixture must keep temperatures below the advisory threshold.

## Sources

[1] Voyager-TPF v1.4.1 source code, fixtures and `scripts/eval_hybrid_detection.py`, tag
    `v1.4.1` (`d356ad2`), github.com/abhikank90/voyager-tpf.
