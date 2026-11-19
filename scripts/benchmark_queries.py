"""
Benchmark script for Voyager metrics collection.

Runs diverse queries through the collaborative graph and writes per-session
records to metrics/sessions.jsonl.  After completion, prints a summary with
the article-ready metrics.

Usage:
    python scripts/benchmark_queries.py                       # full mode, all 25 queries
    python scripts/benchmark_queries.py --mode baseline       # skip refinement rounds
    python scripts/benchmark_queries.py --mode compare        # run each query in both modes
    python scripts/benchmark_queries.py --mode compare --inventory mock
    python scripts/benchmark_queries.py --mode compare --inventory replay
    python scripts/benchmark_queries.py --inventory capture --query-count 15
    python scripts/benchmark_queries.py --limit 3             # smoke test (first 3 queries)
    python scripts/benchmark_queries.py --summary             # print summary of past runs
    python scripts/benchmark_queries.py --dry-run             # list queries without running

Requires ANTHROPIC_API_KEY in environment or .env file.

Results are written against the current git commit; published numbers reference tag v1.0-infoq.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Make project root importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import get_settings, reload_settings  # noqa: E402

QUERIES = [
    # Beach / relaxation
    "Greece under $2000, beaches and local food, summer 2026, flying from NYC",
    "Maldives honeymoon for 2, $5000 budget, snorkeling and luxury resorts, March 2026",
    "Bali for solo traveler, $1500 budget, beaches and yoga, July 2026 from LA",
    "Thailand beaches, $1800 for couple, mix of island hopping and street food, October 2026",
    "Portugal Algarve, $2200, beach holiday with wine tasting, September 2026 from London",

    # City / culture
    "Tokyo city break, $3000 for couple, food and tech culture, April 2026 from SFO",
    "Rome art and food trip, $2500, 7 days, June 2026 from Chicago",
    "Istanbul cultural exploration, $1800 solo, historical sites and cuisine, May 2026",
    "Barcelona architecture and nightlife, $2000, 5 days, August 2026 from NYC",
    "Seoul K-culture and food, $2200 couple, winter 2026 from LAX",

    # Adventure / active
    "New Zealand South Island, $4000, hiking and bungee jumping, November 2026",
    "Costa Rica eco-adventure, $2500, zip-lining and wildlife, February 2026 from Miami",
    "Nepal trekking Annapurna, $2000 solo, October 2026, 14 days",
    "Iceland Northern Lights and hot springs, $3500 couple, January 2026",
    "Patagonia hiking Torres del Paine, $4500, 12 days, December 2026",

    # Budget constrained
    "Vietnam on $800, backpacking 10 days, street food and culture, April 2026",
    "Eastern Europe city hop Prague + Budapest, $1200, 8 days, September 2026",
    "Morocco Marrakech and Sahara, $1000 solo, 7 days, March 2026",
    "Mexico City food and culture, $900 solo, 5 days, any month 2026",
    "Cambodia Angkor Wat + beaches, $1100, 10 days, November 2026",

    # Family / specific needs
    "Disney World Orlando family of 4, $6000, 7 days, June 2026",
    "Safari Kenya family of 3, $8000, 10 days, July 2026",
    "Swiss Alps ski holiday family, $7000, 7 days, February 2026",
    "Hawaii family beach vacation, $5000, 10 days, August 2026 from Seattle",
    "Japan family with kids, $4500, cherry blossom season 2026, 10 days from NYC",
]


async def run_query(
    query: str,
    idx: int,
    total: int,
    enable_refinement: bool,
) -> bool:
    from graph.travel_graph import run_collaborative_travel_query
    mode_label = "full" if enable_refinement else "baseline"
    print(f"  [{idx+1}/{total}] [{mode_label}] {query[:65]}...")
    try:
        await run_collaborative_travel_query(query, enable_refinement=enable_refinement)
        print("           ✓")
        return True
    except Exception as e:
        print(f"           ✗ {e}")
        return False


async def run_batch(queries: list[str], enable_refinement: bool) -> None:
    ok = 0
    for i, q in enumerate(queries):
        success = await run_query(q, i, len(queries), enable_refinement)
        if success:
            ok += 1
    mode = "full" if enable_refinement else "baseline"
    print(f"\n  {mode} mode: {ok}/{len(queries)} queries succeeded")


async def run_compare(queries: list[str]) -> None:
    """Run each query in both modes back-to-back for paired comparison."""
    print(f"\nRunning {len(queries)} queries × 2 modes (full then baseline)\n")
    ok = 0
    for i, q in enumerate(queries):
        print(f"  [{i+1}/{len(queries)}] {q[:65]}...")
        for enable_ref, label in ((True, "full"), (False, "baseline")):
            from graph.travel_graph import run_collaborative_travel_query
            try:
                await run_collaborative_travel_query(q, enable_refinement=enable_ref)
                print(f"           [{label}] ✓")
                ok += 1
            except Exception as e:
                print(f"           [{label}] ✗ {e}")
    print(f"\n  compare mode: {ok}/{len(queries)*2} total runs succeeded")


CONDITIONS = ("baseline", "targeted", "full_rerun", "broadcast", "two_way")


def load_manifest_queries(path: str, query_set: str) -> list[tuple[str, str]]:
    """(query_id, query_text) rows of one set from results/v1.5/query_manifest.csv."""
    import csv

    with open(path, newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["set"] == query_set]
    if not rows:
        raise SystemExit(f"no '{query_set}' rows in {path}")
    return [(r["query_id"], r["query_text"]) for r in rows]


def _manifest_rows(path: str) -> list[dict]:
    import csv

    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


async def run_conditions(
    queries: list[tuple[str, str]],
    conditions: list[str],
    repeats: int,
    inventory_profile: str = "default",
    query_set: str | None = None,
    run_index_start: int = 1,
) -> None:
    """Run every condition for each query back to back, repeated ``repeats`` times.

    Interleaving by query keeps time-of-day and provider-load effects out of the
    paired comparison. The condition order rotates by query index so no
    condition always runs first. Each session is tagged with query_id,
    condition and run index (``run_index_start`` … ``run_index_start + repeats − 1``).
    A failed session is logged to the failure log (§8) and re-run once via
    ``--rerun-failures``.
    """
    from graph.travel_graph import run_collaborative_travel_query

    total = len(queries) * len(conditions) * repeats
    done = ok = 0
    for run_idx in range(run_index_start, run_index_start + repeats):
        for qi, (query_id, query) in enumerate(queries):
            shift = qi % len(conditions)
            order = conditions[shift:] + conditions[:shift]
            for condition in order:
                done += 1
                print(f"  [{done}/{total}] run {run_idx} {query_id} {condition}")
                try:
                    await run_collaborative_travel_query(
                        query,
                        enable_refinement=(condition != "baseline"),
                        refinement_mode=None if condition == "baseline" else condition,
                        session_tags={
                            "query_id": query_id, "condition": condition, "run_index": run_idx,
                        },
                        inventory_profile=inventory_profile,
                    )
                    ok += 1
                except Exception as exc:  # protocol §8: logged, re-run once
                    print(f"           ✗ {exc}")
                    if query_set:
                        _log_failure(query_set, query_id, condition, run_idx, exc)
    print(f"\n  conditions: {ok}/{total} sessions succeeded")


def _current_partial_usage() -> tuple[int, int, float] | None:
    """The ``TokenUsage`` a failed session accumulated (still bound to this task)."""
    from metrics.token_tracker import compute_session_cost, get_current

    usage = get_current()
    if usage is None:
        return None
    return usage.input_tokens, usage.output_tokens, round(compute_session_cost(usage), 6)


def _log_failure(
    query_set: str, query_id: str, condition: str, run_index: int, exc: BaseException,
) -> None:
    """Append one row to the §8 failure log, with the partial usage so far.

    The session's ``TokenUsage`` is still bound to this task's context when the
    graph raises, so the tokens spent before the crash are recorded and can be
    counted by the billing reconciliation instead of silently inflating its Δ.
    """
    from scripts.failure_log import append_failure, reason_for

    row = {
        "set": query_set,
        "query_id": query_id,
        "condition": condition,
        "run_index": run_index,
        "reason": reason_for(exc),
        "rerun_ok": "",
    }
    partial = _current_partial_usage()
    if partial is not None:
        row["input_tokens"], row["output_tokens"], row["cost_usd"] = partial
    append_failure(row)


def _record_rerun_failure_usage(
    set_name: str, query_id: str, condition: str, run_index: int,
) -> None:
    """Sum a failed re-run's partial usage into the row (never overwrite)."""
    from scripts.failure_log import add_partial_usage

    partial = _current_partial_usage()
    if partial is None:
        return
    add_partial_usage(
        set_name, query_id, condition, run_index,
        input_tokens=partial[0], output_tokens=partial[1], cost_usd=partial[2],
    )


