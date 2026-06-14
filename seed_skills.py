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

try:
    from dotenv import load_dotenv
    # load a local .env if present (dev), but don't require one (Docker passes env)
    for p in (os.path.join(os.path.dirname(__file__), ".env"), ".env"):
        if os.path.isfile(p):
            load_dotenv(p)
            break
except Exception:
    pass

URL = os.environ.get("MGIMIND_URL", "http://127.0.0.1:8765")
# in the bundled-brain container a single MGIMIND_TOKEN is set; fall back to it.
TOKEN = (os.environ.get("MGIMIND_TOKEN_ARCHIVIST")
         or os.environ.get("MGIMIND_TOKEN")
         or "crescendo_archivist_tok")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# library -> list of skill snippets. Each snippet is one focused, actionable rule.
# fetch_skills pulls the top-3 SEMANTICALLY relevant per library per task, so a
# bigger, well-scoped pool means a sharper match to each brief: not noise.
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
        "Visual hierarchy: the most important element should be the largest / boldest / highest "
        "contrast. A hero headline at 2.5-3.5rem, section headings ~1.5rem, body 1rem. Size and "
        "weight guide the eye — don't make everything the same size.",
        "Contrast for readability: body text must hit WCAG AA (~4.5:1) against its background. "
        "Light-grey text on white (#999 on #fff) fails and reads as unfinished. Darken it.",
        "Color harmony: derive the palette from one hue. Use HSL — keep hue fixed, vary lightness "
        "for tints/shades. A single accent + neutrals beats a rainbow. For dark themes use a very "
        "dark desaturated background (#10131a), not pure black.",
        "Depth: convey elevation with soft shadows (0 4px 20px rgba(0,0,0,.08)) and subtle borders, "
        "not heavy 3D bevels. One or two elevation levels is enough.",
        "Imagery: if there's no real image, use a tasteful CSS gradient, a solid block, or an inline "
        "SVG shape — never a broken <img> or a base64 blob. An emoji can stand in as an icon.",
        "Sections: a landing page reads top-to-bottom as hero -> value/features -> proof/detail -> "
        "call to action. Give each section breathing room and a clear single purpose.",
        "Consistency: reuse the SAME border-radius, shadow, spacing scale, and accent everywhere. "
        "Inconsistent radii/spacing is the single biggest 'amateur' tell.",
        "Empty/edge states: a counter starts at a real value, a list shows real items, a form shows "
        "a real success message. Never ship a control that visibly does nothing.",
    ],
    "skill-css": [
        "Reset: set box-sizing:border-box on *, margin:0 on body. Prevents layout surprises.",
        "Responsive: use a single max-width container + flexbox/grid that wraps. Add one media "
        "query at ~640px to stack columns. Avoid fixed pixel widths on containers.",
        "Center anything: display:grid; place-items:center on the parent. For full-page centering, "
        "min-height:100vh on body.",
        "Micro-interactions: transition:all .2s ease on interactive elements; transform/opacity on "
        "hover. Cheap, makes the page feel alive. Don't animate layout properties (width/height) — "
        "use transform.",
        "Inline everything for a single-file page: <style> in head, <script> before </body>. No "
        "external requests means it always loads.",
        "Responsive grid without media queries: grid-template-columns: repeat(auto-fit, "
        "minmax(240px, 1fr)). Cards reflow on their own as the viewport narrows — ideal for "
        "feature cards.",
        "Fluid type: clamp(1.5rem, 4vw, 3rem) scales a headline smoothly between phone and desktop "
        "with no breakpoint. Use it for hero text.",
        "Use CSS custom properties for the theme: define --accent, --bg, --text, --radius on :root "
        "and reference them everywhere. One-line theme changes, guaranteed consistency.",
        "Prevent horizontal scroll: never set a fixed width wider than the viewport; use max-width "
        "+ width:100%. Add overflow-x:hidden on body as a safety net.",
        "Entry animation: @keyframes fade-up { from { opacity:0; transform:translateY(12px) } } and "
        "apply on hero/sections for a polished load. Keep it under 0.6s; respect "
        "prefers-reduced-motion.",
        "Buttons that work AND look it: cursor:pointer, a clear :hover and :active state, and a "
        ":focus-visible outline for keyboard users. A button with no hover state feels dead.",
        "Sticky header that doesn't jump: position:sticky; top:0; with a backdrop-filter:blur(8px) "
        "and a semi-transparent background reads as modern and stays readable over content.",
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
        "Pick a real, characterful font for headings and actually load it: a Google Fonts <link> in "
        "the head, e.g. Space Grotesk, Fraunces, Sora, Outfit. A system-font heading is the #1 "
        "generic-AI tell. The font must be linked or it silently falls back to system serif/sans.",
        "Give the page a theme that fits its subject: a finance app reads calm and trustworthy "
        "(deep blue/teal, crisp), a fitness app reads energetic (bold, high-contrast), a journal "
        "reads warm (serif, muted). The vibe should match the product, not a generic SaaS template.",
        "Real, specific copy beats lorem ipsum and vague claims. Name concrete features and "
        "benefits tied to THIS product. Never ship 'Lorem ipsum', 'Feature one', 'Your text here', "
        "or TODO placeholders — they fail the deploy gate.",
        "Headlines: write a specific, benefit-led headline, not 'Welcome' or the product name alone. "
        "'Track every expense in ten seconds' beats 'The best budgeting app'.",
        "Avoid the default purple-to-blue gradient and the three-identical-cards row unless the "
        "brief asks for them. Vary card sizes, use an asymmetric layout, or a distinct accent to "
        "escape the template look.",
    ],
    # These mirror the deterministic acceptance gate (check_page) so the model
    # prevents what the gate would otherwise block.
    "skill-security": [
        "Forms: set the input type correctly (email/tel/number), add required where needed, and "
        "never trust client input — note in a comment that real validation happens server-side.",
        "Don't inject user input into innerHTML — use textContent to avoid XSS. If you must build "
        "HTML, escape <, >, &.",
        "External links: ALWAYS add rel='noopener noreferrer' on any target='_blank' link to "
        "prevent tab-nabbing. The deploy gate blocks a _blank link missing noopener.",
        "Don't put secrets, API keys, or tokens in client-side JS — they're visible to anyone, and "
        "a key-shaped string (sk-..., ghp_..., AKIA...) hard-codes the deploy.",
        "Load every resource over https:// — scripts, stylesheets, fonts, images. An http:// "
        "subresource is mixed content the browser blocks, and the deploy gate refuses it.",
        "Every interactive control must actually do something: a button needs a real click handler "
        "(addEventListener) with a visible effect, or be type=submit inside a form; a link needs a "
        "real URL or an in-page #id that exists. The gate blocks dead and empty controls.",
        "Forms with no backend: handle submit in JS (e.preventDefault) and show a real success "
        "message, so the control works end-to-end on a static page instead of going nowhere.",
        "Accessibility is correctness: every <img> needs an alt, every icon-only button needs an "
        "aria-label, inputs need a <label>. It also keeps controls from reading as empty/broken.",
    ],
    "skill-process": [
        "REVIEW (Tuning Fork): FIRST call check_page (pass a key brief term as must_contain) to run "
        "the deterministic gate. If it reports CHECK FAILED, those are issues — list them. Then read "
        "the code and review correctness against the brief. Reply CLEAN only if the gate passed and "
        "the code is right.",
        "REVIEW depth: confirm the file is complete (ends with </html>, no truncation), the feature "
        "actually works (handlers wired, logic sound), it's responsive, has no placeholder/lorem "
        "text, and every control does something. Adversarially name the strongest way it would fail "
        "in production. Report concrete issues, not vibes.",
        "DEPLOY (Stage Tech): only deploy after review passes. Call deploy_site, report the EXACT "
        "URL the tool returns — never invent one. If the tool refuses (invalid page), report that "
        "so the Soloist can fix it.",
        "PLAN (Conductor): break the brief into the minimum steps. Name which agent does each. Don't "
        "over-engineer — a one-page site doesn't need a build system. Keep the plan to 3-5 steps.",
        "CODE (Soloist): build the smallest thing that fully satisfies the brief. Complete and "
        "valid beats elaborate and broken. Use the write_page tool; fill body/css/js slots. ACTUALLY "
        "call the tool — narrating that you built it leaves no file and ships nothing.",
        "RESOURCE CONTRACT (Conductor): infer from the brief the complete list of external access "
        "the project needs (hosting, domain, API key, data source) before any work. Don't assume "
        "tools the brief didn't imply. A plain static page needs nothing beyond the deploy account.",
        "FIX LOOP (Soloist): when the reviewer lists issues, fix exactly those with write_page and "
        "reply one line. Don't rewrite from scratch and don't add unrelated changes — converge, so "
        "the review loop closes within its round cap.",
        "HONESTY: never claim an artifact you didn't produce. Report the real deploy URL, the real "
        "review verdict, the real failure. The audit trail grounds every claim against the artifact, "
        "so a fabricated one is caught.",
    ],
}


