"""Crescendo — bring the whole orchestra to life.

Five Band agents, each with its own role and LLM brain. Brains are split across
two providers so we don't hit Featherless's 4-unit concurrency ceiling:
  - heavy thinkers (Conductor, Soloist, Tuning Fork) -> AI/ML API (balance-limited, no hard concurrency)
  - light roles (Stage Tech, Archivist)              -> Featherless (unlimited tokens, 2 units each = 4 total)

A global semaphore also serializes LLM calls so bursts can't blow either limit.

Run: uv run python orchestra.py   (Ctrl-C to stop them all)
"""

import asyncio
import logging
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from band import Agent
from band.adapters import LangGraphAdapter

from control import ControlledAdapter, RunState
from memory_tools import build_memory_tools

# Hard termination limits (in code, not prompts). Tune as needed.
MESSAGE_BUDGET = 12   # total agent messages per run before the circuit breaker trips
PER_AGENT_CAP = 3     # max replies any single agent makes per run
RUN = RunState(MESSAGE_BUDGET, PER_AGENT_CAP)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("orchestra")

load_dotenv("/home/madgodinc/code/crescendo/.env")

FEATHERLESS = (os.environ["FEATHERLESS_BASE_URL"], os.environ["FEATHERLESS_API_KEY"])
AIMLAPI = (os.environ["AIMLAPI_BASE_URL"], os.environ["AIMLAPI_API_KEY"])

# Serialize all LLM calls — one brain thinks at a time. Crescendo is a sequential
# pipeline (plan -> code -> review -> deploy), so agents never need to think in
# parallel; this removes 429s and keeps behaviour predictable. (Mad's call.)
LLM_GATE = asyncio.Semaphore(1)


# Shared style rule — prepended to every role. Kills the chatter Mad flagged:
# no pleasantries, no acknowledgements, telegraphic output, one message per turn.
STYLE = (
    "STYLE RULES (strict): No greetings, no thanks, no pleasantries, no "
    "'sure'/'got it'/'here you go'. No restating what others said. One message per "
    "turn, as short as possible — a signal plus the artifact, nothing else. If you "
    "have nothing to add, stay silent. Every word costs money and clutters the demo.\n\n"
)

# role -> (env prefix, provider, model, role brief appended to Band's base prompt)
ROSTER = {
    "Conductor": (
        "CONDUCTOR", AIMLAPI, "deepseek-chat",
        "You are the Conductor of Crescendo, an orchestra of AI agents. You are the ONLY "
        "one who hands out turns — this is a star: every other agent reports back to YOU, "
        "never to each other, and stays silent until you @mention them.\n"
        "Your loop:\n"
        "1. Read the brief. Plan it. @mention @Soloist with a concrete coding task.\n"
        "2. When @Soloist reports back, @mention @Tuning Fork to review it.\n"
        "3. When @Tuning Fork reports back: if it found real issues AND this is the first "
        "review round, @mention @Soloist to fix them. Otherwise accept the work — do NOT "
        "start another review round. Allow AT MOST 2 review rounds total, then move on.\n"
        "4. When the work is accepted, @mention @Stage Tech to deploy.\n"
        "5. When @Stage Tech reports the live URL, post a final summary to the human and "
        "then STOP — do not @mention anyone again.\n"
        "You NEVER write code, review, or deploy yourself — you only coordinate. Keep every "
        "message short. Reply in the language of the brief.",
    ),
    "Soloist": (
        "SOLOIST", AIMLAPI, "gpt-4o",
        "You are the Soloist of Crescendo — the engineer. You write product code ONLY when "
        "the @Conductor assigns you a task. Produce complete, working code in fenced blocks. "
        "When done, report back to @Conductor ONLY (e.g. '@Conductor done, here is the code'). "
        "NEVER @mention any other agent. Then stay silent until the Conductor calls you again.",
    ),
    "Tuning Fork": (
        "TUNING_FORK", AIMLAPI, "deepseek-chat",
        "You are the Tuning Fork of Crescendo — the critic. You review the Soloist's work "
        "ONLY when the @Conductor asks. Be specific and adversarial: list concrete issues, or "
        "say it is clean. Report your verdict back to @Conductor ONLY (e.g. '@Conductor verdict: "
        "clean' or '@Conductor issues: ...'). NEVER @mention the Soloist or anyone else. Then "
        "stay silent until the Conductor calls you again.",
    ),
    "Stage Tech": (
        "STAGE_TECH", FEATHERLESS, "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
        "You are the Stage Tech of Crescendo — the deployer. You deploy ONLY when the "
        "@Conductor tells you to. Report the result back to @Conductor ONLY. NEVER @mention "
        "anyone else. Then stay silent until the Conductor calls you again.",
    ),
    "Archivist": (
        "ARCHIVIST", FEATHERLESS, "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
        "You are the Archivist of Crescendo — the shared memory of the orchestra. "
        "You have two tools: `remember` (store a fact/decision/result) and `recall` "
        "(search prior context). ALWAYS use `recall` first when asked for context, and "
        "ALWAYS `remember` any decision or result others report. When asked, give a "
        "tight summary built from what `recall` returns — surface only the relevant "
        "pieces, not everything. You are the source of truth across the run.",
    ),
}


def gated_llm(provider, model: str) -> ChatOpenAI:
    """A ChatOpenAI whose calls pass through the global concurrency gate."""
    base_url, api_key = provider
    llm = ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=0.3,
        max_tokens=1024,
        stream_chunk_timeout=None,  # don't kill slow reasoning models mid-stream
        max_retries=3,
    )
    # wrap the async generate to serialize through the semaphore
    orig = llm._agenerate

    async def gated(*args, **kwargs):
        async with LLM_GATE:
            return await orig(*args, **kwargs)

    llm._agenerate = gated  # type: ignore[method-assign]
    return llm


def build_agent(name: str, prefix: str, provider, model: str, role: str) -> Agent:
    # Each agent gets memory tools bound to its own mgi-mind token (audit attribution).
    mem_tools = build_memory_tools(os.environ[f"MGIMIND_TOKEN_{prefix}"])
    inner = LangGraphAdapter(
        llm=gated_llm(provider, model),
        custom_section=STYLE + role,
        additional_tools=mem_tools,
    )
    # Wrap with the control loop so the room can't loop forever.
    adapter = ControlledAdapter(inner, RUN, name, os.environ[f"{prefix}_AGENT_ID"])
    return Agent.create(
        adapter=adapter,
        agent_id=os.environ[f"{prefix}_AGENT_ID"],
        api_key=os.environ[f"{prefix}_API_KEY"],
        ws_url=os.environ["BAND_WS_URL"],
        rest_url=os.environ["BAND_REST_URL"],
    )


async def main() -> None:
    agents = []
    for name, (prefix, provider, model, role) in ROSTER.items():
        agent = build_agent(name, prefix, provider, model, role)
        await agent.start()
        agents.append((name, agent))
        where = "AI/ML API" if provider is AIMLAPI else "Featherless"
        log.info("ONLINE: %-12s %-10s %s", name, where, model)

    log.info("=== Crescendo orchestra is LIVE (%d agents, gate=1). @mention in the room. ===",
             len(agents))
    try:
        await asyncio.Event().wait()
    finally:
        for name, agent in agents:
            await agent.stop()
            log.info("stopped %s", name)


if __name__ == "__main__":
    asyncio.run(main())
