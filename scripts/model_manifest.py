#!/usr/bin/env python3
"""Model manifest and canary prompts for the v1.5 benchmark window (protocol §2).

``claude-sonnet-4-6`` exists only as an undated canonical ID, so a model-version
change during the window would otherwise be invisible. This script records, at the
start and end of the window:

- the ``/v1/models`` entry for every model ID the system is configured to use
  (including ``created_at``), and
- five fixed canary prompts per model at temperature 0, each asked
  ``CANARY_REPEATS`` times, with the SHA-256 of every output, which catches
  behavioural drift that ``created_at`` would not.

Temperature 0 is not bit-deterministic across API calls, so a canary is only
flagged when its start and end outputs share no hash at all. Canaries ask for
short, constrained answers to keep that residual variation rare, and any
variation inside one snapshot is reported separately as instability.

Usage (from the repo root, ANTHROPIC_API_KEY set):
    python3 scripts/model_manifest.py snapshot --label start
    ... benchmark runs ...
    python3 scripts/model_manifest.py snapshot --label end
    python3 scripts/model_manifest.py compare            # exit 1 if anything changed

Writes results/v1.5/model_manifest.json (snapshots are appended, never replaced).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from anthropic import Anthropic

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_PATH = ROOT / "results" / "v1.5" / "model_manifest.json"
CANARY_MAX_TOKENS = 64
CANARY_REPEATS = 3

# Fixed for the whole window. Changing these between snapshots invalidates the
# comparison, so they are stored in every snapshot and checked by `compare`.
CANARIES: dict[str, str] = {
    "primes": "List the first ten prime numbers, separated by commas, and nothing else.",
    "json": 'Return exactly this JSON and nothing else: {"city": "Athens", "nights": 3}',
    "arith": "What is 17 multiplied by 23? Answer with the number only.",
    "capital": "What is the capital of Australia? Answer with one word.",
    "month": "Which month comes after March? Answer with the month name only.",
}


def configured_models() -> list[str]:
    """Every distinct model ID in config.api_config's LLM settings."""
    from config import get_api_config

    llm = get_api_config().llm
    ids = {
        value for name, value in vars(llm).items()
        if name.endswith("_model") or name == "default_model"
    }
    return sorted(v for v in ids if isinstance(v, str) and v.startswith("claude-"))


def _model_entry(client, model_id: str) -> dict:
    try:
        info = client.models.retrieve(model_id)
        data = info.model_dump() if hasattr(info, "model_dump") else dict(info)
        return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in data.items()}
    except Exception as exc:  # recorded, not fatal: the canaries still run
        return {"error": f"{type(exc).__name__}: {exc}"}


