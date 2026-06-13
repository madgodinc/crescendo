"""Give the Conductor a brain (Featherless via LangGraph) and let it reply.

Connect Conductor with a real LLM, then it answers @mentions in its room.
Run: uv run python brain_test.py   (Ctrl-C to stop)
"""

import asyncio
import logging
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from band import Agent
from band.adapters import LangGraphAdapter

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("brain")

FEATHERLESS_BASE = "https://api.featherless.ai/v1"
MODEL = "deepseek-ai/DeepSeek-V3.1"

CUSTOM = (
    "You are the Conductor of Crescendo, an orchestra of AI agents. "
    "You plan a brief and route work to other agents. "
    "Reply briefly and in the language the user wrote in."
)


async def main() -> None:
    load_dotenv("/home/madgodinc/code/crescendo/.env")

    llm = ChatOpenAI(
        model=MODEL,
        base_url=FEATHERLESS_BASE,
        api_key=os.environ["FEATHERLESS_API_KEY"],
        temperature=0.3,
        max_tokens=512,
    )

    adapter = LangGraphAdapter(llm=llm, custom_section=CUSTOM)

    agent = Agent.create(
        adapter=adapter,
        agent_id=os.environ["CONDUCTOR_AGENT_ID"],
        api_key=os.environ["CONDUCTOR_API_KEY"],
        ws_url=os.environ["BAND_WS_URL"],
        rest_url=os.environ["BAND_REST_URL"],
    )
    await agent.start()
    log.info("Conductor is ALIVE with a brain (%s). @mention it in the room.", MODEL)
    try:
        await asyncio.Event().wait()
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
