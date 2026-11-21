# Kalyan's RQ4 labels: self-consistency check (2026-11-19)

**Procedure.** The Planner compared Kalyan's raw labels only against his own notes and
his written labeling standards (`rq4_labeling_conventions_kalyan.md`), **not** against
Abhishek's gold. Three rows where a label contradicted his own note or standard were sent
back to him through Abhishek. The questions were neutral: no gold labels and no agreement
information were shared. He chose to change all three.

| Sheet | First pass (raw export) | After self-check | Basis he was pointed to |
|---|---|---|---|
| S03 | `checkin_impossible` | `timing_inefficiency;checkin_impossible` | His timing standard (≈20:00+ loses Day 1); arrival 22:30 |
| S04 | `none` | `preference_mismatch` | His own note ("does not list the beaches") and his preference standard |
| S10 | `none` | `weather_activity_mismatch` | His weather standard ("hot" ~32C with exposed outdoor activities) |

**Files.**
- `rq4_labels_kalyan_raw_export.csv` is his first pass, unchanged (a Google Sheets export).
- `../rq4_labels_kalyan.csv` is the analysed file: the original blank sheet with his
  labels and notes filled in by `sheet_id`, the export's encoding damage repaired, and the
  three self-check changes applied. Every scenario description is byte-identical to
  `rq4_labeling_sheet.csv`.

**Reporting.** Agreement is reported on the self-checked file. Agreement on the raw first
pass is reported alongside, so the effect of the self-check is visible.
