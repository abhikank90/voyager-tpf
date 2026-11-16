# Gold cross-check log — synthetic set

**Labeling run:** 2026-11-15 · baseline mock (`--mode baseline --inventory mock`) · cost ~$4–6
(inventory mocked; LLM calls live).
**Role:** labeling aid, not a benchmark result. Recorded under "Pre-registration activities" in protocol §13.
**Code:** v1.4.1 (`d356ad2`) detectors, unchanged.

## Run settings (required)

| Setting | Value | Why |
|---|---|---|
| `VOYAGER_INVENTORY_MODE` | `mock` | Fully offline inventory, including weather, even with API keys set |
| `VOYAGER_ENABLE_LLM_CONFLICT_CANDIDATES` | off (default) | The in-hub LLM detector would add candidate types and trip the "other types" check |
| `VOYAGER_TEMPERATURE_OVERRIDE` | unset | Agents run at their v1.4.1 defaults |
| Mode | `baseline` | Only the R1 audit is needed for labeling |

## How each rule fires on mock data (derived from v1.4.1 code)

- **timing_inefficiency** (`collaboration_hub.py`, arrival hour ≥ 20): Round-1 mock flights
  arrive at **22:30 and 23:45** (`_mock_flight_data`). The 13:00 option exists only when a hub
  arrival constraint is present, so it is never offered in Round 1. Fires on every query
  regardless of which flight is selected.
- **location_mismatch** (substring match, top-3 experiences vs. hotel location): the mock
  hotel's location is the string "Beachfront", with no coordinates. The rule fires unless one
  of the top-3 experience location strings is a substring of "beachfront" or contains it.
  Because experience locations are LLM output (experience agent, temperature 0.3), this label
  is **per session, not per query**, and is **planted by construction**, not geographic
  truth. It does not fire if all top-3 experience locations are empty.
- **weather_activity_mismatch** (heat ≥ 32°C, or rain: precipitation > 50 mm or summary
  containing "rain", "storm", "monsoon" or "heavy shower"; and ≥ 2 outdoor or beach
  experiences in the top 5):
  - **Greece** has a full monthly table. Heat fires in July and August (33°C). Rain fires in
    January (55 mm), November (55 mm), December (60 mm), and in **February (48 mm) and
    October (35 mm) through the word "rain" in the summary**, not through precipitation.
    June (29°C, 8 mm, no rain keyword) does not fire.
  - **All other destinations** use the `default` table, which has only a July entry (25°C,
    30 mm, "Warm summer weather expected."). Other months resolve to an empty dict, so the
    rule sees 25°C with no rain. Never fires.
- **Not conflicts:** `location_synergy` and `weather_activity_synergy` are positive matches
  emitted by the hub. They are excluded from all counts below.

## Expected vs observed (R1 audit)

| Check | Expected (hand-derived) | Observed | Disposition |
|---|---|---|---|
| timing_inefficiency on 25/25 | 25 (R1 options 22:30 / 23:45, both ≥ 20:00) | 25 (all selected 22:30) | Any miss: stop and investigate |
| location_mismatch on 25/25 | 25, planted by construction ("Beachfront" has no coordinates and is not a place name) | 25 (no top-3 location empty or overlapping "beachfront") | Miss: log that session's top-3 experience locations. If empty, or a substring of "beachfront" (e.g. a bare "Beach"), record a labeling note, not a rule bug |
| weather_activity_mismatch on SYN-01 | Fires iff parsed month ∈ {jul, aug} (heat) or {jan, feb, oct, nov, dec} (rain), AND ≥ 2 outdoor/beach experiences in the top 5 | N: parsed June 2026, 29°C / 8 mm, no rain keyword; outdoor/beach count 2 | If parsed month = June: gold drops the label; logged below |
| weather_activity_mismatch on SYN-02..25 | 0 (default climate) | 0 | Any fire: stop and investigate rule or data |
| Other conflict types | 0 (LLM candidates off; synergies excluded) | 0 | Any fire: code drift against v1.4.1, stop |

## Per-query record

One row per synthetic query from the labeling run. `gold_conflicts` in `query_manifest.csv`
is updated from the final column.

