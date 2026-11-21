"""Control-set mock inventory (protocol §5, item 5a).

The default mock data is *planted* to conflict: flights land at 22:30/23:45 and
hotels sit at the literal strings ``Beachfront``/``Old Town``, so every synthetic
query starts in conflict — that's what makes resolution measurable. The control
set needs the opposite: a satisfiable, conflict-free trip, so rule false
positives on clean queries are measurable (H4, "rules: 0 spurious on controls").

Each control destination therefore gets a dedicated profile:

- **Flights** land 10:00–15:00 local (the timing rule fires at arrival ≥ 20:00);
  the cheapest option is the daytime one, because the flight agent selects by
  lowest price when no hub constraint is present.
- **Hotels**: the affordable hotel's ``location`` is the **city name alone**, so
  experience locations that name the city substring-match it. The location rule
  is a pure substring test in either direction (`collaboration_hub.py`), with no
  distance threshold; coordinates only feed the hotel agent's re-selection and
  the LLM detector's context.
- **Weather** is benign or absent. The weather agent's default climate table is
  July-only (`weather_agent.CLIMATE_FALLBACK`), so every control destination
  resolves to either an empty dict (non-July months) or the benign July row
  (25 °C, 30 mm, "Warm summer weather expected." — no heat ≥ 32, no rain
  keyword). The weather rule therefore cannot fire on controls. Benign values
  are listed here for documentation and for the RQ4 probe states, not because
  the weather agent consults them in mock mode.

Selection is by the *parsed destination* (case-insensitive, accent-tolerant).
A destination with no entry is an error — controls must never silently fall
back to the planted-conflict data.

Budget sizing: flight (per-adult × group) + hotel (per-night × window nights) +
the option generator's food/misc must fit each row's budget with ≥ 10 % margin.
The per-row budgets, group sizes and stays are asserted explicitly in
``tests/unit/test_mock_control.py`` (parsed from the manifest text, not an LLM).
"""
from __future__ import annotations

import unicodedata


def _norm(text: str) -> str:
    """Lowercase, strip diacritics, collapse whitespace, take the first comma part."""
    head = (text or "").split(",")[0]
    decomposed = unicodedata.normalize("NFKD", head)
    ascii_text = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(ascii_text.lower().split())


