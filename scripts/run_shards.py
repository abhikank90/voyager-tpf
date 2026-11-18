"""Shard the official v1.5 condition runs (§6, §8, §10).

Subcommands:

    plan    Assign whole queries to K shards (contiguous, balanced) and print,
            per shard, the offset, limit, query ids and the exact env + command.
            Writes DIR/plan.json.

    launch  Start the K shards as subprocesses with VOYAGER_QUERY_OFFSET/--limit,
            per-shard VOYAGER_METRICS_PATH=DIR/shard_k/sessions.jsonl and
            VOYAGER_RESULTS_DIR=DIR/shard_k, a shared VOYAGER_PLANS_DIR
            (default results/v1.5/plans, so all sets land in one audit dir), a
            shared VOYAGER_FAILURES_PATH (default results/v1.5/failures.csv, the
            single file paper_numbers.py reads), logging to DIR/shard_k/run.log.
            Writes DIR/launch.json (git HEAD, env minus secrets, commands, start
            time, failures_path, plans_dir). Refuses shards > 1 in capture mode
            or with VOYAGER_REPLAY_RECORD_MISSING=1.

    merge   Concatenate the shard session files (plus DIR/reruns.jsonl, if the
            failure re-runs were written there) into DIR/sessions.jsonl and
            validate it: no duplicate (query_id, condition, run_index) keys
            unless the duplicate is a re-run that supersedes an earlier record
            (failures.csv row with rerun_ok=true — the latest is kept), every
            query has every condition × run, and anything missing is reported
            (exit non-zero). The failure log is read from launch.json's
            recorded failures_path, else VOYAGER_FAILURES_PATH / the default.

    status  Per shard: sessions done / expected, failures, 429/overloaded lines
            in run.log, mean session time and the ETA.

After launch, re-run failures once (§8) with::

    VOYAGER_METRICS_PATH=DIR/reruns.jsonl \
      .venv/bin/python scripts/benchmark_queries.py --rerun-failures \
      --manifest results/v1.5/query_manifest.csv --inventory <mode>

then merge again.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.failure_log import failures_path, load_failures

CONDITIONS = ("baseline", "targeted", "full_rerun", "broadcast", "two_way")
DEFAULT_MANIFEST = "results/v1.5/query_manifest.csv"
DEFAULT_PLANS_DIR = Path("results") / "v1.5" / "plans"
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


# ── Small helpers ─────────────────────────────────────────────────────────────

def _manifest_queries(manifest_path: str, set_name: str) -> list[dict]:
    with open(manifest_path, newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["set"] == set_name]
    if not rows:
        raise SystemExit(f"no '{set_name}' rows in {manifest_path}")
    return rows


def _balanced_sizes(n: int, k: int) -> list[int]:
    base, rem = divmod(n, k)
    return [base + (1 if i < rem else 0) for i in range(k)]


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _sanitized_env(env: dict) -> dict:
    return {
        k: v for k, v in sorted(env.items())
        if not any(m in k.upper() for m in _SECRET_MARKERS)
    }


def _load_plan(out: Path) -> dict:
    path = out / "plan.json"
    if not path.exists():
        raise SystemExit(f"{path} missing — run `plan` first")
    return json.loads(path.read_text())


def _plans_dir() -> str:
    """Shared plans dir across sets; ``VOYAGER_PLANS_DIR`` overrides the default."""
    return os.environ.get("VOYAGER_PLANS_DIR", str(DEFAULT_PLANS_DIR))


def _failures_path_for(out: Path) -> Path:
    """The failure log merge/status read: launch.json's recorded path, else env/default."""
    launch = out / "launch.json"
    if launch.exists():
        try:
            recorded = json.loads(launch.read_text()).get("failures_path")
            if recorded:
                return Path(recorded)
        except json.JSONDecodeError:
            pass
    return failures_path()


def _shard_env(plan: dict, shard: dict, out: Path) -> dict:
    sid = shard["shard_id"]
    return {
        "VOYAGER_QUERY_OFFSET": str(shard["offset"]),
        "VOYAGER_METRICS_PATH": str(out / f"shard_{sid}" / "sessions.jsonl"),
        "VOYAGER_RESULTS_DIR": str(out / f"shard_{sid}"),
        "VOYAGER_PLANS_DIR": _plans_dir(),
        "VOYAGER_FAILURES_PATH": str(failures_path()),
    }


