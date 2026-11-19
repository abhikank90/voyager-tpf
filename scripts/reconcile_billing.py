"""Billing reconciliation for the v1.5 window (protocol §10 step 16, §7).

Compares Anthropic Console usage/cost exports against the tracked tokens and
``estimated_cost_usd`` in every session under ``results/v1.5/runs/*/sessions.jsonl``
(plus any usage recorded in ``results/v1.5/rq4/*.json``), inside the window. The
window is ``--start``/``--end`` or, by default, the first and last snapshot
timestamps in ``results/v1.5/model_manifest.json``.

The Console export format is not fixed, so the parser detects date / model /
input / output / cost columns by header-name matching and fails with a clear
message listing the headers it saw when a required column is missing.

Passes when ``|tracked − billed| ≤ 1%`` of billed cost (the v1.4 standard).

Usage:
    python scripts/reconcile_billing.py --exports usage1.csv usage2.csv \
        [--start 2026-11-18 --end 2026-11-20] [--root results/v1.5]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

# Make the project root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

THRESHOLD_PCT = 1.0


# ── Column detection (tolerant of unknown export layouts) ─────────────────────

def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _pick(headers: dict[str, str], *needles: str) -> str | None:
    """First original header whose normalised form contains any needle."""
    for needle in needles:
        for norm, original in headers.items():
            if needle in norm:
                return original
    return None


def _to_int(value) -> int:
    if value is None:
        return 0
    text = str(value).replace(",", "").strip()
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _to_float(value) -> float:
    if value is None:
        return 0.0
    text = str(value).replace(",", "").replace("$", "").strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _to_date(value) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_export(path: Path) -> list[dict]:
    """Parse one Console export into [{date, model, input, output, cost}]."""
    lines = [
        line for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    headers = {_norm(h): h for h in (reader.fieldnames or [])}

    date_col = _pick(headers, "date", "timestamp", "time")
    model_col = _pick(headers, "model")
    input_col = _pick(headers, "inputtoken", "input")
    output_col = _pick(headers, "outputtoken", "output")
    cost_col = _pick(headers, "cost", "amount", "usd")

    required = {"date": date_col, "model": model_col, "input tokens": input_col,
                "output tokens": output_col, "cost": cost_col}
    missing = [name for name, col in required.items() if col is None]
    if missing:
        raise SystemExit(
            f"cannot parse export {path}: missing column(s) for {missing}. "
            f"Headers seen: {sorted(reader.fieldnames or [])}"
        )

    rows = []
    for row in reader:
        rows.append({
            "date": _to_date(row.get(date_col)),
            "model": (row.get(model_col) or "").strip(),
            "input": _to_int(row.get(input_col)),
            "output": _to_int(row.get(output_col)),
            "cost": _to_float(row.get(cost_col)),
        })
    return rows


# ── Window ────────────────────────────────────────────────────────────────────

def _window(args, root: Path) -> tuple[date, date, str]:
    start = _to_date(args.start) if args.start else None
    end = _to_date(args.end) if args.end else None
    source = "args"
    if start is None or end is None:
        manifest = root / "model_manifest.json"
        if manifest.exists():
            doc = json.loads(manifest.read_text())
            taken = sorted(
                d for s in doc.get("snapshots") or [] if s.get("taken_at")
                for d in [_to_date(s.get("taken_at"))] if d is not None
            )
            if taken:
                start, end = taken[0], taken[-1]
                source = "model_manifest"
    if start is None or end is None:
        raise SystemExit(
            "need --start and --end (or results/v1.5/model_manifest.json with "
            "start/end snapshot timestamps)"
        )
    return start, end, source


def _in_window(value: date | None, start: date, end: date) -> bool:
    if value is None:
        return True  # undated rows are kept (tolerant)
    return not ((start and value < start) or (end and value > end))


# ── Tracked side ──────────────────────────────────────────────────────────────

def _scan_rq4(root: Path) -> dict:
    """Any token usage recorded inside rq4/*.json (none today; forward-compatible)."""
    found: list[str] = []
    models: dict[str, dict] = defaultdict(lambda: {"input": 0, "output": 0})
    total = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    def walk(node) -> None:
        if isinstance(node, dict):
            if "input_tokens" in node or "output_tokens" in node:
                i = _to_int(node.get("input_tokens"))
                o = _to_int(node.get("output_tokens"))
                model = node.get("model")
                if model:
                    models[model]["input"] += i
                    models[model]["output"] += o
                total["input_tokens"] += i
                total["output_tokens"] += o
                total["cost_usd"] += _to_float(node.get("cost_usd"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for path in sorted((root / "rq4").glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        found.append(path.name)
        walk(doc)
    return {"files": found, "models": dict(models), **total}


def _load_tracked(root: Path, start: date, end: date) -> dict:
    models: dict[str, dict] = defaultdict(lambda: {"input": 0, "output": 0})
    total = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    n_sessions = 0
    for path in sorted((root / "runs").glob("*/sessions.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            session = json.loads(line)
            if not _in_window(_to_date(session.get("timestamp")), start, end):
                continue
            n_sessions += 1
            for model, usage in (session.get("per_model_tokens") or {}).items():
                models[model]["input"] += _to_int(usage.get("input"))
                models[model]["output"] += _to_int(usage.get("output"))
            total["input_tokens"] += _to_int(session.get("input_tokens"))
            total["output_tokens"] += _to_int(session.get("output_tokens"))
            total["cost_usd"] += _to_float(session.get("estimated_cost_usd"))

    rq4 = _scan_rq4(root)
    for model, usage in rq4["models"].items():
        models[model]["input"] += usage["input"]
        models[model]["output"] += usage["output"]
    total["input_tokens"] += rq4["input_tokens"]
    total["output_tokens"] += rq4["output_tokens"]
    total["cost_usd"] += rq4["cost_usd"]
    return {"models": dict(models), "totals": total, "n_sessions": n_sessions, "rq4": rq4}


