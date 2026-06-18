# Crescendo — submission (lablab.ai · Band of Agents Hackathon)

> Ready text for the submission form. Split into sections; adapt to the form fields.
> No competitors named (rule).

---

## One-liner

An orchestra of AI agents that ships a working product to a live URL, proves what it shipped, and gets better at it each run, coordinated through Band.

---

## What it does (Elevator pitch)

Crescendo takes one brief, plans it, builds it, reviews its own work, fixes the bugs it catches, and deploys a working product to a live URL a judge can click. Most multi-agent systems stop at a document: a plan, a review, a pull request, an advisory verdict. Crescendo stops at a **running artifact behind a public address**.

The ship is conditional: a deterministic gate headless-renders the page and refuses the deploy unless every control works. After the deploy lands, Crescendo fetches the live URL back and hashes the DOM a browser actually receives into a per-author-signed, hash-chained audit trail, so the last link records the artifact the judge sees. You open the audit report, edit any row in the browser, and watch the chain break from that point on: you re-run the tamper-evidence yourself instead of taking it on faith.

The orchestra also carries memory between runs: the Archivist pulls skills and previously-learned fixes into each new brief, so the work compounds instead of starting from zero.

---

## How we used Band (coordination layer)

Band is the **visible coordination layer**, not a pipe hidden behind the orchestrator. Crescendo runs as a star: every step goes through the Maestro, so two agents never ping-pong forever (the failure that kills most multi-agent demos).

- **One shared Band room** holds the whole run; the five agents are participants.
- **`@mention` routing** drives each step. The Maestro addresses exactly one agent per turn (`mentions=[...]`); a `GatedAdapter` makes a worker act only when it's the one mentioned, so nobody speaks out of turn.
- **Replies land back in the room**; an `AutoReplyLangGraphAdapter` guarantees an agent's plain-text reply reaches the room even when the model forgets to call the send tool.
- **A control loop in code** sequences these Band primitives deterministically and bounds the run.

Each Band handoff the loop drives (the mention out, the reply back) is the exact unit the audit trail records and signs. So the thing the judges look for (agents collaborating through Band) is the thing the audit trail attests.

---

## The five agents

- **Conductor** plans the brief, routes work, gates human approvals.
- **Soloist** writes the product code.
- **Tuning Fork** reviews the work, catches bugs before they ship.
- **Stage Tech** deploys the finished product to a live URL.
- **Archivist** pulls relevant context/skills and keeps the audit trail.

Per-author provenance is the reason there are five agents and not one tool-calling loop, because a trail that attributes each decision to a distinct author needs distinct authors.

---

## What makes it different (key features)

1. **Conditional ship (deterministic gate).** Headless render checks working controls, console errors, leaked secrets, placeholder text. No pass → no deploy.
2. **Grounding.** Every audit row that points at an external artifact (written page, live URL, check result) is verified to actually have one. The report shows `N/N grounded`. Deterministic and report-only: it adds no model and can't stall a run.
3. **Per-author signing.** Each row carries `HMAC(agent_key, agent ‖ action ‖ content ‖ timestamp)` over a hash chain. Tamper-evident: edit a past row and every hash after it breaks.
4. **Real code↔review negotiation.** Tuning Fork finds real ISSUES, Soloist fixes, verdict flips to CLEAN, not a scripted round. (Live run example: it caught a missing brand name and forced the fix.)
5. **Memory as an active expertise layer.** The Archivist feeds each agent the relevant skills + learned fixes before it works, so weaker models punch above their weight.

---

## The brain

mgi-mind is a Rust service bundled here as a submodule. It runs its own store (not a wrapper over a hosted vector DB) and carries the audit chain, the skills the agents pull from, and the checkpoints that let a crashed run resume.

---

## Tech stack

- **Coordination:** Band (one room, @mention routing, GatedAdapter + AutoReplyLangGraphAdapter), LangGraph adapters.
- **Agents:** Python control loop (deterministic star orchestrator), 5 role agents. Models: gpt-4o (primary, reliable tool-caller) with cross-provider fallback (Featherless or DeepSeek).
- **Brain:** mgi-mind (Rust, bundled Qdrant + ONNX embeddings/reranker), HTTP tool surface with per-agent bearer-token auth (token-derived authorship).
- **Deploy:** Cloudflare Pages via wrangler (real live `*.pages.dev`).
- **Dashboard:** zero-dependency Python stdlib server + static HTML (orchestra graph, audit report, flywheel).

---

## Honest limitations (stated up front)

- Per-author signing is provenance/integrity of the **published trail against an outside editor**, not zero-trust against the orchestrator itself (which holds the keys). Tamper-evident, not tamper-proof, and the report says so.
- The flywheel's "cheaper and faster every run" is the loop's **design intent** (memory learns fixes from gate refusals); the long-run cost curve is honest future work, not a measured number. The mechanism is wired and firing.

---

## Links

- **Live dashboard:** https://crescendo-dashboard.pages.dev
- **Example shipped page:** https://a6dbddda.crescendo-demo-5vj.pages.dev
- **Repo:** https://github.com/madgodinc/crescendo
- **Run it:** `git clone --recursive … && cd crescendo && docker compose up` (brain :8765 + dashboard :8000)

---

## Try it in 30 seconds (for judges)

1. Open the live dashboard: a recorded run with full decision trail, audit report, and learned-fixes view (zero setup).
2. In the audit report, hit **Falsify** on any row → the chain breaks from there → **Restore** → it heals. That's the tamper-evidence, verified by you.