| query_id | parsed month | weather evidence | arrival | top-3 experience locations | outdoor/beach count (top 5) | timing | location | weather | gold_conflicts |
|---|---|---|---|---|---|---|---|---|---|
| SYN-01 | June 2026 | 29°C / 8 mm / "Hot and sunny. Sea warm enough for swimming." | 22:30 | Navagio Beach, Zakynthos Island · Varvakios Agora, Monastiraki, Athens · Little Venice, Mykonos Town | 2 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-02 | March 2027 | n/a (empty climate entry) | 22:30 | North Malé Atoll, Maldives · Rangali Island, South Ari Atoll · Vaadhoo Island, Raa Atoll | 1 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-03 | July 2026 | 25°C / 30 mm / "Warm summer weather expected." | 22:30 | Campuhan Ridge, Ubud · Seminyak Beach, Seminyak · Berawa Beach, Canggu | 3 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-04 | October 2026 | n/a (empty climate entry) | 22:30 | Koh Phi Phi, Krabi Province · Chatuchak District, Bangkok · Railay Beach, Krabi | 2 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-05 | September 2026 | n/a (empty climate entry) | 22:30 | Lagoa, Central Algarve · Lagoa Wine Region, Central Algarve · Benagil, Lagoa | 4 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-06 | April 2026 | n/a (empty climate entry) | 22:30 | Tsukiji, Chuo City · Akihabara, Chiyoda City · Asakusa, Taito City | 0 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-07 | June 2026 | n/a (empty climate entry) | 22:30 | Vatican City, Borgo district · Trastevere neighborhood · Villa Borghese, Parioli district | 0 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-08 | May 2026 | n/a (empty climate entry) | 22:30 | Sultanahmet Square, Fatih · Sultanahmet, Fatih · Eminönü & Beyazıt, Fatih | 0 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-09 | August 2026 | n/a (empty climate entry) | 22:30 | Eixample district, Barcelona · Barri Gòtic & El Born, Barcelona · La Rambla, El Raval, Barcelona | 1 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-10 | December 2026 | n/a (empty climate entry) | 22:30 | Gyeongbokgung, Jongno-gu · Gwangjang Market, Jongno-gu · Hongdae, Mapo-gu | 0 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-11 | November 2026 | n/a (empty climate entry) | 22:30 | Kawarau Gorge, Queenstown · Tongariro National Park, Ruapehu District · Fiordland National Park, Te Anau | 0 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-12 | February 2027 | n/a (empty climate entry) | 22:30 | Monteverde, Puntarenas Province · Tortuguero National Park, Limón Province · La Fortuna, Alajuela Province | 0 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-13 | October 2026 | n/a (empty climate entry) | 22:30 | Annapurna Sanctuary, Modi Khola Valley · Ghorepani, Poon Hill · Mardi Himal, Eastern Annapurna | 0 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-14 | January 2026 | n/a (empty climate entry) | 22:30 | Þingvellir National Park, Golden Circle · Grindavík, Reykjanes Peninsula · Flúðir, South Iceland | 1 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-15 | December 2026 | n/a (empty climate entry) | 22:30 | Torres del Paine National Park, Central Circuit · Mirador Las Torres, Eastern Sector · Lake Grey, Grey Glacier Sector | 0 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-16 | April 2026 | n/a (empty climate entry) | 22:30 | Old Quarter, Hanoi · Ancient Town, Hoi An · Ha Giang Province, Northern Vietnam | 0 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-17 | September 2026 | n/a (empty climate entry) | 22:30 | Hradčany district, Prague · City Park (Városliget), Budapest · Vigadó Square Pier, Budapest | 0 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-18 | March 2026 | n/a (empty climate entry) | 22:30 | Djemaa el-Fna, Medina, Marrakech · Erg Chebbi Dunes, Merzouga, Sahara Desert · Medina and Souk district, Marrakech | 0 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-19 | None 2026 | 25°C / 30 mm / "Warm summer weather expected." | 22:30 | Centro Histórico, Mexico City · Chapultepec, Mexico City · Coyoacán, Mexico City | 0 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-20 | November 2026 | n/a (empty climate entry) | 22:30 | Angkor Archaeological Park, Siem Reap · Daun Penh district, Phnom Penh · Koh Rong Samloem Island, Sihanoukville | 1 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-21 | June 2026 | n/a (empty climate entry) | 22:30 | Lake Buena Vista, Orlando · Universal Orlando Resort, International Drive · Lake Buena Vista, Orlando | 0 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-22 | July 2026 | 25°C / 30 mm / "Warm summer weather expected." | 22:30 | Maasai Mara National Reserve, Narok County · Amboseli National Park, Kajiado County · Ol Pejeta Conservancy, Laikipia County | 0 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-23 | February 2027 | n/a (empty climate entry) | 22:30 | Grindelwald-Wengen, Jungfrau Region · Zermatt to St. Moritz, Valais & Graubünden · Verbier Village Center, Verbier | 1 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-24 | August 2027 | n/a (empty climate entry) | 22:30 | Hanauma Bay, East Oahu · Lahaina, West Maui · Waimea Valley, North Shore Oahu | 2 | Y | Y | N | location_mismatch;timing_inefficiency |
| SYN-25 | April 2026 | n/a (empty climate entry) | 22:30 | Maruyama Park, Gion district, Kyoto · Tsukiji Outer Market, Chuo City, Tokyo · Odaiba, Minato City, Tokyo | 0 | Y | Y | N | location_mismatch;timing_inefficiency |

## Implications for RQ4 scoring

- **timing_inefficiency** and **weather_activity_mismatch** on synthetic data are fully
  determined by fixtures plus the parsed month and the experience list, so they serve as gold
  for detector precision and recall.
- **location_mismatch** on synthetic data is construct gold: it says the harness planted a
  mismatch, not that a hotel is geographically far from the activities. Geographic gold for
  the location rule comes from the RQ4 probe states, which carry coordinates.
- Because the location label can vary between sessions, the 5 benchmark runs record it per
  session. This log fixes only the per-query expectation and its known exceptions.

## Resolution notes

One line per disagreement: what was observed, how it was resolved, and who decided.

| query_id | Observed | Resolution | Decided by |
|---|---|---|---|
| All | Observed R1 types match hand-derived gold on 25/25 | No disagreements | Abhishek Kankanala |
| SYN-01 | "summer 2026" parsed as June 2026 (29°C, 8 mm, no rain keyword) | Weather label dropped; gold = location + timing. Had the parser chosen July or August, weather would fire (33°C) | Abhishek Kankanala |
| SYN-02, 12, 23, 24 | Parsed year rolled to 2027 (March, February, February, August) against the run date | No effect on mock conflicts; recorded as RQ3 date-drift source (protocol §2) | Abhishek Kankanala |
| SYN-19 | "any month" parsed as month = None; weather resolved to the default July entry | No effect; recorded | Abhishek Kankanala |
| All | Mock flight dates are July 1–14 of the parsed year regardless of parsed month (`base_agent._effective_dates`, mock branch) | No effect on conflicts; fixed in v1.5 by the trip-window rule (protocol §5.2); the step-10 smoke test re-checks gold on v1.5 code | Abhishek Kankanala |
| Coverage | `weather_activity_mismatch` has **0 positive cases** in the synthetic set | RQ4 weather recall cannot be measured on synthetic; it comes from probe states | Abhishek Kankanala |
