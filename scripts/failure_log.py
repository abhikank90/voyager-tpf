"""§8 failure log: one CSV row per failed session, written under a file lock.

The official runs are sharded (§6, §10), so several benchmark processes append
to the same ``VOYAGER_FAILURES_PATH`` at once. Writers take an exclusive
``flock`` on a sidecar lock file so rows are never interleaved or lost.

Columns are exactly what ``scripts/paper_numbers.load_failures`` and
``dropped_queries`` read: ``set,query_id,condition,run_index,reason,rerun_ok``.
``rerun_ok`` is empty until ``--rerun-failures`` re-runs the row once.
"""

from __future__ import annotations

import csv
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

FAILURES_COLUMNS = ("set", "query_id", "condition", "run_index", "reason", "rerun_ok")
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
    """Read the failure log. Missing file means no failures yet."""
    path = path or failures_path()
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def reason_for(exc: BaseException) -> str:
    """Exception class + message, truncated to one CSV cell."""
    text = f"{type(exc).__name__}: {exc}"
    return text[:REASON_MAX_CHARS]


def append_failure(row: dict, path: Path | None = None) -> None:
    """Append one failure row, writing the header on first use (under lock)."""
    path = path or failures_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(path):
        new_file = not path.exists() or path.stat().st_size == 0
        with open(path, "a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FAILURES_COLUMNS)
            if new_file:
                writer.writeheader()
            writer.writerow({k: row.get(k, "") for k in FAILURES_COLUMNS})


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
            writer = csv.DictWriter(fh, fieldnames=FAILURES_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in FAILURES_COLUMNS})
        return True
