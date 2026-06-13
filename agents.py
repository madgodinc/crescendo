"""Worker agents for Crescendo — the bodies Maestro conducts.

These five just listen and answer when @mentioned, each in its role, with its
own brain (LLM) and tools (memory, and for Soloist/Stage Tech also file+deploy).
No control loop here — Maestro (maestro.py) drives the flow deterministically.

Run: uv run python agents.py   (keep running while Maestro orchestrates)
"""

import asyncio
import logging
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from band import Agent
from band.adapters import LangGraphAdapter
from band.core import SimpleAdapter

from deploy_tools import build_deploy_tools
from memory_tools import build_memory_tools


class GatedAdapter(SimpleAdapter):
    """Only run the LLM when Maestro addresses THIS agent. Kills cross-agent
    chatter — workers answer the orchestrator, never each other."""

    def __init__(self, inner: SimpleAdapter, my_uuid: str):
        super().__init__()
        self._inner = inner
        self._uuid = my_uuid

    async def on_event(self, inp) -> None:
        msg = getattr(inp, "msg", None)
        sender = getattr(msg, "sender_id", "")
        content = (getattr(msg, "content", "") or "")
        addressed = self._uuid in content  # Band encodes mentions as @[[uuid]]
        if sender != os.environ["MAESTRO_AGENT_ID"] or not addressed:
            return  # ignore anything not a direct task from Maestro
        await self._inner.on_event(inp)

    async def on_message(self, *a, **k):
        return await self._inner.on_message(*a, **k)

    async def on_started(self, *a, **k):
        return await self._inner.on_started(*a, **k)

    async def on_cleanup(self, *a, **k):
        return await self._inner.on_cleanup(*a, **k)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agents")

load_dotenv("/home/madgodinc/code/crescendo/.env")

FEATHERLESS = (os.environ["FEATHERLESS_BASE_URL"], os.environ["FEATHERLESS_API_KEY"])
AIMLAPI = (os.environ["AIMLAPI_BASE_URL"], os.environ["AIMLAPI_API_KEY"])

# Every role is told to use band_send_message to reply — the LangGraph adapter
# does NOT auto-send text to chat, so the agent must call the tool explicitly.
REPLY_RULE = (
    "To reply, you MUST call the band_send_message tool — plain text is NOT sent. "
    "Keep replies short and telegraphic: no greetings, no thanks, no filler. "
    "Reply only to Maestro. NEVER @mention any other agent — Maestro routes all work. "
)

ROSTER = {
    "Conductor": ("CONDUCTOR", AIMLAPI, "deepseek-chat",
        REPLY_RULE + "You are the Conductor — you turn a brief into a short build plan. "
        "Reply with the plan only. NEVER @mention other agents — Maestro routes the work."),
    "Soloist": ("SOLOIST", AIMLAPI, "gpt-4o",
        REPLY_RULE + "You are the Soloist — the engineer. Write the product with the "
        "write_file tool (a single self-contained index.html is ideal), then reply with a "
        "one-line summary of what you wrote."),
    "Tuning Fork": ("TUNING_FORK", AIMLAPI, "deepseek-chat",
        REPLY_RULE + "You are the Tuning Fork — the critic. Review the work. Reply 'CLEAN' "
        "if good, or 'ISSUES: ...' listing concrete fixes."),
    "Stage Tech": ("STAGE_TECH", AIMLAPI, "deepseek-chat",
        REPLY_RULE + "You are the Stage Tech — the deployer. Call deploy_site and reply with "
        "the exact live URL it returns. Never invent a URL."),
    "Archivist": ("ARCHIVIST", AIMLAPI, "deepseek-chat",
        REPLY_RULE + "You are the Archivist — memory. Use remember to store what you're told, "
        "recall to fetch context, and reply with a one-line confirmation or summary."),
}


def build(prefix, provider, model, role) -> Agent:
    base_url, api_key = provider
    llm = ChatOpenAI(model=model, base_url=base_url, api_key=api_key,
                     temperature=0.3, max_tokens=1024)
    tools = build_memory_tools(os.environ[f"MGIMIND_TOKEN_{prefix}"])
    if prefix in ("SOLOIST", "STAGE_TECH"):
        tools = tools + build_deploy_tools()
    inner = LangGraphAdapter(llm=llm, custom_section=role, additional_tools=tools)
    gated = GatedAdapter(inner, os.environ[f"{prefix}_AGENT_ID"])
    return Agent.create(
        adapter=gated,
        agent_id=os.environ[f"{prefix}_AGENT_ID"],
        api_key=os.environ[f"{prefix}_API_KEY"],
        ws_url=os.environ["BAND_WS_URL"],
        rest_url=os.environ["BAND_REST_URL"],
    )


async def main() -> None:
    agents = []
    for name, (prefix, provider, model, role) in ROSTER.items():
        a = build(prefix, provider, model, role)
        await a.start()
        agents.append((name, a))
        log.info("listening: %s (%s)", name, model)
    log.info("=== %d worker agents listening — start maestro.py ===", len(agents))
    try:
        await asyncio.Event().wait()
    finally:
        for name, a in agents:
            await a.stop()


if __name__ == "__main__":
    asyncio.run(main())
