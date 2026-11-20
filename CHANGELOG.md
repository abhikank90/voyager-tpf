# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.0] - 2026-11-20

The v1.5 evaluation window. The full protocol is in
`docs/EVALUATION_PROTOCOL.md`; every number, with its inputs and method, is in
`results/v1.5/paper_numbers.{json,md}`.

### Added

- Condition runs (`baseline`, `targeted`, `full_rerun`, `broadcast`, `two_way`)
  over 25 synthetic queries (mock inventory), 5 runs per condition: 625 sessions.
- Control profiles (10 control queries × 5 conditions × 3 runs): 150 sessions.
- Real-inventory lane: 1 capture (25 sessions, 10 recorded) plus 3 replays of the
  25 real queries × 5 conditions (375 sessions), replayed from the committed
  `fixtures/live_inventory_v1.5/` fixture set.
- H2c coherence audit: 30 plans sampled from resolved outcomes (seed 20261117),
  20 double-scored across two rounds.
- RQ4 hybrid-conflict-detector evaluation: 32 probes, deterministic rules vs
  LLM candidates (isolation and shipped variants), with a second labeler.
- Billing reconciliation and per-component usage accounting
  (`scripts/reconcile_billing.py`, `results/v1.5/billing_reconciliation.*`).

### Reported outcomes

As `paper_numbers` reports them: H2a pass, H2b pass, H2c fail, H3a pass,
H3b pass. H1/H4/H5 carry no single pass flag; see the numbers.

### Deviations (logged in §13)

Aborted first window (key exposure); capture-lane defects and their fixes;
the narrowed capture guard; real-set provenance and sensitivity strata; the
RQ4 shipped stub-client re-run; the `paper_numbers` candidate-field fix; the
second labeler's self-check; the billing reconciliation fail (+1.18%) and its
itemization; H2c eligibility and mock-only sample; the H2c contingency round.

## [1.4.2] - 2026-11-16

### Fixed

- Trip options showed $0 for flights, hotels and experiences. The option
  generator read `price` and `price_per_night`, which no agent writes; it now
  reads `price_usd`, `total_usd`, `price_per_night_usd` and `cost_usd`.
- Trip options could use a different flight or hotel from the one the
  refinement rounds settled on, so a delivered option could still contain a
  conflict that the final audit reported as resolved. All three options are now
  built on the coordinated `selected_flight` and `selected_hotel`; the styles
  differ in experiences, food and extras.
- The itinerary prompt now includes the flight's arrival time, the hotel's area
  and the month's weather, and asks for Day 1 to start after arrival.

Conflict detection, routing and the `scripts/reproduce.sh` metrics are
unchanged: the option generator runs after the final audit.

## [1.4.1] - 2026-11-14
- Added JOSS paper (paper/paper.md) and draft-PDF workflow.

### Changed

- Project renamed from `voyager-travel-agent` to **Voyager-TPF** (repository
  `abhikank90/voyager-tpf`, package `voyager-tpf`) to distinguish it from other
  projects named Voyager. GitHub redirects the old repository URL.

## [1.4.0] - 2026-10-16

### Added

- Replay now records intent and experiences as fixtures, so LLM-generated
  research outputs are replayed instead of regenerated. Intent parser outputs recorded in
  `*_intent.json`, experience agent outputs in `*_experiences.json`.
  Consecutive replay runs produce identical Round-1 conflict types and
  final counts on all 24 query pairs.
- Timezone-independent replay anchor date (fixtures/live_inventory manifest)
  enables stable replay across timezones.
- Research-round exceptions are recorded in `state["errors"]` instead of
  being silently dropped.

### Changed

- `benchmarks/expected_replay.json` now holds the single reproducible v1.4
  result instead of midpoints across varying v1.3 runs. The v1.4 run matches
  v1.3 run 1 exactly; expected values move accordingly
  (unsatisfiable_constraint_rate_pct 64 → 67, baseline conflicts
  1.28 → 1.25, Round-1 location_mismatch 17 → 16).
- Per-query tolerance for q0 removed: its variance came from the experience
  list being regenerated on every replay run.

### Fixed

