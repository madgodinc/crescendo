# Crescendo — recording script (OBS)

> Everything is already up and verified. Services run as systemd --user units and
> survive a terminal restart. You just open the browser, check the list, hit
> record, and walk the scenes. Narration below is in English (international judges).

---

## 0. Pre-flight checklist (30 sec)

Ask me to run this, or run it yourself:

```bash
cd ~/Brain/projects/crescendo
systemctl --user is-active crescendo-brain crescendo-agents crescendo-dashboard
curl -s -o /dev/null -w 'brain %{http_code}\n' http://127.0.0.1:8765/health
curl -s -o /dev/null -w 'dashboard %{http_code}\n' http://127.0.0.1:8000/
```

Expect: three `active`, brain `200`, dashboard `200`. If anything is off, tell me — I bring it back in ~10s.

**Browser tabs, open in advance (all English):**
1. `http://127.0.0.1:8000/` — dashboard (live orchestra graph)
2. `http://127.0.0.1:8000/audit/run_374bb5fdb625` — audit report of the finished run
3. `https://ffa562ba.crescendo-demo-5vj.pages.dev` — the live page the orchestra built (coffee shop "Morning Brew" with a booking form)

**OBS:** scene = browser full-screen, UI zoom 110–125% (Ctrl +), cursor visible.

---

## 1. Timeline (~2:30–3:00)

| Scene | Time | Screen | What you say (gist, not verbatim) |
|---|---|---|---|
| Hook | 0:00–0:20 | Live page (tab 3) | "This isn't a mockup or a PR. It's a working page on a live URL — built from scratch by an orchestra of AI agents from a one-sentence brief. They reviewed their own work, fixed the bugs, and deployed it." Fill a form field to show it works. |
| What it is | 0:20–0:40 | Dashboard (tab 1), orchestra graph | "Five agents coordinate through Band: Conductor plans, Soloist writes, Tuning Fork reviews, Stage Tech deploys, Archivist keeps the memory. Everything flows through the conductor — a star, so no two agents loop forever." |
| Live run | 0:40–1:30 | Dashboard, **run live** (see §2) | As the graph lights up: "A brief goes in. Plan… code… review — Tuning Fork caught real problems… Soloist fixes… CLEAN… deploy to a live URL." |
| Proof | 1:30–2:30 | Audit report (tab 2) | "Every step is signed and chained. N over N grounded — each claim ties to a real artifact. Now let's tamper with a row…" → click **Falsify** → "the chain breaks from here down" → **Restore** → "and it heals. You verify it yourself, not on trust." |
| Memory | 2:30–2:50 | Dashboard, flywheel / learned | "The orchestra remembers between runs: the Archivist feeds each agent the skills and fixes it has learned, so the work compounds." |
| Close | 2:50–3:00 | Live page again | "Most agent demos end at a document. Crescendo ends at a working product — and proof that it works." |

---

## 2. Running the live run on camera

You said you drive OBS and I tell you what to run. Two options:

### Option A — I launch, you film the graph (reliable)
When you're recording and reach the "Live run" scene, say the word and I run the
brief. The dashboard graph (tab 1) animates in real time (polls ~1.5s):
plan → code → review → fix → CLEAN → deploy. A run takes ~2–4 minutes.

The brief I'll use (English, fresh = clean run from scratch):

> *Landing page for a yoga studio "Still Point": a hero with a tagline and a
> "Book a class" button, a class schedule (3–4 rows: day, time, class name), an
> instructor blurb, and a sign-up form (name, phone, class choice) with
> client-side validation. Calm palette, responsive.*

(Different from the coffee run so the recording shows a brand-new build, not a repeat.)

### Option B — you type the brief into the Band chat (most on-message)
This is the "human briefs the orchestra in Band" story. Needs maestro in
listen-mode (I'm finishing that). If you want B, tell me — I'll wire and verify it,
then give you the exact English brief to paste into the Band room.

### If a live run stumbles on camera
No problem — switch to tab 2 (finished audit) and tab 3 (finished live page) and
finish the story on those. That's the hybrid: live action + a guaranteed result.

---

## 3. The audit report — the climax (tab 2)

Scroll top to bottom:
- Title "Audit report · run_…", row table: human → conductor → soloist → tuning_fork → stage_tech → archivist.
- **N/N grounded** badge — the grounding pass.
- **HMAC / signed** columns — per-author signing.
- **Falsify** button (edit a row) → "Chain broken at row X" appears.
- **Restore** button → the chain is whole again.

This is the strongest beat: the judge re-runs the tamper-evidence, doesn't take it on faith.

---

## 4. Ready-made fallback artifacts (English, always valid)

- **Coffee "Morning Brew"** (primary, English) — `run_374bb5fdb625` · audit `/audit/run_374bb5fdb625` · page https://ffa562ba.crescendo-demo-5vj.pages.dev

Both showed a real review loop (Tuning Fork ISSUES → Soloist fix → CLEAN), visible in the audit report as review_1 / review_2.

---

## 5. Restarting the stack (just in case)

```bash
cd ~/Brain/projects/crescendo
bash run_brain.sh    # brain :8765 (systemd, with ORT_DYLIB_PATH)
# dashboard + agents — ask me, I bring up the same systemd units
```
