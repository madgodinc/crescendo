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
    """Guarantee the LLM's final text reaches the chat even when the model
    didn't call a send tool.

    Band's base adapter relies on the model calling `band_send_message`; an agent
    that replies with plain text (e.g. the Conductor, which has no other tools)
    silently drops its answer. We wrap on_message: capture the final assistant
    text and whether any tool was used during the turn; if no message was sent,
    deliver the text ourselves via tools.send_message.

    `deliver_text` is False for tool roles (Soloist, Stage Tech): for those, a
    plain-text reply means the model NARRATED instead of calling its tool
    (write_page / deploy_site). Delivering that narration would mask the missing
    tool call and let a stale page ship, so we drop it — the orchestrator then
    sees no reply, and its write-guard insists on a real tool call."""

    def __init__(self, *a, deliver_text: bool = True, **k):
        super().__init__(*a, **k)
        self._deliver_text = deliver_text

    async def on_message(self, msg, tools, history, participants_msg, contacts_msg,
                         *, is_session_bootstrap, room_id):
        sent = {"any": False}
        final_text = {"v": ""}

        # wrap send_message so we know if the model already replied
        orig_send = tools.send_message

        async def tracked_send(*a, **k):
            sent["any"] = True
            return await orig_send(*a, **k)
        tools.send_message = tracked_send

        # capture the last assistant text from the model-end stream events
        orig_handle = self._handle_stream_event

        async def capture(event, rid, t):
            await orig_handle(event, rid, t)
            if isinstance(event, dict) and event.get("event") == "on_chat_model_end":
                out = (event.get("data") or {}).get("output")
                if not getattr(out, "tool_calls", None):
                    txt = getattr(out, "content", "") or ""
                    if isinstance(txt, list):
                        txt = "".join(b.get("text", "") for b in txt if isinstance(b, dict))
                    if txt and txt.strip():
                        final_text["v"] = txt.strip()
        self._handle_stream_event = capture
        try:
            await super().on_message(msg, tools, history, participants_msg, contacts_msg,
                                     is_session_bootstrap=is_session_bootstrap, room_id=room_id)
        finally:
            self._handle_stream_event = orig_handle
            tools.send_message = orig_send

        # fallback delivery: model produced text but never sent it. A lost reply
        # is what makes the Maestro wait out a full timeout, so retry once before
        # giving up: a transient Band hiccup shouldn't strand the reply. For tool
        # roles (deliver_text=False) we skip this: a text-only turn there means a
        # narrated-not-called tool, and delivering it would mask the missing call.
        if self._deliver_text and not sent["any"] and final_text["v"]:
            log = logging.getLogger("agents")
            for attempt in (1, 2):
                try:
                    await tracked_send(content=final_text["v"],
                                       mentions=[os.environ["MAESTRO_AGENT_ID"]])
                    log.info("AUTOREPLY delivered %d chars", len(final_text["v"]))
                    break
                except Exception as e:
                    log.warning("auto-reply send failed (try %d): %s", attempt, e)

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

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Sponsor providers are optional: a clone with only an OpenAI or Gemini key
# should still run. Read them tolerantly here; the preflight below requires only
# the keys the selected LLM_TIER actually uses.
FEATHERLESS = (os.environ.get("FEATHERLESS_BASE_URL", ""), os.environ.get("FEATHERLESS_API_KEY", ""))
AIMLAPI = (os.environ.get("AIMLAPI_BASE_URL", ""), os.environ.get("AIMLAPI_API_KEY", ""))
# Optional top-tier provider for the comparison/turbo tier (LLM_TIER=turbo).
# The sponsor APIs (Featherless / AIMLAPI) remain the default: this is only a
# benchmark lever to measure the quality/speed delta vs a frontier model.
_OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI = (os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"), _OPENAI_KEY)
# Gemini via its OpenAI-compatible endpoint, so the same ChatOpenAI wrapper works.
_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI = (os.environ.get("GEMINI_BASE_URL",
          "https://generativelanguage.googleapis.com/v1beta/openai"), _GEMINI_KEY)
# DeepSeek direct API (OpenAI-compatible). Verified 2026-06-16 to EMIT tool_calls
# (not narrate them), so it's safe in a tool role — unlike most Featherless models.
# A separate provider with its own rate limit, so it's a real cross-provider
# fallback for an OpenAI 429 (where falling back to gpt-4o again is a no-op).
_DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK = (os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"), _DEEPSEEK_KEY)
# Default tier: OpenAI (reliable, no daily cap) if its key is set, else Gemini,
# else the sponsor path. Gemini's free tier is 20 requests/day/model, enough for
# a demo but it runs dry under repeated testing: use LLM_TIER=gemini when the
# daily quota is fresh. Override with LLM_TIER=sponsor|gemini.
# Default tier: hybrid (tool roles on OpenAI, Conductor on free Featherless) when
# an OpenAI key is present — it's the demo default and keeps a sponsor model in
# the loop. Falls back to gemini, then Featherless-only sponsor, by available key.
LLM_TIER = os.environ.get(
    "LLM_TIER",
    "hybrid" if _OPENAI_KEY else ("gemini" if _GEMINI_KEY else "sponsor")).strip().lower()

# Every role is told to use band_send_message to reply: the LangGraph adapter
# does NOT auto-send text to chat, so the agent must call the tool explicitly.
REPLY_RULE = (
    "To reply, you MUST call the band_send_message tool — plain text is NOT sent. "
    "ALWAYS write in English, regardless of the input language. "
    "Keep replies short and telegraphic: no greetings, no thanks, no filler. "
    "Reply only to Maestro. NEVER @mention any other agent — Maestro routes all work. "
    "NEVER repeat, echo, or include any @[[...]] mention tokens or UUIDs from the "
    "conversation in your reply — they are routing markers, not content. Do your "
    "task and answer; do not parrot the message you received. "
)

# Model picks. Featherless is the primary provider (Premium, unlimited tokens).
# CRITICAL: most Featherless models will NARRATE a tool call instead of emitting
# one (verified 2026-06-14: DeepSeek-V3.1 and Qwen3-Coder-Next returned 0 tool
# calls on a realistic Soloist prompt, so the Soloist "said" it wrote the page
# but never called write_page → a stale page shipped). Qwen2.5-72B-Instruct is
# the one that reliably emits tool calls, so every tool-using role runs on it.
# DeepSeek-V3.1 is fine for the Conductor (plan text, no tools). AIMLAPI is the
# cross-provider fallback (it was down, Cloudflare 522, on 2026-06-14).
# 2026-06-14 PM: Featherless began rate-limiting hard (429 storms stalling runs);
# AIMLAPI recovered. Flip primary->AIMLAPI (gpt-4o is a reliable tool-caller),
# Featherless->fallback. Both providers are wired so whichever is healthy wins.
# Sponsor models (Featherless). Live re-probe 2026-06-16: of the three, ONLY
# DeepSeek-V3.1 actually emits tool calls (write_page/read_file/deploy_site all
# fire) — Mistral-Small-24B and Qwen2.5-72B narrate the call as text and return
# zero tool_calls even with tool_choice="required", so a Soloist on them ships
# nothing. So tool-using roles run on DeepSeek; Mistral stays as a text fallback
# and for the Conductor (plan text, no tools), where it's faster.
FB_MISTRAL = (FEATHERLESS, "mistralai/Mistral-Small-24B-Instruct-2501")
FB_DEEPSEEK = (FEATHERLESS, "deepseek-ai/DeepSeek-V3.1")
# GPT4O = the spec used by every TOOL role (Soloist/Tuning Fork/Stage Tech/
# Archivist); DSCHAT = the Conductor (plan text, no tools). Defaults below are
# overridden per LLM_TIER.
GPT4O = FB_DEEPSEEK
DSCHAT = FB_MISTRAL
FB_TOOL_FALLBACK = FB_DEEPSEEK

# Provider reality (re-probed live 2026-06-16):
#  - Featherless TOOL CALLS: only DeepSeek-V3.1 emits them; Mistral-24B & Qwen-72B
#    narrate instead (0 tool_calls even with tool_choice=required).
#  - BUT Featherless cuts the chunked stream at a ~60s proxy timeout, so a full
#    DeepSeek write_page generation dies mid-stream (reproduced 3/3 at ~61s) and
#    the Soloist comes back "silent". So NO Featherless model can carry a tool
#    role that emits a full page. Featherless is fine only for SHORT text replies
#    (the Conductor's plan), which finish inside the 60s window.
#  - OpenAI gpt-4o is a reliable tool-caller; the key is Tier-1 (30k TPM/500 RPM),
#    so it 429s only on parallel bursts — the star topology serializes maestro's
#    calls, so one role at a time stays under the cap.
# LLM_TIER: "hybrid" (tool roles->OpenAI, Conductor->Featherless Mistral; the
# default for the partner-prize demo — keeps a free sponsor model in the loop),
# "openai" (everything on gpt-4o), "gemini", or "sponsor" (Featherless-only;
# KNOWN-BROKEN for tool roles, kept only for diagnostics).
if LLM_TIER == "gemini" and _GEMINI_KEY:
    GEM = (GEMINI, "gemini-2.5-flash")  # fast, passes the gate (thinking off)
    OAI = (OPENAI, "gpt-4o") if _OPENAI_KEY else FB_MISTRAL
    GPT4O = DSCHAT = GEM
    FB_DEEPSEEK = OAI                  # OpenAI as the cross-provider fallback
    FB_TOOL_FALLBACK = OAI             # tool roles fall back to OpenAI (a real tool-caller)
elif LLM_TIER == "openai" and _OPENAI_KEY:
    GPT4O = (OPENAI, "gpt-4o")          # direct OpenAI: fast, passes the gate
    DSCHAT = (OPENAI, "gpt-4o")
    FB_DEEPSEEK = FB_MISTRAL               # sponsor Mistral as the working fallback
    FB_TOOL_FALLBACK = FB_DEEPSEEK         # tool roles fall back to DeepSeek (sponsor tool-caller)
elif LLM_TIER == "deepseek" and _DEEPSEEK_KEY:
    # Everything on DeepSeek-direct (verified tool-caller). Used to log a run on
    # DeepSeek for the cross-provider comparison stat. Falls back to OpenAI if set.
    GPT4O = (DEEPSEEK, "deepseek-chat")
    DSCHAT = (DEEPSEEK, "deepseek-chat")
    FB_TOOL_FALLBACK = (OPENAI, "gpt-4o") if _OPENAI_KEY else (DEEPSEEK, "deepseek-chat")
elif LLM_TIER == "hybrid" and _OPENAI_KEY:
    GPT4O = (OPENAI, "gpt-4o")             # tool roles on OpenAI (only reliable tool-caller)
    DSCHAT = FB_MISTRAL                    # Conductor's plan stays on free Featherless (short reply)
    # Tool-role fallback must be a DIFFERENT provider, else a 429 just retries the
    # same OpenAI org under the same 30k TPM cap (a heavy review turn bursts over
    # it). Prefer DeepSeek-direct: a separate provider with its own limit, paid
    # balance, and verified tool_calls. Gemini next (free, also OpenAI-compatible,
    # but a 20 req/day cap dries up under testing). Last resort gpt-4o (no-op vs
    # rate-limit, but covers a transient non-429 OpenAI error).
    if _DEEPSEEK_KEY:
        FB_TOOL_FALLBACK = (DEEPSEEK, "deepseek-chat")
    elif _GEMINI_KEY:
        FB_TOOL_FALLBACK = (GEMINI, "gemini-2.5-flash")
    else:
        FB_TOOL_FALLBACK = (OPENAI, "gpt-4o")

# Deploy is the one tool call that MUST land: it fires wrangler and is the whole
# point (the live URL). Verified 2026-06-16: deepseek-chat reliably emits tool
# calls for short replies but goes quiet on the heavy deploy turn in the live Band
# loop (3/5 phases, then silent at deploy). So pin Stage Tech to the most reliable
# tool-caller available regardless of tier — gpt-4o if its key is set, else Gemini,
# else whatever the tier picked. Its fallback stays the tier default. This keeps a
# cheap-model run (LLM_TIER=deepseek) end-to-end instead of stalling at deploy.
if _OPENAI_KEY:
    DEPLOY_MODEL = (OPENAI, "gpt-4o")
elif _GEMINI_KEY:
    DEPLOY_MODEL = (GEMINI, "gemini-2.5-flash")
else:
    DEPLOY_MODEL = GPT4O

# Fail fast with a clear message if the resolved tier has no usable provider key,
# instead of a confusing 401 mid-run. Every role still needs a working primary.
if not (_OPENAI_KEY or _GEMINI_KEY or FEATHERLESS[1] or AIMLAPI[1]):
    raise SystemExit(
        "No LLM provider key found. Set at least one of OPENAI_API_KEY, "
        "GEMINI_API_KEY, or the sponsor FEATHERLESS_API_KEY / AIMLAPI_API_KEY in "
        ".env (see .env.example).")

# role -> (prefix, primary (provider,model), fallback (provider,model), system text)
ROSTER = {
    "Conductor": ("CONDUCTOR", DSCHAT, FB_DEEPSEEK,
        REPLY_RULE + "You are the Conductor — you turn a brief into a short build plan. "
        "Reply with the plan only. NEVER @mention other agents — Maestro routes the work."),
    "Soloist": ("SOLOIST", GPT4O, FB_TOOL_FALLBACK,
        "You are the Soloist — the engineer. Your FIRST action on every turn is a "
        "tool call to write_page. Do NOT send any chat message before it. Do NOT "
        "describe, summarize, or narrate the page in text — describing it instead of "
        "calling the tool ships nothing and fails the task. Just call write_page.\n"
        "write_page args: title; body (markup INSIDE <body> only — NO "
        "<html>/<head>/<body>/<script>/<style> tags); css (rules only); js (code only). "
        "The HTML shell is fixed for you. Build EXACTLY what the brief asks, in English. "
        "EVERY interactive control MUST actually work: a button needs a real click handler "
        "(addEventListener in the js) that does something visible, or be type=submit in a "
        "form; a link needs a real URL or an in-page #id that exists (no href='#'). No dead "
        "controls, no empty buttons, no placeholder/lorem/TODO text. Security: target='_blank' "
        "links MUST have rel='noopener'; https only; never hard-code a key or token. No favicon, "
        "no base64.\n"
        "ONLY AFTER write_page returns, call band_send_message with a one-line summary in "
        "English. Order is strict: write_page first, band_send_message second. Never the reverse, "
        "never text without the tool call. Never @mention another agent or echo @[[...]] tokens."),
    "Tuning Fork": ("TUNING_FORK", GPT4O, FB_TOOL_FALLBACK,
        REPLY_RULE + "You are the Tuning Fork — the critic. Call check_page ONCE (pass a key term "
        "from the brief as must_contain): it runs a deterministic gate — structure, a headless "
        "render reporting console/JS errors, dead controls, secrets, placeholder text. That gate is "
        "your source of truth. If it reports CHECK FAILED, list those as ISSUES. If it PASSED, the "
        "page is structurally sound — do a quick correctness pass against the brief (you may read_file "
        "index.html ONCE if needed, but don't over-inspect) and flag only a real, concrete defect. "
        "Every ISSUE you raise MUST cite its evidence so the verdict is verifiable, not opinion: "
        "either quote the check_page line that flagged it (e.g. \"check_page: dead control\") or cite "
        "the exact offending snippet/line from index.html. No citation, no ISSUE — a claim you can't "
        "ground, you drop. Reply 'CLEAN' if the gate passed and the brief is met, else "
        "'ISSUES: ...' where each issue is 'defect [evidence] -> fix'. Be decisive and brief — one "
        "check, one short verdict. Write in English."),
    "Stage Tech": ("STAGE_TECH", DEPLOY_MODEL, FB_TOOL_FALLBACK,
        REPLY_RULE + "You are the Stage Tech — the deployer. CALL deploy_site and reply with "
        "the exact live URL it returns. Never invent a URL."),
    "Archivist": ("ARCHIVIST", GPT4O, FB_TOOL_FALLBACK,
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

    def _bump_usage(self, usage):
        try:
            tot = int((usage or {}).get("total_tokens") or 0)
            if tot and self.mind_token:
                _TOK_RUNNING[self.agent_name] = _TOK_RUNNING.get(self.agent_name, 0) + tot
                from memory_tools import set_token_total
                asyncio.create_task(set_token_total(self.mind_token, self.agent_name,
                                                    _TOK_RUNNING[self.agent_name]))
        except Exception:
            pass

    def _bump(self, result):
        gens = getattr(result, "generations", None) or []
        msg = gens[0].message if gens else None
        self._bump_usage(getattr(msg, "usage_metadata", None) if msg else None)

    async def _agenerate(self, *a, **k):
        result = await super()._agenerate(*a, **k)
        self._bump(result)
        return result

    def _generate(self, *a, **k):
        result = super()._generate(*a, **k)
        self._bump(result)
        return result

    # the LangGraph agent streams via _astream: usage rides the LAST chunk
    async def _astream(self, *a, **k):
        async for chunk in super()._astream(*a, **k):
            u = getattr(getattr(chunk, "message", None), "usage_metadata", None)
            if u:
                self._bump_usage(u)
            yield chunk

    def _stream(self, *a, **k):
        for chunk in super()._stream(*a, **k):
            u = getattr(getattr(chunk, "message", None), "usage_metadata", None)
            if u:
                self._bump_usage(u)
            yield chunk


_TOK_RUNNING: dict[str, int] = {}   # per-agent running total this process


def _llm(spec, agent_name="agent", mind_token=""):
    """One chat model with a bounded timeout and NO internal retries — retrying is
    delegated to .with_fallbacks() (the other provider) and maestro's ask() retry.
    Tracks token usage per agent for the dashboard."""
    (base_url, api_key), model = spec
    extra = {}
    # Gemini 2.5 'thinking' burns the whole token budget before it writes any
    # output: turn it off so the model spends its budget on the actual answer.
    if "gemini" in model.lower():
        extra["model_kwargs"] = {"reasoning_effort": "none"}
    # max_tokens is reserved against the per-minute token budget, so an 8k
    # reservation plus a big prompt trips OpenAI's 30k TPM Tier-1 limit. 4k is
    # plenty for a one-page site and a short review, and leaves room for input.
    return TokenTrackingChatOpenAI(model=model, base_url=base_url, api_key=api_key,
                      temperature=0, max_tokens=4096, timeout=45, max_retries=0,
                      stream_usage=True,   # emit usage_metadata on the final stream chunk
                      agent_name=agent_name, mind_token=mind_token, **extra)


def build(prefix, primary, fallback, role) -> Agent:
    # actor id used in the dashboard timeline (CONDUCTOR -> conductor, TUNING_FORK -> tuningfork)
    actor = prefix.lower().replace("_", "")
    mtok = os.environ[f"MGIMIND_TOKEN_{prefix}"]
    # primary with a fallback model: survives a provider outage transparently;
    # both track tokens to the same per-agent counter.
    llm = _llm(primary, actor, mtok).with_fallbacks([_llm(fallback, actor, mtok)])
    tools = build_memory_tools(mtok)
    if prefix == "STAGE_TECH":
        tools = tools + build_deploy_tools()          # read + deploy (with validate gate)
    elif prefix == "SOLOIST":
        tools = tools + build_author_tools()          # write_page (fixed shell) + read
    elif prefix == "TUNING_FORK":
        tools = tools + build_review_tools()          # read files to review
    # tool roles must CALL their tool, not narrate it — don't auto-deliver their
    # plain text (that would mask a missing write_page / deploy_site call).
    deliver_text = prefix not in ("SOLOIST", "STAGE_TECH")
    inner = AutoReplyLangGraphAdapter(llm=llm, custom_section=role,
                                      additional_tools=tools, deliver_text=deliver_text)
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
