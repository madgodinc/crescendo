"""Maestro — the deterministic orchestrator for Crescendo.

Maestro is a Band agent, but it does NOT react to chat. It drives the run from
Python: it creates a room, pulls in the five worker agents, then walks fixed
phases (plan -> code -> review -> deploy -> archive). At each phase it @mentions
one agent and waits for that specific agent's reply (polling by sender_id since a
timestamp), with retries. A bounded code<->review negotiation loop lets the
Tuning Fork send work back to the Soloist — real collaboration through Band,
under deterministic control.

Run: uv run python maestro.py "your brief here"
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from band.client.rest import AsyncRestClient
from thenvoi_rest.types.chat_room_request import ChatRoomRequest
from thenvoi_rest.types.chat_message_request import ChatMessageRequest
from thenvoi_rest.types.chat_message_request_mentions_item import ChatMessageRequestMentionsItem as Mention
from thenvoi_rest.types.participant_request import ParticipantRequest

load_dotenv("/home/madgodinc/code/crescendo/.env")

REST = "https://app.band.ai"
POLL_INTERVAL = 3        # seconds between reply polls
REPLY_TIMEOUT = 120      # seconds to wait for an agent's reply
MAX_REVIEW_ROUNDS = 2    # bounded code<->review negotiation

# worker agents Maestro pulls into the room (handle + uuid from .env)
WORKERS = {
    "conductor": (os.environ["CONDUCTOR_AGENT_ID"], "trolltina1/conductor"),
    "soloist": (os.environ["SOLOIST_AGENT_ID"], "trolltina1/soloist"),
    "tuningfork": (os.environ["TUNING_FORK_AGENT_ID"], "trolltina1/tuning-fork"),
    "stagetech": (os.environ["STAGE_TECH_AGENT_ID"], "trolltina1/stage-tech"),
    "archivist": (os.environ["ARCHIVIST_AGENT_ID"], "trolltina1/archivist"),
}


def log(phase: str, msg: str) -> None:
    print(f"[maestro:{phase}] {msg}", flush=True)


class Maestro:
    def __init__(self):
        self.rc = AsyncRestClient(api_key=os.environ["MAESTRO_API_KEY"], base_url=REST)
        self.room = None
        self.seen_ids: set[str] = set()

    async def open_room(self) -> str:
        room = await self.rc.agent_api_chats.create_agent_chat(chat=ChatRoomRequest())
        self.room = room.data.id
        log("setup", f"room {self.room}")
        for key, (uuid, handle) in WORKERS.items():
            await self.rc.agent_api_participants.add_agent_chat_participant(
                self.room, participant=ParticipantRequest(participant_id=uuid, role="member"))
            log("setup", f"added {key}")
        return self.room

    async def say(self, to_key: str, text: str) -> datetime:
        """Post a message @mentioning one worker. Returns the send timestamp."""
        uuid, handle = WORKERS[to_key]
        await self.rc.agent_api_messages.create_agent_chat_message(
            self.room,
            message=ChatMessageRequest(
                content=f"@{handle.split('/')[-1]} {text}",
                mentions=[Mention(id=uuid, handle=handle)],
            ),
        )
        sent_at = datetime.now(timezone.utc)
        log("say", f"-> {to_key}: {text[:70]}")
        return sent_at

    async def _all_messages(self) -> list:
        """Fetch every message in the room across all pages."""
        out, page = [], 1
        while True:
            lst = await self.rc.agent_api_messages.list_agent_messages(
                self.room, status="all", page=page, page_size=100)
            data = getattr(lst, "data", None) or []
            out.extend(data)
            meta = getattr(lst, "metadata", None)
            total = getattr(meta, "total_pages", 1) or 1
            if page >= total or not data:
                break
            page += 1
        return out

    async def wait_reply(self, from_key: str, since: datetime) -> str:
        """Poll until `from_key` posts a non-empty message AFTER `since`, or timeout."""
        uuid, _ = WORKERS[from_key]
        waited = 0
        while waited < REPLY_TIMEOUT:
            items = await self._all_messages()
            fresh = [m for m in items
                     if m.sender_id == uuid
                     and (m.content or "").strip()
                     and m.inserted_at and m.inserted_at > since
                     and m.id not in self.seen_ids]
            if fresh:
                fresh.sort(key=lambda x: x.inserted_at)
                m = fresh[0]
                self.seen_ids.add(m.id)
                log("reply", f"<- {from_key}: {m.content[:80]}")
                return m.content
            await asyncio.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL
        raise TimeoutError(f"{from_key} did not reply within {REPLY_TIMEOUT}s")

    async def ask(self, to_key: str, text: str, retries: int = 1) -> str:
        """Send to one agent and wait for its reply, with retry."""
        for attempt in range(retries + 1):
            sent_at = await self.say(to_key, text)
            try:
                return await self.wait_reply(to_key, sent_at)
            except TimeoutError:
                if attempt < retries:
                    log("retry", f"{to_key} silent, retrying ({attempt + 1})")
                else:
                    raise

    async def run(self, brief: str) -> dict:
        await self.open_room()
        result = {"room": self.room, "brief": brief}

        # PHASE 1 — plan
        plan = await self.ask("conductor",
            f"Brief from the human: {brief}\nProduce a short build plan (3-5 steps). "
            f"Reply with the plan only.")
        result["plan"] = plan

        # PHASE 2/3 — code <-> review negotiation
        code_task = f"Implement this brief: {brief}\nUse write_file to save a single self-contained index.html. Reply with a one-line summary."
        verdict = ""
        for rnd in range(1, MAX_REVIEW_ROUNDS + 1):
            log("phase", f"code round {rnd}")
            code_summary = await self.ask("soloist", code_task)
            review = await self.ask("tuningfork",
                f"Review the Soloist's latest work for the brief: {brief}\n"
                f"Soloist says: {code_summary}\nReply 'CLEAN' if good, or 'ISSUES: ...' with concrete fixes.")
            result[f"review_{rnd}"] = review
            if "CLEAN" in review.upper() or "ISSUE" not in review.upper():
                verdict = "clean"
                break
            code_task = f"The reviewer found issues: {review}\nFix them with write_file and reply with a one-line summary."
        result["review_verdict"] = verdict or "max rounds reached"

        # PHASE 4 — deploy
        deploy = await self.ask("stagetech",
            "The work passed review. Call deploy_site and reply with the exact live URL it returns.")
        result["deploy"] = deploy

        # PHASE 5 — archive
        archive = await self.ask("archivist",
            f"Remember this run: brief={brief}; result={deploy}. Confirm in one line.")
        result["archive"] = archive

        log("done", "run complete")
        return result


async def main() -> None:
    brief = sys.argv[1] if len(sys.argv) > 1 else "Make a simple web page with a click counter button."
    m = Maestro()
    try:
        result = await m.run(brief)
        print("\n=== RUN RESULT ===")
        for k, v in result.items():
            print(f"{k}: {str(v)[:120]}")
    except Exception as e:
        log("error", f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
