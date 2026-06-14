# Crescendo

**An orchestra of AI agents where you can prove every decision.**

Crescendo takes one brief, plans it, builds it, reviews its own work, fixes the
bugs it catches, and deploys a working product to a live URL. It writes every
decision to a SHA-256 hash-chained audit trail. It is tamper-evident: no decision
can be altered after the fact, and you can open it and verify the chain yourself.

Built for the [Band of Agents Hackathon](https://lablab.ai/ai-hackathons/band-of-agents-hackathon)
(June 12–19, 2026).

**Live dashboard:** https://crescendo-dashboard.pages.dev · **Example shipped page:** https://crescendo-demo-5vj.pages.dev

## Prove every decision

Every agent writes to one shared memory ([mgi-mind](https://github.com/madgodinc/mgi-mind))
with its own token, so every entry carries its author. The audit report chains
those entries: each row's hash is `SHA-256(previous_hash + agent + action +
content + timestamp)`. Change any past decision and every hash after it breaks, so
the trail is tamper-evident. You can replay who decided what and when, and verify
nothing was edited after the fact. That is the Track-3 claim, made checkable.

mgi-mind is a Rust service bundled in this repo as a submodule. It runs its own
store rather than wrapping a hosted vector DB, and it carries the audit chain, the
skills the agents pull from, and the checkpoints that let a crashed run resume.

## The flywheel

Crescendo gets cheaper and more accurate the longer it runs. A deploy failure is
reduced to a stable signature; the Archivist recalls whether memory already solved
that failure class and feeds the fix to the Soloist; a fix that then passes is
learned back as a verified procedure. Next run recalls it instead of rediscovering
it. The Archivist also pulls only the *relevant* context for each step rather than
the full window, so longer projects stay coherent on fewer tokens.

## Why this is different

Most multi-agent demos print text and stop. Crescendo ships a working product to a
live URL, then proves how it got there. It coordinates through
[Band](https://band.ai), the visible layer of rooms, `@mention` routing, handoffs,
and shared state, so every step is attributable to a specific author and provable
after the fact.

## The agents

| Agent | Role |
|-------|------|
| **Conductor** | Plans the brief, routes work, gates human approvals |
| **Soloist** | Writes the product code |
| **Tuning Fork** | Reviews the work and catches bugs before they ship |
| **Stage Tech** | Deploys the finished product to a live URL |
| **Archivist** | Pulls relevant context and keeps the audit trail |

Control flows Conductor → room → agent → room → Conductor. The coordination
happens *through* Band, not around it.

## How Band does the work

Crescendo runs as a star: every step goes through the Maestro, so two agents never
ping-pong forever, the failure that kills most multi-agent demos. Band is the
visible coordination layer, not a pipe behind the orchestrator. Every handoff runs
through real Band primitives:

- **One shared room** holds the whole run; agents are pulled in as participants.
- **`@mention` routing** drives each step: the Maestro addresses exactly one
  agent per turn (`mentions=[...]`), and a `GatedAdapter` makes a worker act only
  when it's the one mentioned, so nobody speaks out of turn.
- **Replies land back in the room**; the Maestro reads them by `sender_id` since a
  timestamp. An `AutoReplyLangGraphAdapter` guarantees an agent's plain-text reply
  reaches the room even when the model forgets to call the send tool.
- **A control loop in code** is the safety net under the prompts: it ignores system
  events, lets an agent act only when mentioned, and stops a run that runs too long.

## Architecture

```mermaid
flowchart TD
    H([Human brief]) -->|Band room| M
    subgraph BAND["Band — the coordination layer, one shared room"]
        M{{Maestro: conducts}}
        M <-->|mention, reply| C[Conductor: plans]
        M <-->|mention, reply| S[Soloist: writes code]
        M <-->|mention, reply| T[Tuning Fork: reviews]
        M <-->|mention, reply| D[Stage Tech: deploys]
        M <-->|mention, reply| A[Archivist: memory and skills]
    end
    A -. skills, recall, learn .-> S
    A -. skills .-> C
    A -. skills .-> T
    BRAIN[(mgi-mind: memory, audit, skills)]
    A <--> BRAIN
    M -->|live state, checkpoints, audit| BRAIN
    D -->|deploy| CF[(Cloudflare Pages: live URL)]
    BRAIN --> DASH[Dashboard: audit report, flywheel]
```

Solid lines are control flow through Band; dotted lines are the Archivist feeding
skills and recalled fixes straight to the worker that needs them.

## Run it

Pick the path that fits how far you want to go. Crescendo needs its brain
([mgi-mind](https://github.com/madgodinc/mgi-mind)) for memory, the audit trail,
skills, and crash-resume. The brain is bundled here as a submodule, so it comes
along with a clone.

### 1. Just look (zero setup)

Open the live dashboard, a recorded run with its full decision trail, audit
report, and learned-fixes view:

**→ https://crescendo-dashboard.pages.dev**

Or, after cloning, double-click `dashboard/index.html`. The recorded run is
embedded, so it works straight off the filesystem with no server and no keys.

### 2. Run the engine (Docker, one command)

Brings up the brain (on `:8765`) and the dashboard (on `:8000`):

```
git clone --recursive https://github.com/madgodinc/crescendo.git
cd crescendo
docker compose up
```

Open http://localhost:8000. The dashboard talks to a real brain; the recorded run
is shown until you drive a live one (next). The first build compiles the brain
(Rust) and bakes in ~550MB of models, so it takes a few minutes; after that it's
cached. Once the prebuilt image is published, skip the build with a fast pull:

```
docker compose -f docker-compose.yml -f docker-compose.image.yml up
```

### 3. Drive the live orchestra (bring your own keys)

The engine is here; the fuel is yours: five [Band](https://band.ai) agents, an LLM
key (Featherless or AI/ML API), and a Cloudflare account for the deploy. Copy the
template and fill it in:

```
cp .env.example .env        # add your Band + LLM + Cloudflare keys
uv sync                     # install the orchestrator deps

uv run python agents.py     # 1. start the five worker agents (keep running)
uv run python maestro.py "build a dark-themed pomodoro timer page"   # 2. send a brief
```

Watch the dashboard at http://localhost:8000 as it happens: the Conductor plans,
the Archivist feeds skills from memory, the Soloist writes the page, the Tuning
Fork runs a real check and reviews it, the Stage Tech deploys it, and the Archivist
saves the run. The deploy event shows the live URL; the **Audit report** button
opens the tamper-evident trail of every decision.

## What's built

- Five agents on Band, each with its own LLM brain (Featherless primary, AI/ML API fallback)
- Star coordination with a control loop in code
- Shared memory, every write attributed to its agent
- A resource contract: the Conductor infers the access a brief needs before any work starts
- Self-learning: a deploy failure recalls a known fix, and a verified fix is learned for next time
- The Stage Tech deploys to a live Cloudflare Pages URL and reports the real link
- A live dashboard for the orchestra graph and the decision trail, served from memory
- Crash-proof runs: state is checkpointed to memory and resumes after any kill

## License

MIT. See [LICENSE](LICENSE).
