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


class AutoReplyLangGraphAdapter(LangGraphAdapter):
    """Guarantee the LLM's final text reaches the chat, even when the model
    forgot to call band_send_message. Idempotent: skips if the model used tools."""

    async def _handle_stream_event(self, event, room_id, tools) -> None:
        await super()._handle_stream_event(event, room_id, tools)
        if not isinstance(event, dict) or event.get("event") != "on_chat_model_end":
            return
        output = (event.get("data") or {}).get("output")
        if getattr(output, "tool_calls", None):
            return  # model already replied via a tool — no double-post
        text = getattr(output, "content", "") or ""
        if isinstance(text, list):
            text = "".join(b.get("text", "") for b in text if isinstance(b, dict))
        text = (text or "").strip()
        if not text:
            return
        # reply to Maestro (Band needs >=1 mention; the orchestrator is the addressee)
        try:
            await tools.send_message(content=text,
                                     mentions=[os.environ["MAESTRO_AGENT_ID"]])
        except Exception as e:
            logging.getLogger("agents").warning("auto-reply failed: %s", e)

from deploy_tools import build_author_tools, build_deploy_tools, build_review_tools
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
        # 1. never react to our own messages (kills self-reply loops)
        if sender == self._uuid:
            return
        # 2. only act on a direct task from Maestro that mentions us
        addressed = self._uuid in content  # Band encodes mentions as @[[uuid]]
        if sender != os.environ["MAESTRO_AGENT_ID"] or not addressed:
            return
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
    "ALWAYS write in English, regardless of the input language. "
    "Keep replies short and telegraphic: no greetings, no thanks, no filler. "
    "Reply only to Maestro. NEVER @mention any other agent — Maestro routes all work. "
)

# Model picks. Featherless is the primary provider (Premium, unlimited tokens).
# CRITICAL: most Featherless models will NARRATE a tool call instead of emitting
# one (verified 2026-06-14: DeepSeek-V3.1 and Qwen3-Coder-Next returned 0 tool
# calls on a realistic Soloist prompt, so the Soloist "said" it wrote the page
# but never called write_page → a stale page shipped). Qwen2.5-72B-Instruct is
# the one that reliably emits tool calls, so every tool-using role runs on it.
# DeepSeek-V3.1 is fine for the Conductor (plan text, no tools). AIMLAPI is the
# cross-provider fallback (it was down — Cloudflare 522 — on 2026-06-14).
QWEN72 = (FEATHERLESS, "Qwen/Qwen2.5-72B-Instruct")     # reliable tool calls — all tool roles
DEEPSEEK = (FEATHERLESS, "deepseek-ai/DeepSeek-V3.1")   # strong reasoning, text-only roles
FB_DEEPSEEK = (AIMLAPI, "deepseek-chat")                 # fallback
FB_GPT4O = (AIMLAPI, "gpt-4o")                            # fallback (reliable tool calls)

# role -> (prefix, primary (provider,model), fallback (provider,model), system text)
ROSTER = {
    "Conductor": ("CONDUCTOR", DEEPSEEK, FB_DEEPSEEK,
        REPLY_RULE + "You are the Conductor — you turn a brief into a short build plan. "
        "Reply with the plan only. NEVER @mention other agents — Maestro routes the work."),
    "Soloist": ("SOLOIST", QWEN72, FB_GPT4O,
        REPLY_RULE + "You are the Soloist — the engineer. You MUST actually CALL the write_page "
        "tool (do not just describe it): pass title, body (markup INSIDE <body> only — NO "
        "<html>/<head>/<body>/<script>/<style> tags), css (rules only), js (code only). The HTML "
        "shell is fixed for you. Build EXACTLY what the brief asks for, in English. Do NOT add a "
        "favicon or base64. Do NOT paste code in chat. After the tool returns, reply one line."),
    "Tuning Fork": ("TUNING_FORK", QWEN72, FB_GPT4O,
        REPLY_RULE + "You are the Tuning Fork — the critic. CALL list_files then read_file to read "
        "the Soloist's code yourself — never expect it in chat. CHECK FIRST that the file is "
        "complete and not truncated (must end with </html>); if truncated or empty, that's an "
        "ISSUE. Then review correctness against the brief. Reply 'CLEAN' only if the file is whole "
        "and works, else 'ISSUES: ...' with concrete fixes. Write the review in English."),
    "Stage Tech": ("STAGE_TECH", QWEN72, FB_GPT4O,
        REPLY_RULE + "You are the Stage Tech — the deployer. CALL deploy_site and reply with "
        "the exact live URL it returns. Never invent a URL."),
    "Archivist": ("ARCHIVIST", QWEN72, FB_GPT4O,
        REPLY_RULE + "You are the Archivist — memory. CALL remember to store what you're told, "
        "recall to fetch context, and reply with a one-line confirmation or summary in English."),
}