# Per-destination control data. ``flights`` entries are per-adult prices; the
# builder multiplies by group size and injects the trip dates. ``hotels``
# ``location`` is the city name so the substring rule cannot fire on it.
_CONTROL = {
    "lisbon": {
        "city": "Lisbon",
        "flights": [
            {"airline": "TP", "departure_hour": 8, "arrival_hour": 13,
             "price_usd": 420, "stops": 1, "duration": "PT7H00M"},
            {"airline": "UA", "departure_hour": 11, "arrival_hour": 15,
             "price_usd": 480, "stops": 1, "duration": "PT6H30M"},
        ],
        "hotels": [
            {"name": "Lisbon City Stay", "price_per_night_usd": 110, "rating": 4.4,
             "amenities": ["wifi", "breakfast included"], "location": "Lisbon"},
            {"name": "Baixa Boutique", "price_per_night_usd": 140, "rating": 4.6,
             "amenities": ["wifi", "rooftop bar"], "location": "Lisbon"},
        ],
        "weather": {"avg_temp_c": 20, "precipitation_mm": 12, "summary": "Mild and dry."},
    },
    "kyoto": {
        "city": "Kyoto",
        "flights": [
            {"airline": "JL", "departure_hour": 10, "arrival_hour": 14,
             "price_usd": 520, "stops": 1, "duration": "PT11H00M"},
            {"airline": "UA", "departure_hour": 12, "arrival_hour": 15,
             "price_usd": 580, "stops": 1, "duration": "PT11H30M"},
        ],
        "hotels": [
            {"name": "Kyoto Garden Inn", "price_per_night_usd": 140, "rating": 4.5,
             "amenities": ["wifi", "breakfast included"], "location": "Kyoto"},
            {"name": "Gion Ryokan", "price_per_night_usd": 170, "rating": 4.7,
             "amenities": ["wifi", "onsen"], "location": "Kyoto"},
        ],
        "weather": {"avg_temp_c": 15, "precipitation_mm": 30, "summary": "Mild spring."},
    },
    "vancouver": {
        "city": "Vancouver",
        "flights": [
            {"airline": "AC", "departure_hour": 8, "arrival_hour": 12,
             "price_usd": 180, "stops": 0, "duration": "PT1H00M"},
            {"airline": "AS", "departure_hour": 10, "arrival_hour": 13,
             "price_usd": 210, "stops": 0, "duration": "PT1H00M"},
        ],
        "hotels": [
            {"name": "Vancouver Harbour Stay", "price_per_night_usd": 130, "rating": 4.3,
             "amenities": ["wifi", "breakfast included"], "location": "Vancouver"},
            {"name": "Stanley Park Lodge", "price_per_night_usd": 160, "rating": 4.6,
             "amenities": ["wifi", "gym"], "location": "Vancouver"},
        ],
        "weather": {"avg_temp_c": 16, "precipitation_mm": 25, "summary": "Pleasant early summer."},
    },
    "amsterdam": {
        "city": "Amsterdam",
        "flights": [
            {"airline": "KL", "departure_hour": 9, "arrival_hour": 13,
             "price_usd": 350, "stops": 0, "duration": "PT7H00M"},
            {"airline": "DL", "departure_hour": 11, "arrival_hour": 15,
             "price_usd": 400, "stops": 1, "duration": "PT7H30M"},
        ],
        "hotels": [
            {"name": "Canal House Amsterdam", "price_per_night_usd": 140, "rating": 4.4,
             "amenities": ["wifi", "breakfast included"], "location": "Amsterdam"},
            {"name": "Jordaan Boutique", "price_per_night_usd": 165, "rating": 4.6,
             "amenities": ["wifi", "bicycle rental"], "location": "Amsterdam"},
        ],
        "weather": {"avg_temp_c": 14, "precipitation_mm": 22, "summary": "Mild spring."},
    },
    "cancun": {
        "city": "Cancun",
        "flights": [
            {"airline": "AA", "departure_hour": 7, "arrival_hour": 11,
             "price_usd": 260, "stops": 0, "duration": "PT2H30M"},
            {"airline": "WN", "departure_hour": 9, "arrival_hour": 13,
             "price_usd": 300, "stops": 1, "duration": "PT3H00M"},
        ],
        "hotels": [
            {"name": "Cancun Beach Resort", "price_per_night_usd": 150, "rating": 4.4,
             "amenities": ["beach access", "wifi", "pool"], "location": "Cancun"},
            {"name": "Hotel Zone Boutique", "price_per_night_usd": 180, "rating": 4.6,
             "amenities": ["beach access", "wifi"], "location": "Cancun"},
        ],
        "weather": {"avg_temp_c": 27, "precipitation_mm": 15, "summary": "Warm and sunny."},
    },
    "sydney": {
        "city": "Sydney",
        "flights": [
            {"airline": "QF", "departure_hour": 10, "arrival_hour": 10,
             "price_usd": 680, "stops": 1, "duration": "PT15H00M"},
            {"airline": "UA", "departure_hour": 12, "arrival_hour": 13,
             "price_usd": 740, "stops": 1, "duration": "PT15H30M"},
        ],
        "hotels": [
            {"name": "Harbour View Sydney", "price_per_night_usd": 180, "rating": 4.4,
             "amenities": ["wifi", "gym"], "location": "Sydney"},
            {"name": "Bondi Beach Lodge", "price_per_night_usd": 210, "rating": 4.6,
             "amenities": ["wifi", "beach access"], "location": "Sydney"},
        ],
        "weather": {"avg_temp_c": 24, "precipitation_mm": 18, "summary": "Warm summer."},
    },
    "edinburgh": {
        "city": "Edinburgh",
        "flights": [
            {"airline": "BA", "departure_hour": 8, "arrival_hour": 12,
             "price_usd": 120, "stops": 0, "duration": "PT1H15M"},
            {"airline": "U2", "departure_hour": 10, "arrival_hour": 13,
             "price_usd": 140, "stops": 0, "duration": "PT1H15M"},
        ],
        "hotels": [
            {"name": "Old Town Edinburgh", "price_per_night_usd": 110, "rating": 4.3,
             "amenities": ["wifi", "breakfast included"], "location": "Edinburgh"},
            {"name": "Royal Mile Inn", "price_per_night_usd": 135, "rating": 4.5,
             "amenities": ["wifi", "bar"], "location": "Edinburgh"},
        ],
        "weather": {"avg_temp_c": 13, "precipitation_mm": 20, "summary": "Mild autumn."},
    },
    "banff": {
        "city": "Banff",
        "flights": [
            {"airline": "AC", "departure_hour": 9, "arrival_hour": 14,
             "price_usd": 200, "stops": 1, "duration": "PT1H30M"},
            {"airline": "WS", "departure_hour": 11, "arrival_hour": 15,
             "price_usd": 240, "stops": 1, "duration": "PT1H30M"},
        ],
        "hotels": [
            {"name": "Banff Mountain Lodge", "price_per_night_usd": 160, "rating": 4.4,
             "amenities": ["wifi", "breakfast included"], "location": "Banff"},
            {"name": "Lake Louise Inn", "price_per_night_usd": 190, "rating": 4.6,
             "amenities": ["wifi", "hot tub"], "location": "Banff"},
        ],
        "weather": {"avg_temp_c": 18, "precipitation_mm": 12, "summary": "Clear mountain summer."},
    },
    "san diego": {
        "city": "San Diego",
        "flights": [
            {"airline": "WN", "departure_hour": 8, "arrival_hour": 12,
             "price_usd": 220, "stops": 0, "duration": "PT1H20M"},
            {"airline": "AA", "departure_hour": 10, "arrival_hour": 14,
             "price_usd": 260, "stops": 0, "duration": "PT1H20M"},
        ],
        "hotels": [
            {"name": "Mission Bay Family Resort", "price_per_night_usd": 180, "rating": 4.4,
             "amenities": ["beach access", "wifi", "pool"], "location": "San Diego"},
            {"name": "Gaslamp Quarter Hotel", "price_per_night_usd": 210, "rating": 4.6,
             "amenities": ["wifi", "gym"], "location": "San Diego"},
        ],
        "weather": {"avg_temp_c": 22, "precipitation_mm": 2, "summary": "Sunny and mild."},
    },
    "montreal": {
        "city": "Montreal",
        "flights": [
            {"airline": "AC", "departure_hour": 9, "arrival_hour": 13,
             "price_usd": 130, "stops": 0, "duration": "PT1H00M"},
            {"airline": "WS", "departure_hour": 11, "arrival_hour": 15,
             "price_usd": 150, "stops": 0, "duration": "PT1H00M"},
        ],
        "hotels": [
            {"name": "Old Montreal Stay", "price_per_night_usd": 110, "rating": 4.3,
             "amenities": ["wifi", "breakfast included"], "location": "Montreal"},
            {"name": "Plateau Boutique", "price_per_night_usd": 135, "rating": 4.5,
             "amenities": ["wifi", "bar"], "location": "Montreal"},
        ],
        "weather": {"avg_temp_c": 25, "precipitation_mm": 30, "summary": "Warm summer weather expected."},
    },
}


