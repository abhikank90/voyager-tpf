"""Evaluate whether an LLM-augmented conflict detector adds value over rules.

Answers three questions for the InfoQ article (and the roadmap item in
collaboration_hub._identify_conflicts):

  1. AGREEMENT  — does the LLM find the conflicts the rules find?
  2. RECALL GAIN — does the LLM find real conflicts the rules cannot see?
  3. CONSISTENCY — does the LLM return the same conflicts across repeated runs?
                   (Rules are deterministic by construction; this measures what
                   we'd give up by trusting the LLM for routing.)

Usage:
    ANTHROPIC_API_KEY=sk-... python scripts/eval_hybrid_detection.py
    # optional: HYBRID_EVAL_MODEL=claude-haiku-4-5-20251001 to test a cheaper detector;
    # default matches the benchmark (claude-sonnet-4-6)

Cost: ~16 LLM calls (8 states x 2 runs), small prompts. A few cents.
"""

import argparse
import csv
import io
import json
import os
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from anthropic import Anthropic  # noqa: E402


# ── Rule-based detector (LLM narrative call mocked out, rules untouched) ─────

def build_rules_hub():
    from agents.collaboration_hub import CollaborationHubAgent
    with patch("agents.collaboration_hub.Anthropic"), \
         patch("agents.collaboration_hub.get_api_config") as cfg:
        cfg.return_value.llm.api_key = "unused"
        cfg.return_value.llm.collaboration_hub_model = "unused"
        return CollaborationHubAgent()


def make_client():
    """Anthropic client with the §8 retry policy (VOYAGER_LLM_MAX_RETRIES)."""
    from config.settings import get_settings

    return Anthropic(max_retries=get_settings().llm_max_retries)


# ── Usage recording (§8: every window call must be tracked) ───────────────────

def _usage_tokens(response) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    in_tok = getattr(usage, "input_tokens", None) if usage is not None else None
    out_tok = getattr(usage, "output_tokens", None) if usage is not None else None
    if not isinstance(in_tok, int) or not isinstance(out_tok, int):
        # A stub/mock client yields MagicMock fields that ``int()`` would coerce to 1,
        # silently pretending a real call happened. Fail fast instead.
        raise RuntimeError(
            "LLM response carried no real integer usage (stub client?); refusing to record"
        )
    return in_tok, out_tok


class _UsageAccumulator:
    """Accumulates per-call usage and prices it with token_tracker.MODEL_PRICING."""

    def __init__(self, component: str, model: str):
        from metrics.token_tracker import compute_cost

        self.component = component
        self.model = model
        self._compute_cost = compute_cost
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls: list[dict] = []

    def record(self, model: str, response) -> None:
        input_tokens, output_tokens = _usage_tokens(response)
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.calls.append({
            "component": self.component,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": self._compute_cost(input_tokens, output_tokens, model),
        })

    def total(self) -> dict:
        return {
            "component": self.component,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self._compute_cost(self.input_tokens, self.output_tokens, self.model),
            "calls": self.calls,
        }


class _RecordingMessages:
    def __init__(self, messages, accumulator: _UsageAccumulator):
        self._messages = messages
        self._accumulator = accumulator

    def create(self, **kwargs):
        response = self._messages.create(**kwargs)
        self._accumulator.record(kwargs.get("model") or self._accumulator.model, response)
        return response


class _RecordingClient:
    """Proxies a client and records usage from every ``messages.create`` call."""

    def __init__(self, client, accumulator: _UsageAccumulator):
        self._client = client
        self._accumulator = accumulator

    @property
    def messages(self):
        return _RecordingMessages(self._client.messages, self._accumulator)


# ── Evaluation states ─────────────────────────────────────────────────────────
# Each: (name, state, conflicts_a_human_would_flag) — the last field is the
# ground truth used to judge LLM "extras" as real recall vs. noise.