def _already_seeded(c, lib: str) -> bool:
    """True if this library already has skills — keeps re-runs idempotent so a
    second `docker compose up` doesn't duplicate every entry."""
    try:
        r = c.post(f"{URL}/memory/search", headers=HEADERS,
                   json={"query": "skill", "library": lib, "limit": 1})
        return bool(r.json().get("results"))
    except Exception:
        return False


def main() -> None:
    # --force re-seeds even libraries that already have skills (used to top up an
    # existing brain with an expanded skill set; the idempotent skip is for the
    # clean-clone docker path).
    import sys
    force = "--force" in sys.argv
    seeded, skipped = 0, 0
    with httpx.Client(timeout=30) as c:
        for lib, snippets in SKILLS.items():
            c.post(f"{URL}/library/create", headers=HEADERS, json={"name": lib})
            if not force and _already_seeded(c, lib):
                print(f"library {lib}: already seeded, skipping")
                skipped += 1
                continue
            print(f"library {lib}: seeding {len(snippets)} skills")
            for snip in snippets:
                rr = c.post(f"{URL}/memory/add", headers=HEADERS,
                            json={"library": lib, "content": snip})
                if rr.json().get("ok"):
                    seeded += 1
                print(f"  + {'ok' if rr.json().get('ok') else 'FAIL'}: {snip[:55]}...")
    print(f"\ndone — {seeded} skills seeded, {skipped} libraries already present")


if __name__ == "__main__":
    main()
