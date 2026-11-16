import logging
import time
from abc import ABC, abstractmethod
from datetime import date

from langsmith import traceable

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Base class for all Voyager travel agents."""

    name: str = "base_agent"
    description: str = ""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._setup()

    def _setup(self):
        """Override to add agent-specific setup."""
        pass

    @traceable(name="agent_run")
    async def run(self, state: dict) -> dict:
        start = time.perf_counter()
        try:
            result = await self._execute(state)
            logger.info(
                "agent=%s status=ok duration=%.2fs",
                self.name,
                time.perf_counter() - start,
            )
            return result
        except Exception as exc:
            logger.error("agent=%s error=%s", self.name, exc, exc_info=True)
            raise

    @abstractmethod
    async def _execute(self, state: dict) -> dict:
        """Core agent logic — must return a dict of state updates."""
        ...

    def _messages_for_me(self, state: dict) -> list[dict]:
        """Return collaboration messages targeting this agent from prior rounds.

        Matches both the full agent name and the short hub-convention name
        (e.g. "hotel_agent" and "hotel") so research agents can read what
        the CollaborationHub addressed to them without knowing hub internals.
        """
        current_round = state.get("collaboration_round", 1)
        my_ids = {self.name}
        short = getattr(self, "short_name", None)
        if short:
            my_ids.add(short)
        # round < current_round intentionally includes messages from all prior
        # rounds (not just round - 1). Constraints are idempotent — applying
        # the same location hint or timing preference twice changes nothing —
        # so accumulating them is harmless and avoids needing per-round bookkeeping.
        return [
            m for m in state.get("agent_messages", [])
            if m.get("to_agent") in my_ids | {"all"}
            and m.get("round", 0) < current_round
        ]

    def _error_state(self, message: str) -> dict:
        return {"errors": {self.name: message}}

    def _inbox_digest(self, state: dict) -> str:
        """Content hash of the messages this agent consumes, or "" if none.

        Recorded LLM outputs are keyed on their prompt inputs. A re-run agent's
        inputs include its inbox, so the inbox must be part of the key: without
        it a replayed re-run would be served its Round-1 output again whatever
        feedback it received (protocol §4). Empty for Round 1, which keeps
        Round-1 fixture keys unchanged.
        """
        import hashlib
        import json

        messages = self._messages_for_me(state)
        if not messages:
            return ""
        canonical = json.dumps(
            sorted(messages, key=lambda m: json.dumps(m, sort_keys=True, default=str)),
            sort_keys=True, default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def _trip_window(self, intent: dict, legacy_duration_days: int = 13):
        """Inventory date window for this session (protocol §5.2).

        Mock and capture always use the v1.5 month rule. Replay uses the rule the
        fixture set was captured under, so pre-v1.5 sets (no ``trip_window_rule``
        in the manifest) keep reproducing with their original legacy dates.
        """
        from agents import inventory
        from agents.trip_window import (
            RULE_LEGACY,
            RULE_MONTH,
            legacy_trip_window,
            resolve_trip_window,
        )
        from config.settings import get_settings

        mode = self._inventory_mode()
        if mode == "mock":
            anchor, rule = get_settings().mock_anchor_date, RULE_MONTH
        elif mode == "capture":
            anchor, rule = inventory.ensure_capture_anchor(RULE_MONTH), RULE_MONTH
        else:
            anchor = self._get_fixture_capture_date()
            rule = inventory.trip_window_rule() or RULE_LEGACY

        if rule == RULE_LEGACY:
            return legacy_trip_window(
                mode, intent.get("travel_year"), legacy_duration_days, anchor,
                intent.get("travel_month"),
            )
        return resolve_trip_window(
            intent.get("travel_month"), intent.get("duration_days"), anchor,
        )

    def _inventory_mode(self) -> str:
        from config.settings import get_settings
        return get_settings().inventory_mode

    def _get_fixture_capture_date(self) -> date:
        """Capture date (local) anchored on the replay manifest's first fixture.

        Replay anchors its query window to when the fixtures were captured rather
        than to `today`, so reruns across days stay identical (date-stable replay).
        The manifest is keyed in insertion order, so its first entry is the
        reference capture run; we take that fixture's local capture date so the
        derived +90d window reproduces the exact query ids used at capture time.
        Falls back to today if the manifest is empty.
        """
        from datetime import datetime

        from agents.inventory import load_manifest
        manifest = load_manifest()
        # An explicit anchor makes replay independent of the machine's timezone.
        # Without it the anchor is the first fixture's capture time converted to
        # *local* time, which lands on a different calendar day east of the
        # capture timezone and makes every fixture lookup miss.
        anchor = manifest.get("replay_anchor_date")
        if anchor:
            return date.fromisoformat(anchor)
        fixtures = manifest.get("fixtures") or {}
        for meta in fixtures.values():
            ts = meta.get("captured_at")
            if ts:
                try:
                    return datetime.fromisoformat(ts).astimezone().date()
                except (TypeError, ValueError):
                    continue
        return date.today()
