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
)

load_dotenv("/home/madgodinc/code/crescendo/.env")

REST = "https://app.band.ai"
POLL_INTERVAL = 3        # seconds between reply polls
REPLY_TIMEOUT = 200      # wait for a reply; allows the agent's LLM fallback (≤90s+90s) to complete
MAX_REVIEW_ROUNDS = 3    # bounded code<->review negotiation

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

    def record(self, actor: str, kind: str, text: str, meta: dict | None = None) -> None:
        """Append one event to the replay trail (rendered by the dashboard)."""
        self.events.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": actor, "kind": kind, "text": text[:600], "meta": meta or {},
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
        raise RuntimeError(f"{to_key} ask exhausted retries")  # unreachable, keeps a str return contract

    async def ask_with_skills(self, to_key: str, text: str, skill_query: str,
                              retries: int = 1) -> str:
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
        return await self.ask(to_key, text, retries=retries)

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
    # "no problems", "without bugs", "zero issues", "nothing missing" — a NEGATED
    # negative is actually positive, so strip these before scanning.
    _NEGATED = re.compile(r"\b(NO|ZERO|WITHOUT|NOTHING)\s+\w*\s*"
                          r"(ISSUES?|PROBLEMS?|BUGS?|ERRORS?|CONCERNS?|MISSING)\b", re.I)

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
                        rider_line = ("\n🎫 Нужен был доступ: "
                                      + ", ".join(r["name"] for r in rider)) if rider else ""
                        verdict = result.get("review_verdict", "")
                        review_line = (f"\n⚠ Зашло с открытыми замечаниями ({verdict})"
                                       if verdict.startswith("shipped-with") else "")
                        await self.rc.agent_api_messages.create_agent_chat_message(
                            command_room,
                            message=ChatMessageRequest(
                                content=f"✅ Готово: {result.get('deploy', '')}{rider_line}{review_line}",
                                mentions=self_m))
                    except Exception as e:
                        # surface the failure on the dashboard (not frozen on "running")
                        if self._run_key:
                            await self._push_live("failed", self._phase)
                            await update_active(ARCHIVIST_TOKEN, self._run_key, brief,
                                                "failed", datetime.now(timezone.utc).isoformat())
                        await self.rc.agent_api_messages.create_agent_chat_message(
                            command_room,
                            message=ChatMessageRequest(content=f"⚠️ Сбой: {type(e).__name__}: {e}", mentions=self_m))
                        log("error", f"{type(e).__name__}: {e}")
                    # after a run, ignore everything up to now so we wait for the NEXT brief
                    seen = {x.id for x in await self._room_messages(command_room)}
            await asyncio.sleep(POLL_INTERVAL)

    async def run(self, brief: str, room: str = "") -> dict:
        # Everything runs in ONE chat (the command room) — no per-project rooms.
        self.room = room or self.room
        # baseline: ignore all prior messages so we only read THIS run's replies
        self.seen_ids = {m.id for m in await self._room_messages(self.room)}

        # CRASH-PROOF RESUME: look for a checkpoint from a prior crashed run of
        # this exact brief. If found, restore its state and skip the phases that
        # already completed — the run picks up where it died, no rework.
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
        # Make the run visible on the dashboard immediately (before the first
        # agent reply) and survive a restart — state lives in mgi-mind.
        await update_active(ARCHIVIST_TOKEN, self._run_key, brief, "running",
                            datetime.now(timezone.utc).isoformat())
        await self._push_live("running", "rider")

        # PHASE 0 — Resource Contract (the "give this, go rest" magic moment).
        # The Conductor INFERS from the brief the one upfront list of access the
        # project needs — credentials, services, integrations — instead of us
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

        # PHASE 1 — plan (Archivist feeds planning skills)
        if not self._resumed("plan"):
            plan = await self.ask_with_skills("conductor",
                f"Brief from the human: {brief}\nProduce a short build plan (3-5 steps). "
                f"Reply with the plan only.", skill_query=brief)
            result["plan"] = plan
            self.record("conductor", "plan", _clean(plan))
            self._done.add("plan")
            await self._save("plan")
            await self._push_live("running", "code-review")

        # PHASE 2/3 — code <-> review negotiation (Archivist feeds design/css/antislop skills)
        self._phase = "code-review"
        if self._resumed("code-review"):
            verdict = result.get("review_verdict", "unknown")
        else:
            code_task = f"Implement this brief: {brief}\nUse the write_page tool (title/body/css/js). Reply with a one-line summary."
            verdict = ""
            last_review = ""
            for rnd in range(1, MAX_REVIEW_ROUNDS + 1):
                log("phase", f"code round {rnd}")
                code_summary = await self.ask_with_skills("soloist", code_task, skill_query=brief)
                self.record("soloist", "code", _clean(code_summary), {"round": rnd})
                await self._push_live("running", "code-review")
                # Tuning Fork reads the files itself (list_files/read_file) — no code in chat.
                review = await self.ask_with_skills("tuningfork",
                    f"The Soloist finished work for the brief: {brief}\n"
                    f"Read the workspace files yourself and review. "
                    f"Reply 'CLEAN' if good, or 'ISSUES: ...' with concrete fixes.", skill_query=brief)
                result[f"review_{rnd}"] = review
                last_review = review
                clean = self._is_clean(review)
                self.record("tuningfork", "review", _clean(review),
                            {"round": rnd, "verdict": "clean" if clean else "issues"})
                await self._push_live("running", "code-review")
                if clean:
                    verdict = "clean"
                    break
                code_task = f"The reviewer found issues: {review}\nFix them with write_page and reply with a one-line summary."

            # If we ran out of rounds with issues still open, ship the valid page
            # but say so honestly — don't pass it off as clean.
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

        # PHASE 4 — deploy. If the deploy gate refuses (invalid/truncated page),
        # bounce back to the Soloist to rebuild, then retry — don't ship junk.
        # SELF-LEARNING LOOP: on a refusal we (1) reduce it to a stable signature,
        # (2) RECALL whether memory already solved this class of failure and feed
        # that fix to the Soloist, (3) on a successful rebuild LEARN the fix back
        # as a verified procedure. Next run recalls it instead of re-grinding.
        self._phase = "deploy"
        if self._resumed("deploy"):
            deploy = result.get("deploy", "")
        else:
            deploy = await self._deploy_phase(brief)
            # A gate refusal / timeout has no pages.dev URL — that's a real
            # failure. Don't checkpoint deploy as done or report it as success;
            # raise so the run is honestly marked failed and can be retried.
            if "pages.dev" not in deploy:
                self.record("stagetech", "deploy", _clean(deploy), {"failed": True})
                await self._push_live("failed", "deploy")
                raise RuntimeError(f"deploy failed: {_clean(deploy)[:160]}")
            result["deploy"] = deploy
            url = _clean(deploy)
            self.record("stagetech", "deploy", url,
                        {"url": next((w for w in url.split() if "pages.dev" in w), "")})
            self._done.add("deploy")
            await self._save("deploy")
            await self._push_live("running", "archive")

        # PHASE 5 — archive
        self._phase = "archive"
        if not self._resumed("archive"):
            archive = await self.ask("archivist",
                f"Remember this run: brief={brief}; result={deploy}. Confirm in one line.")
            result["archive"] = archive
            self.record("archivist", "archive", _clean(archive))
            self._done.add("archive")

        # run finished cleanly — mark the checkpoint done so a re-run starts fresh
        self._result["_finished"] = True
        await self._save("done")
        self._dump_replay(brief)
        # publish final state to the dashboard (done) and update history
        await self._push_live("done", "done")
        await update_active(ARCHIVIST_TOKEN, self._run_key, brief, "done",
                            datetime.now(timezone.utc).isoformat())
        log("done", "run complete")
        return result

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
            # whose stored error is actually about deploying — semantic recall can
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
        """Write the replay trail for the static (offline) dashboard."""
        import json
        path = "/home/madgodinc/code/crescendo/dashboard/replay.json"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"brief": brief, "agents": AGENTS, "timeline": self.events},
                      f, ensure_ascii=False, indent=2)
        log("replay", f"wrote {len(self.events)} events -> {path}")


async def main() -> None:
    m = Maestro()
    command_room = os.environ.get("MAESTRO_COMMAND_ROOM", "").strip()

    # One-shot mode: a brief on the command line runs once in the command room and exits.
    if len(sys.argv) > 1:
        result = await m.run(sys.argv[1], room=command_room)
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
