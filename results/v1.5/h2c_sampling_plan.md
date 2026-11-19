# H2c sampling plan (v1.5, §11) — committed before the draw

## Seed

`20261117` — the analysis seed already committed in `scripts/paper_stats.py`
(commit `cf3c626`, "v1.5: paper stats core (scipy)") before the run window. No seed
was chosen after seeing any session outcome.

## Inputs

- `results/v1.5/runs/synthetic/sessions.jsonl`
- `results/v1.5/runs/control/sessions.jsonl`
- `results/v1.5/runs/real/sessions.jsonl`

(the combined files, not the per-replay files — no duplicates). Plans dir:
`--plans-dir results/v1.5/plans`.

## Draw

`--n 30 --double 10`. Sample size per stratum is proportional to the stratum's
eligible size, with at least one plan per non-empty stratum (largest-remainder).

## Eligibility rule (explicit)

A session is eligible for the H2c sample iff **all** of:

1. `refinement_mode`/`condition` is not `baseline`;
2. `round_1_conflict_count` ≥ 1;
3. `final_conflict_count` == 0 (fully resolved);
4. a saved plan exists at `results/v1.5/plans/<session_id>.json`;
5. that saved plan contains a `balanced` trip option (the packet shows the
   `balanced` option — the docstring's "delivered plan… shows the balanced option"
   is now enforced, not implied).

Sessions that fail any condition are excluded and counted, never rendered.
Exclusion reasons: `baseline`, `no_round1_conflict`, `unresolved_final`,
`no_plan_file`, `no_balanced_option`, `calibration_excluded`.

## Calibration leakage check

- `cal_B.md` is built from session `de43fd9b-bf9f-453a-b459-7a6d8cb6e0a4`
  (`results/v1.5/h2c_calibration/cal_B_source.json`). That session id does **not**
  occur in any of the three input files, so it cannot be in the eligible pool.
- `cal_A.md` is the Lisbon control (`CTL-01`). No `CTL-01` session in the control
  file has a Round-1 conflict (checked: 0 of 15), so every `CTL-01` session is
  excluded by `no_round1_conflict` and cannot be eligible. (The single eligible
  control session is CTL-05, the disclosed spurious fire.)

Neither calibration plan is in the eligible pool. `--exclude-sessions` remains
available to force-exclude a session id (reported as `calibration_excluded`) should
one ever enter the pool.

## Pre-check numbers (before the draw)

Using this rule, the planner's eligibility pre-check reported: synthetic 332,
control 1 (the CTL-05 spurious session), real 9 (all empty-inventory sessions whose
weather conflict cleared, hence no trip options — see Task 17 preamble). The final
counts, after the `balanced`-option condition, are recorded in
`results/v1.5/h2c/SAMPLING.json` when the draw runs.