def _shard_command(plan: dict, shard: dict, inventory_mode: str | None = None) -> list[str]:
    cmd = [
        sys.executable, "scripts/benchmark_queries.py", "--mode", "conditions",
        "--manifest", plan["manifest"], "--set", plan["set"],
        "--repeats", str(plan["repeats"]), "--limit", str(shard["limit"]),
    ]
    if plan["conditions"] != list(CONDITIONS):
        cmd += ["--conditions", ",".join(plan["conditions"])]
    if inventory_mode:
        cmd += ["--inventory", inventory_mode]
    return cmd


def _inventory_mode(args) -> str:
    from config.settings import get_settings, reload_settings

    if getattr(args, "inventory", None):
        os.environ["VOYAGER_INVENTORY_MODE"] = args.inventory
        reload_settings()
    return get_settings().inventory_mode


def _shard_refusal(k: int, inventory_mode: str, replay_record_missing: bool) -> str | None:
    if k <= 1:
        return None
    if inventory_mode == "capture":
        return ("refusing --shards > 1 in capture mode: the live capture must be one "
                "sequential pass (protocol §10 step 12)")
    if replay_record_missing:
        return ("refusing --shards > 1 with VOYAGER_REPLAY_RECORD_MISSING=1: missing "
                "replay keys are recorded once, sequentially (protocol §10 step 13)")
    return None


def _session_key(s: dict) -> tuple:
    tags = s.get("session_tags") or {}
    qid = tags.get("query_id") or s.get("query_id")
    cond = tags.get("condition") or s.get("condition")
    run = tags.get("run_index") or s.get("run_index") or 1
    try:
        run = int(run)
    except (TypeError, ValueError):
        run = 1
    return (qid, cond, run)


# ── plan ──────────────────────────────────────────────────────────────────────

