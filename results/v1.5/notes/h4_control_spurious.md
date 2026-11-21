# H4 controls — one spurious rule fire (disclosed, no change made)

## Facts

- **Session**: `dbb61178-84cf-4317-9206-4e375d217bca`
- **Query / set**: CTL-05 (Cancún), control set
- **Condition / run**: `full_rerun`, run_index 3
- **Fired**: round 1 — `location_mismatch` (severity `medium`)
- **Resolved**: round 2. A second `location_mismatch` was detected in round 2 and
  resolved in round 3.
- **Frequency**: 1 of 3 `full_rerun` runs of CTL-05 fired at round 1. Across the whole
  control set this is **1 of 150 sessions** (Wilson CI not computed here).

## Evidence (round 1, as recorded)

```json
{
  "type": "location_mismatch",
  "severity": "medium",
  "data": {
    "activity_locations": [
      "Playa Delfines, Zona Hotelera (Hotel Zone), Km 17.5",
      "Isla Mujeres, departing from Puerto Juárez Ferry Terminal",
      "Punta Nizuc, southern end of Zona Hotelera, Km 22"
    ],
    "activity_centroid": {"lat": null, "lon": null},
    "current_hotel_location": "Cancun"
  }
}
```

## Rule code path

`CollaborationHub._check_hotel_experience_mismatch` (`agents/collaboration_hub.py`):
the rule fires when none of the top-3 experience `location` strings is a substring of
the selected hotel's `location` string (or vice versa). Here the hotel location is the
city name `Cancun`, while the LLM-generated experience locations name specific
neighbourhoods/venues (`Zona Hotelera`, `Isla Mujeres`, `Punta Nizuc`), so the
substring test fails even though all three experiences are in the Cancún area.

## Decision

**Disclosed, no change made.** Changing the rule or the control data after observing
the outcome would be an outcome-driven deviation. This is reported as a result in the
H4 discussion and threats-to-validity (deterministic rule false-positive rate
1/150 = 0.67% on controls; root cause city-vs-neighbourhood substring matching).