def _canary(client, model_id: str, prompt: str) -> tuple[dict, dict]:
    response = client.messages.create(
        model=model_id,
        max_tokens=CANARY_MAX_TOKENS,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(getattr(b, "text", "") for b in response.content)
    from metrics.token_tracker import compute_cost

    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return (
        {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "output": text},
        {
            "component": "canary",
            "model": model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": compute_cost(input_tokens, output_tokens, model_id),
        },
    )


def _canary_set(client, model_id: str, prompt: str) -> tuple[dict, list[dict]]:
    results, calls = [], []
    for _ in range(CANARY_REPEATS):
        result, call = _canary(client, model_id, prompt)
        results.append(result)
        calls.append(call)
    return (
        {
            "sha256": sorted({r["sha256"] for r in results}),
            "outputs": sorted({r["output"] for r in results}),
        },
        calls,
    )


def take_snapshot(client, label: str, models: list[str]) -> dict:
    calls: list[dict] = []
    models_out = {}
    for model_id in models:
        entry = _model_entry(client, model_id)
        canaries = {}
        for name, prompt in CANARIES.items():
            canaries[name], model_calls = _canary_set(client, model_id, prompt)
            calls.extend(model_calls)
        models_out[model_id] = {"entry": entry, "canaries": canaries}
    return {
        "label": label,
        "taken_at": datetime.now(UTC).isoformat(),
        "canary_prompts": CANARIES,
        "canary_max_tokens": CANARY_MAX_TOKENS,
        "canary_repeats": CANARY_REPEATS,
        "usage": {
            "component": "canary",
            "input_tokens": sum(c["input_tokens"] for c in calls),
            "output_tokens": sum(c["output_tokens"] for c in calls),
            "cost_usd": round(sum(c["cost_usd"] for c in calls), 6),
            "calls": calls,
        },
        "models": models_out,
    }


def _hashes(canary: dict | None) -> set[str]:
    value = (canary or {}).get("sha256")
    if isinstance(value, str):  # single-output snapshots from before repeats
        return {value}
    return set(value or [])


def load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {"snapshots": []}


def compare_snapshots(first: dict, last: dict) -> list[str]:
    """Human-readable differences between two snapshots; empty when nothing changed."""
    diffs: list[str] = []
    if first.get("canary_prompts") != last.get("canary_prompts"):
        diffs.append("canary prompts differ between snapshots; comparison invalid")
    for model_id in sorted(set(first["models"]) | set(last["models"])):
        a, b = first["models"].get(model_id), last["models"].get(model_id)
        if a is None or b is None:
            diffs.append(f"{model_id}: present in only one snapshot")
            continue
        for field in ("id", "created_at", "display_name"):
            if a["entry"].get(field) != b["entry"].get(field):
                diffs.append(f"{model_id}: {field} {a['entry'].get(field)!r} -> {b['entry'].get(field)!r}")
        for name in sorted(set(a["canaries"]) | set(b["canaries"])):
            ha, hb = _hashes(a["canaries"].get(name)), _hashes(b["canaries"].get(name))
            if not ha & hb:
                diffs.append(f"{model_id}: canary {name!r} output changed")
    return diffs


def instability(snapshot: dict) -> list[str]:
    """Canaries whose repeated outputs differed inside one snapshot."""
    return [
        f"{model_id}: canary {name!r} gave {len(_hashes(c))} distinct outputs "
        f"in {snapshot.get('canary_repeats', 1)} calls"
        for model_id, m in snapshot["models"].items()
        for name, c in m["canaries"].items()
        if len(_hashes(c)) > 1
    ]


def make_client():
    """Anthropic client with the §8 retry policy (VOYAGER_LLM_MAX_RETRIES)."""
    from config.settings import get_settings

    return Anthropic(max_retries=get_settings().llm_max_retries)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    snap = sub.add_parser("snapshot", help="record model entries and canary outputs")
    snap.add_argument("--label", required=True, help="e.g. start or end")
    cmp_ = sub.add_parser("compare", help="compare the first and last snapshots")
    for p in (snap, cmp_):
        p.add_argument("--path", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()

    manifest = load(args.path)
    if args.cmd == "snapshot":
        snapshot = take_snapshot(make_client(), args.label, configured_models())
        manifest["snapshots"].append(snapshot)
        args.path.parent.mkdir(parents=True, exist_ok=True)
        args.path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"snapshot '{args.label}' -> {args.path} ({len(snapshot['models'])} models)")
        return 0

    snaps = manifest["snapshots"]
    if len(snaps) < 2:
        print("need at least two snapshots to compare")
        return 2
    diffs = compare_snapshots(snaps[0], snaps[-1])
    noisy = instability(snaps[0]) + instability(snaps[-1])
    if noisy:
        print("temperature-0 variation within a snapshot (not drift; report under RQ3):")
        for n in noisy:
            print(f"  - {n}")
    if diffs:
        print(f"CHANGED between '{snaps[0]['label']}' and '{snaps[-1]['label']}' "
              "(declare as an RQ3 variance source):")
        for d in diffs:
            print(f"  - {d}")
        return 1
    print(f"no change between '{snaps[0]['label']}' and '{snaps[-1]['label']}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