# ── Billed side ───────────────────────────────────────────────────────────────

def _load_billed(export_paths: list[Path], start: date, end: date) -> dict:
    models: dict[str, dict] = defaultdict(lambda: {"input": 0, "output": 0, "cost": 0.0})
    total = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    for path in export_paths:
        for row in _parse_export(path):
            if not _in_window(row["date"], start, end):
                continue
            model = row["model"] or "(unknown)"
            models[model]["input"] += row["input"]
            models[model]["output"] += row["output"]
            models[model]["cost"] += row["cost"]
            total["input_tokens"] += row["input"]
            total["output_tokens"] += row["output"]
            total["cost_usd"] += row["cost"]
    return {"models": dict(models), "totals": total}


# ── Deltas and pass ───────────────────────────────────────────────────────────

def _reconcile(billed: dict, tracked: dict) -> dict:
    b = billed["totals"]
    t = tracked["totals"]
    cost_delta = b["cost_usd"] - t["cost_usd"]
    if b["cost_usd"]:
        cost_delta_pct = abs(cost_delta) / b["cost_usd"] * 100
    else:
        cost_delta_pct = 0.0 if t["cost_usd"] == 0 else 100.0

    model_names = sorted(set(billed["models"]) | set(tracked["models"]))
    models = {}
    for model in model_names:
        bm = billed["models"].get(model, {"input": 0, "output": 0, "cost": 0.0})
        tm = tracked["models"].get(model, {"input": 0, "output": 0})
        models[model] = {
            "billed": {"input_tokens": bm["input"], "output_tokens": bm["output"],
                       "cost_usd": round(bm["cost"], 6)},
            "tracked": {"input_tokens": tm["input"], "output_tokens": tm["output"]},
            "input_delta": bm["input"] - tm["input"],
            "output_delta": bm["output"] - tm["output"],
        }

    return {
        "input_tokens": b["input_tokens"] - t["input_tokens"],
        "output_tokens": b["output_tokens"] - t["output_tokens"],
        "cost_usd": round(cost_delta, 6),
        "cost_delta_pct": round(cost_delta_pct, 4),
        "pass": cost_delta_pct <= THRESHOLD_PCT,
        "models": models,
    }


# ── Output ────────────────────────────────────────────────────────────────────

def _md(doc: dict) -> str:
    d = doc["deltas"]
    lines = [
        "# Billing reconciliation", "",
        f"- window: {doc['window']['start']} → {doc['window']['end']} "
        f"({doc['window']['source']})",
        f"- threshold: |Δ| ≤ {doc['threshold_pct']}% of billed cost",
        f"- **pass: {doc['pass']}**", "",
        "## Totals", "",
        "| side | input tokens | output tokens | cost (USD) |",
        "|---|---|---|---|",
        f"| billed | {doc['billed']['totals']['input_tokens']} | "
        f"{doc['billed']['totals']['output_tokens']} | "
        f"{doc['billed']['totals']['cost_usd']:.6f} |",
        f"| tracked | {doc['tracked']['totals']['input_tokens']} | "
        f"{doc['tracked']['totals']['output_tokens']} | "
        f"{doc['tracked']['totals']['cost_usd']:.6f} |",
        f"| Δ | {d['input_tokens']} | {d['output_tokens']} | {d['cost_usd']:.6f} |",
        "", f"cost Δ = {d['cost_delta_pct']}%", "",
        "## Per model", "",
        "| model | billed in/out | tracked in/out | Δ in/out |",
        "|---|---|---|---|",
    ]
    for model, row in sorted(d["models"].items()):
        lines.append(
            f"| {model} | {row['billed']['input_tokens']}/{row['billed']['output_tokens']} | "
            f"{row['tracked']['input_tokens']}/{row['tracked']['output_tokens']} | "
            f"{row['input_delta']}/{row['output_delta']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exports", nargs="+", type=Path, required=True,
                        help="Anthropic Console usage/cost export CSV(s)")
    parser.add_argument("--start", help="window start (ISO date/datetime)")
    parser.add_argument("--end", help="window end (ISO date/datetime)")
    parser.add_argument("--root", type=Path, default=Path("results/v1.5"),
                        help="results root (default results/v1.5)")
    parser.add_argument("--out", default="results/v1.5/billing_reconciliation",
                        help="output prefix → <out>.json and <out>.md")
    args = parser.parse_args(argv)

    root = args.root
    start, end, source = _window(args, root)
    billed = _load_billed(args.exports, start, end)
    tracked = _load_tracked(root, start, end)
    deltas = _reconcile(billed, tracked)

    doc = {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "source": source},
        "threshold_pct": THRESHOLD_PCT,
        "billed": billed,
        "tracked": {k: v for k, v in tracked.items() if k != "rq4"},
        "tracked_sources": {
            "sessions": tracked["n_sessions"],
            "rq4_files": tracked["rq4"]["files"],
            "rq4_note": "rq4 reports carry no token usage today; their billed "
                        "calls are not in the tracked total until they record usage.",
        },
        "deltas": deltas,
        "pass": deltas["pass"],
    }
    out = Path(args.out)
    out.with_suffix(".json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    out.with_suffix(".md").write_text(_md(doc))
    print(f"wrote {out}.json and {out}.md (pass={deltas['pass']}, "
          f"cost Δ={deltas['cost_delta_pct']}%)")
    return 0 if deltas["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
