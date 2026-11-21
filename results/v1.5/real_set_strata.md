# Real-set strata — declared before replay 1

Defined **from fixtures only** (`fixtures/live_inventory_v1.5/`), before any replay
outcome existed. No hypothesis tests are re-decided on the subsets; they are a declared
sensitivity analysis with n shown.

- **S_all (25)** — the primary real-set analysis, as pre-registered.
  `REAL-01` … `REAL-25`.
- **S_serpapi_flight (14)** — queries whose flight fixture is a real SerpApi search
  (`flight_source = serpapi`), i.e. excluding the 11 `mock_fallback` flights.
  `REAL-01, REAL-02, REAL-03, REAL-04, REAL-07, REAL-08, REAL-09, REAL-12, REAL-14,
  REAL-16, REAL-19, REAL-20, REAL-22, REAL-24`.
- **S_full_real (5)** — `flight_source = serpapi` **and** `n_flights ≥ 1` **and**
  `n_hotels ≥ 1`.
  `REAL-01, REAL-07, REAL-09, REAL-14, REAL-24`.

Every real-set statistic (H1 real rows, H2a/H2b, H3a/H3b, H4 real) is reported on S_all;
S_serpapi_flight and S_full_real are reported alongside as sensitivity analyses.

Provenance per query is in `results/v1.5/real_inventory_provenance.csv` (derived from the
fixtures and intent fixtures, query→key mapping computed by the fixture-key functions).

## Hotel geography (added at the capture gate, still before replay 1)

`hotel_geography_matches_destination` is judged by hand from `hotel_geography`: the
country or region of the first sandbox hotel against the requested destination. It is
blank when there are no hotels.
- **No (4):**
  - REAL-01: Greece → Rochester, US
  - REAL-03: Bali → Balıkesir, TR
  - REAL-07: Rome → Rome, US
  - REAL-24: Hawaii → Buena Park, US

  These are the documented LiteAPI sandbox place-resolution errors. The location rule can
  flag them, so `location_mismatch` counts on the real set partly reflect sandbox
  geography, not the plan.
- **Yes (9).**
- **S_full_real_geo (2):** S_full_real with matching hotel geography: `REAL-09, REAL-14`.
  Reported descriptively only (n too small for any test).
