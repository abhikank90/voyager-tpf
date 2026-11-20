# Billing reconciliation

- window: 2026-11-19 → 2026-11-19 (model_manifest)
- threshold: |Δ| ≤ 1.0% of billed cost
- **pass: False**

## Totals

| side | input tokens | output tokens | cost (USD) |
|---|---|---|---|
| billed | 3364373 | 16160057 | 239.510000 |
| tracked | 3260313 | 15978079 | 236.675236 |
| Δ | 104060 | 181978 | 2.834764 |

cost Δ = 1.1836%

## Tracked sources

| source | input tokens | output tokens | cost (USD) |
|---|---|---|---|
| canary | 1356 | 786 | 0.010422 |
| failed_sessions | 0 | 0 | 0.000000 |
| rerun_sessions | 6877 | 30327 | 0.454270 |
| rq4 | 93076 | 92863 | 1.672173 |
| sessions | 3159004 | 15854103 | 234.538371 |

## Billed input-token parts (Console tokens export)

| part | tokens |
|---|---|
| no_cache | 3364373 |
| cache_write_5m | 0 |
| cache_write_1h | 0 |
| cache_read | 0 |

## Token-file key split (list-price cost match)

| model | list-price cost | key id | id cost |
|---|---|---|---|
| claude-haiku-4-5-20251001 | 6.490000 | apikey_019UUra2ru8uMDziHkiMKroD | 6.490000 |
| claude-haiku-4-5-20251001 | 0.120000 | apikey_016azyMATQe5h9cquzd6LB5g | 0.120000 |
| claude-sonnet-4-6 | 233.020000 | apikey_019UUra2ru8uMDziHkiMKroD | 233.020000 |
| claude-sonnet-4-6 | 4.650000 | apikey_016azyMATQe5h9cquzd6LB5g | 4.650000 |

ignored zero-cost billed rows: 12

## Per model

| model | billed in/out | tracked in/out | Δ in/out |
|---|---|---|---|
| claude-haiku-4-5-20251001 | 336672/1230811 | 331281/1210889 | 5391/19922 |
| claude-sonnet-4-6 | 3027701/14929246 | 2834600/14673541 | 193101/255705 |

## Pre-registered result

pass: **False** — billed **$239.51** (window key `apikey_019UUra2ru8uMDziHkiMKroD`) vs
tracked **$236.675236** → **Δ +$2.8348 = +1.1836%** of billed (threshold 1.0%).

## Key identity

Two API keys share the name `voyager-v1.5-window` on 2026-11-19:

| api_key_id | Sonnet | Haiku | total | what it is |
|---|---|---|---|---|
| `apikey_019UUra2ru8uMDziHkiMKroD` | 233.02 | 6.49 | **239.51** | the rotated key — the tracked window (manifest start 02:24Z = the restart) |
| `apikey_016azyMATQe5h9cquzd6LB5g` | 4.65 | 0.12 | **4.77** | the first window key, exposed then rotated — aborted window 1 |

The name-filtered figure from Task 16b (**+3.1131%** on $244.28) is **superseded**: it
merged both keys. The pre-registered comparison is tracked vs `apikey_019UU…` only.

## Per model — cost and output tokens (window key)

| model | billed cost | tracked cost | Δ cost | billed out-tok | tracked out-tok | Δ out-tok |
|---|---|---|---|---|---|---|
| claude-sonnet-4-6 | 233.020000 | 230.279088 | +2.740912 | 14,929,246 | 14,673,541 | +255,705 |
| claude-haiku-4-5-20251001 | 6.490000 | 6.385726 | +0.104274 | 1,230,811 | 1,210,889 | +19,922 |
| (canary, mixed model) | 0.000000 | 0.010422 | -0.010422 | 0 | 786 | -786 |
| **total** | **239.510000** | **236.675236** | **+2.834764** | **16,160,057** | **15,978,079** | **+181,978** |

## Itemized untracked uses on the window key (a–d)

| item | sessions | cost (USD) | output tokens | evidence |
|---|---|---|---|---|
| (a) aborted capture 2 — 6 completed sessions set aside | 6 | 0.2007 | 14,524 | `handoff/aborted_capture_2/runs_real_capture/shard_1/sessions.jsonl` |
| (b) 15 relaunched capture failures (empty-inventory, option_generator early return) | 15 | 0.3891 | 31,546 | replay-1 `baseline` cost per query; `failures.csv` usage blank |
| (c) CAPFIX live smoke (1 session, REAL-01) + key-checks | 1 | 0.2742 | 18,087 | `/tmp/cap_smoke/sessions.jsonl` |
| (d) 1 live integration full-suite run (Greece + Maldives, mock) | 2 | 0.5546 (est.) | 36,709 (est.) | SYN-01 + SYN-02 `baseline` means |
| **subtotal** | 24 | **1.4186** | **100,866** | |
| **residual** | — | **1.4162** | **81,112** | ≈0.59% of billed — not itemized (see Conclusion) |

(b) old estimate (mean $0.2495 × 19 = $4.7411) is biased high; the replay-1 `baseline`
cost for the same 15 queries is $0.3891. The planner's "4 pre-relaunch" failures are
REAL-02/03/04/06 (the empty-inventory sessions among the aborted lane's first six);
their usage is already recorded inside (a), so they are not added again (their
replay-1 `baseline` total would be $0.1024 — shown only to avoid double-counting).

## Aborted window 1 (old key, not part of the check)

Billed on `apikey_016azyMATQe5h9cquzd6LB5g`: **$4.77** (Sonnet $4.65 + Haiku $0.12).
Its set-aside tracked usage: 24 sessions `handoff/aborted_window_1/runs/**`
(**$3.9135**, in 60,243 / out 260,609) + the RQ4 `isolation.json` run (**$0.2418**,
in 39,880 / out 8,143) = **$4.1553**. The remaining ≈$0.61 is calls in flight and
key-check calls made after the export's set-aside sessions. Reported separately.

## Instrumentation gap

`failures.csv` partial-usage columns were **blank for every failure row** — the 15
capture failures above and the 2 synthetic `APITimeoutError` rows (SYN-19 baseline,
SYN-11 broadcast). The 2 synthetic failures were re-run successfully and their re-run
usage is tracked under `rerun_sessions` ($0.4543). No code change now.

## Conclusion

The check fails at 1% (**+1.1836%**). Untracked runs on the window key (a–d) account
for **$1.42 / ~101K output tokens** of the +$2.83 / +181,978-token gap (≈50%); the
residual **$1.42 / ~81K output tokens (≈0.59% of billed)** is not itemized. Known but
unmeasured candidates: the failed first attempts of SYN-19 baseline and SYN-11 broadcast
(usage not logged; at most ≈$0.45, the cost of their full re-runs), and requests that
timed out or were retried, which may be billed without being recorded. Reported as-is. Analysis costs are unaffected.
