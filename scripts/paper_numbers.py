"""Regenerate every v1.5 paper number from ``results/v1.5/`` (protocol §9, §11–12).

One command reproduces the reported values from the run artifacts, so no number is
hand-copied. Inputs are read under ``--root`` (default ``results/v1.5``). When an input
is missing the dependent rows are marked ``"status": "missing_input"``; without
``--allow-missing`` the run exits non-zero and lists what is missing.

Statistics come only from ``scripts.paper_stats`` (fixed seed, Pratt Wilcoxon, paired
bootstrap). The JSON output is deterministic for fixed inputs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

# Make the project root importable when run directly (python scripts/paper_numbers.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.paper_stats import (
    bootstrap_mean_diff,
    cohen_kappa,
    cv,
    holm,
    median_paired_diff,
    percent_agreement,
    rank_biserial,
    wilcoxon_pratt,
    wilson,
)

SETS = ("synthetic", "control", "real")
CONDITIONS = ("baseline", "targeted", "full_rerun", "broadcast", "two_way")
H2C_DIMENSIONS = ("D1_temporal", "D2_budget", "D3_preference", "D4_logistics")


# ── IO helpers ─────────────────────────────────────────────────────────────────

def _read_json(path: Path):
    with open(path) as fh:
        return json.load(fh)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in open(path) if line.strip()]


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _read_csv_skipping_comments(path: Path) -> list[dict]:
    """Read a CSV whose first lines may be ``#`` comments (labeling sheets).

    Tolerates a leading BOM and Google-Sheets quoted comment rows (``"..."``),
    which the raw exports carry.
    """
    import io

    lines = [
        line for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith(("#", '"'))
    ]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


# ── Loaders ───────────────────────────────────────────────────────────────────

def load_manifest(root: Path) -> dict | None:
    """query_id → {set, pair_id, gold: [types]}. None when the file is missing."""
    path = root / "query_manifest.csv"
    if not path.exists():
        return None
    out = {}
    for row in _read_csv(path):
        gold = [t.strip() for t in (row.get("gold_conflicts") or "").split(";") if t.strip()]
        out[row["query_id"]] = {
            "set": row.get("set"), "pair_id": row.get("pair_id"), "gold": gold,
        }
    return out


def load_sessions(root: Path, set_name: str) -> list[dict] | None:
    path = root / "runs" / set_name / "sessions.jsonl"
    if not path.exists():
        return None
    return _read_jsonl(path)


def load_failures(root: Path) -> list[dict]:
    path = root / "failures.csv"
    return _read_csv(path) if path.exists() else []


def load_model_manifest_diffs(root: Path):
    path = root / "model_manifest.json"
    if not path.exists():
        return None
    from scripts.model_manifest import compare_snapshots

    doc = _read_json(path)
    snapshots = doc.get("snapshots") or []
    if len(snapshots) < 2:
        return None
    return compare_snapshots(snapshots[0], snapshots[-1])


# ── Session keying and §8 drops ───────────────────────────────────────────────

def session_key(s: dict) -> tuple:
    tags = s.get("session_tags") or {}
    return (
        tags.get("query_id") or s.get("query_id"),
        tags.get("condition") or s.get("condition"),
        tags.get("run_index") or s.get("run_index") or 1,
    )


def dropped_queries(failures: list[dict]) -> dict[str, set]:
    """§8: a query whose re-run also failed is dropped from every condition in its set."""
    dropped: dict[str, set] = defaultdict(set)
    for f in failures:
        if str(f.get("rerun_ok", "")).strip().lower() in ("false", "0", "no", ""):
            dropped[f["set"]].add(f["query_id"])
    return dropped


def apply_drops(sessions: list[dict], set_name: str, dropped: dict[str, set]) -> list[dict]:
    kill = dropped.get(set_name, set())
    return [s for s in sessions if session_key(s)[0] not in kill]


# ── Per-session metrics (§7) ──────────────────────────────────────────────────

def final_conflicts(s: dict) -> int:
    return s.get("final_conflict_count", 0)


def _lifecycle(s: dict) -> list[dict]:
    return s.get("conflict_lifecycle") or []


def r1_lifecycle(s: dict) -> list[dict]:
    return [r for r in _lifecycle(s) if r.get("first_seen_round") == 1]


def resolution_rate_conflict(s: dict):
    r1 = r1_lifecycle(s)
    if not r1:
        return None
    return sum(1 for r in r1 if r.get("status") == "resolved") / len(r1)


def introductions(s: dict) -> int:
    return sum(1 for r in _lifecycle(s) if (r.get("first_seen_round") or 1) > 1)


def reopened(s: dict) -> int:
    return sum(1 for r in _lifecycle(s) if r.get("status") == "reopened")


def rounds_to_converge(s: dict):
    return (s.get("convergence_summary") or {}).get("converged_round")


def invocations(s: dict) -> int:
    return s.get("actual_agent_calls", 0)


def hub_calls(s: dict) -> int:
    nodes = s.get("node_durations_s") or {}
    return sum(1 for k in nodes if k.startswith("collaboration_hub"))


def cost_usd(s: dict) -> float:
    return float(s.get("estimated_cost_usd") or 0.0)


def wall_s(s: dict) -> float:
    return float(s.get("duration_s") or 0.0)


def r3_used(s: dict) -> bool:
    return (s.get("round_3_agents_rerun_count") or 0) > 0


# ── Aggregation to the unit of analysis ───────────────────────────────────────

def per_query(sessions: list[dict], metric, agg: str = "median") -> dict:
    """query_id → per-query value (median across runs, or the single replay value)."""
    grouped: dict[str, list] = defaultdict(list)
    for s in sessions:
        value = metric(s)
        if value is not None:
            grouped[session_key(s)[0]].append(value)
    if agg == "median":
        return {q: median(vals) for q, vals in sorted(grouped.items())}
    return {q: vals for q, vals in sorted(grouped.items())}


def real_primary(sessions: list[dict]) -> list[dict]:
    """Real set: run_index 1 is the primary run (§6)."""
    return [s for s in sessions if session_key(s)[2] == 1]


def pair_queries(a: dict, b: dict):
    """Align two per-query dicts on the queries present in both."""
    common = sorted(set(a) & set(b))
    return [a[q] for q in common], [b[q] for q in common], common


def _sessions_by_condition(sessions: list[dict], condition: str) -> list[dict]:
    return [s for s in sessions if session_key(s)[1] == condition]


# ── Row builders (one per §9 row) ─────────────────────────────────────────────

def _missing(paths: list[str]) -> dict:
    return {"status": "missing_input", "missing": sorted(paths)}


def _no_data(sources: list[str]) -> dict:
    return {"status": "no_data", "sources": sources}


def row_h1(root: Path, sessions: dict[str, list[dict]], allow_missing: bool) -> dict:
    """H1a / H1c: paired Wilcoxon (targeted < baseline, targeted < broadcast), Holm."""
    missing = [n for n in ("synthetic", "real") if sessions.get(n) is None]
    if missing:
        return _missing([f"runs/{n}/sessions.jsonl" for n in missing])
    out = {"status": "ok", "tests": {}, "holm": None,
           "method": "Paired Wilcoxon signed-rank (Pratt zeros), d = targeted − other, "
                     "alternative='less'; Holm across exactly the 4 confirmatory p-values "
                     "(H1a/H1c × synthetic/real)"}
    pvals = {}
    for set_name, agg in (("synthetic", "median"), ("real", "median")):
        sess = sessions.get(set_name) or []
        if set_name == "real":
            sess = real_primary(sess)
        targeted = per_query(_sessions_by_condition(sess, "targeted"), final_conflicts)
        baseline = per_query(_sessions_by_condition(sess, "baseline"), final_conflicts)
        broadcast = per_query(_sessions_by_condition(sess, "broadcast"), final_conflicts)
        for label, other in (("baseline", baseline), ("broadcast", broadcast)):
            x, y, queries = pair_queries(targeted, other)
            key = f"h1_{'a' if label == 'baseline' else 'c'}_{set_name}"
            if not queries:
                out["tests"][key] = _no_data([f"runs/{set_name}/sessions.jsonl"])
                continue
            res = wilcoxon_pratt(x, y, alternative="less")
            pvals[key] = res.p
            out["tests"][key] = {
                "statistic": res.statistic, "p": res.p, "n": res.n, "n_zero": res.n_zero,
                "all_zero": res.all_zero, "queries": queries,
                "median_paired_diff": median_paired_diff(x, y),
                "rank_biserial": rank_biserial(x, y),
                "sources": [f"runs/{set_name}/sessions.jsonl"],
            }
    out["holm"] = holm(pvals) if pvals else None
    return out


def row_h1b(root: Path, sessions: dict[str, list[dict]], allow_missing: bool) -> dict:
    """H1b: one-sided paired bootstrap (targeted vs full_rerun), 0.1 margin + quality/cost."""
    if sessions.get("synthetic") is None:
        return _missing(["runs/synthetic/sessions.jsonl"])
    out = {"status": "ok", "margin": 0.1, "tests": {},
           "method": "One-sided paired bootstrap (SEED=20261117, n=10000) of "
                     "mean(targeted − full_rerun); margin 0.1 for final_conflicts and 0 "
                     "for introductions/reopened/invocations/cost"}
    sess = sessions.get("synthetic") or []
    targeted = per_query(_sessions_by_condition(sess, "targeted"), final_conflicts)
    full = per_query(_sessions_by_condition(sess, "full_rerun"), final_conflicts)
    x, y, queries = pair_queries(targeted, full)
    if not queries:
        out["tests"]["final_conflicts"] = _no_data(["runs/synthetic/sessions.jsonl"])
    else:
        est, _, upper = bootstrap_mean_diff(x, y, sided="upper")
        out["tests"]["final_conflicts"] = {
            "estimate": est, "upper": upper, "margin": 0.1, "pass": upper < 0.1,
            "queries": queries, "sources": ["runs/synthetic/sessions.jsonl"],
        }
    for label, metric in (("introductions", introductions), ("reopened", reopened),
                          ("invocations", invocations), ("cost_usd", cost_usd)):
        t = per_query(_sessions_by_condition(sess, "targeted"), metric)
        f = per_query(_sessions_by_condition(sess, "full_rerun"), metric)
        tx, fy, queries = pair_queries(t, f)
        if not queries:
            out["tests"][label] = _no_data(["runs/synthetic/sessions.jsonl"])
            continue
        e, _, u = bootstrap_mean_diff(tx, fy, sided="upper")
        out["tests"][label] = {
            "estimate": e, "upper": u, "pass": u < 0,
            "queries": queries, "sources": ["runs/synthetic/sessions.jsonl"],
        }
    return out


def row_h1d(root: Path, sessions: dict[str, list[dict]], allow_missing: bool) -> dict:
    """H1d: two-sided bootstrap on cost (targeted − baseline) + per-node wall time."""
    if sessions.get("synthetic") is None:
        return _missing(["runs/synthetic/sessions.jsonl"])
    out = {"status": "ok", "cost": None, "node_wall_time": {},
           "method": "Two-sided paired bootstrap of mean cost(targeted − baseline); "
                     "per-node wall time is the median across targeted synthetic sessions"}
    sess = sessions.get("synthetic") or []
    targeted = per_query(_sessions_by_condition(sess, "targeted"), cost_usd)
    baseline = per_query(_sessions_by_condition(sess, "baseline"), cost_usd)
    x, y, queries = pair_queries(targeted, baseline)
    if not queries:
        out["cost"] = _no_data(["runs/synthetic/sessions.jsonl"])
    else:
        est, lo, hi = bootstrap_mean_diff(x, y, sided="two")
        out["cost"] = {
            "estimate": est, "lo": lo, "hi": hi, "queries": queries,
            "includes_zero_or_below": lo <= 0,
            "sources": ["runs/synthetic/sessions.jsonl"],
        }
    node_totals: dict[str, dict] = {}
    for condition in CONDITIONS:
        rows = _sessions_by_condition(sess, condition)
        nodes = defaultdict(list)
        for s in rows:
            for k, v in (s.get("node_durations_s") or {}).items():
                nodes[k].append(v)
        node_totals[condition] = {k: median(v) for k, v in sorted(nodes.items())}
    out["node_wall_time"] = node_totals
    return out


def row_h2a(root: Path, sessions: dict[str, list[dict]], allow_missing: bool) -> dict:
    """H2a: pooled targeted synthetic resolution rate (Wilson), introductions = 0."""
    if sessions.get("synthetic") is None:
        return _missing(["runs/synthetic/sessions.jsonl"])
    sess = _sessions_by_condition(sessions.get("synthetic") or [], "targeted")
    resolved = sum(len([r for r in r1_lifecycle(s) if r.get("status") == "resolved"]) for s in sess)
    r1_total = sum(len(r1_lifecycle(s)) for s in sess)
    intro_total = sum(introductions(s) for s in sess)
    rate = wilson(resolved, r1_total) if r1_total else None
    return {
        "status": "ok",
        "method": "Pooled over all targeted synthetic sessions and runs: R1 conflicts "
                  "resolved / R1 conflicts, Wilson 95% CI; pass ≥ 0.97 and introductions = 0",
        "resolved": resolved, "r1_total": r1_total,
        "wilson": rate,
        "introductions_total": intro_total,
        "pass": (rate is not None and rate[0] >= 0.97 and intro_total == 0),
        "sources": ["runs/synthetic/sessions.jsonl"],
    }


def row_h2b(root: Path, sessions: dict[str, list[dict]], allow_missing: bool) -> dict:
    """H2b: real unresolved-reason distribution (Wilson) and 100% classifiable."""
    if sessions.get("real") is None:
        return _missing(["runs/real/sessions.jsonl"])
    sess = [s for s in (sessions.get("real") or [])
            if session_key(s)[1] != "baseline" and session_key(s)[2] == 1]
    reasons: dict[str, int] = defaultdict(int)
    unresolved_total = 0
    for s in sess:
        counts = s.get("unresolved_reason_counts") or {}
        for reason, n in counts.items():
            if n:
                reasons[reason] += n
                unresolved_total += n
    rate = {r: wilson(n, unresolved_total) for r, n in sorted(reasons.items())} if unresolved_total else {}
    classifiable = sum(n for r, n in reasons.items() if r and r not in ("", "unknown"))
    return {
        "status": "ok",
        "method": "Real run_index 1, every condition except baseline: per-reason Wilson CIs "
                  "over unresolved conflicts; classifiable fraction must be 1.0",
        "unresolved_total": unresolved_total,
        "reasons": {r: {"count": n, "wilson": rate[r]} for r, n in sorted(reasons.items())},
        "classifiable_fraction": (classifiable / unresolved_total) if unresolved_total else None,
        "pass": bool(unresolved_total == 0 or (classifiable / unresolved_total) == 1.0),
        "sources": ["runs/real/sessions.jsonl"],
    }


def row_h2c(root: Path, allow_missing: bool) -> dict:
    """H2c: coherence audit from the author/rater score sheets (§11)."""
    author_path = root / "h2c" / "author" / "author_scores.csv"
    rater_path = root / "h2c" / "rater" / "rater_scores.csv"
    rater2_path = root / "h2c" / "rater" / "rater_scores_round2.csv"
    key_path = root / "h2c" / "KEY_DO_NOT_SHARE.csv"
    if not all(p.exists() for p in (author_path, rater_path, key_path)):
        return _missing(["h2c/author/author_scores.csv", "h2c/rater/rater_scores.csv"])
    author = _read_csv(author_path)
    rater = _read_csv(rater_path)
    rater2 = _read_csv(rater2_path) if rater2_path.exists() else []

    def assessable(rows):
        return [r for r in rows
                if all(r.get(d) != "U" for d in H2C_DIMENSIONS)]

    a_assess = assessable(author)
    fully = sum(1 for r in a_assess if str(r.get("fully_coherent", "")).upper() == "Y")
    rate = (fully / len(a_assess)) if a_assess else None
    u_rate = (sum(1 for r in author if any(r.get(d) == "U" for d in H2C_DIMENSIONS))
              / len(author)) if author else None

    # U-as-coherent variant: treat U as passing.
    def no_f(rows):
        return [r for r in rows if not any(r.get(d) == "F" for d in H2C_DIMENSIONS)]
    u_as_coherent = (len(no_f(author)) / len(author)) if author else None

    author_by_id = {r.get("plan_id"): r for r in author}

    def agreements(rater_rows):
        out = {}
        for d in H2C_DIMENSIONS + ("fully_coherent",):
            pairs = [(author_by_id[r["plan_id"]].get(d), r.get(d))
                     for r in rater_rows if r.get("plan_id") in author_by_id]
            a_vals = [p[0] for p in pairs]
            b_vals = [p[1] for p in pairs]
            out[d] = {
                "percent_agreement": percent_agreement(a_vals, b_vals),
                "cohen_kappa": cohen_kappa(a_vals, b_vals),
            }
        return out

    def contingency(agr):
        return any((agr[d]["percent_agreement"] or 0.0) < 0.80 for d in H2C_DIMENSIONS)

    rounds = {}
    rounds["round1"] = {"n_plans": len(rater), "double_scored": agreements(rater)}
    rounds["round1"]["contingency_triggered"] = contingency(rounds["round1"]["double_scored"])
    if rater2:
        rounds["round2"] = {"n_plans": len(rater2), "double_scored": agreements(rater2)}
        rounds["round2"]["contingency_triggered"] = contingency(rounds["round2"]["double_scored"])
        combined = rater + rater2
        rounds["combined"] = {"n_plans": len(combined), "double_scored": agreements(combined)}
        rounds["combined"]["contingency_triggered"] = contingency(rounds["combined"]["double_scored"])

    # Post hoc (not pre-registered): the H2c verdict's dependence on D1.
    post_hoc = {
        "fully_coherent_rate_excluding_d1": (
            len([r for r in author
                 if not any(r.get(d) == "F" for d in H2C_DIMENSIONS[1:])]) / len(author)
        ) if author else None,
        "f_counts": {d: sum(1 for r in author if r.get(d) == "F") for d in H2C_DIMENSIONS},
    }

    return {
        "status": "ok",
        "method": "fully coherent = no F among D1–D4; assessable = no U; "
                  "threshold 0.90 on assessable plans; agreement/κ per double-scoring "
                  "round and combined (rounds.round1/round2/combined); "
                  "post_hoc_sensitivity is post hoc (excluding D1, per-dimension F counts)",
        "author_n": len(author), "assessable_n": len(a_assess),
        "fully_coherent_rate": rate, "threshold": 0.90,
        "pass": rate is not None and rate >= 0.90,
        "u_rate": u_rate, "u_as_coherent": u_as_coherent,
        "rounds": rounds,
        "double_scored": rounds["round1"]["double_scored"],
        "contingency_triggered": rounds["round1"]["contingency_triggered"],
        "post_hoc_sensitivity": post_hoc,
        "sources": ["h2c/author/author_scores.csv", "h2c/rater/rater_scores.csv",
                    "h2c/rater/rater_scores_round2.csv"],
    }


def _h3a_signature(s: dict) -> tuple:
    r1_fps = tuple(sorted(r.get("fingerprint") for r in r1_lifecycle(s)))
    final = tuple(sorted((r.get("fingerprint"), r.get("status")) for r in _lifecycle(s)))
    return (
        tuple(sorted(s.get("round_1_conflict_types") or [])),
        r1_fps,
        tuple(sorted(s.get("round_1_message_recipients") or [])),
        tuple(sorted(s.get("round_2_agents_rerun") or [])),
        tuple(sorted(s.get("round_3_agents_rerun") or [])),
        final,
    )


def row_h3a(root: Path, sessions: dict[str, list[dict]], allow_missing: bool) -> dict:
    """H3a: real replay determinism across run_index 1..3 (exact-match %)."""
    if sessions.get("real") is None:
        return _missing(["runs/real/sessions.jsonl"])
    by_query_cond: dict[tuple, list[dict]] = defaultdict(list)
    for s in sessions.get("real") or []:
        qid, cond, _ = session_key(s)
        by_query_cond[(qid, cond)].append(s)
    mismatches = []
    matched = 0
    for key, rows in sorted(by_query_cond.items()):
        runs = sorted(rows, key=lambda s: session_key(s)[2])
        if len(runs) < 2:
            continue
        sigs = [_h3a_signature(s) for s in runs]
        if all(sig == sigs[0] for sig in sigs):
            matched += 1
        else:
            mismatches.append({"query_id": key[0], "condition": key[1], "runs": len(runs)})
    total = len(by_query_cond)
    return {
        "status": "ok",
        "method": "Exact match across run_index 1..3 of R1 types/fingerprints, "
                  "round_1_message_recipients, re-run sets and the final lifecycle set",
        "pairs": total, "exact": matched, "mismatched": mismatches,
        "exact_pct": (matched / total) if total else None,
        "pass": total > 0 and matched == total,
        "sources": ["runs/real/sessions.jsonl"],
    }


COMPONENT_AGENT = {
    "flight_agent": "flight", "hotel_agent": "hotel", "experience_agent": "experience",
    "weather_agent": "weather", "visa_safety_agent": "visa_safety",
    "critique_flight": "flight", "critique_hotel": "hotel",
}
NON_CONFLICT_COMPONENTS = {"intent_parser", "itinerary_builder", "option_generator",
                           "collaboration_hub_narrative", "collaboration_hub_llm_detector"}


def _involved_agents(s: dict) -> set:
    from agents.conflicts import CONFLICT_RESOLVER

    involved: set = set()
    for r in _lifecycle(s):
        involved.update(r.get("agents") or [])
        resolver = CONFLICT_RESOLVER.get(r.get("type", ""))
        if resolver:
            involved.add(resolver)
    return involved


def row_h3b(root: Path, sessions: dict[str, list[dict]], allow_missing: bool) -> dict:
    """H3b: per-component cost CV across replays; variance from non-conflict components."""
    if sessions.get("real") is None:
        return _missing(["runs/real/sessions.jsonl"])
    sess = sessions.get("real") or []
    by_query_cond: dict[tuple, list[dict]] = defaultdict(list)
    for s in sess:
        qid, cond, _ = session_key(s)
        by_query_cond[(qid, cond)].append(s)

    comps: dict[str, list] = defaultdict(list)
    variance_non_conflict = variance_total = 0.0
    for key, rows in by_query_cond.items():
        if len(rows) < 2:
            continue
        involved = _involved_agents(rows[0])
        comp_costs: dict[str, list] = defaultdict(list)
        for s in rows:
            for comp, usage in (s.get("per_component_usage") or {}).items():
                comp_costs[comp].append(float(usage.get("cost_usd") or 0.0))
        for comp, costs in comp_costs.items():
            comps[comp].extend(costs)
            v = _variance(costs)
            variance_total += v
            agent = COMPONENT_AGENT.get(comp, comp)
            if comp in NON_CONFLICT_COMPONENTS or agent not in involved:
                variance_non_conflict += v
    share = (variance_non_conflict / variance_total) if variance_total else None
    return {
        "status": "ok",
        "method": "Per-component CV of cost across replays; share of Σ per-component "
                  "sample variance from non-conflict components; covariance ignored",
        "component_cv": {c: cv(vals) for c, vals in sorted(comps.items())},
        "variance_share_non_conflict": share,
        "pass": share is not None and share >= 0.80,
        "sources": ["runs/real/sessions.jsonl"],
    }


def _variance(values) -> float:
    if len(values) < 2:
        return 0.0
    m = sum(values) / len(values)
    return sum((v - m) ** 2 for v in values) / (len(values) - 1)


def _precision_recall(predicted: dict, gold: dict, types) -> dict:
    out = {}
    for t in types:
        tp = sum(1 for q in gold if t in gold[q] and t in predicted.get(q, set()))
        fp = sum(1 for q in gold if t in predicted.get(q, set()) and t not in gold[q])
        fn = sum(1 for q in gold if t in gold[q] and t not in predicted.get(q, set()))
        out[t] = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": wilson(tp, tp + fp) if tp + fp else None,
            "recall": wilson(tp, tp + fn) if tp + fn else None,
        }
    return out


def _overall_recall(pr: dict):
    tp = sum(v["tp"] for v in pr.values())
    fn = sum(v["fn"] for v in pr.values())
    return tp / (tp + fn) if tp + fn else None


def _resolver_agent(conflict_type: str):
    from agents.conflicts import CONFLICT_RESOLVER

    return CONFLICT_RESOLVER.get(conflict_type)


_AGENT_COMPONENT = {"flight": "flight_agent", "hotel": "hotel_agent", "experience": "experience_agent"}


def _mean_per_call_cost(sessions: list[dict], agent: str):
    """Mean per-call cost of an agent across sessions (component cost ÷ calls)."""
    component = _AGENT_COMPONENT.get(agent)
    if component is None:
        return None
    per_call = []
    for s in sessions:
        usage = (s.get("per_component_usage") or {}).get(component)
        if not usage:
            continue
        calls = 1
        if agent in (s.get("round_2_agents_rerun") or []):
            calls += 1
        if agent in (s.get("round_3_agents_rerun") or []):
            calls += 1
        per_call.append(float(usage.get("cost_usd") or 0.0) / calls)
    return median(per_call) if per_call else None


def row_h4(root: Path, manifest, sessions: dict[str, list[dict]], allow_missing: bool) -> dict:
    """H4: rules vs LLM precision/recall on synthetic, controls and probes."""
    missing = []
    if manifest is None:
        missing.append("query_manifest.csv")
    if sessions.get("synthetic") is None:
        missing.append("runs/synthetic/sessions.jsonl")
    if missing:
        return _missing(missing)

    synthetic_sessions = sessions.get("synthetic") or []
    control_sessions = sessions.get("control")
    probes_path = root / "rq4_probes.json"
    iso_path = root / "rq4" / "isolation.json"
    ship_path = root / "rq4" / "shipped.json"

    types = ["location_mismatch", "timing_inefficiency", "weather_activity_mismatch"]
    probe_types = types + ["checkin_impossible", "budget_violation", "visa_timing",
                           "preference_mismatch"]
    out: dict = {
        "status": "ok",
        "method": "H4 synthetic: majority vote of R1 rule types across all conditions "
                  "and runs (R1 precedes refinement, so it is condition-independent)",
        "rules": {}, "llm": {}, "self_consistency": None,
        "recall_gain": {}, "fp_rerun_cost_usd": None, "detector_cost_usd": None,
    }

    # Rules on synthetic (majority vote across runs vs manifest gold).
    by_query: dict[str, list] = defaultdict(list)
    for s in synthetic_sessions:
        by_query[session_key(s)[0]].append(set(s.get("round_1_conflict_types") or []))
    predicted, gold = {}, {}
    for qid, type_sets in sorted(by_query.items()):
        votes = Counter(t for ts in type_sets for t in ts)
        predicted[qid] = {t for t, c in votes.items() if c > len(type_sets) / 2}
        gold[qid] = set(manifest.get(qid, {}).get("gold") or [])
    out["rules"]["synthetic"] = _precision_recall(predicted, gold, types)

    # Rules on controls (gold none → spurious fires, per session and per query).
    if control_sessions is None:
        out["rules"]["controls"] = {"status": "missing_input"}
    else:
        fire_counts = Counter(
            t for s in control_sessions for t in (s.get("round_1_conflict_types") or [])
        )
        spurious_sessions = sum(1 for s in control_sessions if s.get("round_1_conflict_types"))
        fired_queries = {session_key(s)[0] for s in control_sessions
                         if s.get("round_1_conflict_types")}
        out["rules"]["controls"] = {
            "spurious_sessions": spurious_sessions,
            "spurious_queries": len(fired_queries),
            "n_sessions": len(control_sessions),
            "n_queries": len({session_key(s)[0] for s in control_sessions}),
            "per_type_fires": dict(sorted(fire_counts.items())),
            "pass": spurious_sessions == 0,
        }

    if not probes_path.exists():
        out["llm"] = {"status": "missing_input"}
        return out

    probes = _read_json(probes_path)
    probe_gold = {p["id"]: set(p.get("gold") or []) for p in probes["probes"]}
    report_rules, report_llm, validated_lists = {}, {}, {}
    for label, path in (("isolation", iso_path), ("shipped", ship_path)):
        if not path.exists():
            out["llm"][label] = {"status": "missing_input"}
            out["rules"].setdefault(label, {"status": "missing_input"})
            continue
        report = _read_json(path)
        rules_pred, llm_pred, valid_list = {}, {}, {}
        consistency_total = consistent = 0
        for res in report.get("results", []):
            rules_pred[res["probe"]] = {r["type"] for r in res.get("rules", [])}
            if label == "isolation":
                runs = res.get("llm_runs", [{}])
                run1 = runs[0].get("candidates", []) if runs else []
                run2 = runs[1].get("candidates", []) if len(runs) > 1 else []
                llm_pred[res["probe"]] = {c["type"] for c in run1}
                consistency_total += 1
                if {c["type"] for c in run1} == {c["type"] for c in run2}:
                    consistent += 1
            else:
                llm_pred[res["probe"]] = {
                    c.get("type", c.get("conflict_type")) for c in res.get("candidates", [])
                }
                valid_list[res["probe"]] = [v["type"] for v in res.get("validated", [])]
        out["rules"][label] = _precision_recall(rules_pred, probe_gold, probe_types)
        out["llm"][label] = _precision_recall(llm_pred, probe_gold, probe_types)
        report_rules[label] = rules_pred
        report_llm[label] = llm_pred
        validated_lists[label] = valid_list
        if label == "isolation":
            out["self_consistency"] = (consistent / consistency_total) if consistency_total else None

    # Recall gain: isolation = recall(LLM run 1) − recall(rules);
    # shipped = recall(rules ∪ validated) − recall(rules) (only validated candidates route).
    if "isolation" in report_llm and "isolation" in report_rules:
        base = out["rules"]["isolation"]
        llm = out["llm"]["isolation"]
        out["recall_gain"]["isolation"] = {
            "overall": _overall_recall(llm) - _overall_recall(base)
            if _overall_recall(llm) is not None and _overall_recall(base) is not None else None,
            "per_type": {
                t: (llm[t]["recall"][0] - base[t]["recall"][0])
                if llm.get(t, {}).get("recall") and base.get(t, {}).get("recall") else None
                for t in probe_types
            },
        }
    if "shipped" in report_rules and "shipped" in validated_lists:
        combined = {
            q: report_rules["shipped"][q] | set(validated_lists["shipped"].get(q, []))
            for q in report_rules["shipped"]
        }
        combined_pr = _precision_recall(combined, probe_gold, probe_types)
        base = out["rules"]["shipped"]
        out["recall_gain"]["shipped"] = {
            "overall": _overall_recall(combined_pr) - _overall_recall(base)
            if _overall_recall(combined_pr) is not None and _overall_recall(base) is not None else None,
        }

        # FP re-run cost: validated candidates not in gold × the resolver's mean per-call cost.
        target = _sessions_by_condition(synthetic_sessions, "targeted")
        cost_map = {a: _mean_per_call_cost(target, a) for a in ("flight", "hotel", "experience")}
        fp_total = 0.0
        no_resolver = 0
        for probe, type_list in validated_lists["shipped"].items():
            for t in type_list:
                if t in probe_gold.get(probe, set()):
                    continue
                agent = _resolver_agent(t)
                if agent is None:
                    no_resolver += 1
                    continue
                cost = cost_map.get(agent)
                if cost is not None:
                    fp_total += cost
        out["fp_rerun_cost_usd"] = {
            "total": fp_total,
            "no_resolver_count": no_resolver,
            "method": "Σ over shipped validated candidates not in gold of the resolver "
                      "agent's mean per-call cost (component cost ÷ the agent's calls per "
                      "session, median across targeted synthetic sessions)",
        }

    detector_costs = [
        float((s.get("per_component_usage") or {}).get("collaboration_hub_llm_detector", {}).get("cost_usd") or 0.0)
        for s in synthetic_sessions
        if "collaboration_hub_llm_detector" in (s.get("per_component_usage") or {})
    ]
    out["detector_cost_usd"] = median(detector_costs) if detector_costs else None

    return out


def row_h5(root: Path, sessions: dict[str, list[dict]], allow_missing: bool) -> dict:
    """H5 (exploratory): two_way vs targeted on reopened and r3_used."""
    if sessions.get("synthetic") is None:
        return _missing(["runs/synthetic/sessions.jsonl"])
    sess = sessions.get("synthetic") or []
    two = per_query(_sessions_by_condition(sess, "two_way"), final_conflicts)
    targeted = per_query(_sessions_by_condition(sess, "targeted"), final_conflicts)
    out = {"status": "ok", "tests": {},
           "method": "Exploratory; not in Holm. Paired Wilcoxon (alternative='greater') "
                     "on reopened and r3_used; final_conflicts_not_lower is the median check"}
    for label, metric in (("reopened", reopened), ("r3_used", lambda s: int(r3_used(s)))):
        t = per_query(_sessions_by_condition(sess, "two_way"), metric)
        g = per_query(_sessions_by_condition(sess, "targeted"), metric)
        x, y, queries = pair_queries(t, g)
        if not queries:
            out["tests"][label] = _no_data(["runs/synthetic/sessions.jsonl"])
            continue
        res = wilcoxon_pratt(x, y, alternative="greater")
        out["tests"][label] = {
            "p": res.p, "n": res.n, "queries": queries,
            "median_paired_diff": median_paired_diff(x, y),
        }
    tx, gx, _ = pair_queries(two, targeted)
    out["final_conflicts_not_lower"] = median(tx) >= median(gx) if tx else None
    out["sources"] = ["runs/synthetic/sessions.jsonl"]
    return out


def _read_kalyan_labels(path: Path, types: list[str], *, strict: bool) -> dict[str, set[str]]:
    """Read a Kalyan labeling CSV → {sheet_id: set(types)}.

    ``strict`` mirrors the cleaned ``rq4_labels_kalyan.csv`` (a blank sheet id or a
    blank ``gold_types`` fails loudly). The raw first-pass export may carry a stray
    all-empty row, which ``strict=False`` skips.
    """
    labels: dict[str, set[str]] = {}
    for row in _read_csv_skipping_comments(path):
        sheet_id = (row.get("sheet_id") or "").strip()
        raw = (row.get("gold_types") or "").strip()
        if not sheet_id:
            if strict:
                raise SystemExit(f"{path.name}: a data row has a blank sheet_id")
            continue
        if not raw:
            raise SystemExit(
                f"{path.name}: blank gold_types for sheet {sheet_id!r} "
                "(enter 'none' or ';'-separated types)"
            )
        if raw.lower() == "none":
            labels[sheet_id] = set()
            continue
        lbl: set[str] = set()
        for t in raw.split(";"):
            t = t.strip()
            if not t:
                continue
            if t not in types:
                raise SystemExit(
                    f"{path.name}: unknown type {t!r} for sheet {sheet_id!r}; "
                    f"allowed types are {types}"
                )
            lbl.add(t)
        labels[sheet_id] = lbl
    return labels


def _rq4_agreement(probe_gold: dict[str, set[str]], types: list[str],
                   key: dict[str, str], kalyan: dict[str, set[str]], label: str) -> dict:
    abhishek: list[set[str]] = []
    second: list[set[str]] = []
    disagreements: list[tuple[str, list, list]] = []
    for sheet_id in sorted(key):
        probe_id = key[sheet_id]
        if probe_id not in probe_gold:
            raise SystemExit(
                f"rq4_sheet_key.csv: sheet {sheet_id!r} maps to unknown probe {probe_id!r}"
            )
        if sheet_id not in kalyan:
            raise SystemExit(f"{label}: missing row for sheet {sheet_id!r}")
        a = probe_gold[probe_id]
        k = kalyan[sheet_id]
        abhishek.append(a)
        second.append(k)
        if a != k:
            disagreements.append((probe_id, sorted(a), sorted(k)))

    per_type = {}
    for t in types:
        a_bits = [1 if t in s else 0 for s in abhishek]
        k_bits = [1 if t in s else 0 for s in second]
        per_type[t] = {
            "percent_agreement": percent_agreement(a_bits, k_bits),
            "cohen_kappa": cohen_kappa(a_bits, k_bits),
        }

    exact = sum(1 for a, k in zip(abhishek, second) if a == k)
    return {
        "n_probes": len(abhishek),
        "exact_set_agreement_rate": round(exact / len(abhishek), 6) if abhishek else None,
        "per_type": per_type,
        "disagreements": disagreements,
    }


def row_rq4_gold_agreement(root: Path, allow_missing: bool) -> dict:
    """RQ4 inter-labeler agreement: Kalyan's labels vs Abhishek's merged gold.

    Reports the self-checked labels (``rq4_labels_kalyan.csv``) at the top level and,
    when present, the same statistics for Kalyan's raw first pass
    (``rq4_kalyan/rq4_labels_kalyan_raw_export.csv``, cleaned the same way, without
    the three self-check changes) under ``raw_first_pass``.
    """
    kalyan_path = root / "rq4_labels_kalyan.csv"
    key_path = root / "rq4_sheet_key.csv"
    probes_path = root / "rq4_probes.json"
    raw_path = root / "rq4_kalyan" / "rq4_labels_kalyan_raw_export.csv"

    missing = [p.name for p in (probes_path, key_path, kalyan_path) if not p.exists()]
    if missing:
        return _missing(missing)

    probes = _read_json(probes_path)
    types = list(probes.get("types") or [])
    probe_gold = {p["id"]: set(p.get("gold") or []) for p in probes.get("probes", [])}
    key = {r["sheet_id"]: r["probe_id"] for r in _read_csv_skipping_comments(key_path)}

    final = _rq4_agreement(probe_gold, types, key,
                           _read_kalyan_labels(kalyan_path, types, strict=True), "final")
    result = {
        "status": "ok",
        "method": "Second labeler (Kalyan) vs Abhishek's merged gold in rq4_probes.json: "
                  "per-type presence/absence percent agreement and Cohen's κ, plus the "
                  "exact-set agreement rate; disagreements listed as (probe, Abhishek, Kalyan). "
                  "Top level = self-checked labels; raw_first_pass = the raw export",
        "n_probes": final["n_probes"],
        "exact_set_agreement_rate": final["exact_set_agreement_rate"],
        "per_type": final["per_type"],
        "disagreements": final["disagreements"],
        "sources": ["rq4_labels_kalyan.csv", "rq4_sheet_key.csv", "rq4_probes.json"],
    }
    if raw_path.exists():
        raw = _rq4_agreement(probe_gold, types, key,
                             _read_kalyan_labels(raw_path, types, strict=False),
                             "raw_first_pass")
        result["raw_first_pass"] = raw
        result["sources"].append("rq4_kalyan/rq4_labels_kalyan_raw_export.csv")
    return result


def load_billing(root: Path) -> dict:
    """Billing reconciliation summary for the header (written by reconcile_billing.py)."""
    path = root / "billing_reconciliation.json"
    if not path.exists():
        return {"status": "missing_input"}
    doc = _read_json(path)
    return {
        "pass": doc.get("pass"),
        "delta_pct": doc.get("deltas", {}).get("cost_delta_pct"),
    }


# ── Report assembly and output ────────────────────────────────────────────────

def build_report(root: Path, allow_missing: bool) -> tuple[dict, list[str]]:
    manifest = load_manifest(root)
    sessions = {n: load_sessions(root, n) for n in SETS}
    sessions = {n: apply_drops(v, n, dropped_queries(load_failures(root)))
                for n, v in sessions.items() if v is not None}

    missing = []
    if manifest is None:
        missing.append("query_manifest.csv")
    for n in SETS:
        if n not in sessions:
            missing.append(f"runs/{n}/sessions.jsonl")
    for rel in ("h2c/author/author_scores.csv", "h2c/rater/rater_scores.csv",
                "h2c/KEY_DO_NOT_SHARE.csv", "rq4_probes.json", "rq4/isolation.json",
                "rq4/shipped.json", "model_manifest.json"):
        if not (root / rel).exists():
            missing.append(rel)

    if missing and not allow_missing:
        raise SystemExit(
            "missing inputs (use --allow-missing to emit missing_input rows):\n  "
            + "\n  ".join(sorted(set(missing)))
        )

    rows = {
        "H1a_H1c": row_h1(root, sessions, allow_missing),
        "H1b": row_h1b(root, sessions, allow_missing),
        "H1d": row_h1d(root, sessions, allow_missing),
        "H2a": row_h2a(root, sessions, allow_missing),
        "H2b": row_h2b(root, sessions, allow_missing),
        "H2c": row_h2c(root, allow_missing),
        "H3a": row_h3a(root, sessions, allow_missing),
        "H3b": row_h3b(root, sessions, allow_missing),
        "H4": row_h4(root, manifest, sessions, allow_missing),
        "H5": row_h5(root, sessions, allow_missing),
        "rq4_gold_agreement": row_rq4_gold_agreement(root, allow_missing),
    }

    mm_diffs = load_model_manifest_diffs(root)
    header = {
        "git_head": _git_head(),
        "root": str(root),
        "inputs": {rel: _sha256(root / rel) for rel in sorted(missing) if (root / rel).exists()},
        "model_manifest": {
            "diffs": mm_diffs,
            "unchanged": mm_diffs is not None and len(mm_diffs) == 0,
            "method": "compare_snapshots diff list; empty = unchanged",
        },
        "billing": load_billing(root),
    }
    return {"header": header, "rows": rows}, missing


def _md_table(doc: dict) -> str:
    lines = []
    for hypothesis, row in doc["rows"].items():
        lines.append(f"## {hypothesis}")
        lines.append(f"status: {row.get('status')}")
        if row.get("status") == "missing_input":
            lines.append("missing: " + ", ".join(row.get("missing", [])))
            continue
        for key, value in row.items():
            if key in ("status", "sources"):
                continue
            lines.append(f"- {key}: {json.dumps(value, sort_keys=True, default=str)}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default="results/v1.5",
                        help="directory of the v1.5 inputs (default results/v1.5)")
    parser.add_argument("--out", default="results/v1.5/paper_numbers",
                        help="output prefix → <out>.json and <out>.md")
    parser.add_argument("--allow-missing", action="store_true",
                        help="emit missing_input rows instead of exiting non-zero")
    args = parser.parse_args(argv)

    doc, _ = build_report(Path(args.root), args.allow_missing)
    out = Path(args.out)
    out.with_suffix(".json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    out.with_suffix(".md").write_text(_md_table(doc) + "\n")
    print(f"wrote {out}.json and {out}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
