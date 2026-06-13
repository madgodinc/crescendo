# CRESCENDO — concept & decision rationale (archive)

> This is the CONCEPT doc: the *why* behind the decisions. The executable *what*
> lives in [CRESCENDO.md](CRESCENDO.md) (the execution schema). Kept so the
> reasoning behind each fixed decision isn't lost — when the schema says "Band =
> visible brain, Rust = one tool," this file says why the alternative was killed.
>
> Note: the demo scenario here (marketing-band) is SUPERSEDED. The 2026-06-10
> track decision moved the demo to multi-agent SOFTWARE DEVELOPMENT under a
> Track-3 (regulated/high-stakes, audit-trail) scoring frame. The execution
> schema reflects that; this file keeps the older framing only for the decision
> history that still applies (hybrid inversion, scope-to-floor, name, killer
> features, submission strategy under the language barrier).

---

## TWO LOAD-BEARING DECISIONS (after critic)
1. **HYBRID INVERTED.** Band = the VISIBLE coordination brain, in Python, using
   Band-native primitives (rooms, @mention routing, handoff, shared state). Rust
   = ONLY mgi-mind, exposed as ONE callable HTTP tool (memory + continuity). No
   FFI, no Rust core holding logic. One boundary (HTTP), deep Band usage (fixes
   criterion #1), fast iteration. The earlier "Rust core + thin Python Band
   layer" is CANCELLED — it made Band look like a pipe = criterion #1 fail.
2. **SCOPE TO A FLOOR.** 5 live roles, 1 preset, everything else = slides. The
   floor must work flawlessly. Build more on top by Mad's own pace, but
   DEMONSTRATE 5 (a phone-watching judge drowns in more).

## Track decision (2026-06-10) — supersedes the marketing-band framing
3 tracks: (1) Multi-Agent Workflow Automation (weak fit), (2) Multi-Agent
Software Development (our working process, most competitive track), (3) Regulated
& High-Stakes (accuracy, traceability, audit trails, compliance — strongest fit,
least competitive). DECISION: submit under TRACK 3 as the SCORING FRAME
(traceability/audit = the criterion = our differentiator) with the TRACK 2
SCENARIO (planner/engineer/reviewer/tester building real code) as the demo, where
every decision is provable through mgi-mind's audit log. NOT two tracks (dilution,
no clean one-liner). One-liner: "an orchestra of agents where you can prove every
decision."

## Name — DECIDED (no rename)
Ship "Crescendo." Alternatives are worse: Ratchet (US slang landmine), Brief
(hooks input not autonomy, un-ownable), Band puns (try-hard pandering). Naming is
lowest-leverage; the live URL + Band depth are what's scored. One concession: do
NOT claim on stage "crescendo literally means the flywheel" — the product
narrative carries the flywheel ("cheaper and more accurate every run because it
remembers its fixes"), the name is just a confident mark.

## Killer features (narrative spine)
1. **Resource Contract / Rider** — band infers from the task ONE upfront list of
   needed access → "give this, go rest, we do it all." Inferred, not hardcoded.
2. **Self-deploy magic moment** — agents ship the live product themselves.
3. **Self-learning via memory** — error → recall-or-find-fix → protocol
   (mgi-mind procedural memory). Gets better over runs. Don't stage growth; state
   the fact, let verification over time prove it.
4. **Context-efficiency via memory** — relevant context pulled by the Archivist
   (top-N) instead of the full window → fewer tokens, longer sessions, bigger
   projects. R@k/STALE = quiet guarantee the offload doesn't cost quality. Frame
   as "relevant context instead of full context," NOT "16k acts like 32k."
5. **Crash-proof continuity** — full state in mgi-mind, resume after any incident.
6. **Track-3 spine** — every decision auditable (token-derived author).

### The flywheel (narrative thesis)
Self-learning writes fixes to memory → context-efficiency pulls only the relevant
fix → longer sessions, fewer errors, cheaper calls over time. That IS "Crescendo":
the system gets cheaper, more accurate, faster the longer it runs.

## Memory — correct positioning
Memory = FOUNDATION (continuity, self-learning, context-efficiency), NOT the hero.
2026 memory is table stakes (Mem0/Zep/Letta); raw R@k doesn't land as novelty.
Behavior = loud/demonstrated; R@k/STALE = quiet guarantee under it. Raw numbers as
a headline only on memory-hackathons, not on Band.

## Submission strategy (under the language barrier)
Mad has no English speech, little video experience. Hackathon is async (no live
pitch). Bet on Mad's STRENGTH — a reproducible, verifiable product (judges click
the link themselves). Load-bearing: working product + public repo + live deployed
URL + clean English README. Video = bonus, captions/TTS, no pressure. CRITICAL:
judging must NOT touch a live run — "live URL" = pre-deployed static artifact on
Cloudflare Pages.

## TOP KILL-SHOTS (ranked) + mitigation
1. **Live demo crashes during judging.** → Pre-recorded flawless video + static
   pre-deployed URL; reduce boundaries (mgi-mind HTTP only); kill/resume cuttable.
2. **"Band looks bolted on" → criterion #1 fail.** → Inverted hybrid: Band-native
   coordination, Rust demoted to one tool, Band room visible in every handoff.
3. **"Seen it / agent-washing."** → Build the demo around the VISIBLE Tuning Fork
   catch (proves multi-agent necessity); cut redundant roles; lead with the
   business delta.
Also: Conductor as a thin policy layer USING Band's coordination, not replacing
it.

## What we DON'T do
Don't headline memory/Rust/universality. Not "integrate with everything" — 2 real
(GitHub, Cloudflare) + sockets on a slide. Don't dump 9 roles — show 5. No real
spend. Don't write core/AI logic before June 12. Don't stage growth — state the
fact, let verification prove it.

## Links to other projects
mgi-mind = the foundation (memory/continuity/self-learning/context-efficiency).
Coordination Hub (roadmap) = conceptually related; Crescendo's engine, memory-
economy, self-learning reuse in Mad's projects afterward — long-term infra, not a
throwaway. Even without a prize, Mad is ahead on roadmap.