def normalize_destination(destination: str) -> str:
    """Case-insensitive, accent-tolerant, first-comma-part key for a destination."""
    return _norm(destination)


def control_profile(destination: str) -> dict:
    """The control profile for ``destination``, or raise if there is none.

    Raising (rather than falling back to the planted-conflict default data) is
    deliberate: a control run that silently degraded would report clean trips
    on top of conflict fixtures and invalidate the H4 spurious-count check.
    """
    key = normalize_destination(destination)
    if key not in _CONTROL:
        raise ValueError(
            f"no control profile for destination {destination!r}; "
            "control runs must not fall back to the default mock data"
        )
    return _CONTROL[key]


def mock_control_flights(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str,
    adults: int,
    preferred_arrival_hour: int | None = None,
) -> dict:
    """Control flight candidates: cheapest arrives 10:00–15:00 local."""
    profile = control_profile(destination)
    flights = []
    for f in profile["flights"]:
        flights.append({
            "price_usd": f["price_usd"] * adults,
            "airline": f["airline"],
            "departure": f"{departure_date}T{f['departure_hour']:02d}:00:00",
            "arrival": f"{departure_date}T{f['arrival_hour']:02d}:00:00",
            "stops": f["stops"],
            "duration": f["duration"],
        })
    if preferred_arrival_hour is not None:
        # The daytime options already satisfy any hub arrival constraint.
        flights.append({
            "price_usd": 890 * adults,
            "airline": "LH",
            "departure": f"{departure_date}T06:00:00",
            "arrival": f"{departure_date}T13:00:00",
            "stops": 1,
            "duration": "PT7H00M",
        })
    return {"flights": flights, "mock": True}


def mock_control_hotels(
    destination: str,
    check_in: str,
    check_out: str,
    guests: int,
    duration: int,
    location_hint: str | None = None,
) -> dict:
    """Control hotel candidates: the affordable hotel is located in the city itself."""
    profile = control_profile(destination)
    hotels = []
    for h in profile["hotels"]:
        hotels.append({
            "name": h["name"],
            "price_per_night_usd": h["price_per_night_usd"],
            "total_usd": h["price_per_night_usd"] * duration,
            "rating": h["rating"],
            "amenities": list(h["amenities"]),
            "location": h["location"],
        })
    if location_hint:
        hotels.insert(0, {
            "name": f"{location_hint} Boutique Stay",
            "price_per_night_usd": profile["hotels"][0]["price_per_night_usd"],
            "total_usd": profile["hotels"][0]["price_per_night_usd"] * duration,
            "rating": 4.4,
            "amenities": ["wifi", "breakfast included"],
            "location": location_hint,
        })
    return {"hotels": hotels, "mock": True}


def mock_control_weather(destination: str, month: str) -> dict:
    """Benign weather for the control destination's month (or empty)."""
    return dict(control_profile(destination).get("weather") or {})
