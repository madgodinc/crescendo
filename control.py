"""Control loop — the rules that stop the orchestra from looping forever.

Band agents reply to any @mention, so a Soloist<->Tuning Fork ping-pong never
ends on its own. This wraps each agent's adapter and enforces hard limits in
code (not prompts), so termination is guaranteed regardless of what the LLM
decides:

  1. Per-run message budget (circuit breaker) — a shared counter across all
     agents in a room. Past the budget, every agent goes silent.
  2. Per-agent reply cap — each agent answers at most N times per run, so no
     single pair can ping-pong.
  3. Bootstrap pass-through — the first human brief always gets through.

A "run" is a room. Counters reset when a fresh human (non-agent) message with
no prior agent activity arrives — i.e. a new brief.
"""

import logging

from band.core import SimpleAdapter

log = logging.getLogger("control")


class RunState:
    """Shared, per-room counters for one orchestra run."""

    def __init__(self, message_budget: int, per_agent_cap: int):
        self.message_budget = message_budget
        self.per_agent_cap = per_agent_cap
        self.total = 0
        self.per_agent: dict[str, int] = {}
        self.stopped = False

    def allow(self, agent_id: str) -> tuple[bool, str]:
        if self.stopped:
            return False, "run stopped"
        if self.total >= self.message_budget:
            self.stopped = True
            return False, f"circuit breaker: {self.total} msgs >= budget {self.message_budget}"
        used = self.per_agent.get(agent_id, 0)
        if used >= self.per_agent_cap:
            return False, f"agent reply cap reached ({used}/{self.per_agent_cap})"
        return True, ""

    def record(self, agent_id: str) -> None:
        self.total += 1
        self.per_agent[agent_id] = self.per_agent.get(agent_id, 0) + 1


class ControlledAdapter(SimpleAdapter):
    """Wraps a real adapter and gates on_message through RunState."""

    def __init__(self, inner: SimpleAdapter, run_state: RunState, agent_name: str,
                 agent_uuid: str = ""):
        super().__init__()
        self._inner = inner
        self._state = run_state
        self._name = agent_name
        self._agent_uuid = agent_uuid

    async def on_message(self, *args, **kwargs):
        # Band drives the inner adapter via on_event, not this. Kept only to
        # satisfy the abstract base; delegate just in case it is ever called.
        return await self._inner.on_message(*args, **kwargs)

    async def on_event(self, inp) -> None:
        # THIS is Band's real entry point (it internally calls inner.on_message).
        msg = getattr(inp, "msg", None)
        stype = getattr(msg, "sender_type", "?")
        mtype = getattr(msg, "message_type", "?")
        content = getattr(msg, "content", "") or ""
        # Diagnostic so we can see exactly what triggers each turn.
        log.info("EVENT %s: sender_type=%s msg_type=%s content=%r",
                 self._name, stype, mtype, content[:80])

        # 1. Ignore system/non-chat events (participant joined/left, etc.) — these
        #    must NOT cost a turn. Only react to real chat messages.
        if mtype not in ("chat", "message", "text", "user_message", "agent_message"):
            log.info("IGNORE %s: non-chat event (%s)", self._name, mtype)
            return

        # 2. React only if this agent is actually addressed (mentioned by handle/name)
        #    or it's the very first human brief. Otherwise stay silent — no chatter.
        addressed = self._is_addressed(content)
        if not getattr(inp, "is_session_bootstrap", False) and not addressed:
            log.info("IGNORE %s: not addressed", self._name)
            return

        # 3. Circuit breaker.
        if not getattr(inp, "is_session_bootstrap", False):
            ok, why = self._state.allow(self._name)
            if not ok:
                log.warning("SILENCED %s: %s", self._name, why)
                return

        await self._inner.on_event(inp)
        self._state.record(self._name)
        log.info("turn %s done (total=%d, %s used=%d)",
                 self._name, self._state.total, self._name,
                 self._state.per_agent.get(self._name, 0))

    def _is_addressed(self, content: str) -> bool:
        """True if this agent is @mentioned. Band encodes mentions as @[[uuid]];
        also accept the plain display name as a fallback."""
        c = content.lower()
        if self._agent_uuid and self._agent_uuid.lower() in c:
            return True
        return self._name.lower() in c

    # Forward remaining lifecycle hooks to the wrapped adapter.
    async def on_started(self, *args, **kwargs):
        return await self._inner.on_started(*args, **kwargs)

    async def on_cleanup(self, *args, **kwargs):
        return await self._inner.on_cleanup(*args, **kwargs)
