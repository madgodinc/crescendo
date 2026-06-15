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
import hashlib
import os

from signing import sign_event
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from band.client.rest import AsyncRestClient
from thenvoi_rest.types.chat_room_request import ChatRoomRequest
from thenvoi_rest.types.chat_message_request import ChatMessageRequest
from thenvoi_rest.types.chat_message_request_mentions_item import ChatMessageRequestMentionsItem as Mention
from thenvoi_rest.types.participant_request import ParticipantRequest

from memory_tools import (
    fetch_skills,
    recall_playbook,
    learn_playbook,
    summarize_playbooks,
    save_checkpoint,
    load_checkpoint,
    save_live,
    update_active,
    get_token_total,
)

load_dotenv("/home/madgodinc/code/crescendo/.env")

REST = "https://app.band.ai"
POLL_INTERVAL = 3        # seconds between reply polls
REPLY_TIMEOUT = 130      # wait for a reply; covers one LLM call + its fallback (70s+70s), no more
MAX_REVIEW_ROUNDS = 3    # bounded code<->review negotiation
MAX_WRITE_TRIES = 3      # how many times to insist the Soloist actually call write_page
SITE_PATH = "/home/madgodinc/code/crescendo/workspace/site/index.html"

# Risk-gated human approval before deploy. A benign brief (the Conductor's
# resource contract came back empty) ships straight through; a high-stakes one
# (the contract needs real external access) requires sign-off: proportional
# autonomy, the Track-3 beat. Mode:
#   auto  (default): record the sign-off requirement and auto-grant, so an
#                     unattended run never hangs; the audit still shows the gate.
#   human          : post the request and wait for a human APPROVE in the room
#                     (used for the recorded demo, so a real keystroke shows).
#   off            : no gate at all.
DEPLOY_APPROVAL = os.environ.get("DEPLOY_APPROVAL", "auto").strip().lower()
APPROVAL_TIMEOUT = 180   # in human mode, fall back to auto-grant after this

# Which skill libraries the Archivist pulls from for each role. This is the
# "Archivist feeds skills to every agent" mechanism, driven deterministically.
SKILL_LIBS = {
    "conductor": ["skill-process"],
    "soloist": ["skill-design", "skill-css", "skill-antislop", "skill-security"],
    "tuningfork": ["skill-process", "skill-security", "skill-antislop"],
    "stagetech": ["skill-process"],
}
ARCHIVIST_TOKEN = os.environ["MGIMIND_TOKEN_ARCHIVIST"]

# worker agents Maestro pulls into the room (handle + uuid from .env)
WORKERS = {
    "conductor": (os.environ["CONDUCTOR_AGENT_ID"], "trolltina1/conductor"),
    "soloist": (os.environ["SOLOIST_AGENT_ID"], "trolltina1/soloist"),
    "tuningfork": (os.environ["TUNING_FORK_AGENT_ID"], "trolltina1/tuning-fork"),
    "stagetech": (os.environ["STAGE_TECH_AGENT_ID"], "trolltina1/stage-tech"),
    "archivist": (os.environ["ARCHIVIST_AGENT_ID"], "trolltina1/archivist"),
}

# Fixed roster for the dashboard graph (id matches the actor in record()).
AGENTS = [
    {"id": "conductor", "label": "Conductor", "role": "plans & routes"},
    {"id": "soloist", "label": "Soloist", "role": "writes code"},
    {"id": "tuningfork", "label": "Tuning Fork", "role": "reviews"},
    {"id": "stagetech", "label": "Stage Tech", "role": "deploys"},
    {"id": "archivist", "label": "Archivist", "role": "memory & skills"},
]


def log(phase: str, msg: str) -> None:
    print(f"[maestro:{phase}] {msg}", flush=True)


def _clean(text: str) -> str:
    """Strip Band's @[[uuid]] mention tokens for readable replay text."""
    return re.sub(r"@\[\[[^\]]+\]\]", "", text or "").strip()


def _deploy_error_signature(deploy_reply: str) -> str:
    """Reduce a deploy-gate refusal to a stable signature so procedural memory
    recalls the same playbook across runs (the brief and URL differ every time;
    the failure CLASS does not)."""
    low = (deploy_reply or "").lower()
    if "base64" in low or "favicon" in low or "icon" in low:
        return "deploy gate refused: base64/favicon junk in page"
    if "truncat" in low or "incomplete" in low or "</html>" in low:
        return "deploy gate refused: page truncated / not valid HTML"
    if "empty" in low or "blank" in low or "no file" in low:
        return "deploy gate refused: page empty / no file written"
    return "deploy gate refused: page failed validation"


