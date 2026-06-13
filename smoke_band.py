"""Smoke test: can the Conductor agent authenticate and connect to Band?

No LLM involved — uses a bare SimpleAdapter just to validate the key and the
websocket handshake. Run: uv run python smoke_band.py
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

from band import Agent
from band.core import SimpleAdapter

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smoke")


class NoopAdapter(SimpleAdapter):
    """Connects and logs incoming messages without invoking any LLM."""

    async def on_message(self, msg, tools, history, participants_msg,
                         contacts_msg, *, is_session_bootstrap, room_id):
        log.info("message in room %s from %s: %r", room_id,
                 getattr(msg, "sender", "?"), getattr(msg, "text", msg))


async def main() -> None:
    load_dotenv()

    agent_id = os.environ["CONDUCTOR_AGENT_ID"]
    api_key = os.environ["CONDUCTOR_API_KEY"]
    ws_url = os.environ["BAND_WS_URL"]
    rest_url = os.environ["BAND_REST_URL"]

    log.info("connecting agent %s ...", agent_id)
    agent = Agent.create(
        adapter=NoopAdapter(),
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    await agent.start()
    log.info("connected OK")

    # hold the connection briefly so the platform registers presence, then leave
    await asyncio.sleep(3)
    await agent.stop()
    log.info("disconnected cleanly — smoke test passed")


if __name__ == "__main__":
    asyncio.run(main())
