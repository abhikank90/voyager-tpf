"""Reason codes for conflicts still present at the final audit (protocol §7).

Every conflict that is not ``resolved`` at the final audit gets exactly one reason,
checked in this order:

- ``refinement_disabled``   baseline session: no refinement was attempted.
- ``reopened``              it resolved in an earlier round and came back.
- ``introduced_late``       first seen after the Round-1 audit.
- ``no_qualifying_option``  the resolving agent re-ran, applied the constraint and
                            reported that no candidate satisfied it (fell back to
                            its original selection).
- ``routing_miss``          the resolving agent never re-ran, so no correction
                            reached an agent able to change the conflicting field.
- ``round_limit``           the resolver re-ran with a satisfiable constraint and
                            the conflict still persisted after the last round.
"""
from __future__ import annotations

from typing import Any

UNRESOLVED_REASONS: tuple[str, ...] = (
    "refinement_disabled",
    "reopened",
    "introduced_late",
    "no_qualifying_option",
    "routing_miss",
    "round_limit",
)


def _latest_feedback(feedback_by_round: dict, agent: str) -> dict:
    """The resolver's most recent feedback metrics (Round 3 before Round 2)."""
    for round_key in sorted(feedback_by_round, key=lambda r: int(r), reverse=True):
        fb = (feedback_by_round.get(round_key) or {}).get(agent)
        if fb:
            return fb
    return {}


def classify_unresolved(
    final_conflicts: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    run_metrics: dict[str, Any],
    resolver: dict[str, str],
    refinement_enabled: bool,
) -> list[dict[str, Any]]:
    """One entry per final conflict: type, fingerprint, lifecycle status, reason."""
    rerun: set[str] = set(run_metrics.get("round_2_agents_rerun") or []) | set(
        run_metrics.get("round_3_agents_rerun") or []
    )
    feedback_by_round = run_metrics.get("feedback_by_round") or {}

    out: list[dict[str, Any]] = []
    for conflict in final_conflicts:
        fp = conflict.get("fingerprint", "")
        rec = records.get(fp, {})
        status = rec.get("status", "persisting")
        ctype = conflict.get("type", "")
        agent = resolver.get(ctype, "")

        if not refinement_enabled:
            reason = "refinement_disabled"
        elif status == "reopened":
            reason = "reopened"
        elif rec.get("first_seen_round", 1) > 1:
            reason = "introduced_late"
        elif agent not in rerun:
            reason = "routing_miss"
        else:
            fb = _latest_feedback(feedback_by_round, agent)
            if fb.get("fallback_to_original_selection") or fb.get("reason") == "no_qualifying_option":
                reason = "no_qualifying_option"
            else:
                reason = "round_limit"

        out.append({
            "type": ctype,
            "fingerprint": fp,
            "status": status,
            "resolver": agent,
            "reason": reason,
        })
    return out


def reason_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(UNRESOLVED_REASONS, 0)
    for e in entries:
        counts[e["reason"]] = counts.get(e["reason"], 0) + 1
    return counts
