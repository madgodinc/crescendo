# Crescendo

**An orchestra of AI agents where you can prove every decision.**

Crescendo takes one brief, plans it, builds it, reviews its own work, fixes the
bugs it catches, and deploys a working product to a live URL. It writes every
decision to an append-only audit trail you can open and verify.

Built for the [Band of Agents Hackathon](https://lablab.ai/ai-hackathons/band-of-agents-hackathon)
(June 12–19, 2026).

**Live dashboard:** https://crescendo-dashboard.pages.dev · **Example shipped page:** https://b83e8f9c.crescendo-demo-5vj.pages.dev

## Idea

Most agents work alone, locked inside one framework. Real work is collaborative:
agents pass context, hand off tasks, bring in other agents, and stay accountable
across a long-running process.

Crescendo is a small team of specialized agents that coordinate through
[Band](https://band.ai). Band is the visible coordination layer: rooms, @mention
routing, handoffs, shared state. Every decision an agent makes is attributable to
a specific author and provable after the fact.

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

## How coordination works

Crescendo runs as a star. Every agent reports to the Conductor and waits until the
Conductor hands it the turn. The Conductor holds the plan, picks the next step,
caps review rounds, and calls the work done. Most multi-agent demos die when two
agents ping-pong forever. The star stops that, because nobody speaks out of turn.

A control loop in code backs up the prompts. It ignores system events, lets an
agent act only when something @mentions it, and stops a run that runs too long.
The Conductor's judgement does the work; the control loop is the safety net.

## The flywheel

Crescendo gets cheaper, more accurate, and faster the longer it runs. When an
agent hits an error and finds a fix, the Archivist writes that fix to memory. Next
time the same class of problem appears, an agent recalls the fix instead of
rediscovering it. The Archivist also pulls only the *relevant* context for each
step instead of carrying the full window, so runs use fewer tokens and stay
coherent over longer projects.

## Memory and the audit trail

Agents share one memory ([mgi-mind](https://github.com/madgodinc/mgi-mind)) over
HTTP. Each agent writes with its own token, so every entry carries its author. The
dashboard reads that trail, so you can replay who decided what and when.

## Run it

Pick the path that fits how far you want to go. Crescendo needs its brain
([mgi-mind](https://github.com/madgodinc/mgi-mind)) for memory, the audit trail,
skills, and crash-resume — the brain is bundled here as a submodule so it comes
along with a clone.

### 1. Just look (zero setup)

Open the live dashboard — a recorded run with its full decision trail, audit
report, and learned-fixes view:

**→ https://crescendo-dashboard.pages.dev**

Or, after cloning, double-click `dashboard/index.html`. The recorded run is
embedded, so it works straight off the filesystem — no server, no keys.

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

The engine is here; the fuel is yours — five [Band](https://band.ai) agents, an LLM
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

MIT — see [LICENSE](LICENSE).
