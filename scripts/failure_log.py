"""§8 failure log: one CSV row per failed session, written under a file lock.

The official runs are sharded (§6, §10), so several benchmark processes append
to the same ``VOYAGER_FAILURES_PATH`` at once. Writers take an exclusive
``flock`` on a sidecar lock file so rows are never interleaved or lost.

The first six columns are exactly what ``scripts/paper_numbers.load_failures``
and ``dropped_queries`` read: ``set,query_id,condition,run_index,reason,rerun_ok``.
Three trailing columns carry the partial ``TokenUsage`` a failed session had
accumulated before it raised: ``input_tokens,output_tokens,cost_usd`` (§8:
"anything billed but untracked counts against the reconciliation"). Both the
reader and the writer tolerate the old 6-column shape, so pre-existing files
keep working.
"""

from __future__ import annotations

import csv
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

FAILURES_COLUMNS = (
    "set", "query_id", "condition", "run_index", "reason", "rerun_ok",
    "input_tokens", "output_tokens", "cost_usd",
)
BASE_COLUMNS = FAILURES_COLUMNS[:6]
REASON_MAX_CHARS = 500
DEFAULT_FAILURES_PATH = Path("results") / "v1.5" / "failures.csv"


def failures_path() -> Path:
    """Where failures are logged. ``VOYAGER_FAILURES_PATH`` wins; see protocol §8."""
    return Path(os.environ.get("VOYAGER_FAILURES_PATH", DEFAULT_FAILURES_PATH))


def _lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


@contextmanager
def _file_lock(path: Path):
    """Exclusive advisory lock on a sidecar file (created if absent)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(_lock_path(path), "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def load_failures(path: Path | None = None) -> list[dict]:
    """Read the failure log. Missing file means no failures yet.

    Tolerates both the 6-column (old) and 9-column (new) shapes: rows are
    returned as-read, and consumers use ``.get`` for the trailing columns.
    """
    path = path or failures_path()
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def reason_for(exc: BaseException) -> str:
    """Exception class + message, truncated to one CSV cell."""
    text = f"{type(exc).__name__}: {exc}"
    return text[:REASON_MAX_CHARS]


def _int_val(value) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _float_val(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _existing_columns(path: Path) -> list[str]:
    """Header columns of an existing file, or None when absent/empty."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    with open(path, newline="") as fh:
        first = fh.readline().strip()
    return [c.strip() for c in first.split(",")] if first else None


def append_failure(row: dict, path: Path | None = None) -> None:
    """Append one failure row, writing the header on first use (under lock).

    A new file gets the full 9-column header; an existing 6-column file keeps
    its old shape (the trailing usage columns are simply omitted).
    """
    path = path or failures_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(path):
        columns = _existing_columns(path) or list(FAILURES_COLUMNS)
        with open(path, "a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            if _existing_columns(path) is None:
                writer.writeheader()
            writer.writerow({k: row.get(k, "") for k in columns})


def add_partial_usage(
    set_name: str,
    query_id: str,
    condition: str,
    run_index: int,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    path: Path | None = None,
) -> bool:
    """Add a failed re-run's partial usage to its row (summed, never overwritten)."""
    path = path or failures_path()
    if not path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(path):
        columns = _existing_columns(path) or list(FAILURES_COLUMNS)
        if not {"input_tokens", "output_tokens", "cost_usd"} <= set(columns):
            return False  # old 6-column shape: nothing to sum into
        rows = load_failures(path)
        found = False
        for row in rows:
            if (
                row.get("set") == set_name
                and row.get("query_id") == query_id
                and row.get("condition") == condition
                and str(row.get("run_index")) == str(run_index)
            ):
                row["input_tokens"] = _int_val(row.get("input_tokens")) + input_tokens
                row["output_tokens"] = _int_val(row.get("output_tokens")) + output_tokens
                row["cost_usd"] = round(_float_val(row.get("cost_usd")) + cost_usd, 6)
                found = True
        if not found:
            return False
        with open(path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in columns})
        return True


def set_rerun_ok(
    set_name: str,
    query_id: str,
    condition: str,
    run_index: int,
    value: str,
    path: Path | None = None,
) -> bool:
    """Mark a failure row ``rerun_ok=value`` ("true"/"false"). Returns True if found."""
    path = path or failures_path()
    if not path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(path):
        columns = _existing_columns(path) or list(FAILURES_COLUMNS)
        rows = load_failures(path)
        found = False
        for row in rows:
            if (
                row.get("set") == set_name
                and row.get("query_id") == query_id
                and row.get("condition") == condition
                and str(row.get("run_index")) == str(run_index)
            ):
                row["rerun_ok"] = value
                found = True
        if not found:
            return False
        with open(path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in columns})
        return True
