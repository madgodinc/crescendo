# Sponsor model feedback: Crescendo

Honest field notes from running Crescendo's five-agent pipeline (plan → code →
review → deploy) on the sponsor APIs. The workload is demanding in a specific
way: every tool-using role must **emit real tool calls** (not describe them),
hold a multi-turn conversation in one Band room, and a single review turn can
carry a system prompt + injected skills + chat history + file contents in one
request. That is what surfaced the issues below.

Each page Crescendo builds is graded by a **deterministic acceptance gate** (a
headless render that checks for working controls, console errors, leaked
secrets, placeholder text). "Passes the gate" below means an objective check,
not a subjective read.

## AI/ML API (AIMLAPI)

- **Ran out of funds mid-testing.** The $10 partner credit was consumed by a
  day of runs, after which every request returns
  `OutOfFundsException: "You've run out of funds."` The orchestrator then stalls
  on that role until it times out and falls back.
- **Intermittent Cloudflare 522** earlier in testing (2026-06-14), upstream
  gateway timeouts unrelated to our load.
- **gpt-4o via the reseller path was noticeably slower** than calling OpenAI
  directly for the same model. Extra reseller hops added latency that pushed
  some turns close to our reply timeout.

**Net:** the model itself (gpt-4o) is a reliable tool-caller; the blocker was
the credit cap. $10 is not enough to test a five-agent pipeline that makes
dozens of calls per run.

## Featherless AI

The unlimited monthly plan is the right shape for a multi-agent workload, with no
per-call billing anxiety. The problems are per-model, and they matter because a
multi-agent run needs models that (a) emit tool calls and (b) answer fast enough
to not trip a reply timeout.

| Model | Tool calls | Latency / build | Acceptance gate | Notes |
|-------|-----------|-----------------|-----------------|-------|
| **Mistral-Small-24B-Instruct** | ✅ yes | ~68s | ✅ **passes** | The one model that works end-to-end. Our sponsor runs use it. |
| **Qwen2.5-72B-Instruct** | ✅ yes | 64–103s | ❌ **fails** | Slow, and the page it builds fails the gate (no working JS, `net::ERR_CONNECTION_CLOSED` console errors from a broken resource link). |
| **DeepSeek-V3.1** | ❌ no | — | — | **Narrates** the tool call instead of emitting it ("Created the page…") so no file is written and a stale page would ship. Unusable for tool roles. |
| **Qwen3-Coder-Next** | ❌ no | — | — | Same narration problem: 0 tool calls on a realistic build prompt. |
| **Llama-3.3-70B-Instruct** | — | — | — | **Gated**, requires connecting a HuggingFace org; not callable out of the box. |
| **Qwen2.5-Coder-32B** | — | — | — | Request died with `peer closed connection without sending complete message body`. |

### The two concrete blockers on Featherless

1. **Tool-call narration.** Several models (DeepSeek-V3.1, Qwen3-Coder) return a
   text description of a tool call instead of an actual `tool_calls` payload.
   For an agent that must call `write_page` / `deploy_site`, this silently does
   nothing: the agent "says" it built the page, no file is written, and a stale
   page would ship. This was the single hardest bug to diagnose because the chat
   looks successful. Only Mistral-Small-24B and Qwen2.5-72B reliably emit tool
   calls.

2. **Concurrency cap → 429.** On the plan, a 72B-class model costs 4 concurrency
   units, and the plan's limit is 4, so **one in-flight 72B request saturates
   the whole account**. The moment a second role (or a fallback) overlaps, every
   request returns `429 Concurrency limit exceeded`. A star-topology orchestrator
   only sends one request at a time in theory, but a slow request still holding
   its slot while the next starts is enough to trip it. Smaller models
   (Mistral-24B) cost fewer units and avoid this, which is the other reason
   Mistral is the only viable choice.

3. **Latency.** Even the working model (~68s/build) is 4–8× slower than a
   frontier model (Gemini 2.5 Flash ~15s, GPT-4o ~8s on the same brief). With a
   3-round review loop that's the difference between a 2-minute run and a
   6-minute run, and it pushes individual turns near the 130s reply timeout, so a
   slow turn occasionally times out mid-review and the run has to resume.

## What would make the sponsor APIs first-class for multi-agent work

- **AI/ML API:** a larger free tier (the $10 cap dies in one day of agent
  testing), and a direct (non-reseller) path for frontier models to cut latency.
- **Featherless:** clearer per-model **tool-calling support flags** (so we don't
  discover narration at runtime), a **higher default concurrency** so a single
  72B request doesn't lock the account, and ungated access to the popular
  instruct models (Llama-3.3-70B).

## Bottom line

Crescendo runs end-to-end on Featherless today using **Mistral-Small-24B**, the
one model that emits tool calls, fits the concurrency budget, and passes the
acceptance gate. The frontier models (Gemini, GPT-4o) are faster; the sponsor
path is the slower, cheaper tier. The same deterministic gate measures every
model, so the trade-off is visible rather than asserted.