async def rerun_failures(manifest_path: str) -> None:
    """Re-run every failure row with empty ``rerun_ok`` once (protocol §8).

    Sessions are appended to the current ``VOYAGER_METRICS_PATH``; each row's
    ``rerun_ok`` is then set to "true" (re-run succeeded) or "false" (the re-run
    failed too, so the query is dropped from every condition in its set). A
    failed re-run's partial usage is summed into the row's trailing usage
    columns, so nothing billed mid-crash is lost from the reconciliation.
    """
    from graph.travel_graph import run_collaborative_travel_query
    from scripts.failure_log import failures_path, load_failures, set_rerun_ok

    path = failures_path()
    rows = [r for r in load_failures(path) if str(r.get("rerun_ok", "")).strip() == ""]
    if not rows:
        print(f"\nNo failure rows with empty rerun_ok in {path}; nothing to re-run.")
        return

    manifest = {(r["set"], r["query_id"]): r["query_text"] for r in _manifest_rows(manifest_path)}
    print(f"\nRe-running {len(rows)} failed session(s) once (§8):")
    print(f"Failures → {path}")
    print(f"Sessions → {os.environ.get('VOYAGER_METRICS_PATH', 'metrics/sessions.jsonl')}\n")

    ok = 0
    for row in rows:
        set_name = row["set"]
        query_id = row["query_id"]
        condition = row["condition"]
        run_index = int(row["run_index"])
        query = manifest.get((set_name, query_id))
        if query is None:
            print(f"  skip {set_name} {query_id}: query text not in {manifest_path}")
            continue
        print(f"  {set_name} {query_id} {condition} run {run_index}")
        try:
            await run_collaborative_travel_query(
                query,
                enable_refinement=(condition != "baseline"),
                refinement_mode=None if condition == "baseline" else condition,
                session_tags={
                    "query_id": query_id, "condition": condition, "run_index": run_index,
                },
                inventory_profile="control" if set_name == "control" else "default",
            )
            set_rerun_ok(set_name, query_id, condition, run_index, "true")
            ok += 1
        except Exception as exc:
            print(f"           ✗ {exc}")
            _record_rerun_failure_usage(set_name, query_id, condition, run_index)
            set_rerun_ok(set_name, query_id, condition, run_index, "false")
    print(f"\n  rerun: {ok}/{len(rows)} sessions succeeded; rerun_ok updated in {path}")