- Replay non-determinism from regenerating LLM outputs at temperature 0
  on each run. Reported by @KalyanMatta-SDE (#23).

## [1.3.0] - 2026-10-03

### Fixed

- Round 3 now re-runs only the designated resolver for each surviving
  conflict type, matching the documented one-direction rule; it previously
  re-ran all conflict participants (#11). Measured post-fix (three replay
  runs, results/v1.3/): post-refinement introductions 0.25–0.42 → 0.0/query,
  agent-call savings 36.9% → 45.2%, mean re-executed agents 3.46 → 2.42;
  resolution rate unchanged at 10%, reopened conflicts 0.

## [1.2.0] - 2026-10-02

### Added

- One-command benchmark reproduction: scripts/reproduce.sh with
  tolerance-aware expected-value checks (compare_results.py,
  benchmarks/expected_replay.json)
- DESIGN.md — design rationale, trade-offs, and rejected alternatives
- CONTRIBUTING.md, CHANGELOG.md, Support & Governance section, codemeta.json
- Replay verification artifacts (results/v1.2/)

### Changed

- Replay is date-stable: query dates anchor to the fixture manifest's
  capture date, so replay works on any day after capture
- Replay decoding pinned to temperature 0 via the central
  effective_temperature() resolver (override: VOYAGER_TEMPERATURE_OVERRIDE)
- README: research-testbed positioning, statement of need, reproduce docs
- Canonical Zenodo concept DOI across README and CITATION.cff

### Fixed

- Replay fixtures unfindable across calendar days (query dates were derived
  from the wall clock)
- Benchmark runner applies query offset before limit
- Ruff E501 regressions from the temperature-routing change

## [1.1.0] - 2026-09-05

Tag: `v1.1-infoq`. Live capture/replay benchmark work (real API inventory,
hash-verified fixtures, deterministic replay).

### Added

- v1.1 live inventory capture/replay: SerpApi + Nuitee + OpenWeather providers,
  hash-verified fixtures, and conflict lifecycle tracking
  ([e532da9](https://github.com/abhikank90/voyager-tpf/commit/e532da9))
- v1.1 benchmark artifacts: mock + capture + replay (11 queries)
  ([2f04296](https://github.com/abhikank90/voyager-tpf/commit/2f04296))

### Changed

- Gate geocoding to capture/replay modes; mock mode stays offline deterministic
  ([cde0608](https://github.com/abhikank90/voyager-tpf/commit/cde0608))
- Align eval model with benchmark model (claude-sonnet-4-6)
  ([c43d406](https://github.com/abhikank90/voyager-tpf/commit/c43d406))
- Update README, GETTING_STARTED, ARCHITECTURE, API_REQUIREMENTS, TESTING for v1.1
  ([2f11d11](https://github.com/abhikank90/voyager-tpf/commit/2f11d11),
  [bc14e92](https://github.com/abhikank90/voyager-tpf/commit/bc14e92))

## [1.0.0] - 2026-05-15

Initial public release. Tags: `v1.0`, `v1.0-infoq`. Covers work from the
initial commit (2026-05-15) through the Zenodo DOI registration (2026-07-08).

### Added

- Initial commit: Voyager Travel Agent v1
  ([7e3f414](https://github.com/abhikank90/voyager-tpf/commit/7e3f414))
- Metrics instrumentation for InfoQ article benchmarks
  ([9fff1ff](https://github.com/abhikank90/voyager-tpf/commit/9fff1ff))
- Close open-circuit feedback loop: research agents now read collaboration
  messages ([6c70c38](https://github.com/abhikank90/voyager-tpf/commit/6c70c38))
- MIT License ([34a6064](https://github.com/abhikank90/voyager-tpf/commit/34a6064))
- ARCHITECTURE.md and README feedback-loop details
  ([fc8b75a](https://github.com/abhikank90/voyager-tpf/commit/fc8b75a))
- Per-model pricing, complete token tracking, round 2/3 duration
  instrumentation, pinned per-agent models via config, conflict evidence in
  sessions, hybrid detection eval script
  ([d770645](https://github.com/abhikank90/voyager-tpf/commit/d770645))
- CITATION.cff and Zenodo DOI
  ([ce5553b](https://github.com/abhikank90/voyager-tpf/commit/ce5553b),
  [c56f593](https://github.com/abhikank90/voyager-tpf/commit/c56f593))

### Changed

- Update architecture diagram width to 100%
  ([bc0a339](https://github.com/abhikank90/voyager-tpf/commit/bc0a339))
- Update demo GIF to 3x speed (71.7s)
  ([6ce9903](https://github.com/abhikank90/voyager-tpf/commit/6ce9903))
- Update README with latest benchmark details
  ([5b703f2](https://github.com/abhikank90/voyager-tpf/commit/5b703f2))
- Update ARCHITECTURE.md to reflect latest changes
  ([d443e37](https://github.com/abhikank90/voyager-tpf/commit/d443e37))
- Add benchmark methodology disclosure, model ID comments, and docstring note
  ([58b4d74](https://github.com/abhikank90/voyager-tpf/commit/58b4d74))
- Remove iteration-leftover diagram and demo-gif scripts
  ([e8a643f](https://github.com/abhikank90/voyager-tpf/commit/e8a643f))

### Fixed

- Structural bugs in conflict detectors and metrics instrumentation
  ([01017ad](https://github.com/abhikank90/voyager-tpf/commit/01017ad))
- Hotel resolution window mismatch; selection is now constraint-aware
  ([895a952](https://github.com/abhikank90/voyager-tpf/commit/895a952))
- CI pipeline: feedback loop, lint, tests, coverage, and frontend build
  ([9327668](https://github.com/abhikank90/voyager-tpf/commit/9327668))
- Frontend build: remove unused imports flagged by tsc
  ([2d87f9e](https://github.com/abhikank90/voyager-tpf/commit/2d87f9e))
- Docker deploy job no longer blocks when AWS creds are absent
  ([d879335](https://github.com/abhikank90/voyager-tpf/commit/d879335))
- Per-model cost computation and conflict evidence payloads
  ([ffe46ed](https://github.com/abhikank90/voyager-tpf/commit/ffe46ed))
- Cost tracking gaps: model attribution, missing callbacks, leak-proof session
  cost, regression tests
  ([cb877f9](https://github.com/abhikank90/voyager-tpf/commit/cb877f9))