def _base():
    return {
        "intent": {"destination": "Greece", "budget_usd": 2000,
                   "interests": ["beaches"], "travel_month": "July 2026"},
        "collaboration_round": 1,
        "selected_hotel": {"name": "Santorini Resort", "location": "Santorini",
                           "total_usd": 700},
        "experiences": [
            {"name": "Red Beach", "category": "beach", "location": "Santorini"},
            {"name": "Oia Walk", "category": "culture", "location": "Santorini"},
            {"name": "Taverna", "category": "food", "location": "Santorini"},
        ],
        "selected_flight": {"airline": "LH", "arrival": "2026-07-01T13:00:00",
                            "price_usd": 890},
        "weather": {"avg_temp_c": 28, "summary": "warm", "precipitation_mm": 2},
        "visa_safety": {"visa_required": False, "safety_level": "safe"},
        "flights": [], "hotels": [], "agent_messages": [], "conflicts": [],
    }


RULES = ("location_mismatch", "timing_inefficiency", "weather_activity_mismatch")


def load_probes(path: str) -> dict:
    """Load the v1.5 RQ4 probe file (``results/v1.5/rq4_probes.json``)."""
    with open(path) as fh:
        doc = json.load(fh)
    if doc.get("version") != 1 or "probes" not in doc:
        raise ValueError(f"{path} is not a version-1 probe file")
    return doc