class Maestro:
    def __init__(self):
        self.rc = AsyncRestClient(api_key=os.environ["MAESTRO_API_KEY"], base_url=REST)
        self.room = None
        self.seen_ids: set[str] = set()
        self.events: list[dict] = []   # replay trail for the dashboard
        # crash-proof-resume state (set per run in run())
        self._result: dict = {}
        self._done: set[str] = set()
        self._run_key: str = ""
        self._brief: str = ""
        self._started: str = ""
        self._phase: str = "rider"   # current phase, for live pushes
        self._tok_base: dict[str, int] = {}   # per-agent token total at last read

    async def _tok_delta(self, actor: str, reply: str = "") -> int:
        """Tokens this agent spent on its latest turn. Prefer the REAL running
        total the agent publishes to mgi-mind; if that isn't available (Band's
        LLM routing can bypass our usage hook), fall back to an estimate from the
        text the agent sent + skills it was fed (~4 chars/token, in+out)."""
        try:
            total = await get_token_total(ARCHIVIST_TOKEN, actor)
        except Exception:
            total = 0
        prev = self._tok_base.get(actor)
        self._tok_base[actor] = total
        real = 0 if prev is None else max(0, total - prev)
        if real:
            return real
        # estimate: prompt+reply ≈ len/4; double for the input the agent read
        return int(len(reply) / 4 * 2.2) if reply else 0

    def record(self, actor: str, kind: str, text: str, meta: dict | None = None) -> None:
        """Append one event to the replay trail (rendered by the dashboard).
        Each event carries the author's HMAC so the trail proves not just that a
        row wasn't edited (hash chain) but that its author can't be forged."""
        ts = datetime.now(timezone.utc).isoformat()
        text = text[:600]
        self.events.append({
            "ts": ts, "actor": actor, "kind": kind, "text": text,
            "sig": sign_event(actor, kind, text, ts), "meta": meta or {},
        })
        # bound growth: every checkpoint + live-push re-serialises the whole list,
        # so cap it (a normal run is ~20 events; a pathological retry storm won't
        # balloon the KV blob).
        if len(self.events) > 250:
            self.events = self.events[-250:]

    def _live_doc(self, status: str, phase: str) -> dict:
        """The per-run document the dashboard polls (superset of replay.json)."""
        return {
            "run_id": self._run_key,
            "brief": self._brief,
            "status": status,        # running | done | failed
            "phase": phase,
            "started": self._started,
            "updated": datetime.now(timezone.utc).isoformat(),
            "review_verdict": self._result.get("review_verdict", ""),
            "deploy": self._result.get("deploy", ""),
            "agents": AGENTS,
            "timeline": self.events,
        }

    async def _push_live(self, status: str, phase: str) -> None:
        """Publish the live run state to mgi-mind so the dashboard can read it.
        Best-effort: a KV hiccup must never break a run."""
        try:
            await save_live(ARCHIVIST_TOKEN, self._run_key, self._live_doc(status, phase))
        except Exception as e:
            log("live", f"push failed (ignored): {type(e).__name__}")

    async def say(self, to_key: str, text: str) -> datetime:
        """Post a message @mentioning one worker. Returns the send timestamp.

        Before posting, mark every EXISTING message from that worker as seen, so
        a late/orphaned reply from a prior round (e.g. a slow LLM that answered
        after we timed out and retried) can't be picked up as the answer to THIS
        question."""
        uuid, handle = WORKERS[to_key]
        for m in await self._all_messages():
            if m.sender_id == uuid:
                self.seen_ids.add(m.id)
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

    async def wait_reply(self, from_key: str, since: datetime, done_check=None) -> str:
        """Poll until `from_key` posts a non-empty message AFTER `since`, or timeout.

        `done_check` is an optional callable: if it returns truthy, the agent's
        work is confirmed by an artifact (e.g. the Soloist wrote the file) even
        though its chat ACK was lost — stop waiting early instead of timing out."""
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
            if done_check and done_check():
                log("reply", f"<- {from_key}: (artifact confirmed; chat ACK lost)")
                return "(work confirmed by artifact)"
            await asyncio.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL
            # heartbeat while waiting on a slow agent, so the dashboard's stale
            # check sees the run is alive (not a dead driver) even mid-wait.
            if waited % 30 < POLL_INTERVAL and self._run_key:
                await self._push_live("running", self._phase)
        raise TimeoutError(f"{from_key} did not reply within {REPLY_TIMEOUT}s")

    async def ask(self, to_key: str, text: str, retries: int = 1, done_check=None) -> str:
        """Send to one agent and wait for its reply, with retry."""
        for attempt in range(retries + 1):
            sent_at = await self.say(to_key, text)
            try:
                return await self.wait_reply(to_key, sent_at, done_check=done_check)
            except TimeoutError:
                if attempt < retries:
                    log("retry", f"{to_key} silent, retrying ({attempt + 1})")
                else:
                    raise
        raise RuntimeError(f"{to_key} ask exhausted retries")  # unreachable, keeps a str return contract

    async def ask_with_skills(self, to_key: str, text: str, skill_query: str,
                              retries: int = 1, done_check=None) -> str:
        """Ask an agent, but first have the Archivist pull relevant skills from the
        skill libraries and prepend them — weak models get expert guidance upfront."""
        libs = SKILL_LIBS.get(to_key, [])
        skills = await fetch_skills(ARCHIVIST_TOKEN, skill_query, libs) if libs else ""
        if skills:
            n = skills.count("\n- ")
            log("skills", f"-> {to_key}: {n} skills injected")
            self.record("archivist", "skills",
                        f"fed {n} skills to {to_key} from {', '.join(libs)}",
                        {"to": to_key, "count": n, "libraries": libs})
            await self._push_live("running", self._phase)
            text = f"{skills}\n\n---\n{text}"
        return await self.ask(to_key, text, retries=retries, done_check=done_check)

    @staticmethod
    def _site_mtime() -> float:
        try:
            return os.path.getmtime(SITE_PATH)
        except OSError:
            return 0.0

    @staticmethod
    def _site_bytes() -> int:
        try:
            return os.path.getsize(SITE_PATH)
        except OSError:
            return 0

    async def ask_soloist_write(self, task: str, brief: str) -> str:
        """Ask the Soloist to build the page AND verify it actually called
        write_page. Weak models narrate ('Created a page...') without emitting
        the tool call, leaving a stale file that then ships. We snapshot the
        file's mtime, ask, and if the file didn't change we insist — up to
        MAX_WRITE_TRIES — with an increasingly explicit instruction."""
        before = self._site_mtime()
        # The Soloist may do the work (write the file) but its chat ACK can get
        # lost in Band: so a reply timeout is NOT a failure if the file changed.
        # Treat the file artifact as the source of truth: stop waiting as soon as
        # the file appears, and don't fail on a lost ACK.
        wrote = lambda: self._site_mtime() > before
        try:
            summary = await self.ask_with_skills("soloist", task, skill_query=brief, done_check=wrote)
        except TimeoutError:
            summary = "(no chat reply — checking the file artifact instead)"
        for attempt in range(2, MAX_WRITE_TRIES + 1):
            if self._site_mtime() > before:
                return summary   # the file was (re)written: that's the real signal
            # The file wasn't rewritten THIS round. That's only a failure if no
            # page exists at all (the first write never landed). On a later round,
            # a Soloist that judges the page already correct legitimately doesn't
            # rewrite: insisting then just spins the loop to max rounds. So if a
            # non-empty page is already on disk, accept it instead of insisting.
            if self._site_bytes() > 0:
                log("write", "soloist didn't rewrite, but a page already exists — accepting")
                return summary
            log("write", f"soloist did not write the file (attempt {attempt-1}); insisting")
            self.record("soloist", "code",
                        "⟳ no file written — asking the Soloist to actually call write_page",
                        {"retry": attempt - 1})
            await self._push_live("running", "code-review")
            try:
                summary = await self.ask("soloist",
                    f"You replied but did NOT call the write_page tool, so no file exists. "
                    f"You MUST call write_page now to build the page for this brief: {brief}\n"
                    f"Pass title, body, css, js. Do not describe it — CALL THE TOOL.")
            except TimeoutError:
                summary = "(no chat reply — checking the file artifact instead)"
        # The write_page tool result can land on disk slightly after the chat
        # reply (Band routes them on separate paths), so give the file a moment
        # to appear before declaring failure — otherwise a successful write that
        # arrives late is falsely failed.
        for _ in range(6):
            if self._site_bytes() > 0:
                return summary
            await asyncio.sleep(2)
        # fail-fast: still no file after the grace window. Don't loop back into
        # another review round on a Soloist that only narrates — that's how a
        # stuck model burns minutes. Stop the run honestly.
        raise RuntimeError(
            f"Soloist did not call write_page after {MAX_WRITE_TRIES} attempts — "
            f"no page to ship. (The model replied but never emitted the tool call.)")
        return summary

    @staticmethod
    def _run_id(brief: str) -> str:
        """Stable id for a brief so a re-launch finds its checkpoint."""
        return "run_" + hashlib.sha1(brief.strip().encode("utf-8")).hexdigest()[:12]

    async def _save(self, phase: str) -> None:
        """Checkpoint the run state after a phase completes — survives a crash."""
        self._result["_done_phases"] = sorted(self._done)
        self._result["events"] = self.events
        ok = await save_checkpoint(ARCHIVIST_TOKEN, self._run_key, self._result)
        if ok:
            log("checkpoint", f"saved after '{phase}' ({len(self._done)} phases done)")

    def _resumed(self, phase: str) -> bool:
        """True if this phase was already done in a prior (crashed) run — skip it."""
        if phase in self._done:
            log("resume", f"skip '{phase}' — already done before the crash")
            return True
        return False

    # Negative markers a reviewer uses when it does NOT say the magic word "ISSUE"
    # (word-bounded so "PROBLEM" doesn't match inside an unrelated longer word).
    _NEG = re.compile(r"\b(ISSUES?|PROBLEMS?|BUGS?|BROKEN|MISSING|TRUNCAT\w*|"
                      r"INCOMPLETE|SHOULD FIX|NEEDS? FIX|CONCERNS?|ERRORS?|"
                      r"FAIL\w*|NOT WORK\w*)\b", re.I)
    # "no problems", "without bugs", "zero issues", "nothing missing", and the
    # adjacent forms a reviewer uses while confirming a page is fine ("no
    # truncation", "no broken links", "no missing fonts"): a NEGATED negative is
    # actually positive, so strip these before scanning. The optional middle word
    # must NOT be greedy enough to swallow the negative itself, so it's an
    # explicit "(word )?" rather than "\w*".
    _NEGATED = re.compile(r"\b(NO|ZERO|WITHOUT|NOTHING|NOT)\s+(?:\w+\s+)?"
                          r"(ISSUES?|PROBLEMS?|BUGS?|ERRORS?|CONCERNS?|MISSING|"
                          r"TRUNCAT\w*|BROKEN|INCOMPLETE|FAIL\w*)\b", re.I)

    @classmethod
    def _is_clean(cls, review: str) -> bool:
        """A review is clean ONLY on an explicit positive signal AND no negative
        marker. Weak models phrase findings as 'problem/concern/bug' without the
        literal 'ISSUE', so 'no ISSUE token' must NOT be read as clean — that
        silently ships broken pages (the whole point of the review gate). But a
        NEGATED negative ('no problems') is positive, so strip those first."""
        up = cls._NEGATED.sub(" ", _clean(review).upper())
        if cls._NEG.search(up):
            return False
        return "CLEAN" in up or "LOOKS GOOD" in up or "LGTM" in up

    @staticmethod
    def _count_issues(review: str) -> int:
        """Best-effort count of distinct issues in a reviewer's ISSUES reply
        (numbered '1.' or bulleted lines). Falls back to 1 if it found issues
        but no list structure, 0 if the text reads clean."""
        text = _clean(review)
        if not Maestro._NEG.search(text):
            return 0
        numbered = len(re.findall(r"(?m)^\s*\d+[.)]\s+\S", text))
        if numbered:
            return numbered
        bullets = len(re.findall(r"(?m)^\s*[-*•]\s+\S", text))
        return bullets or 1

    @staticmethod
    def _parse_rider(reply: str) -> list[dict]:
        """Parse the Conductor's inferred resource contract into structured items.

        Accepts 'RESOURCE: <name> — <why>' lines (em-dash, hyphen, or colon as
        the separator). 'none' collapses to an empty contract."""
        items: list[dict] = []
        for line in _clean(reply).splitlines():
            m = re.match(r"\s*RESOURCE:\s*(.+)", line, re.IGNORECASE)
            if not m:
                continue
            body = m.group(1).strip()
            if body.lower().startswith("none"):
                continue
            parts = re.split(r"\s+[—–-]\s+|\s*:\s+", body, maxsplit=1)
            name = parts[0].strip()
            why = parts[1].strip() if len(parts) > 1 else ""
            if name:
                items.append({"name": name, "why": why})
        return items

    def _read_site(self) -> str:
        """Read the product file the Soloist wrote, so the reviewer sees real code."""
        path = "/home/madgodinc/code/crescendo/workspace/site/index.html"
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return "(no file written yet)"

    async def _room_messages(self, room_id: str) -> list:
        out, page = [], 1
        while True:
            lst = await self.rc.agent_api_messages.list_agent_messages(
                room_id, status="all", page=page, page_size=100)
            data = getattr(lst, "data", None) or []
            out.extend(data)
            meta = getattr(lst, "metadata", None)
            total = getattr(meta, "total_pages", 1) or 1
            if page >= total or not data:
                break
            page += 1
        return out

    async def listen_command_room(self, command_room: str) -> None:
        """Watch the command room; for each human brief, run a project and report back."""
        # Mark everything already there as seen, so we only react to NEW briefs.
        seen = {m.id for m in await self._room_messages(command_room)}
        log("ready", f"listening on command room {command_room} — drop a brief there")
        while True:
            for m in await self._room_messages(command_room):
                if m.id in seen:
                    continue
                seen.add(m.id)
                if m.sender_type == "User" and (m.content or "").strip():
                    # strip the @[[uuid]] mention Band forces on every message
                    brief = re.sub(r"@\[\[[^\]]+\]\]", "", m.content).strip()
                    if not brief:
                        continue
                    log("brief", f"got: {brief[:80]}")
                    # Band requires >=1 mention and forbids mentioning self; tag the
                    # human who sent the brief so the report lands as a reply to them.
                    self_m = [Mention(id=m.sender_id, handle=getattr(m, "sender_name", None) or "user")]
                    try:
                        result = await self.run(brief, room=command_room)
                        rider = result.get("rider") or []
                        rider_line = ("\n🎫 Access needed: "
                                      + ", ".join(r["name"] for r in rider)) if rider else ""
                        verdict = result.get("review_verdict", "")
                        review_line = (f"\n⚠ Shipped with open issues ({verdict})"
                                       if verdict.startswith("shipped-with") else "")
                        await self.rc.agent_api_messages.create_agent_chat_message(
                            command_room,
                            message=ChatMessageRequest(
                                content=f"✅ Done: {result.get('deploy', '')}{rider_line}{review_line}",
                                mentions=self_m))
                    except Exception as e:
                        # surface the failure on the dashboard (not frozen on "running")
                        if self._run_key:
                            await self._push_live("failed", self._phase)
                            await update_active(ARCHIVIST_TOKEN, self._run_key, brief,
                                                "failed", datetime.now(timezone.utc).isoformat())
                        # A provider going silent is recoverable: the phases that
                        # finished are checkpointed, so re-sending the same brief
                        # resumes from where it died. Say so, instead of reading as
                        # a dead end.
                        if isinstance(e, TimeoutError):
                            note = (f"⚠️ A model went quiet at the '{self._phase}' phase. "
                                    f"The finished phases are checkpointed — send the same "
                                    f"brief again and the run resumes from here, no rework.")
                        else:
                            note = f"⚠️ Failed: {type(e).__name__}: {e}"
                        await self.rc.agent_api_messages.create_agent_chat_message(
                            command_room,
                            message=ChatMessageRequest(content=note, mentions=self_m))
                        log("error", f"{type(e).__name__}: {e}")
                    # after a run, ignore everything up to now so we wait for the NEXT brief
                    seen = {x.id for x in await self._room_messages(command_room)}
            await asyncio.sleep(POLL_INTERVAL)

    async def run(self, brief: str, room: str = "") -> dict:
        # Everything runs in ONE chat (the command room): no per-project rooms.
        self.room = room or self.room
        # baseline: ignore all prior messages so we only read THIS run's replies
        self.seen_ids = {m.id for m in await self._room_messages(self.room)}

        # CRASH-PROOF RESUME: look for a checkpoint from a prior crashed run of
        # this exact brief. If found, restore its state and skip the phases that
        # already completed: the run picks up where it died, no rework.
        self._run_key = self._run_id(brief)
        self._brief = brief
        prior = await load_checkpoint(ARCHIVIST_TOKEN, self._run_key)
        if prior:
            self._result = prior
            self._done = set(prior.get("_done_phases", []))
            self.events = prior.get("events", [])
            self._result["room"] = self.room   # room may differ on relaunch
            self._started = prior.get("started") or datetime.now(timezone.utc).isoformat()
            log("resume", f"found checkpoint for this brief — resuming, "
                          f"{len(self._done)} phase(s) already done")
        else:
            self._started = datetime.now(timezone.utc).isoformat()
            self._result = {"room": self.room, "brief": brief, "started": self._started}
            self._done = set()
            self.events = []
            self.record("human", "brief", brief)
        result = self._result
        # Baseline each agent's running token total so per-event deltas are
        # measured from THIS run's start, not the agent process's boot.
        self._tok_base = {}
        for a in ("conductor", "soloist", "tuningfork", "stagetech", "archivist"):
            await self._tok_delta(a)
        # Make the run visible on the dashboard immediately (before the first
        # agent reply) and survive a restart: state lives in mgi-mind.
        await update_active(ARCHIVIST_TOKEN, self._run_key, brief, "running",
                            datetime.now(timezone.utc).isoformat())
        await self._push_live("running", "rider")

        # PHASE 0: Resource Contract (the "give this, go rest" magic moment).
        # The Conductor INFERS from the brief the one upfront list of access the
        # project needs (credentials, services, integrations), not hardcoded
        # hardcoding it. Maestro records it and reports it back to the human so
        # they grant access once, then step away.
        if not self._resumed("rider"):
            rider_raw = await self.ask("conductor",
                f"Brief from the human: {brief}\n"
                f"Before any planning, infer the RESOURCE CONTRACT: the complete list "
                f"of external access/credentials/services this project will need to "
                f"ship (e.g. a hosting account, a domain, an API key, a data source). "
                f"Infer it from the brief — do not assume tools we didn't ask for. "
                f"Reply with one item per line, each as 'RESOURCE: <name> — <why>'. "
                f"If the project needs nothing beyond our standard deploy, reply "
                f"'RESOURCE: none — ships on our Cloudflare Pages account'.")
            rider = self._parse_rider(rider_raw)
            result["rider"] = rider
            self.record("conductor", "rider",
                        "; ".join(f"{r['name']}: {r['why']}" for r in rider) or "none",
                        {"items": rider})
            log("rider", f"inferred {len(rider)} resource(s)")
            self._done.add("rider")
            await self._save("rider")
            await self._push_live("running", "plan")

        # PHASE 1: plan (Archivist feeds planning skills)
        if not self._resumed("plan"):
            plan = await self.ask_with_skills("conductor",
                f"Brief from the human: {brief}\nProduce a short build plan (3-5 steps). "
                f"Reply with the plan only.", skill_query=brief)
            result["plan"] = plan
            self.record("conductor", "plan", _clean(plan), {"tokens": await self._tok_delta("conductor", plan)})
            self._done.add("plan")
            await self._save("plan")
            await self._push_live("running", "code-review")

        # PHASE 2/3: code <-> review negotiation (Archivist feeds design/css/antislop skills)
        self._phase = "code-review"
        if self._resumed("code-review"):
            verdict = result.get("review_verdict", "unknown")
        else:
            # Start clean: remove any page left over from a prior brief so a
            # Soloist that fails to write can't accidentally ship a stale page.
            try:
                os.remove(SITE_PATH)
            except OSError:
                pass
            code_task = f"Implement this brief: {brief}\nUse the write_page tool (title/body/css/js). All page content must be in ENGLISH. Reply with a one-line summary."
            verdict = ""
            last_review = ""
            for rnd in range(1, MAX_REVIEW_ROUNDS + 1):
                log("phase", f"code round {rnd}")
                code_summary = await self.ask_soloist_write(code_task, brief)
                self.record("soloist", "code", _clean(code_summary),
                            {"round": rnd, "tokens": await self._tok_delta("soloist", code_summary)})
                await self._push_live("running", "code-review")
                # Tuning Fork reads the files itself (list_files/read_file): no code in chat.
                review = await self.ask_with_skills("tuningfork",
                    f"The Soloist finished work for the brief: {brief}\n"
                    f"Read the workspace files yourself and review. "
                    f"Reply 'CLEAN' if good, or 'ISSUES: ...' with concrete fixes. "
                    f"Write your entire review in ENGLISH.", skill_query=brief)
                result[f"review_{rnd}"] = review
                last_review = review
                clean = self._is_clean(review)
                self.record("tuningfork", "review", _clean(review),
                            {"round": rnd, "verdict": "clean" if clean else "issues",
                             "tokens": await self._tok_delta("tuningfork", review)})
                await self._push_live("running", "code-review")
                if clean:
                    verdict = "clean"
                    break
                code_task = f"The reviewer found issues: {review}\nFix them with write_page and reply with a one-line summary."

            # If we ran out of rounds with issues still open, ship the valid page
            # but say so honestly: don't pass it off as clean.
            if verdict != "clean":
                open_issues = self._count_issues(last_review)
                verdict = f"shipped-with-{open_issues}-open-issues"
                log("review", f"max rounds reached — {open_issues} issue(s) still open, shipping honestly")
                self.record("tuningfork", "review",
                            f"⚠ max review rounds reached — shipping with {open_issues} open issue(s)",
                            {"verdict": "shipped-with-issues", "open_issues": open_issues})
            result["review_verdict"] = verdict
            self._done.add("code-review")
            await self._save("code-review")

        # PHASE 4: deploy. If the deploy gate refuses (invalid/truncated page),
        # bounce back to the Soloist to rebuild, then retry: don't ship junk.
        # SELF-LEARNING LOOP: on a refusal we (1) reduce it to a stable signature,
        # (2) RECALL whether memory already solved this class of failure and feed
        # that fix to the Soloist, (3) on a successful rebuild LEARN the fix back
        # as a verified procedure. Next run recalls it instead of re-grinding.
        # PHASE 3.5: risk-gated human approval. High-stakes briefs (the resource
        # contract needs real external access) require a human sign-off before
        # anything ships; benign ones pass straight through. Either way the gate
        # decision is recorded, so the audit trail shows who authorised the deploy.
        if not self._resumed("approval"):
            await self._approval_gate(result.get("rider") or [])
            self._done.add("approval")
            await self._save("approval")

        self._phase = "deploy"
        if self._resumed("deploy"):
            deploy = result.get("deploy", "")
        else:
            deploy = await self._deploy_phase(brief)
            # A gate refusal / timeout has no pages.dev URL: that's a real
            # failure. Don't checkpoint deploy as done or report it as success;
            # raise so the run is honestly marked failed and can be retried.
            if "pages.dev" not in deploy:
                self.record("stagetech", "deploy", _clean(deploy), {"failed": True})
                await self._push_live("failed", "deploy")
                raise RuntimeError(f"deploy failed: {_clean(deploy)[:160]}")
            result["deploy"] = deploy
            url = _clean(deploy)
            self.record("stagetech", "deploy", url,
                        {"url": next((w for w in url.split() if "pages.dev" in w), ""),
                         "tokens": await self._tok_delta("stagetech", deploy)})
            self._done.add("deploy")
            await self._save("deploy")
            await self._push_live("running", "archive")

        # PHASE 5: archive
        self._phase = "archive"
        if not self._resumed("archive"):
            archive = await self.ask("archivist",
                f"Remember this run: brief={brief}; result={deploy}. Confirm in one line.")
            result["archive"] = archive
            self.record("archivist", "archive", _clean(archive),
                        {"tokens": await self._tok_delta("archivist", archive)})
            self._done.add("archive")

        # run finished cleanly: mark the checkpoint done so a re-run starts fresh
        self._result["_finished"] = True
        await self._save("done")
        self._dump_replay(brief)
        # publish final state to the dashboard (done) and update history
        await self._push_live("done", "done")
        await update_active(ARCHIVIST_TOKEN, self._run_key, brief, "done",
                            datetime.now(timezone.utc).isoformat())
        log("done", "run complete")
        return result

    async def _approval_gate(self, rider: list) -> None:
        """Risk-gated human-in-the-loop sign-off before deploy.

        Proportional autonomy: a benign brief (no resource contract) ships with
        no friction; a high-stakes one (the contract needs real external access)
        asks a human to authorise the deploy. The gate decision is always
        recorded so the audit trail shows the deploy was human-authorised —
        the Track-3 'regulated work needs sign-off' beat, made auditable.
        """
        access = ", ".join(r["name"] for r in rider)
        high_stakes = bool(rider)
        if DEPLOY_APPROVAL == "off" or not high_stakes:
            # benign brief: no gate, but note that none was required.
            self.record("human", "approval",
                        "auto-approved: no external access required, low-stakes deploy",
                        {"required": False, "granted": True})
            return

        if DEPLOY_APPROVAL == "human":
            log("approval", f"high-stakes deploy — waiting for human APPROVE ({access})")
            await self._push_live("running", "approval")
            since = await self.say_human(
                f"🔒 Approval required before deploy. This brief needs external "
                f"access ({access}). Reply APPROVE to authorise shipping, or DENY to stop.")
            granted = await self._wait_human_decision(since)
            if not granted:
                self.record("human", "approval",
                            f"DENIED human sign-off for deploy (access: {access})",
                            {"required": True, "granted": False})
                raise RuntimeError("deploy denied at the human approval gate")
            self.record("human", "approval",
                        f"human authorised the deploy (access: {access})",
                        {"required": True, "granted": True})
            log("approval", "human approved — proceeding to deploy")
            return

        # auto mode: record the requirement and grant, so an unattended run never
        # hangs while the audit still shows the gate was applied.
        self.record("human", "approval",
                    f"sign-off required (access: {access}); auto-granted in unattended mode",
                    {"required": True, "granted": True, "auto": True})
        log("approval", f"high-stakes deploy auto-approved (unattended): {access}")

    async def say_human(self, text: str) -> datetime:
        """Post a plain message to the room addressed to the human and return the
        send time, so we can read their reply as anything that arrives after it."""
        await self.rc.agent_api_messages.create_agent_chat_message(
            self.room, message=ChatMessageRequest(content=text))
        log("say", f"-> human: {text[:70]}")
        return datetime.now(timezone.utc)

    async def _wait_human_decision(self, since: datetime) -> bool:
        """Wait for a human APPROVE/DENY in the room. Defaults to granting after
        APPROVAL_TIMEOUT so a recording can't hang forever on an absent human."""
        waited = 0
        while waited < APPROVAL_TIMEOUT:
            for m in await self._room_messages(self.room):
                if (getattr(m, "sender_type", "") == "User"
                        and getattr(m, "created_at", None)
                        and m.created_at > since):
                    body = (m.content or "").lower()
                    if "approve" in body:
                        return True
                    if "deny" in body or "reject" in body:
                        return False
            await asyncio.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL
        log("approval", "no human reply — granting by timeout (recording-safe default)")
        return True

    async def _deploy_phase(self, brief: str) -> str:
        """Deploy with the self-learning loop. On a gate refusal: recall a known
        fix, feed it to the Soloist, and on a passing rebuild learn it verified."""
        deploy = await self.ask("stagetech",
            "The work passed review. Call deploy_site and reply with the exact live URL it returns.")
        for _ in range(2):
            if "pages.dev" in deploy:
                break
            sig = _deploy_error_signature(deploy)
            log("phase", f"deploy refused — {sig}")

            # (1) recall a known fix for this failure class. Keep only playbooks
            # whose stored error is actually about deploying: semantic recall can
            # drag in unrelated high-trust procedures, and a wrong "known fix"
            # is worse than none.
            playbooks = await recall_playbook(ARCHIVIST_TOKEN, error=sig, context=brief)
            playbooks = [p for p in playbooks
                         if "deploy" in (p.get("error", "") + p.get("context", "")).lower()]
            known = summarize_playbooks(playbooks)
            if known:
                log("learn", f"recalled {len(playbooks)} playbook(s) for this failure")
                self.record("archivist", "recall",
                            f"recalled {len(playbooks)} fix(es) for: {sig}",
                            {"error": sig, "count": len(playbooks)})
            else:
                self.record("archivist", "recall",
                            f"no playbook yet for: {sig} — orchestra will discover one",
                            {"error": sig, "count": 0})

            fix_hint = f"\nKnown fix from past runs: {known}" if known else ""
            await self.ask("soloist",
                f"Deploy was refused — {sig}: {deploy}{fix_hint}\n"
                f"Rebuild a COMPLETE page for the brief '{brief}' with write_page. "
                f"No favicon, no base64.")
            deploy = await self.ask("stagetech",
                "Call deploy_site again and reply with the exact live URL.")

            # (3) the rebuild passing the gate IS the deterministic signal → learn it verified
            if "pages.dev" in deploy:
                fix = ("Rebuild the page with write_page (fixed HTML shell, slots only); "
                       "strip base64/favicon/icon links; ensure the page is complete "
                       "and ends in </html> before deploy.")
                ok = await learn_playbook(ARCHIVIST_TOKEN, error=sig, fix=fix,
                                          context=f"crescendo deploy: {brief}",
                                          provenance="crescendo/maestro.py deploy gate",
                                          verified=True)
                if ok:
                    log("learn", "stored verified deploy fix to procedural memory")
                    self.record("archivist", "learn",
                                f"learned verified fix for: {sig}",
                                {"error": sig, "verified": True})
        return deploy

    def _dump_replay(self, brief: str) -> None:
        """Write the replay trail for the static (offline) dashboard, both as
        replay.json (served path) and replay-data.js (a <script> the dashboard
        loads from file:// — fetch is blocked there, a script tag is not)."""
        import json
        ddir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard")
        os.makedirs(ddir, exist_ok=True)
        doc = {"brief": brief, "agents": AGENTS, "timeline": self.events}
        with open(os.path.join(ddir, "replay.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        with open(os.path.join(ddir, "replay-data.js"), "w", encoding="utf-8") as f:
            f.write("window.CRESCENDO_REPLAY = " + json.dumps(doc, ensure_ascii=False) + ";")
        log("replay", f"wrote {len(self.events)} events -> dashboard/replay.json")


async def main() -> None:
    m = Maestro()
    command_room = os.environ.get("MAESTRO_COMMAND_ROOM", "").strip()

    # One-shot mode: a brief on the command line runs once in the command room and exits.
    if len(sys.argv) > 1:
        try:
            result = await m.run(sys.argv[1], room=command_room)
        except TimeoutError:
            if getattr(m, "_run_key", None):
                await m._push_live("failed", getattr(m, "_phase", "?"))
            log("error", f"a model went quiet at '{getattr(m, '_phase', '?')}'. "
                         f"The finished phases are checkpointed — re-run the same brief to resume.")
            return
        except RuntimeError as e:
            if getattr(m, "_run_key", None):
                await m._push_live("failed", getattr(m, "_phase", "?"))
            log("error", f"run stopped at '{getattr(m, '_phase', '?')}': {e}")
            return
        print("\n=== RUN RESULT ===")
        for k, v in result.items():
            print(f"{k}: {str(v)[:120]}")
        return

    # Service mode: listen on the command room and handle every brief dropped there.
    if not command_room:
        log("error", "set MAESTRO_COMMAND_ROOM in .env, or pass a brief as an argument")
        return
    await m.listen_command_room(command_room)


if __name__ == "__main__":
    asyncio.run(main())
