#!/usr/bin/env python3
"""Blinded packets for the H2c coherence audit (protocol §11, docs/H2C_RATER_GUIDE.md).

Draws a stratified, seeded sample of resolved outcomes, writes one blinded
markdown packet per plan, and keeps the unblinding key in a separate file.

A resolved outcome is a refinement session (any condition except baseline) that had
at least one Round-1 conflict and none at the final audit, and whose delivered plan
was saved (VOYAGER_PLANS_DIR). Strata are inventory mode x condition; the sample is
allocated across strata in proportion to their size, with at least one plan per
non-empty stratum. The packet shows the ``balanced`` trip option.

Packets contain only what a traveller would see: the request, the flight, the
hotel, the day-by-day plan, the activities, the weather and the costs. They omit
condition names, session and run IDs, round counts, fingerprints, conflict lists
and detector output.

Usage:
    python3 scripts/build_h2c_packets.py \\
        --sessions results/v1.5/runs/*.jsonl --plans-dir results/v1.5/plans \\
        --out results/v1.5/h2c --seed 20261201

Output:
    <out>/author/plan_XX.md       all sampled plans (the author scores these)
    <out>/rater/plan_XX.md        the double-scored subset (give to the second rater)
    <out>/author/author_scores.csv, <out>/rater/rater_scores.csv   blank score sheets
    <out>/KEY_DO_NOT_SHARE.csv    plan_id -> session, condition, inventory, query_id
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

PACKET_STYLE = "balanced"
SCORE_HEADER = ["plan_id", "D1_temporal", "D2_budget", "D3_preference", "D4_logistics",
                "fully_coherent", "notes"]


def load_sessions(paths: list[Path]) -> list[dict]:
    sessions = []
    for path in paths:
        with open(path) as fh:
            sessions += [json.loads(line) for line in fh if line.strip()]
    return sessions


def eligible(sessions: list[dict], plans_dir: Path) -> list[dict]:
    out = []
    for s in sessions:
        condition = s.get("refinement_mode") or s.get("condition")
        if condition in (None, "baseline"):
            continue
        if not s.get("round_1_conflict_count") or s.get("final_conflict_count", 1) != 0:
            continue
        if not (plans_dir / f"{s.get('session_id')}.json").exists():
            continue
        out.append(s)
    return out


def allocate(sizes: dict[tuple, int], n: int) -> dict[tuple, int]:
    """Proportional allocation with at least one per non-empty stratum (largest remainder)."""
    strata = {k: v for k, v in sizes.items() if v > 0}
    total = sum(strata.values())
    if total <= n:
        return dict(strata)
    if n < len(strata):
        raise SystemExit(f"--n {n} is smaller than the number of strata ({len(strata)})")
    alloc = dict.fromkeys(strata, 1)
    remaining = n - len(strata)
    spare = {k: v - 1 for k, v in strata.items()}
    spare_total = sum(spare.values())
    quotas = {k: remaining * spare[k] / spare_total for k in strata} if spare_total else {}
    for k, q in quotas.items():
        alloc[k] += min(int(q), spare[k])
    leftover = n - sum(alloc.values())
    for k in sorted(quotas, key=lambda k: (quotas[k] - int(quotas[k]), str(k)), reverse=True):
        if leftover <= 0:
            break
        if alloc[k] < strata[k]:
            alloc[k] += 1
            leftover -= 1
    return alloc


def stratum(s: dict) -> tuple[str, str]:
    return (s.get("inventory_mode", "unknown"), s.get("refinement_mode") or s.get("condition"))


def draw(candidates: list[dict], n: int, n_double: int, seed: int) -> tuple[list[dict], set[str]]:
    rng = random.Random(seed)
    by_stratum: dict[tuple, list[dict]] = {}
    for s in sorted(candidates, key=lambda s: s["session_id"]):
        by_stratum.setdefault(stratum(s), []).append(s)
    alloc = allocate({k: len(v) for k, v in by_stratum.items()}, n)
    sample: list[dict] = []
    for key in sorted(alloc, key=str):
        sample += rng.sample(by_stratum[key], alloc[key])
    rng.shuffle(sample)
    double = {s["session_id"] for s in rng.sample(sample, min(n_double, len(sample)))}
    return sample, double


def _fmt_time(value: str | None) -> str:
    return value.replace("T", " ")[:16] if value else "unknown"


def render_packet(plan: dict) -> str:
    """Traveller-facing view of one plan. Nothing about how it was produced."""
    intent = plan.get("intent") or {}
    options = plan.get("trip_options") or []
    option = next((o for o in options if o.get("style") == PACKET_STYLE), options[0] if options else {})
    flight = option.get("flight") or plan.get("selected_flight") or {}
    hotel = option.get("hotel") or plan.get("selected_hotel") or {}
    weather = plan.get("weather") or {}
    breakdown = (option.get("itinerary") or {}).get("breakdown") or {}

    lines = ["# Travel plan", "", "## Traveller request", "", f"> {plan.get('query', '')}", ""]
    lines += [
        f"- Destination: {intent.get('destination', 'unknown')}",
        f"- Budget: ${intent.get('budget_usd', 'not stated')}",
        f"- Travellers: {intent.get('group_size', 1)}",
        f"- Interests: {', '.join(intent.get('interests') or []) or 'not stated'}",
        "",
        "## Flight",
        "",
        f"- {flight.get('airline', 'unknown airline')}: departs {_fmt_time(flight.get('departure'))}, "
        f"arrives {_fmt_time(flight.get('arrival'))}",
        "",
        "## Hotel",
        "",
        f"- {hotel.get('name', 'unknown')}, located in {hotel.get('location', 'unknown')}",
        "",
        "## Weather for the trip month",
        "",
    ]
    temp, rain = weather.get("avg_temp_c"), weather.get("precipitation_mm")
    lines.append(f"- Average high: {temp if temp is not None else 'unknown'}°C; "
                 f"monthly rain: {rain if rain is not None else 'unknown'} mm")
    if weather.get("summary"):
        lines.append(f"- {weather['summary']}")
    lines += ["", "## Activities", ""]
    for e in option.get("experiences") or plan.get("experiences") or []:
        cost = e.get("cost_usd", e.get("price"))
        lines.append(f"- {e.get('name', 'unknown')} ({e.get('location', 'location not given')})"
                     + (f", ${cost}" if cost is not None else ""))
    lines += ["", "## Day by day", ""]
    for day in option.get("day_by_day") or []:
        lines.append(f"**{day.get('date') or 'Day ' + str(day.get('day', '?'))}**")
        for part in ("morning", "afternoon", "evening", "notes"):
            if day.get(part):
                lines.append(f"- {part.capitalize()}: {day[part]}")
        lines.append("")
    lines += ["## Estimated cost", ""]
    if option.get("total_cost_usd") is not None:
        lines.append(f"- Total: ${option['total_cost_usd']}")
    for k, v in breakdown.items():
        lines.append(f"- {k.capitalize()}: ${v}")
    return "\n".join(lines).rstrip() + "\n"


def _blank_sheet(path: Path, plan_ids: list[str]) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(SCORE_HEADER)
        for pid in plan_ids:
            w.writerow([pid] + [""] * (len(SCORE_HEADER) - 1))


def build(sessions: list[dict], plans_dir: Path, out: Path, seed: int, n: int, n_double: int) -> dict:
    candidates = eligible(sessions, plans_dir)
    if not candidates:
        raise SystemExit("no resolved outcomes with saved plans")
    sample, double = draw(candidates, n, n_double, seed)

    (out / "author").mkdir(parents=True, exist_ok=True)
    (out / "rater").mkdir(parents=True, exist_ok=True)
    key_rows, author_ids, rater_ids = [], [], []
    for i, s in enumerate(sample, 1):
        pid = f"plan_{i:02d}"
        plan = json.loads((plans_dir / f"{s['session_id']}.json").read_text())
        packet = render_packet(plan)
        (out / "author" / f"{pid}.md").write_text(packet)
        author_ids.append(pid)
        if s["session_id"] in double:
            (out / "rater" / f"{pid}.md").write_text(packet)
            rater_ids.append(pid)
        inv, cond = stratum(s)
        key_rows.append([pid, s["session_id"], cond, inv, s.get("query_id", ""),
                         s.get("run_index", ""), s["session_id"] in double])

    with open(out / "KEY_DO_NOT_SHARE.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["plan_id", "session_id", "condition", "inventory_mode", "query_id",
                    "run_index", "double_scored"])
        w.writerows(key_rows)
    _blank_sheet(out / "author" / "author_scores.csv", author_ids)
    _blank_sheet(out / "rater" / "rater_scores.csv", rater_ids)
    (out / "SAMPLING.json").write_text(json.dumps({
        "seed": seed, "n": len(sample), "n_double": len(rater_ids),
        "eligible": len(candidates), "packet_style": PACKET_STYLE,
    }, indent=2) + "\n")
    return {"sampled": len(sample), "double": len(rater_ids), "eligible": len(candidates)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessions", nargs="+", type=Path, required=True)
    parser.add_argument("--plans-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True, help="commit this value before scoring")
    parser.add_argument("--n", type=int, default=30)
    parser.add_argument("--double", type=int, default=10)
    args = parser.parse_args()
    result = build(load_sessions(args.sessions), args.plans_dir, args.out, args.seed, args.n, args.double)
    print(f"{result['sampled']} plans from {result['eligible']} resolved outcomes; "
          f"{result['double']} double-scored -> {args.out}")


if __name__ == "__main__":
    main()
