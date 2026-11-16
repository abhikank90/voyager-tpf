"""Trip-window resolution shared by every inventory-facing agent (protocol §5.2).

v1.4.1 ignored the requested month when querying inventory: mock trips were always
dated July 1-14 of the parsed year, and capture/replay always searched capture date +
90 days. From v1.5 the month comes from the parsed intent and the year is resolved
in code against a fixed anchor, so the dates searched match the dates asked for and
do not drift with the day a benchmark happens to run.

Rule (``RULE_MONTH``):
- the intent parser chooses the month (seasons and named events included); its year
  is ignored;
- the year is the next occurrence of that month strictly after the anchor month, so
  a month equal to the anchor month resolves to the following year;
- departure is the 15th of the resolved month; return is departure + duration;
- no parsed month ("any month") falls back to anchor + 90 days, as in v1.4.1.

``RULE_LEGACY`` reproduces the v1.4.1 behaviour exactly. It is used only to replay
fixture sets captured before v1.5, whose manifests carry no ``trip_window_rule``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

RULE_MONTH = "month-v1.5"
RULE_LEGACY = "legacy-v1.4"

DEFAULT_DURATION_DAYS = 13
DEPARTURE_DAY = 15
ANY_MONTH_LEAD_DAYS = 90

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_MONTH_NAMES = {v: k for k, v in MONTHS.items()}


@dataclass(frozen=True)
class TripWindow:
    """Resolved inventory window for one session."""

    departure: date
    return_date: date
    month: str          # lowercase month name of the departure, e.g. "march"
    rule: str           # RULE_MONTH or RULE_LEGACY
    anchor: date
    source: str         # "parsed_month", "any_month_fallback" or "legacy"

    @property
    def departure_iso(self) -> str:
        return self.departure.isoformat()

    @property
    def return_iso(self) -> str:
        return self.return_date.isoformat()

    def as_record(self) -> dict:
        return {
            "departure": self.departure_iso,
            "return": self.return_iso,
            "month": self.month,
            "rule": self.rule,
            "anchor": self.anchor.isoformat(),
            "source": self.source,
        }


def parse_month(travel_month: str | None) -> int | None:
    """Month number from a parsed month name ("March", "mar"), or None."""
    if not travel_month:
        return None
    key = str(travel_month).strip().lower()
    if key in MONTHS:
        return MONTHS[key]
    for name, num in MONTHS.items():
        if len(key) >= 3 and name.startswith(key):
            return num
    return None


def _duration(duration_days: int | None) -> int:
    try:
        d = int(duration_days) if duration_days else DEFAULT_DURATION_DAYS
    except (TypeError, ValueError):
        d = DEFAULT_DURATION_DAYS
    return d if d > 0 else DEFAULT_DURATION_DAYS


def resolve_trip_window(
    travel_month: str | None,
    duration_days: int | None,
    anchor: date,
) -> TripWindow:
    """Apply the v1.5 month rule. Pure function; see module docstring."""
    duration = _duration(duration_days)
    month_num = parse_month(travel_month)
    if month_num is None:
        departure = anchor + timedelta(days=ANY_MONTH_LEAD_DAYS)
        source = "any_month_fallback"
    else:
        year = anchor.year if month_num > anchor.month else anchor.year + 1
        departure = date(year, month_num, DEPARTURE_DAY)
        source = "parsed_month"
    window = TripWindow(
        departure=departure,
        return_date=departure + timedelta(days=duration),
        month=_MONTH_NAMES[departure.month],
        rule=RULE_MONTH,
        anchor=anchor,
        source=source,
    )
    assert_after_anchor(window)
    return window


def legacy_trip_window(
    mode: str,
    travel_year: int | None,
    duration_days: int,
    anchor: date,
    travel_month: str | None,
) -> TripWindow:
    """The v1.4.1 window, kept only to replay pre-v1.5 fixture sets unchanged."""
    if mode == "mock":
        year = int(travel_year or 2026)
        departure, ret = date(year, 7, 1), date(year, 7, 14)
    else:
        departure = anchor + timedelta(days=ANY_MONTH_LEAD_DAYS)
        ret = departure + timedelta(days=duration_days)
    month = (travel_month or "July").lower()
    return TripWindow(departure, ret, month, RULE_LEGACY, anchor, "legacy")


def assert_after_anchor(window: TripWindow) -> None:
    """Protocol §5.2 check: every session departs strictly after its anchor."""
    if window.departure <= window.anchor:
        raise ValueError(
            f"trip window departs {window.departure_iso}, not after anchor "
            f"{window.anchor.isoformat()} (rule {window.rule})"
        )