class TokenTrackingChatOpenAI(ChatOpenAI):
    """ChatOpenAI that records token usage at the LLM boundary (framework-
    agnostic — works no matter how Band routes events). After each generation it
    adds the turn's total_tokens to a running per-agent counter and publishes it
    to mgi-mind so the dashboard can show real tokens per phase."""
    agent_name: str = "agent"
    mind_token: str = ""

    def _bump(self, result):
        try:
            gens = getattr(result, "generations", None) or []
            msg = gens[0].message if gens else None
            usage = getattr(msg, "usage_metadata", None) if msg else None
            tot = int((usage or {}).get("total_tokens") or 0)
            if tot and self.mind_token:
                _TOK_RUNNING[self.agent_name] = _TOK_RUNNING.get(self.agent_name, 0) + tot
                from memory_tools import set_token_total
                asyncio.create_task(set_token_total(self.mind_token, self.agent_name,
                                                    _TOK_RUNNING[self.agent_name]))
        except Exception:
            pass

    async def _agenerate(self, *a, **k):
        result = await super()._agenerate(*a, **k)
        self._bump(result)
        return result

    def _generate(self, *a, **k):
        result = super()._generate(*a, **k)
        self._bump(result)
        return result


_TOK_RUNNING: dict[str, int] = {}   # per-agent running total this process


def _llm(spec, agent_name="agent", mind_token=""):
    """One chat model with a bounded timeout and NO internal retries — retrying is
    delegated to .with_fallbacks() (the other provider) and maestro's ask() retry.
    Tracks token usage per agent for the dashboard."""
    (base_url, api_key), model = spec
    return TokenTrackingChatOpenAI(model=model, base_url=base_url, api_key=api_key,
                      temperature=0, max_tokens=8192, timeout=70, max_retries=0,
                      agent_name=agent_name, mind_token=mind_token)


def build(prefix, primary, fallback, role) -> Agent:
    # actor id used in the dashboard timeline (CONDUCTOR -> conductor, TUNING_FORK -> tuningfork)
    actor = prefix.lower().replace("_", "")
    mtok = os.environ[f"MGIMIND_TOKEN_{prefix}"]
    # primary with a fallback model — survives a provider outage transparently;
    # both track tokens to the same per-agent counter.
    llm = _llm(primary, actor, mtok).with_fallbacks([_llm(fallback, actor, mtok)])
    tools = build_memory_tools(mtok)
    if prefix == "STAGE_TECH":
        tools = tools + build_deploy_tools()          # read + deploy (with validate gate)
    elif prefix == "SOLOIST":
        tools = tools + build_author_tools()          # write_page (fixed shell) + read
    elif prefix == "TUNING_FORK":
        tools = tools + build_review_tools()          # read files to review
    inner = AutoReplyLangGraphAdapter(llm=llm, custom_section=role, additional_tools=tools)
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
    for name, (prefix, primary, fallback, role) in ROSTER.items():
        a = build(prefix, primary, fallback, role)
        await a.start()
        agents.append((name, a))
        log.info("listening: %s (%s, fallback %s)", name, primary[1], fallback[1])
    log.info("=== %d worker agents listening — start maestro.py ===", len(agents))
    try:
        await asyncio.Event().wait()
    finally:
        for name, a in agents:
            await a.stop()


if __name__ == "__main__":
    asyncio.run(main())