def cmd_plan(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    queries = [(r["query_id"], r["query_text"]) for r in _manifest_queries(args.manifest, args.set)]
    conditions = list(args.conditions) if args.conditions else list(CONDITIONS)
    unknown = set(conditions) - set(CONDITIONS)
    if unknown:
        raise SystemExit(f"unknown conditions: {sorted(unknown)}")
    if args.shards < 1:
        raise SystemExit("--shards must be >= 1")
    if args.shards > len(queries):
        raise SystemExit(f"more shards ({args.shards}) than queries ({len(queries)}): "
                         "whole-query assignment leaves empty shards")

    sizes = _balanced_sizes(len(queries), args.shards)
    shards = []
    idx = 0
    for i, size in enumerate(sizes, 1):
        chunk = queries[idx:idx + size]
        shards.append({
            "shard_id": i, "offset": idx, "limit": size,
            "query_ids": [q[0] for q in chunk],
        })
        idx += size

    plan = {
        "set": args.set, "shard_count": args.shards, "repeats": args.repeats,
        "conditions": conditions, "manifest": args.manifest,
        "queries": [{"query_id": qid, "query_text": text} for qid, text in queries],
        "shards": shards,
    }
    (out / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")

    inventory_mode = args.inventory
    print(f"\n{len(queries)} queries → {args.shards} shard(s) "
          f"(× {len(conditions)} conditions × {args.repeats} runs)\n")
    for shard in shards:
        env = _shard_env(plan, shard, out)
        cmd = _shard_command(plan, shard, inventory_mode)
        print(f"shard {shard['shard_id']}/{args.shards}: offset={shard['offset']} "
              f"limit={shard['limit']} queries={','.join(shard['query_ids'])}")
        for key, value in env.items():
            print(f"  {key}={value}")
        print(f"  command: {' '.join(cmd)}")
        print()
    print(f"plan written → {out / 'plan.json'}")
    return 0


# ── launch ────────────────────────────────────────────────────────────────────

def cmd_launch(args) -> int:
    from config.settings import get_settings

    out = Path(args.out)
    plan = _load_plan(out)
    inventory_mode = _inventory_mode(args)
    replay_record_missing = get_settings().replay_record_missing or (
        os.environ.get("VOYAGER_REPLAY_RECORD_MISSING", "").strip() in ("1", "true", "yes", "on")
    )
    refusal = _shard_refusal(plan["shard_count"], inventory_mode, replay_record_missing)
    if refusal:
        raise SystemExit(refusal)

    start_time = datetime.now(UTC).isoformat()
    launched = []
    procs = []
    for shard in sorted(plan["shards"], key=lambda s: s["shard_id"]):
        sid = shard["shard_id"]
        shard_dir = out / f"shard_{sid}"
        shard_dir.mkdir(parents=True, exist_ok=True)
        log_path = shard_dir / "run.log"
        env = os.environ.copy()
        env.update(_shard_env(plan, shard, out))
        cmd = _shard_command(plan, shard, inventory_mode)
        log_fh = open(log_path, "a")
        proc = subprocess.Popen(cmd, env=env, stdout=log_fh, stderr=subprocess.STDOUT)
        procs.append((shard, proc, log_fh))
        launched.append({
            "shard_id": sid,
            "command": cmd,
            "env": _sanitized_env(env),
            "log": str(log_path),
            "pid": proc.pid,
        })

    doc = {
        "git_head": _git_head(),
        "start_time": start_time,
        "inventory_mode": inventory_mode,
        "failures_path": str(failures_path()),
        "plans_dir": _plans_dir(),
        "plan": plan,
        "shards": launched,
    }
    (out / "launch.json").write_text(json.dumps(doc, indent=2) + "\n")

    print(f"launched {len(procs)} shard(s); logs → {out}/shard_k/run.log")
    print(f"launch record → {out / 'launch.json'}\n")

    failed = []
    for shard, proc, log_fh in procs:
        proc.wait()
        log_fh.close()
        print(f"  shard {shard['shard_id']}: exited {proc.returncode}")
        if proc.returncode != 0:
            failed.append(shard["shard_id"])
    if failed:
        print(f"\nWARNING: {len(failed)} shard(s) exited non-zero: {failed}")
        return 2
    print("\nall shards exited 0")
    return 0


# ── merge ─────────────────────────────────────────────────────────────────────

def cmd_merge(args) -> int:
    out = Path(args.out)
    plan = _load_plan(out)
    set_name = plan["set"]
    conditions = plan["conditions"]
    repeats = plan["repeats"]
    failures = load_failures(_failures_path_for(out))

    inputs = []
    for shard in sorted(plan["shards"], key=lambda s: s["shard_id"]):
        f = out / f"shard_{shard['shard_id']}" / "sessions.jsonl"
        if f.exists():
            inputs.append(f)
    reruns = out / "reruns.jsonl"
    if reruns.exists():
        inputs.append(reruns)

    sessions = []
    for f in inputs:
        for line in f.read_text().splitlines():
            if line.strip():
                sessions.append(json.loads(line))

    by_key: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
    for i, s in enumerate(sessions):
        by_key[_session_key(s)].append((i, s))

    re_run_keys = {
        (f.get("query_id"), f.get("condition"), _int_run(f.get("run_index")))
        for f in failures
        if f.get("set") == set_name and str(f.get("rerun_ok", "")).strip().lower() == "true"
    }

    dup_errors = []
    superseded = 0
    final_sessions = []
    for key in sorted(by_key):
        items = by_key[key]
        if len(items) == 1:
            final_sessions.append(items[0][1])
        elif key in re_run_keys:
            items.sort(key=lambda t: t[0])
            final_sessions.append(items[-1][1])
            superseded += len(items) - 1
        else:
            dup_errors.append(key)

    expected = {
        (q["query_id"], c, r)
        for q in plan["queries"] for c in conditions for r in range(1, repeats + 1)
    }
    present = set(by_key)

    fail_map: dict[tuple, list[dict]] = defaultdict(list)
    for f in failures:
        if f.get("set") == set_name:
            fail_map[(f.get("query_id"), f.get("condition"), _int_run(f.get("run_index")))].append(f)

    dropped = []
    missing_errors = []
    for key in sorted(expected - present):
        rows = fail_map.get(key, [])
        values = {str(f.get("rerun_ok", "")).strip().lower() for f in rows}
        if values & {"false", "0", "no"}:
            dropped.append(key)
        else:
            missing_errors.append(key)

    (out / "sessions.jsonl").write_text(
        "".join(json.dumps(s) + "\n" for s in final_sessions)
    )

    print(f"merged {len(sessions)} raw session(s) → {len(final_sessions)} unique "
          f"session(s) in {out / 'sessions.jsonl'}")
    if superseded:
        print(f"superseded re-runs: kept the latest session for {superseded} key(s) "
              f"listed in failures.csv with rerun_ok=true (the re-run session supersedes "
              f"any earlier record of the same query/condition/run_index)")
    if dropped:
        print(f"excluded (§8): {len(dropped)} key(s) dropped because their re-run also "
              f"failed (failures.csv rerun_ok=false)")
    if dup_errors:
        print(f"\nERROR: {len(dup_errors)} duplicate key(s) not explained by a "
              f"rerun_ok=true re-run:")
        for key in dup_errors:
            print(f"  {key}")
    if missing_errors:
        print(f"\nERROR: {len(missing_errors)} missing cell(s) (query × condition × run):")
        for key in missing_errors:
            print(f"  {key}")

    if dup_errors or missing_errors:
        return 2
    print("\nmerge OK")
    return 0


def _int_run(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ── status ────────────────────────────────────────────────────────────────────

def _shard_sessions(out: Path, sid: int) -> tuple[int, float | None]:
    f = out / f"shard_{sid}" / "sessions.jsonl"
    if not f.exists():
        return 0, None
    durations = []
    n = 0
    for line in f.read_text().splitlines():
        if not line.strip():
            continue
        n += 1
        try:
            d = json.loads(line).get("duration_s")
            if d is not None:
                durations.append(float(d))
        except json.JSONDecodeError:
            continue
    mean = sum(durations) / len(durations) if durations else None
    return n, mean


def _rate_limited_lines(log: Path) -> int:
    if not log.exists():
        return 0
    text = log.read_text(errors="replace").lower()
    return len(re.findall(r"\b429\b", text)) + text.count("overloaded")


def cmd_status(args) -> int:
    out = Path(args.out)
    plan = _load_plan(out)
    conditions = plan["conditions"]
    repeats = plan["repeats"]
    failures = load_failures(_failures_path_for(out))

    print(f"set={plan['set']} conditions={','.join(conditions)} repeats={repeats}\n")
    header = ("shard", "expected", "done", "failures", "429/overloaded", "mean_session_s", "eta")
    print(f"{header[0]:>5}  {header[1]:>8}  {header[2]:>4}  {header[3]:>8}  "
          f"{header[4]:>14}  {header[5]:>14}  {header[6]}")
    for shard in sorted(plan["shards"], key=lambda s: s["shard_id"]):
        sid = shard["shard_id"]
        expected = len(shard["query_ids"]) * len(conditions) * repeats
        done, mean = _shard_sessions(out, sid)
        log = out / f"shard_{sid}" / "run.log"
        rate_limited = _rate_limited_lines(log)
        query_ids = set(shard["query_ids"])
        n_failures = sum(1 for f in failures if f.get("query_id") in query_ids)
        remaining = max(0, expected - done)
        if mean is not None and remaining > 0:
            eta = str(timedelta(seconds=int(remaining * mean)))
        else:
            eta = "-"
        mean_s = f"{mean:.1f}" if mean is not None else "-"
        print(f"{sid:>5}  {expected:>8}  {done:>4}  {n_failures:>8}  "
              f"{rate_limited:>14}  {mean_s:>14}  {eta}")
    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="assign whole queries to K balanced shards")
    p_plan.add_argument("--set", required=True, choices=["synthetic", "real", "control"])
    p_plan.add_argument("--shards", type=int, required=True)
    p_plan.add_argument("--repeats", type=int, required=True)
    p_plan.add_argument("--out", required=True)
    p_plan.add_argument("--conditions", nargs="+", default=None,
                        help=f"space-separated conditions (default: {' '.join(CONDITIONS)})")
    p_plan.add_argument("--manifest", default=DEFAULT_MANIFEST)
    p_plan.add_argument("--inventory", choices=["mock", "capture", "replay"], default=None)
    p_plan.set_defaults(func=cmd_plan)

    p_launch = sub.add_parser("launch", help="start the shards from DIR/plan.json")
    p_launch.add_argument("--out", required=True)
    p_launch.add_argument("--inventory", choices=["mock", "capture", "replay"], default=None)
    p_launch.set_defaults(func=cmd_launch)

    p_merge = sub.add_parser("merge", help="merge + validate shard session logs")
    p_merge.add_argument("--out", required=True)
    p_merge.set_defaults(func=cmd_merge)

    p_status = sub.add_parser("status", help="per-shard progress and ETA")
    p_status.add_argument("--out", required=True)
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
