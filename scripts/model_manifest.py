#!/usr/bin/env python3
"""Model manifest and canary prompts for the v1.5 benchmark window (protocol §2).

``claude-sonnet-4-6`` exists only as an undated canonical ID, so a model-version
change during the window would otherwise be invisible. This script records, at the
start and end of the window:

- the ``/v1/models`` entry for every model ID the system is configured to use
  (including ``created_at``), and
- five fixed canary prompts per model at temperature 0, with the SHA-256 of each
  output, which catches behavioural drift that ``created_at`` would not.

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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_PATH = ROOT / "results" / "v1.5" / "model_manifest.json"
CANARY_MAX_TOKENS = 64

# Fixed for the whole window. Changing these between snapshots invalidates the
# comparison, so they are stored in every snapshot and checked by `compare`.
CANARIES: dict[str, str] = {
    "primes": "List the first ten prime numbers, separated by commas, and nothing else.",
    "json": 'Return exactly this JSON and nothing else: {"city": "Athens", "nights": 3}',
    "arith": "What is 17 multiplied by 23? Answer with the number only.",
    "capital": "What is the capital of Australia? Answer with one word.",
    "travel": "In one sentence, name one indoor activity for a hot afternoon in Athens.",
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


def _canary(client, model_id: str, prompt: str) -> dict:
    response = client.messages.create(
        model=model_id,
        max_tokens=CANARY_MAX_TOKENS,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(getattr(b, "text", "") for b in response.content)
    return {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "output": text}


def take_snapshot(client, label: str, models: list[str]) -> dict:
    return {
        "label": label,
        "taken_at": datetime.now(UTC).isoformat(),
        "canary_prompts": CANARIES,
        "canary_max_tokens": CANARY_MAX_TOKENS,
        "models": {
            model_id: {
                "entry": _model_entry(client, model_id),
                "canaries": {name: _canary(client, model_id, p) for name, p in CANARIES.items()},
            }
            for model_id in models
        },
    }


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
            ha = (a["canaries"].get(name) or {}).get("sha256")
            hb = (b["canaries"].get(name) or {}).get("sha256")
            if ha != hb:
                diffs.append(f"{model_id}: canary {name!r} output changed")
    return diffs


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
        from anthropic import Anthropic

        snapshot = take_snapshot(Anthropic(), args.label, configured_models())
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
