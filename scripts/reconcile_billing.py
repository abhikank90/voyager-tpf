"""Billing reconciliation for the v1.5 window (protocol §10 step 16, §7).

Compares Anthropic Console usage/cost exports against the tracked usage in the
window. The tracked side is every source of LLM spend, reported separately:

  * sessions        — raw per-shard ``runs/**/shard_*/sessions.jsonl`` (never a
                      merged or combined ``sessions.jsonl``)
  * rerun sessions  — ``runs/**/reruns.jsonl`` (recursive)
  * failed sessions — the partial-usage columns of ``failures.csv``
  * RQ4             — the ``usage`` recorded in ``results/v1.5/rq4/*.json``
  * canaries        — the per-snapshot ``usage`` in ``model_manifest.json``

The window is ``--start``/``--end`` or, by default, the first and last snapshot
timestamps in ``results/v1.5/model_manifest.json``.

The Console export format is not fixed, so the parser detects date / model /
input / output / cost columns by header-name matching and fails with a clear
message listing the headers it saw when a required column is missing.
``--key-filter``/``--workspace-filter`` isolate a dedicated API key's or
workspace's rows when the export carries such a column.

Passes when ``|tracked − billed| ≤ 1%`` of billed cost (the v1.4 standard).

Usage:
    python scripts/reconcile_billing.py --exports usage1.csv usage2.csv \
        [--start 2026-11-18 --end 2026-11-20] [--root results/v1.5] \
        [--key-filter sk-ant-...]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
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


def _parse_export(
    path: Path,
    key_filter: str | None = None,
    workspace_filter: str | None = None,
) -> list[dict]:
    """Parse one Console export into [{date, model, input, output, cost}].

    Optionally keeps only rows whose API-key/workspace column matches the given
    filter (case-insensitive substring). Fails clearly when a required column
    is missing, or when a filter names a column the export does not have.
    """
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
    key_col = _pick(headers, "key")
    workspace_col = _pick(headers, "workspace")

    required = {"date": date_col, "model": model_col, "input tokens": input_col,
                "output tokens": output_col, "cost": cost_col}
    missing = [name for name, col in required.items() if col is None]
    if missing:
        raise SystemExit(
            f"cannot parse export {path}: missing column(s) for {missing}. "
            f"Headers seen: {sorted(reader.fieldnames or [])}"
        )
    if key_filter is not None and key_col is None:
        raise SystemExit(
            f"cannot apply --key-filter to export {path}: no API-key column. "
            f"Headers seen: {sorted(reader.fieldnames or [])}"
        )
    if workspace_filter is not None and workspace_col is None:
        raise SystemExit(
            f"cannot apply --workspace-filter to export {path}: no workspace column. "
            f"Headers seen: {sorted(reader.fieldnames or [])}"
        )

    rows = []
    for row in reader:
        if key_filter is not None and key_filter.lower() not in (row.get(key_col) or "").lower():
            continue
        if workspace_filter is not None and workspace_filter.lower() not in (row.get(workspace_col) or "").lower():
            continue
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


# ── Tracked side (reported per source) ────────────────────────────────────────

def _empty_source(**extra) -> dict:
    return {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, **extra}


def _session_source(root: Path, pattern: str, start: date, end: date) -> dict:
    """Sum one class of raw session file and record which files were read.

    ``pattern`` is a ``Path.glob`` pattern relative to ``root``. Merged
    ``sessions.jsonl`` files and combined run sets are *not* matched: sessions
    come only from per-shard files, and reruns only from ``reruns.jsonl``.
    """
    models: dict[str, dict] = defaultdict(lambda: {"input": 0, "output": 0})
    total = _empty_source(n=0, files=[])
    for path in sorted(root.glob(pattern)):
        total["files"].append(str(path.relative_to(root)))
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            session = json.loads(line)
            if not _in_window(_to_date(session.get("timestamp")), start, end):
                continue
            total["n"] += 1
            for model, usage in (session.get("per_model_tokens") or {}).items():
                models[model]["input"] += _to_int(usage.get("input"))
                models[model]["output"] += _to_int(usage.get("output"))
            total["input_tokens"] += _to_int(session.get("input_tokens"))
            total["output_tokens"] += _to_int(session.get("output_tokens"))
            total["cost_usd"] += _to_float(session.get("estimated_cost_usd"))
    return {"models": dict(models), **total}


def _failed_source(root: Path) -> dict:
    """Partial usage recorded on the failure log's trailing columns."""
    path = Path(os.environ.get("VOYAGER_FAILURES_PATH", root / "failures.csv"))
    total = _empty_source(n=0)
    if not path.exists():
        return total
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            total["n"] += 1
            total["input_tokens"] += _to_int(row.get("input_tokens"))
            total["output_tokens"] += _to_int(row.get("output_tokens"))
            total["cost_usd"] += _to_float(row.get("cost_usd"))
    return total


