# H2c Coherence Audit — Rater Guide

This guide is for the second rater. You will score 10 travel plans that the Voyager system
marked as "resolved", judging for yourself whether each plan actually works. Your scores are
compared with the author's to measure agreement. You don't need to know how the system works
internally, and you shouldn't try to find out which condition or run produced a plan.

**Time needed:** about 60–90 minutes, including calibration.

---

## 1. Why this audit exists

Voyager's final audit decides a conflict is resolved when its detection rules stop firing. Those
are the same rules that triggered the fix, so the audit can't catch a plan that satisfies the
rules but is still unusable. Your job is to judge the plan as a traveler would, without any
reference to the rules.

## 2. Ground rules

1. **Score independently.** Do not discuss any of the 10 plans with the author, or with anyone
   else, until you have submitted all your scores.
2. **Stay blind.** Your packet has no condition names, run IDs or detector output. If you see
   any of these by accident, note it in the `notes` column and keep going.
3. **Judge the plan, not the system.** Ask whether a real traveler with this request could
   follow this plan without trouble. Don't reward or penalise writing style.
4. **Don't look things up beyond what the packet gives you,** except for common-knowledge checks
   (rough distances between cities, whether two places are on the same island). If a judgment
   needs information the packet doesn't contain, score `U` (see §4).
5. **Don't change a score after seeing the author's.** Disagreements are kept as they are; that
   is what the agreement statistic measures.

## 3. What you receive

For each plan, one file `plan_XX.md` containing:
- the **original traveler request** (destination, dates, budget, interests, constraints)
- the **final plan**: flights, hotel (with location), daily activities (with locations and
  times), weather notes, total estimated cost

Plus `rater_scores.csv` for your answers, and two calibration plans, `calib_A.md` and
`calib_B.md`.

## 4. The rubric

Score each plan on four dimensions. Each one is **P** (pass), **F** (fail), or **U** (cannot
judge from the packet). Score F only for a problem that would actually disrupt the trip, not for
a choice you merely wouldn't make.

### D1. Temporal consistency
Does the timing hold together?
- **F if:** an activity is scheduled before the traveler arrives or after they leave; two
  activities overlap; the traveler arrives after the hotel's stated check-in or front-desk
  hours with no late-arrival note; an early-morning activity follows a very late arrival
  (e.g. landing near midnight, then a 5 AM start).
- **P otherwise.**

### D2. Budget consistency
- **F if:** the plan's total estimated cost exceeds the traveler's stated budget, or a large
  item is clearly missing from the total (e.g. no hotel cost listed).
- **P if** the total is within budget, or no budget was stated.
- **U if** the costs needed to check are not in the packet.

### D3. Preference consistency
- **F if:** the plan ignores an explicit request or constraint (e.g. "no hiking" but a hike is
  scheduled; "travelling with toddlers" but activities are unsuitable for them; "beach trip"
  with no beach time), or schedules an outdoor activity the plan's own weather notes say is
  unsafe without flagging it.
- **P otherwise.** Activities that fit the request loosely still pass.

### D4. Logistical feasibility
Could someone physically follow this plan?
- **F if:** activities on the same day are too far apart to reach in the time given; the hotel
  is so far from most activities that daily travel would take a large share of the day; the
  plan requires being in two places at once (e.g. a hotel in Athens with same-day activities
  on another island and no transport arranged); a booked location is plainly wrong (e.g. a
  hotel in a different country or state from the one requested).
- **P otherwise.** Use your own judgment of what is reasonable. There is no fixed distance cutoff.

### Overall
A plan is **fully coherent** only if none of D1–D4 is F. `U` doesn't count as a failure, but
record it. Note anything that bothered you but didn't reach F in `notes`.

## 5. Procedure

1. **Read this guide in full.**
2. **Calibration (about 15 min).** Score `calib_A.md` and `calib_B.md`, then compare your scores
   with the author's reference scores in `calib_answers.md` and talk through any differences.
   The calibration plans are **not** among the 10 you'll score. This is the only discussion
   allowed before you finish.
3. **Score the 10 plans** in the order they're numbered, in one or two sittings. For every F,
   write one short sentence in `notes` saying what fails.
4. **Submit** `rater_scores.csv` to the author. Only then may you discuss the plans.

## 6. Score sheet format

`rater_scores.csv`, one row per plan:

```
plan_id,D1_temporal,D2_budget,D3_preference,D4_logistics,fully_coherent,notes
plan_01,P,P,P,F,N,"Hotel in Athens; day-2 activities on Zakynthos, no ferry or flight"
plan_02,P,U,P,P,Y,"No cost total in packet"
```

`fully_coherent` is `Y` if none of D1–D4 is F, otherwise `N`.

## 7. How the results are used

The author scores all 30 sampled plans; you score 10 of them, chosen at random (fixed seed)
before either of you starts. Agreement is reported as percent agreement and Cohen's κ, per
dimension and overall. Your scores appear in the paper only as aggregate agreement figures.
You may be acknowledged by name if you wish.

## Clarification (2026-11-20, after the first double-scoring round)

**D1 — also F if** an activity is placed on a date when it cannot take place, judged from the packet or from widely known facts about the venue or event (e.g. a museum's weekly closing day, a Sunday-only service), including when the itinerary's weekday labels don't match the calendar dates and that mismatch puts an activity on such a day. A weekday mislabel with no such consequence is written in `notes`, not failed.

**D4 judges the scheduled days only:** getting between the hotel and the places actually scheduled each day. Activities that are listed or booked but never scheduled go in `notes` and are not a D4 failure.

---

## Notes for the author (not part of the rater packet)

- Build the packets with a script that removes condition labels, run IDs, round counts,
  fingerprints and detector output. Commit the script so the blinding can be checked.
- Draw the 10-plan subset with a fixed, committed random seed **before** you score anything.
- Pick the calibration plans from outside the 30-plan sample: one plainly coherent and one
  with a clear failure, preferably D4. Write the reference scores before the rater sees them.
- Declare in threats to validity that the second rater is a project co-contributor and not
  fully independent.
