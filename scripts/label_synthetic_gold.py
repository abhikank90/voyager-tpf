#!/usr/bin/env python3
"""Gold-labeling run for the v1.5 synthetic set (protocol §5.3).

Runs each synthetic query once in baseline mode against mock inventory and records the
raw evidence a human needs to assign gold labels by hand. It deliberately does NOT
re-implement or call any conflict rule: the only rule output it records is the observed
R1 conflict types, which are what the hand labels are cross-checked against.

Usage (from the repo root):
    python3 scripts/label_synthetic_gold.py              # all 25 synthetic queries
    python3 scripts/label_synthetic_gold.py --limit 2    # smoke test

Outputs:
    results/v1.5/gold_labeling/evidence.jsonl   full evidence per query
    results/v1.5/gold_labeling/evidence.csv     one row per query, for the cross-check log

This is a labeling aid, not a benchmark result. It is recorded under "Pre-registration
activities" in docs/EVALUATION_PROTOCOL.md §13.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Settings are read at import time, so pin them before importing project modules.
os.environ["VOYAGER_INVENTORY_MODE"] = "mock"
os.environ["VOYAGER_ENABLE_LLM_CONFLICT_CANDIDATES"] = "false"
os.environ.pop("VOYAGER_TEMPERATURE_OVERRIDE", None)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.benchmark_queries import QUERIES  # noqa: E402

OUT_DIR = ROOT / "results" / "v1.5" / "gold_labeling"
TOP_N = 5  # the weather rule looks at the top 5 experiences; the location rule at the top 3


def _experience_evidence(experiences: list[dict]) -> list[dict]:
    return [
        {
            "rank": i + 1,
            "name": e.get("name", ""),
            "location": e.get("location", ""),
            "category": e.get("category", ""),
            "description": e.get("description", ""),
        }
        for i, e in enumerate(experiences[:TOP_N])
    ]


def _evidence(query_id: str, query: str, state: dict) -> dict:
    intent = state.get("intent") or {}
    weather = state.get("weather") or {}
    flight = state.get("selected_flight") or {}
    hotel = state.get("selected_hotel") or {}
    run_metrics = state.get("run_metrics") or {}
    r1 = run_metrics.get("round_1_conflicts", [])
    return {
        "query_id": query_id,
        "query": query,
        "timestamp": datetime.now(UTC).isoformat(),
        "parsed_month": intent.get("travel_month"),
        "parsed_year": intent.get("travel_year"),
        "destination": intent.get("destination"),
        "weather_avg_temp_c": weather.get("avg_temp_c"),
        "weather_precipitation_mm": weather.get("precipitation_mm"),
        "weather_summary": weather.get("summary"),
        "weather_source": weather.get("source"),
        "selected_flight_arrival": flight.get("arrival"),
        "selected_hotel_name": hotel.get("name"),
        "selected_hotel_location": hotel.get("location"),
        "experiences_top5": _experience_evidence(state.get("experiences") or []),
        "observed_r1_conflict_types": sorted(c.get("type", "") for c in r1),
        "errors": list((state.get("errors") or {}).keys()),
    }


def _csv_row(ev: dict) -> dict:
    exps = ev["experiences_top5"]
    return {
        "query_id": ev["query_id"],
        "parsed_month": f'{ev["parsed_month"]} {ev["parsed_year"]}',
        "weather": f'{ev["weather_avg_temp_c"]}C / {ev["weather_precipitation_mm"]}mm / '
                   f'{ev["weather_summary"]}',
        "arrival": ev["selected_flight_arrival"],
        "hotel_location": ev["selected_hotel_location"],
        "top3_experience_locations": " | ".join(e["location"] for e in exps[:3]),
        "top5_names_categories": " | ".join(f'{e["name"]} [{e["category"]}]' for e in exps),
        "observed_r1_types": ";".join(ev["observed_r1_conflict_types"]),
        "errors": ";".join(ev["errors"]),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None, help="run only the first N queries")
    args = parser.parse_args()

    from config.settings import get_settings
    from graph.travel_graph import run_collaborative_travel_query

    settings = get_settings()
    assert settings.inventory_mode == "mock", settings.inventory_mode
    assert not settings.enable_llm_conflict_candidates
    assert settings.temperature_override is None

    queries = list(QUERIES)[: args.limit] if args.limit else list(QUERIES)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUT_DIR / "evidence.jsonl"
    csv_path = OUT_DIR / "evidence.csv"

    rows = []
    with jsonl_path.open("w") as jf:
        for i, query in enumerate(queries, 1):
            query_id = f"SYN-{i:02d}"
            print(f"[{i}/{len(queries)}] {query_id} {query[:60]}...")
            try:
                state = await run_collaborative_travel_query(
                    query, enable_refinement=False, record_metrics=False,
                )
            except Exception as exc:  # protocol §8: log, re-run once by hand, never drop silently
                print(f"           FAILED: {exc}")
                state = {"errors": {f"run_failed: {type(exc).__name__}": str(exc)}}
            ev = _evidence(query_id, query, state)
            jf.write(json.dumps(ev) + "\n")
            rows.append(_csv_row(ev))
            print(f"           observed R1: {ev['observed_r1_conflict_types'] or 'none'}")

    with csv_path.open("w", newline="") as cf:
        writer = csv.DictWriter(cf, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nEvidence → {jsonl_path}\n         → {csv_path}")


if __name__ == "__main__":
    asyncio.run(main())