def _rq4_source(root: Path) -> dict:
    """Usage recorded in rq4/*.json (the ``usage`` field of each report)."""
    total = _empty_source(files=[])
    for path in sorted((root / "rq4").glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        total["files"].append(path.name)
        usage = doc.get("usage")
        if isinstance(usage, dict):
            total["input_tokens"] += _to_int(usage.get("input_tokens"))
            total["output_tokens"] += _to_int(usage.get("output_tokens"))
            total["cost_usd"] += _to_float(usage.get("cost_usd"))
    return total


def _canary_source(root: Path) -> dict:
    """Per-snapshot canary usage in model_manifest.json."""
    manifest = root / "model_manifest.json"
    total = _empty_source(snapshots=0)
    if not manifest.exists():
        return total
    doc = json.loads(manifest.read_text())
    for snapshot in doc.get("snapshots") or []:
        total["snapshots"] += 1
        usage = snapshot.get("usage")
        if isinstance(usage, dict):
            total["input_tokens"] += _to_int(usage.get("input_tokens"))
            total["output_tokens"] += _to_int(usage.get("output_tokens"))
            total["cost_usd"] += _to_float(usage.get("cost_usd"))
    return total


def _load_tracked(root: Path, start: date, end: date) -> dict:
    sources = {
        "sessions": _session_source(root, "runs/**/shard_*/sessions.jsonl", start, end),
        "rerun_sessions": _session_source(root, "runs/**/reruns.jsonl", start, end),
        "failed_sessions": _failed_source(root),
        "rq4": _rq4_source(root),
        "canary": _canary_source(root),
    }
    models: dict[str, dict] = defaultdict(lambda: {"input": 0, "output": 0})
    total = _empty_source()
    for name, source in sources.items():
        total["input_tokens"] += source["input_tokens"]
        total["output_tokens"] += source["output_tokens"]
        total["cost_usd"] += source["cost_usd"]
        for model, usage in source.get("models", {}).items():
            models[model]["input"] += usage["input"]
            models[model]["output"] += usage["output"]
    return {"models": dict(models), "totals": total, "sources": sources}


# ── Billed side ───────────────────────────────────────────────────────────────

def _load_billed(
    export_paths: list[Path],
    start: date,
    end: date,
    key_filter: str | None = None,
    workspace_filter: str | None = None,
) -> dict:
    models: dict[str, dict] = defaultdict(lambda: {"input": 0, "output": 0, "cost": 0.0})
    total = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    for path in export_paths:
        for row in _parse_export(path, key_filter, workspace_filter):
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
        "## Tracked sources", "",
        "| source | input tokens | output tokens | cost (USD) |",
        "|---|---|---|---|",
    ]
    for name, source in sorted(doc["tracked_sources"].items()):
        lines.append(f"| {name} | {source['input_tokens']} | {source['output_tokens']} | "
                     f"{source['cost_usd']:.6f} |")
    lines += ["", "## Per model", "",
              "| model | billed in/out | tracked in/out | Δ in/out |",
              "|---|---|---|---|"]
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
    parser.add_argument("--key-filter", default=None,
                        help="keep only billed rows whose API-key column matches (substring)")
    parser.add_argument("--workspace-filter", default=None,
                        help="keep only billed rows whose workspace column matches (substring)")
    args = parser.parse_args(argv)

    root = args.root
    start, end, source = _window(args, root)
    billed = _load_billed(args.exports, start, end, args.key_filter, args.workspace_filter)
    tracked = _load_tracked(root, start, end)
    deltas = _reconcile(billed, tracked)

    doc = {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "source": source},
        "threshold_pct": THRESHOLD_PCT,
        "filters": {"key": args.key_filter, "workspace": args.workspace_filter},
        "billed": billed,
        "tracked": {"models": tracked["models"], "totals": tracked["totals"]},
        "tracked_sources": {
            name: {k: v for k, v in source.items() if k != "models"}
            for name, source in tracked["sources"].items()
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
