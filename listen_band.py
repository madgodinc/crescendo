"""Listen as Conductor: connect and log every incoming room message.

Proves the human -> room -> agent channel. No LLM — the agent just receives
and logs. Write to the room in your browser; messages should appear here.

Run: uv run python listen_band.py   (Ctrl-C to stop)
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

from band import Agent
from band.core import SimpleAdapter

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("listen")


class LoggingAdapter(SimpleAdapter):
    async def on_message(self, msg, tools, history, participants_msg,
                         contacts_msg, *, is_session_bootstrap, room_id):
        sender = getattr(msg, "sender", getattr(msg, "sender_id", "?"))
        text = getattr(msg, "text", getattr(msg, "content", msg))
        log.info(">>> ROOM %s | from %s | %r (bootstrap=%s)",
                 room_id, sender, text, is_session_bootstrap)


async def main() -> None:
    load_dotenv("/home/madgodinc/code/crescendo/.env")
    agent = Agent.create(
        adapter=LoggingAdapter(),
        agent_id=os.environ["CONDUCTOR_AGENT_ID"],
        api_key=os.environ["CONDUCTOR_API_KEY"],
        ws_url=os.environ["BAND_WS_URL"],
        rest_url=os.environ["BAND_REST_URL"],
    )
    await agent.start()
    log.info("Conductor listening as %s — write in the room now (Ctrl-C to stop)",
             agent.agent_name)
    try:
        await asyncio.Event().wait()
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
