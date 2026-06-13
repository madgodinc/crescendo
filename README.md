# Crescendo

**An orchestra of AI agents where you can prove every decision.**

Crescendo takes one brief, plans it, builds it, reviews its own work, fixes the
bugs it catches, and deploys a working product to a live URL — and writes every
decision to an append-only audit trail you can open and verify.

Built for the [Band of Agents Hackathon](https://lablab.ai/ai-hackathons/band-of-agents-hackathon)
(June 12–19, 2026).

## Idea

Most agents work alone, locked inside one framework. Real work is collaborative:
agents need to pass context, hand off tasks, bring in other agents, and stay
accountable across a long-running process.

Crescendo is a small team of specialized agents that coordinate through
[Band](https://band.ai). Band is the visible coordination layer — rooms,
@mention routing, handoffs, shared state. Every decision an agent makes is
attributable to a specific author and provable after the fact.

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

## The flywheel

Crescendo gets cheaper, more accurate, and faster the longer it runs. When an
agent hits an error and finds a fix, that fix is written to memory. Next time the
same class of problem appears, the fix is recalled instead of rediscovered. The
Archivist pulls only the *relevant* context for each step instead of carrying the
full window — fewer tokens, longer sessions, bigger projects.

## Status

Day 1 of the build phase. Scaffolding in progress. This README will track what
actually ships.

## License

MIT — see [LICENSE](LICENSE).
