"""Seed the skill libraries in mgi-mind.

Crescendo's edge over bare models: the Archivist pulls relevant skills from these
libraries and feeds them to each agent before it works. The libraries are
category-scoped (design, css, anti-slop, security, process) so semantic search
returns focused, on-topic guidance.

Run once (idempotent-ish — re-running just adds the same entries again, so only
run on a fresh skill set): uv run python seed_skills.py
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv("/home/madgodinc/code/crescendo/.env")

URL = os.environ.get("MGIMIND_URL", "http://127.0.0.1:8765")
TOKEN = os.environ["MGIMIND_TOKEN_ARCHIVIST"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# library -> list of skill snippets. Each snippet is one focused, actionable rule.
SKILLS = {
    "skill-design": [
        "Color: pick ONE accent color and 2-3 neutrals. Use a near-black (#1a1a1a) for text "
        "on near-white (#fafafa), never pure #000 on #fff. Backgrounds slightly off-white feel "
        "more designed than stark white.",
        "Typography: one display font for headings, one readable font for body (system-ui stack "
        "is safe: -apple-system, Segoe UI, Roboto, sans-serif). Body 16-18px, line-height 1.5-1.6, "
        "max line width ~65ch for readability.",
        "Spacing: use a consistent scale (8px base: 8/16/24/32/48). Generous whitespace reads as "
        "premium; cramped reads as amateur. Pad sections vertically at least 48px.",
        "Layout: center content in a max-width container (~720-1100px), don't let text span the "
        "full viewport. Use flexbox/grid, not floats. Mobile-first: stack on small screens.",
        "Buttons: clear affordance — solid accent background, white text, padding 12px 24px, "
        "border-radius 8px, subtle hover state (darken 10% or lift with shadow). One primary "
        "button per view.",
    ],
    "skill-css": [
        "Reset: set box-sizing:border-box on *, margin:0 on body. Prevents layout surprises.",
        "Responsive: use a single max-width container + flexbox that wraps. Add one media query "
        "at ~640px to stack columns. Avoid fixed pixel widths on containers.",
        "Center anything: display:grid; place-items:center on the parent. For full-page centering, "
        "min-height:100vh on body.",
        "Micro-interactions: transition:all .2s ease on interactive elements; transform/opacity on "
        "hover. Cheap, makes the page feel alive. Don't animate layout properties (width/height) — "
        "use transform.",
        "Inline everything for a single-file page: <style> in head, <script> before </body>. No "
        "external requests means it always loads.",
    ],
    # Sourced from Mad's battle-tested stop-slop skill (real anti-AI-slop rules).
    "skill-antislop": [
        "Visual AI-slop to avoid: generic fonts everywhere (Inter, Roboto, Arial, system fonts), "
        "purple gradients on white/dark, predictable centered-hero + three-feature-cards layouts, "
        "cookie-cutter components. Give the page context-specific character with unique fonts and "
        "a cohesive theme.",
        "Cut throat-clearing openers in UI copy: 'Here's the thing', 'Here's what/why', 'The truth "
        "is', 'It turns out', 'Let me be clear'. State the content directly.",
        "No business jargon / marketing slop: 'unlock', 'elevate', 'seamless', 'cutting-edge', "
        "'leverage', 'empower', 'revolutionary', 'game-changing'. Use plain, specific words.",
        "No binary-contrast cliches: 'not X, it's Y', 'isn't X, it's Y', 'the question isn't X, "
        "it's Y'. State Y directly, drop the negation.",
        "No emphasis crutches: 'Full stop.', 'Let that sink in.', 'This matters because', 'Make no "
        "mistake'. They add nothing — delete them.",
        "Copy should be concrete, not filler. No 'Welcome to our amazing platform!'. Say the exact "
        "thing the user needs. Vary sentence length; avoid em-dashes and the rule-of-three everywhere.",
    ],
    "skill-security": [
        "Forms: always set type correctly (email/tel/number), add required where needed, and never "
        "trust client input — note in a comment that real validation happens server-side.",
        "Don't inject user input into innerHTML — use textContent to avoid XSS. If you must build "
        "HTML, escape <, >, &.",
        "External links: add rel='noopener noreferrer' on target='_blank' to prevent tab-nabbing.",
        "Don't put secrets, API keys, or tokens in client-side JS — they're visible to anyone.",
    ],
    "skill-process": [
        "REVIEW (Tuning Fork): check the file is complete (ends with </html>, no truncation), the "
        "feature actually works (logic is sound, event handlers wired), it's responsive, and there's "
        "no leftover placeholder/lorem text. Report concrete issues, not vibes.",
        "DEPLOY (Stage Tech): only deploy after review passes. Call deploy_site, report the EXACT "
        "URL the tool returns — never invent one. If the tool refuses (invalid page), report that "
        "so the Soloist can fix it.",
        "PLAN (Conductor): break the brief into the minimum steps. Name which agent does each. Don't "
        "over-engineer — a one-page site doesn't need a build system. Keep the plan to 3-5 steps.",
        "CODE (Soloist): build the smallest thing that fully satisfies the brief. Complete and "
        "valid beats elaborate and broken. Use the write_page tool; fill body/css/js slots.",
    ],
}


def main() -> None:
    with httpx.Client(timeout=30) as c:
        for lib, snippets in SKILLS.items():
            r = c.post(f"{URL}/library/create", headers=HEADERS, json={"name": lib})
            print(f"library {lib}: {r.json().get('result', r.text)[:60]}")
            for snip in snippets:
                rr = c.post(f"{URL}/memory/add", headers=HEADERS,
                            json={"library": lib, "content": snip})
                ok = rr.json().get("ok")
                print(f"  + {'ok' if ok else 'FAIL'}: {snip[:55]}...")
    print("\ndone — skill libraries seeded")


if __name__ == "__main__":
    main()