def _results_dir() -> str | None:
    """Where summary artifacts go. ``VOYAGER_RESULTS_DIR`` wins; None means results/."""
    return os.environ.get("VOYAGER_RESULTS_DIR") or None


def _conditions_results_dir() -> str:
    """v1.5 condition runs never write to results/, which holds the v1.4 artifacts.

    Next to the session log when ``VOYAGER_METRICS_PATH`` is set, so one run's
    sessions and summaries stay together; otherwise results/v1.5/summary.
    """
    metrics_path = os.environ.get("VOYAGER_METRICS_PATH")
    if metrics_path:
        return str(Path(metrics_path).resolve().parent)
    return str(Path("results") / "v1.5" / "summary")


def _write_results(inventory_mode: str) -> None:
    """Write standardized results artifacts (run_summary.json, CSVs, manifest)."""
    from agents.inventory import load_manifest
    from metrics.collector import load_sessions, write_results_artifacts

    sessions = load_sessions()
    manifest = load_manifest()
    out_dir = write_results_artifacts(
        sessions, inventory_manifest=manifest, out_dir=_results_dir(), inventory_mode=inventory_mode
    )
    print(f"\nResults written → {out_dir} (inventory_mode={inventory_mode})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Voyager metrics benchmark")
    parser.add_argument(
        "--mode",
        choices=["full", "baseline", "compare", "conditions"],
        default="full",
        help=(
            "full: run with refinement; baseline: skip rounds 2/3; compare: run both; "
            "conditions: every v1.5 condition per query, interleaved (protocol §6)"
        ),
    )
    parser.add_argument(
        "--refinement-mode",
        choices=["targeted", "full_rerun", "broadcast", "two_way"],
        default=None,
        help="refinement condition for --mode full (defaults to VOYAGER_REFINEMENT_MODE)",
    )
    parser.add_argument(
        "--conditions",
        default=",".join(CONDITIONS),
        help="comma-separated conditions for --mode conditions",
    )
    parser.add_argument("--repeats", type=int, default=1, help="runs per condition (--mode conditions)")
    parser.add_argument(
        "--run-index-start",
        type=int,
        default=1,
        help="first run_index for --mode conditions (default 1; replays use 2 and 3)",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="read queries and IDs from a query manifest CSV (with --set)",
    )
    parser.add_argument(
        "--set", dest="query_set", choices=["synthetic", "real", "control"], default="synthetic",
        help="manifest set to run",
    )
    parser.add_argument(
        "--inventory",
        choices=["mock", "capture", "replay"],
        default=None,
        help="inventory provider mode (defaults to VOYAGER_INVENTORY_MODE env or 'mock')",
    )
    parser.add_argument(
        "--query-count",
        type=int,
        default=None,
        help="run only the first N queries (alias of --limit)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print queries without running")
    parser.add_argument("--summary", action="store_true", help="Print summary of existing runs")
    parser.add_argument("--limit", type=int, default=None, help="Run only first N queries")
    parser.add_argument(
        "--rerun-failures",
        action="store_true",
        help="re-run every failures.csv row with empty rerun_ok once (protocol §8)",
    )
    args = parser.parse_args()

    if args.inventory:
        os.environ["VOYAGER_INVENTORY_MODE"] = args.inventory
        reload_settings()
    inventory_mode = get_settings().inventory_mode

    # The control mock profiles are mock-only (protocol §5, item 5a). Fail fast
    # here rather than letting every session fail inside the agents.
    if args.query_set == "control" and inventory_mode != "mock":
        raise SystemExit(
            f"the control set requires mock inventory mode (got {inventory_mode!r}); "
            "control profiles exist only for the mock path"
        )

    if args.rerun_failures:
        if not args.manifest:
            raise SystemExit("--rerun-failures needs --manifest (maps set/query_id back to query text)")
        os.environ.setdefault("VOYAGER_PLANS_DIR", str(Path("results") / "v1.5" / "plans"))
        os.environ.setdefault("VOYAGER_RESULTS_DIR", _conditions_results_dir())

        async def _rerun() -> None:
            await rerun_failures(args.manifest)

        asyncio.run(_rerun())
        return

    if args.summary:
        from metrics.collector import print_summary
        print_summary()
        return

    if args.refinement_mode:
        os.environ["VOYAGER_REFINEMENT_MODE"] = args.refinement_mode
        reload_settings()

    limit = args.query_count or args.limit
    if args.manifest:
        tagged = load_manifest_queries(args.manifest, args.query_set)
    else:
        tagged = [(f"Q{i:02d}", q) for i, q in enumerate(QUERIES, 1)]
    offset = int(os.environ.get("VOYAGER_QUERY_OFFSET", "0"))
    if offset:
        tagged = tagged[offset:]
    if limit:
        tagged = tagged[:limit]

    if args.mode == "conditions":
        # Delivered plans are kept for the H2c coherence audit (protocol §11).
        os.environ.setdefault("VOYAGER_PLANS_DIR", str(Path("results") / "v1.5" / "plans"))
        os.environ.setdefault("VOYAGER_RESULTS_DIR", _conditions_results_dir())
        conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
        unknown = set(conditions) - set(CONDITIONS)
        if unknown:
            raise SystemExit(f"unknown conditions: {sorted(unknown)}")
        if args.dry_run:
            print(f"\n{len(tagged)} queries x {conditions} x {args.repeats} runs:\n")
            for qid, q in tagged:
                print(f"  {qid}  {q}")
            return
        print(f"\nRunning {len(tagged)} queries x {len(conditions)} conditions x "
              f"{args.repeats} runs (inventory={inventory_mode})")
        print(f"Sessions  → {os.environ.get('VOYAGER_METRICS_PATH', 'metrics/sessions.jsonl')}")
        print(f"Plans     → {os.environ['VOYAGER_PLANS_DIR']}")
        print(f"Summaries → {os.environ['VOYAGER_RESULTS_DIR']}\n")

        profile = "control" if args.query_set == "control" else "default"

        async def _run_conditions() -> None:
            await run_conditions(
                tagged, conditions, args.repeats,
                inventory_profile=profile, query_set=args.query_set,
                run_index_start=args.run_index_start,
            )
            _write_results(inventory_mode)

        asyncio.run(_run_conditions())
        return

    queries = [q for _, q in tagged]
    if args.dry_run:
        print(f"\n{len(queries)} queries (mode={args.mode}, inventory={inventory_mode}):\n")
        for i, q in enumerate(queries, 1):
            print(f"  {i:2}. {q}")
        return

    mode_desc = {
        "full": "with refinement (Rounds 1-3)",
        "baseline": "without refinement (Round 1 only)",
        "compare": "both modes back-to-back",
    }
    print(f"\nRunning {len(queries)} queries — {mode_desc[args.mode]} (inventory={inventory_mode})")
    print("Metrics → metrics/sessions.jsonl\n")

    async def _run() -> None:
        if args.mode == "compare":
            await run_compare(queries)
        else:
            await run_batch(queries, enable_refinement=(args.mode == "full"))

        print("\nGenerating summary...\n")
        from metrics.collector import print_summary
        print_summary()
        _write_results(inventory_mode)

    asyncio.run(_run())


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    main()