def probes_to_states(doc: dict) -> list[tuple[str, dict, set]]:
    """Convert probes to the ``make_states()`` tuple shape the eval loop consumes.

    Truth is ``gold`` when filled in, else ``pilot_truth_v141``, else empty. Gold is
    null until Abhishek labels the probes, so an unlabelled probe contributes to
    agreement/consistency but not to the recall-gain/noise counts.
    """
    states = []
    for p in doc["probes"]:
        truth = p.get("gold") or p.get("pilot_truth_v141") or []
        states.append((p["id"], p["state"], set(truth)))
    return states


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two coordinates (no LLM, no rules)."""
    from math import asin, cos, radians, sin, sqrt

    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(a))


def state_summary(state: dict) -> str:
    """One-line, deterministic summary of a probe state for the labeling sheet.

    Renders every leaf field present in the state (except ``collaboration_round``
    and empty lists), plus the haversine hotel-to-experience distances. Generated
    from the raw fields only — never from an LLM and never from rule output — so
    the rater sees the same facts the detectors see.
    """
    intent = state.get("intent", {})
    hotel = state.get("selected_hotel", {})
    flight = state.get("selected_flight", {})
    weather = state.get("weather", {})
    visa = state.get("visa_safety", {})
    parts: list[str] = []

    head = [f"dest={intent.get('destination', '')}", f"budget={intent.get('budget_usd', '')}"]
    if intent.get("interests"):
        head.append(f"interests={'+'.join(intent['interests'])}")
    if intent.get("travel_month"):
        head.append(f"month={intent['travel_month']}")
    if intent.get("booking_date"):
        head.append(f"booked={intent['booking_date']}")
    parts.append(" ".join(head))

    hotel_bits = [f"hotel={hotel.get('name', '')}", f"loc={hotel.get('location', '')}"]
    if hotel.get("lat") is not None and hotel.get("lon") is not None:
        hotel_bits.append(f"coord=({hotel['lat']},{hotel['lon']})")
    if hotel.get("total_usd") is not None:
        hotel_bits.append(f"hotel_cost={hotel['total_usd']}")
    if hotel.get("front_desk_hours"):
        hotel_bits.append(f"desk={hotel['front_desk_hours']}")
    parts.append(" ".join(hotel_bits))

    flight_bits = [f"arrival={flight.get('arrival', '')}"]
    if flight.get("airline"):
        flight_bits.append(f"airline={flight['airline']}")
    if flight.get("price_usd") is not None:
        flight_bits.append(f"flight_cost={flight['price_usd']}")
    if flight.get("note"):
        flight_bits.append(f"flight_note={flight['note']}")
    parts.append(" ".join(flight_bits))

    if weather:
        parts.append(
            f"climate={weather.get('avg_temp_c', '')}C/{weather.get('precipitation_mm', '')}mm"
            f" \"{weather.get('summary', '')}\""
        )

    visa_bits = [f"visa_req={visa.get('visa_required', '')}"]
    if visa.get("processing_days") is not None:
        visa_bits.append(f"visa_days={visa['processing_days']}")
    if visa.get("safety_level"):
        visa_bits.append(f"safety={visa['safety_level']}")
    parts.append(" ".join(visa_bits))

    exps = []
    for e in state.get("experiences", [])[:5]:
        bits = [f"{e.get('name', '')} [{e.get('category', '')}] @{e.get('location', '')}"]
        if e.get("lat") is not None and e.get("lon") is not None:
            bits.append(f"coord=({e['lat']},{e['lon']})")
            if hotel.get("lat") is not None and hotel.get("lon") is not None:
                bits.append(f"dist={_haversine_km(hotel['lat'], hotel['lon'], e['lat'], e['lon']):.1f}km")
        if e.get("description"):
            bits.append(f"desc=\"{e['description']}\"")
        exps.append(" ".join(bits))
    parts.append(f"exps={'; '.join(exps)}")

    return " | ".join(parts)


SHEET_SEED = 20261116  # committed: never change, or the blinded shuffle order changes


def _shuffled_probe_order(n: int) -> list[int]:
    """Seeded permutation of probe indices so the sheet is blind but reproducible."""
    import random

    rng = random.Random(SHEET_SEED)
    order = list(range(n))
    rng.shuffle(order)
    return order


def build_labeling_sheet(doc: dict) -> str:
    """Blind CSV for ``rq4_labeling_sheet.csv``.

    Rows are in a seeded shuffled order with neutral ids (``S01..Snn``), and no probe
    id, name or group appears, so gold labelled from this sheet cannot restate the
    design. ``gold_types`` and ``notes`` stay blank.
    """
    probes = doc["probes"]
    order = _shuffled_probe_order(len(probes))
    buf = io.StringIO()
    buf.write(
        "# RQ4 detector evaluation — labeling instructions\n"
        "# Allowed types (enter \"none\" or \";\"-separated types in gold_types):\n"
        "#   location_mismatch      the hotel is far from where the planned activities\n"
        "#                          take place (judge by the distances shown, not by\n"
        "#                          whether place names differ)\n"
        "#   timing_inefficiency    the inbound flight lands too late to use Day 1\n"
        "#   weather_activity_mismatch  planned activities clash with the weather\n"
        "#   checkin_impossible     the flight arrives after the hotel desk closes\n"
        "#   budget_violation       the listed costs add up to more than the stated budget\n"
        "#   visa_timing            the visa cannot be processed before travel\n"
        "#   preference_mismatch    activities contradict a stated interest/dietary need\n"
        "# Label what a careful human planner would flag, not what any rule would output.\n"
    )
    writer = csv.DictWriter(
        buf, fieldnames=["sheet_id", "state_summary", "gold_types", "notes"]
    )
    writer.writeheader()
    for row, probe_idx in enumerate(order, 1):
        p = probes[probe_idx]
        writer.writerow({
            "sheet_id": f"S{row:02d}",
            "state_summary": state_summary(p["state"]),
            "gold_types": "",
            "notes": "",
        })
    return buf.getvalue()


def build_sheet_key(doc: dict) -> str:
    """Mapping ``sheet_id`` → ``probe_id`` for the blinded labeling sheet."""
    probes = doc["probes"]
    order = _shuffled_probe_order(len(probes))
    buf = io.StringIO()
    buf.write("# Key mapping sheet_id -> probe_id. Do not open before labeling is done.\n")
    writer = csv.DictWriter(buf, fieldnames=["sheet_id", "probe_id"])
    writer.writeheader()
    for row, probe_idx in enumerate(order, 1):
        writer.writerow({"sheet_id": f"S{row:02d}", "probe_id": probes[probe_idx]["id"]})
    return buf.getvalue()


def _parse_csv_with_comments(text: str) -> list[dict]:
    """Parse a comment-prefixed CSV (``#`` lines skipped) into a list of row dicts."""
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    reader = csv.reader(lines)
    header = next(reader)
    return [dict(zip(header, row)) for row in reader]


def _sheet_id_map(rows: list[dict]) -> dict[str, dict]:
    ids = [row["sheet_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate sheet_id in the sheet")
    return {row["sheet_id"]: row for row in rows}


def merge_gold(probes_path: str, sheet_path: str) -> dict:
    """Merge a filled labeling sheet back into ``rq4_probes.json``.

    Writes each probe's ``gold`` (a sorted list of types, ``[]`` for ``none``) and saves
    the file. Refuses — raising without writing anything — when the sheet is stale, has
    unknown or duplicated ``sheet_id``s, a blank ``gold_types``, an unknown type,
    ``none`` combined with a type, or leaves any probe unlabelled.
    """
    probes_path = Path(probes_path)
    sheet_path = Path(sheet_path)
    doc = load_probes(str(probes_path))
    probes = doc["probes"]
    allowed_types = set(doc["types"])

    key_rows = _parse_csv_with_comments(
        (probes_path.parent / "rq4_sheet_key.csv").read_text()
    )
    key = {row["sheet_id"]: row["probe_id"] for row in key_rows}

    fresh_rows = _parse_csv_with_comments(build_labeling_sheet(doc))
    fresh = _sheet_id_map(fresh_rows)
    filled = _sheet_id_map(_parse_csv_with_comments(sheet_path.read_text()))

    if set(filled) != set(fresh):
        raise ValueError("sheet sheet_ids do not match a fresh build")
    if not set(filled) <= set(key):
        raise ValueError("sheet contains unknown sheet_id(s)")

    gold_by_probe: dict[str, list] = {}
    for sid, row in filled.items():
        if row["state_summary"] != fresh[sid]["state_summary"]:
            raise ValueError(f"stale sheet: {sid} summary differs from a fresh build")
        raw = (row.get("gold_types") or "").strip()
        if not raw:
            raise ValueError(f"blank gold_types for {sid}")
        types = [t.strip() for t in raw.split(";") if t.strip()]
        if "none" in types:
            if len(types) > 1:
                raise ValueError(f"'none' combined with other types in {sid}")
            gold_by_probe[key[sid]] = []
            continue
        unknown = [t for t in types if t not in allowed_types]
        if unknown:
            raise ValueError(f"unknown type(s) {sorted(unknown)} in {sid}")
        gold_by_probe[key[sid]] = sorted(set(types))

    for probe in probes:
        if probe["id"] not in gold_by_probe:
            raise ValueError(f"probe {probe['id']} is not labelled")
        probe["gold"] = gold_by_probe[probe["id"]]

    probes_path.write_text(json.dumps(doc, indent=2) + "\n")
    return doc


def make_states():
    states = []

    s = _base()
    states.append(("clean_no_conflicts", s, set()))

    s = _base()
    s["selected_hotel"] = {"name": "Aegean Bliss", "location": "Beachfront",
                           "total_usd": 595}
    s["experiences"] = [
        {"name": "Sunset", "category": "culture", "location": "Oia, Santorini"},
        {"name": "Boat Tour", "category": "outdoor", "location": "Fira center"},
        {"name": "Dinner", "category": "food", "location": "Oia village"},
    ]
    states.append(("location_mismatch", s, {"location_mismatch"}))

    s = _base()
    s["selected_flight"] = {"airline": "UA", "arrival": "2026-07-01T22:30:00",
                            "price_usd": 680}
    states.append(("late_arrival_timing", s, {"timing_inefficiency"}))

    s = _base()
    s["weather"] = {"avg_temp_c": 35, "summary": "extreme heat wave",
                    "precipitation_mm": 0}
    s["experiences"] = [
        {"name": "Red Beach Day", "category": "beach", "location": "Santorini"},
        {"name": "Outdoor Hike", "category": "outdoor",
         "description": "outdoor cliff trail", "location": "Santorini"},
        {"name": "Taverna", "category": "food", "location": "Santorini"},
    ]
    states.append(("weather_vs_outdoor", s, {"weather_activity_mismatch"}))

    # ── Conflicts the rules CANNOT currently see (recall-gain probes) ────────
    s = _base()
    s["selected_flight"] = {"airline": "UA", "arrival": "2026-07-01T23:40:00",
                            "price_usd": 680}
    s["selected_hotel"] = {"name": "Boutique Inn", "location": "Santorini",
                           "total_usd": 700, "front_desk_hours": "07:00-22:00"}
    states.append(("arrival_after_desk_close", s, {"checkin_impossible"}))

    s = _base()
    s["selected_flight"]["price_usd"] = 1200
    s["selected_hotel"]["total_usd"] = 1100
    # 1200 + 1100 = 2300 > 2000 budget; rules don't sum components here
    states.append(("component_sum_over_budget", s, {"budget_violation"}))

    s = _base()
    s["visa_safety"] = {"visa_required": True, "processing_days": 30,
                        "safety_level": "safe"}
    s["intent"]["travel_month"] = "July 2026"
    s["intent"]["booking_date"] = "2026-06-20"  # 11 days before travel
    states.append(("visa_lead_time_too_short", s, {"visa_timing"}))

    s = _base()
    s["intent"]["interests"] = ["vegetarian fine dining"]
    s["experiences"] = [
        {"name": "Seafood Grill Crawl", "category": "food",
         "description": "five-stop seafood tasting", "location": "Santorini"},
        {"name": "Octopus BBQ", "category": "food",
         "description": "traditional seafood barbecue", "location": "Santorini"},
        {"name": "Oia Walk", "category": "culture", "location": "Santorini"},
    ]
    states.append(("preference_contradiction", s, {"preference_mismatch"}))

    return states


# ── LLM detector ──────────────────────────────────────────────────────────────

DETECT_PROMPT = """You are the conflict detector in a multi-agent travel planning system.
Below are the combined findings of independent specialist agents (flight, hotel,
experience, weather, visa/safety) for one user query.

Identify CONFLICTS ONLY: places where the combined plan is internally
inconsistent, violates a stated user constraint, or is physically/logistically
impossible. Do not list improvements, synergies, or style preferences.

Respond with ONLY a JSON array (no prose, no markdown fences). Each element:
{{"type": "<short_snake_case_label>", "agents": ["<agent>", ...], "description": "<one sentence>"}}
Return [] if there are no conflicts.

Combined agent findings:
{state_json}
"""


def llm_detect(client, model, state, usage: _UsageAccumulator | None = None):
    prompt = DETECT_PROMPT.format(state_json=json.dumps(state, indent=2))
    resp = client.messages.create(
        model=model, max_tokens=1000, temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )
    if usage is not None:
        usage.record(model, resp)
    text = resp.content[0].text.strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        out = json.loads(text)
        return out if isinstance(out, list) else []
    except json.JSONDecodeError:
        return [{"type": "UNPARSEABLE", "agents": [], "description": text[:120]}]


def types_of(conflicts):
    return Counter(c.get("type", "?") for c in conflicts)


def _to_candidates(conflicts):
    """Convert the eval LLM dicts into strict ``ConflictCandidate`` objects.

    Returns ``None`` for a schema-invalid conflict (missing type/agents).
    """
    from agents.hybrid_conflict_detector import ConflictCandidate

    candidates = []
    for c in conflicts:
        if not isinstance(c, dict) or "type" not in c or "agents" not in c:
            continue
        try:
            candidates.append(ConflictCandidate(
                conflict_type=c.get("type", ""),
                agents=c.get("agents", []) if isinstance(c.get("agents"), list) else [],
                hypothesis=c.get("description", ""),
                evidence=[c.get("description", "")],
                suggested_rule=None,
                confidence=float(c.get("confidence", 0.5)),
            ))
        except Exception:
            continue
    return candidates


def _guard_usage(usage: _UsageAccumulator, variant: str, require_calls: bool = True) -> None:
    """Refuse to write a report whose LLM usage is implausible (§12: no stub calls).

    A stub/mock client returns MagicMock fields that ``int()`` coerces to 1, and a
    mis-wired hub carries the ``"unused"`` placeholder model. Either would silently
    publish a report of zero real detections, so raise instead of writing.
    """
    if require_calls and not usage.calls:
        raise RuntimeError(f"{variant} made 0 LLM calls — refusing to write a report")
    for call in usage.calls:
        if not call.get("model") or call["model"] in ("unused", "None"):
            raise RuntimeError(f"{variant} recorded a call with model {call['model']!r}")
        if not isinstance(call.get("input_tokens"), int) or not isinstance(call.get("output_tokens"), int):
            raise RuntimeError(f"{variant} recorded non-integer token usage")


def _write_shipped_report(args, hub, model: str, client, states) -> None:
    """Drive the hub's own shipped candidate path and write the report (§12.2).

    The shipped configuration is the in-graph detector: the feature flag on,
    ``llm_detector_repetitions`` calls whose candidates are unioned (deduplicated by
    (type, agents)), then deterministic validation. Written to ``--out`` with
    ``variant: "shipped"``. The hub's own client and model come from the caller —
    ``build_rules_hub`` gives the hub a stubbed client and the ``"unused"`` model,
    which must never reach the shipped LLM calls.
    """
    from datetime import UTC, datetime

    from agents.conflicts import conflict_fingerprint
    from config import get_api_config
    from config.settings import reload_settings, get_settings

    previous = os.environ.get("VOYAGER_ENABLE_LLM_CONFLICT_CANDIDATES")
    os.environ["VOYAGER_ENABLE_LLM_CONFLICT_CANDIDATES"] = "1"
    reload_settings()
    settings = get_settings()
    repetitions = settings.llm_detector_repetitions
    detector_temperature = settings.llm_detector_temperature
    shipped_model = get_api_config().llm.collaboration_hub_model or model

    usage = _UsageAccumulator("rq4_shipped", shipped_model)
    original_client = hub.client
    hub.client = _RecordingClient(client, usage)
    hub.model = shipped_model
    try:
        results = []
        total_candidates = total_validated = total_unverified = 0
        for name, state, _truth in states:
            rules = hub.detect_conflicts_only(state)
            out = hub._run_hybrid_detection(state)
            candidates = out["llm_candidate_conflicts"]
            validated = out["validated_llm_conflicts"]
            unverified = out["unverified_llm_conflicts"]
            total_candidates += len(candidates)
            total_validated += len(validated)
            total_unverified += len(unverified)
            results.append({
                "probe": name,
                "rules": [
                    {"type": c.get("type"), "fingerprint": conflict_fingerprint(c)}
                    for c in rules
                ],
                "candidates": candidates,
                "validated": validated,
                "unverified": unverified,
            })
    finally:
        hub.client = original_client
        if previous is None:
            os.environ.pop("VOYAGER_ENABLE_LLM_CONFLICT_CANDIDATES", None)
        else:
            os.environ["VOYAGER_ENABLE_LLM_CONFLICT_CANDIDATES"] = previous
        reload_settings()

    print(f"shipped: {len(results)} probes, candidates={total_candidates}, "
          f"validated={total_validated}, unverified={total_unverified}")

    _guard_usage(usage, "shipped")

    if args.out:
        report = {
            "variant": "shipped",
            "model": shipped_model,
            "temperature": detector_temperature,
            "llm_detector_repetitions": repetitions,
            "timestamp": datetime.now(UTC).isoformat(),
            "usage": usage.total(),
            "summary": {
                "candidates": total_candidates,
                "validated": total_validated,
                "unverified": total_unverified,
            },
            "results": results,
        }
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
        print(f"wrote {args.out}")


def main(argv=None):
    # Load .env like the other entry points, so the key never needs to be passed on
    # a command line (a secret on argv is visible in the process listing).
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--probes", default=None,
        help="load RQ4 probe states from a JSON file (v1.5); default: the 8-state pilot",
    )
    parser.add_argument(
        "--build-sheet", action="store_true",
        help="write rq4_labeling_sheet.csv and rq4_sheet_key.csv next to --probes "
             "and exit (no LLM calls)",
    )
    parser.add_argument(
        "--merge-gold", metavar="SHEET", default=None,
        help="merge a filled labeling sheet into rq4_probes.json (next to SHEET) "
             "and exit (no LLM calls)",
    )
    parser.add_argument(
        "--out", metavar="PATH", default=None,
        help="write a machine-readable report (per probe, per repetition: rule types and "
             "fingerprints, LLM candidate types and agents, validated/unverified extras) "
             "to PATH.json",
    )
    parser.add_argument(
        "--variant", choices=["isolation", "shipped"], default="isolation",
        help="isolation: the standalone detector (temperature 0, two repetitions); "
             "shipped: the hub's own candidate path (VOYAGER_ENABLE_LLM_CONFLICT_CANDIDATES=1, "
             "llm_detector_repetitions=3, union) — protocol §12.2",
    )
    args = parser.parse_args(argv)

    if args.build_sheet:
        if not args.probes:
            parser.error("--build-sheet requires --probes")
        doc = load_probes(args.probes)
        base = Path(args.probes).resolve().parent
        sheet_path = base / "rq4_labeling_sheet.csv"
        key_path = base / "rq4_sheet_key.csv"
        sheet_path.write_text(build_labeling_sheet(doc))
        key_path.write_text(build_sheet_key(doc))
        print(f"wrote {sheet_path}")
        print(f"wrote {key_path}")
        return

    if args.merge_gold:
        sheet = Path(args.merge_gold).resolve()
        probes = sheet.parent / "rq4_probes.json"
        if not probes.exists():
            parser.error(f"no rq4_probes.json next to {sheet}")
        merged = merge_gold(str(probes), str(sheet))
        print(f"merged {len(merged['probes'])} probes into {probes}")
        return

    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first.")
    model = os.getenv("HYBRID_EVAL_MODEL", "claude-sonnet-4-6")
    client = make_client()
    hub = build_rules_hub()

    from agents.hybrid_conflict_detector import HybridConflictDetector
    detector = HybridConflictDetector(hub)

    states = probes_to_states(load_probes(args.probes)) if args.probes else make_states()

    if args.variant == "shipped":
        _write_shipped_report(args, hub, model, client, states)
        return

    print(f"Model under test: {model}\n{'=' * 78}")

    usage = _UsageAccumulator("rq4_isolation", model)

    # ── Aggregate counters (Priority 2.5 expanded output) ──────────────────
    total_rule_conflicts = 0
    total_llm_candidates = 0
    exact_agreement = 0          # LLM candidate type exactly matches a rule type
    missed_by_llm = 0            # rule conflict with no LLM candidate touching it
    extra_llm_candidates = 0     # LLM candidates whose type the rules did NOT emit
    validated_extra = 0          # extras accepted by a deterministic validator
    unverified_extra = 0         # extras rejected by every validator
    consistent = 0
    agree_on_rule_conflicts = 0
    rule_conflict_states = 0
    novel_real, novel_noise = 0, 0
    machine_results: list[dict] = []

    for name, state, human_truth in states:
        rules = hub.detect_conflicts_only(state)
        rule_types = set(types_of(rules))
        rule_agents = {a for c in rules for a in c.get("agents", [])}
        total_rule_conflicts += len(rules)

        run1 = llm_detect(client, model, state, usage)
        run2 = llm_detect(client, model, state, usage)
        t1, t2 = types_of(run1), types_of(run2)
        is_consistent = set(t1) == set(t2)
        consistent += is_consistent

        llm_types = set(t1)
        llm_agents = {a for c in run1 for a in c.get("agents", [])}
        total_llm_candidates += len(run1)

        # Agreement + extras vs rules
        if rules:
            rule_conflict_states += 1
            if rule_agents & llm_agents:
                agree_on_rule_conflicts += 1

        exact_agreement += len(rule_types & llm_types)
        missed_by_llm += len(rule_types - llm_types)
        extra_types = llm_types - rule_types
        extra_llm_candidates += len(extra_types)

        # ── Deterministic validation of *extra* candidates ─────────────────
        candidates = _to_candidates(run1)
        extras = [c for c in candidates if c.conflict_type in extra_types]
        validated, unverified = detector.validate(extras, state)
        validated_extra += len(validated)
        unverified_extra += len(unverified)

        if args.out:
            from agents.conflicts import conflict_fingerprint

            def _candidate(c):
                return {
                    "type": c.get("type", "?"),
                    "agents": sorted(c.get("agents", []) or []),
                }

            machine_results.append({
                "probe": name,
                "rules": [
                    {"type": c.get("type"), "fingerprint": conflict_fingerprint(c)}
                    for c in rules
                ],
                "llm_runs": [
                    {"run": 1, "candidates": [_candidate(c) for c in run1]},
                    {"run": 2, "candidates": [_candidate(c) for c in run2]},
                ],
                "validated": [v.to_dict() for v in validated],
                "unverified": [u.to_dict() for u in unverified],
            })

        # Recall gain / noise (unchanged semantics)
        if not rules and human_truth:
            if run1:
                novel_real += 1
        if not rules and not human_truth and run1:
            novel_noise += 1

        print(f"\n[{name}]")
        print(f"  rules : {sorted(rule_types) or '—'}")
        print(f"  llm r1: {sorted(set(t1)) or '—'}")
        print(f"  llm r2: {sorted(set(t2)) or '—'}   consistent={is_consistent}")
        print(f"  human : {sorted(human_truth) or '—'}")

    n = len(states)
    probes = sum(1 for _, s, t in states
                 if t and not build_rules_hub().detect_conflicts_only(s))

    # ── Precision / recall / FPR over candidate counts ─────────────────────
    precision = round(exact_agreement / total_llm_candidates, 3) if total_llm_candidates else 0.0
    recall = round(exact_agreement / total_rule_conflicts, 3) if total_rule_conflicts else 0.0
    fpr = round(unverified_extra / total_llm_candidates, 3) if total_llm_candidates else 0.0
    self_consistency = round(consistent / n, 3) if n else 0.0

    print(f"\n{'=' * 78}\nSUMMARY")
    print(f"  rule_conflicts:            {total_rule_conflicts}")
    print(f"  llm_candidates:            {total_llm_candidates}")
    print(f"  exact_agreement:           {exact_agreement}")
    print(f"  missed_by_llm:             {missed_by_llm}")
    print(f"  extra_llm_candidates:      {extra_llm_candidates}")
    print(f"  validated_extra_conflicts: {validated_extra}")
    print(f"  unverified_extra_candidates: {unverified_extra}")
    print(f"  precision:                 {precision}")
    print(f"  recall:                    {recall}")
    print(f"  false_positive_rate:       {fpr}")
    print(f"  self_consistency:          {self_consistency}")
    print(f"  (LLM agreement on rule-detected conflicts: "
          f"{agree_on_rule_conflicts}/{rule_conflict_states})")
    print(f"  (Rule-invisible conflicts caught by LLM: {novel_real}/{probes})")
    print(f"  (Hallucinated conflicts on clean states: {novel_noise})")

    print("\nInterpretation guide:")
    print("  consistency < n      → LLM-only routing would be nondeterministic;")
    print("                         rules must stay as the routing layer (hybrid).")
    print("  recall gain > 0      → LLM adds real coverage → hybrid is justified")
    print("                         as 'LLM proposes, rules/schema validate'.")
    print("  validated_extra      → extras that earned routing authority via rules.")
    print("  unverified_extra     → extras that must NOT route (false-positive risk).")
    print("  hallucinations > 0   → unvalidated LLM detection would trigger")
    print("                         unnecessary re-runs and inflate cost.")

    if args.out:
        from datetime import UTC, datetime

        _guard_usage(usage, "isolation")
        report = {
            "variant": "isolation",
            "model": model,
            "temperature": 0.0,
            "timestamp": datetime.now(UTC).isoformat(),
            "usage": usage.total(),
            "summary": {
                "rule_conflicts": total_rule_conflicts,
                "llm_candidates": total_llm_candidates,
                "exact_agreement": exact_agreement,
                "missed_by_llm": missed_by_llm,
                "extra_llm_candidates": extra_llm_candidates,
                "validated_extra_conflicts": validated_extra,
                "unverified_extra_candidates": unverified_extra,
                "precision": precision,
                "recall": recall,
                "false_positive_rate": fpr,
                "self_consistency": self_consistency,
            },
            "results": machine_results,
        }
        out_path = Path(args.out)
        out_path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
