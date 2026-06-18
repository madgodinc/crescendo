# Crescendo — pitch deck

> Band of Agents Hackathon · June 2026. One screen = one slide. Text is a frame
> for the spoken delivery, not to be read verbatim. No competitors named (rule).

---

## Slide 1 — Title

# Crescendo
### An orchestra of AI agents that ships a working product to a live URL, and proves what it shipped.

One-sentence brief → plan → code → review → fix → deploy → signed audit.
Coordinated through **Band**.

*(background: screenshot of the live page + a slice of the orchestra graph)*

---

## Slide 2 — Problem

**Almost every multi-agent demo ends at text.**

A plan. A review. A pull request. An advisory verdict.

Nobody can click the result. And nobody can verify the agents didn't lie about
what they did.

> Two gaps: **no working artifact**, and **no proof**.

---

## Slide 3 — Solution

**Crescendo closes both gaps.**

1. It stops not at a document but at a **working product behind a public address**.
2. It makes the deploy **conditional**: a deterministic gate plus a grounding pass,
   then signs the verification into a per-author hash chain.

The audit records not just "what each agent did," but that **the result was
machine-checked to work before it went live**.

---

## Slide 4 — Five agents, star topology

| Agent | Role |
|---|---|
| **Conductor** | Plans the brief, routes work, holds human-approval gates |
| **Soloist** | Writes the product code |
| **Tuning Fork** | Reviews the work, catches bugs before they ship |
| **Stage Tech** | Deploys to a live URL |
| **Archivist** | Feeds context/skills, keeps the audit chain |

Flow: **Conductor → room → agent → room → Conductor.**
A star, so two agents never ping-pong forever (the death of most multi-agent demos).

---

## Slide 5 — Killer features (what's different)

1. **Conditional ship (gate).** Headless render of the page; deploy is refused
   until every control works.
2. **Grounding.** After the deploy, Crescendo fetches the live URL back and hashes
   the DOM the judge sees. `N/N grounded`.
3. **Per-author signing.** Each audit row is `HMAC(agent_key, …)`. An edit can't be
   forged under another author. That's why there are five agents, not one loop.
4. **Real code↔review negotiation.** Tuning Fork finds real ISSUES → Soloist fixes
   → CLEAN. Not scripted: on the demo run it caught a missing brand name and
   forced the fix.
5. **Memory as an active layer.** The Archivist feeds each agent relevant skills
   and learned fixes before it works. Weak models punch above their weight.

---

## Slide 6 — How it uses Band (hackathon requirement)

Band is the **visible coordination layer**, not a pipe behind the orchestrator.

- **One shared room** holds the whole run; the agents are participants.
- **`@mention` routing**: the Maestro addresses exactly one agent per turn. A
  `GatedAdapter` makes a worker act only when it's the one mentioned.
- **Replies land back in the room**; an `AutoReplyLangGraphAdapter` guarantees
  delivery even when the model forgets to call the send tool.
- **A control loop in code** sequences the Band primitives deterministically and
  bounds the run.

Every Band handoff (mention → reply) is the unit the audit signs. What the judges
look for (agents collaborating through Band) is exactly what the audit attests.

---

## Slide 7 — Architecture

```
Human brief ──Band room──▶ Maestro (conductor)
   ┌─────────────── BAND: one shared room ──────────────────┐
   │  Maestro ⇄ Conductor   (plan)                          │
   │  Maestro ⇄ Soloist     (code)                          │
   │  Maestro ⇄ Tuning Fork (review)                        │
   │  Maestro ⇄ Stage Tech  (deploy)                        │
   │  Maestro ⇄ Archivist   (memory / skills)               │
   └────────────────────────────────────────────────────────┘
Archivist ··skills/recall··▶ Soloist / Conductor / Tuning Fork
mgi-mind (memory, audit, skills, checkpoints) ⇄ Archivist / Maestro
Stage Tech ──deploy──▶ Cloudflare Pages (live URL)
mgi-mind ──▶ Dashboard (audit report, flywheel)
```

Solid arrows = control flow through Band; dotted = the Archivist feeding skills
straight to the worker that needs them. The brain is **mgi-mind** (Rust, its own
store, not a wrapper over a hosted DB).

---

## Slide 8 — Demo (live insert)

*(switch to the recording)*

- One-sentence brief → the graph lights up: plan → code → **review found problems**
  → fix → **CLEAN** → deploy → live URL.
- Open the audit report: **N/N grounded**, per-author HMAC.
- **Falsify** breaks a row → "chain broken at row X" → **Restore** heals it.
  The judge verifies the tamper-evidence themselves.

---

## Slide 9 — Honest about the limits (this builds trust)

- Per-author signing is provenance/integrity of the **published trail against an
  outside editor**, not zero-trust against the orchestrator itself (it holds the
  keys). Tamper-**evident**, not tamper-proof. The report says so.
- "Cheaper and faster every run" is the loop's **design intent** (memory learns
  fixes from gate refusals); the long-run cost curve is honest future work, not a
  measured number.

The mechanism is wired and firing; we don't pass intent off as a measured result.

---

## Slide 10 — Scaling without diluting the proof

The five are the quintet that plays on every brief. Bigger or regulated work plugs
in specialists per phase (Security Auditor, Accessibility Checker…).

Two rules keep it honest, not agent-count theatre:
- **The topology never changes**: every specialist still reports only to the conductor.
- **A specialist must bring a deterministic check**, not just a prompt. Otherwise
  it puts an ungrounded claim in the trail, the one thing the grounding pass catches.

---

## Slide 11 — Close

> Any system can hash what its agents said.
> Only one that **ships** has a live artifact to hash.

**Crescendo ends at a working product, and proof that it works.**

- Live dashboard: https://crescendo-dashboard.pages.dev
- Example page: https://ffa562ba.crescendo-demo-5vj.pages.dev
- Run it: `docker compose up` (brain :8765 + dashboard :8000)
