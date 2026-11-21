"""Refinement conditions for the v1.5 evaluation (protocol §3).

Every condition runs the same deterministic detectors and the same hub. They differ
only in what the hub's messages look like, who receives them, and who re-runs:

- ``targeted``   the pattern: one typed message to the conflict's resolver; only the
                 messaged agents re-run (with the >=3-agent full-rerun escape hatch).
- ``full_rerun`` the same typed messages, addressed to every agent; every agent re-runs.
- ``broadcast``  the same detected conflicts rendered as one prose critique with no
                 typed data and no addressing, sent to every agent; every agent re-runs.
                 Agents that select with code (flight, hotel) read the prose through an
                 LLM interpretation step (``BaseAgent._interpret_critique``).
- ``two_way``    ablation: a conflict with two actionable participants messages both of
                 them, so for ``location_mismatch`` the experience agent is also asked to
                 move toward the hotel. Other conflict types are unchanged.

``baseline`` (no refinement) is selected separately with ``enable_refinement=False``.
"""
from __future__ import annotations

from typing import Literal

RefinementMode = Literal["targeted", "full_rerun", "broadcast", "two_way"]
REFINEMENT_MODES: tuple[str, ...] = ("targeted", "full_rerun", "broadcast", "two_way")

ALL_RESEARCH_AGENTS = frozenset({"flight", "hotel", "experience", "weather", "visa_safety"})
RERUNNABLE = frozenset({"flight", "hotel", "experience"})

CRITIQUE_MESSAGE_TYPE = "critique"


def validate_mode(mode: str) -> str:
    if mode not in REFINEMENT_MODES:
        raise ValueError(f"unknown refinement_mode {mode!r}; expected one of {REFINEMENT_MODES}")
    return mode


def render_critique(messages: list[dict]) -> str:
    """Prose rendering of the hub's typed messages: content only, no data, no addressing."""
    seen: list[str] = []
    for m in messages:
        text = (m.get("content") or "").strip()
        if text and text not in seen:
            seen.append(text)
    return " ".join(seen)


def transform_messages(
    mode: str,
    messages: list[dict],
    conflicts: list[dict],
    state: dict,
    round_num: int,
) -> list[dict]:
    """Rewrite the hub's typed messages for ``mode``. Detection is untouched."""
    validate_mode(mode)
    if mode == "targeted" or not messages:
        return list(messages)

    if mode == "full_rerun":
        return [{**m, "to_agent": "all"} for m in messages]

    if mode == "broadcast":
        return [{
            "from_agent": "collaboration_hub",
            "to_agent": "all",
            "message_type": CRITIQUE_MESSAGE_TYPE,
            "content": render_critique(messages),
            "data": {},
            "round": round_num,
        }]

    # two_way
    out = list(messages)
    if any(c.get("type") == "location_mismatch" for c in conflicts):
        hotel_location = (state.get("selected_hotel") or {}).get("location", "")
        if hotel_location:
            out.append({
                "from_agent": "collaboration_hub",
                "to_agent": "experience",
                "message_type": "constraint",
                "content": (
                    "The selected hotel is far from the top activities."
                    f" Prefer activities near {hotel_location}."
                ),
                "data": {"near_location": hotel_location},
                "round": round_num,
            })
    return out


def round_3_agents(mode: str, conflicts: list[dict], resolver: dict[str, str]) -> set[str]:
    """Agents re-run in Round 3 for the conflicts that survived Round 2."""
    validate_mode(mode)
    if not conflicts:
        return set()
    if mode in ("full_rerun", "broadcast"):
        return set(ALL_RESEARCH_AGENTS)
    agents = {resolver[c["type"]] for c in conflicts if c.get("type") in resolver}
    if mode == "two_way" and any(c.get("type") == "location_mismatch" for c in conflicts):
        agents.add("experience")
    return agents & RERUNNABLE
