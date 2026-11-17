# Control cross-check — CTL-01..10 (template)

**Purpose.** Verify, per session, that the control mock inventory delivers a satisfiable,
conflict-free trip under the v1.5 month rule. The controls' gold label is **`none`**.

**Gold is construct-level, not rule output.** A control trip is "none" because its flight
lands in daytime, its hotel is central to the destination, its weather is benign and it
fits the budget — regardless of what the detectors say. If a rule fires on a control, that
is a **rule false positive**, reported under H4 (which expects "rules: 0 spurious on
controls"). It is **never** relabelled or resolved in this sheet.

**How to fill it.** One row per CTL query from a baseline mock pass. The `timing`,
`location` and `weather` columns are the observed R1 rule outputs. `location` is
LLM-dependent (the experience agent, temperature 0.3): the rule is a substring match
between the top-3 experience locations and the hotel location, so a fire is expected to be
rare on controls but is recorded, not auto-labelled. Abhishek confirms `coherent trip?`
(yes/no) from the evidence. This validates the construction. Gold stays `none` as
pre-registered. If a trip is judged not coherent, the fixture is fixed and the check
redone; the label is never changed.

| query_id | parsed month | arrival hour | hotel location | top-3 experience locations | outdoor count (top-5) | budget_ok | timing | location | weather | coherent trip? (Abhishek) | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| CTL-01 | | | | | | | | | | | |
| CTL-02 | | | | | | | | | | | |
| CTL-03 | | | | | | | | | | | |
| CTL-04 | | | | | | | | | | | |
| CTL-05 | | | | | | | | | | | |
| CTL-06 | | | | | | | | | | | |
| CTL-07 | | | | | | | | | | | |
| CTL-08 | | | | | | | | | | | |
| CTL-09 | | | | | | | | | | | |
| CTL-10 | | | | | | | | | | | |

## Resolution notes

One line per disagreement or rule fire: what was observed, why it does not change the
construct gold, and who decided.

| query_id | Observed | Resolution | Decided by |
|---|---|---|---|
| | | | |
