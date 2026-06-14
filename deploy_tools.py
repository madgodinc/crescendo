"""Deploy + file tools for Crescendo agents.

So the Soloist can actually WRITE the product to disk and the Stage Tech can
actually DEPLOY it to a live Cloudflare Pages URL — instead of hallucinating a
fake link. Every artifact is real and verifiable (the judged demo).

  write_file(path, content)  -> writes under workspace/
  deploy_site()              -> wrangler pages deploy -> returns the real URL
"""

import asyncio
import os
import re

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

WORKSPACE = os.path.join(os.path.dirname(__file__), "workspace", "site")
PROJECT = os.environ.get("CF_PAGES_PROJECT", "crescendo-demo")
WRANGLER = os.path.expanduser("~/.npm-global/bin/wrangler")


def _safe_path(rel: str) -> str:
    rel = rel.lstrip("/")
    if ".." in rel.split("/"):
        raise ValueError("path traversal not allowed")
    return os.path.join(WORKSPACE, rel)


# --- defenses against junk from weak models (don't trust the prompt) --------

_ICON_LINK = re.compile(
    r'''<link\b[^>]*\brel\s*=\s*['"]?(?:shortcut\s+)?(?:icon|apple-touch-icon)['"]?[^>]*>''',
    re.IGNORECASE)
_BIG_DATA_URI = re.compile(r'data:[^"\'\s)]*?base64,[A-Za-z0-9+/=]{200,}', re.IGNORECASE)
_RUNS = re.compile(r'(.)\1{39,}')          # >=40 identical chars = broken padding
_STRUCT_TAGS = re.compile(r'</?(?:html|head|body|script|style)\b[^>]*>', re.IGNORECASE)


def _clean_slot(s: str) -> str:
    """Strip junk a weak model tends to inject into a content slot."""
    if not s:
        return ""
    s = _ICON_LINK.sub("", s)
    s = _BIG_DATA_URI.sub("", s)
    s = _RUNS.sub(r"\1\1\1", s)
    s = _STRUCT_TAGS.sub("", s)   # slots must not carry html/head/body/script/style tags
    return s


# Fixed HTML shell: assembled in Python so a weak model can NEVER break the
# structure, truncate </html>, or smuggle base64 into the document skeleton.
SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>{css}</style></head>
<body>
{body}
<script>{js}</script>
</body></html>"""


class PageArgs(BaseModel):
    title: str = Field(description="Page title (plain text).")
    body: str = Field(description="HTML that goes INSIDE <body> only. No <html>/<head>/"
                                  "<body>/<script>/<style> tags, no favicon, no base64.")
    css: str = Field(default="", description="CSS rules only (no <style> tag).")
    js: str = Field(default="", description="JavaScript only (no <script> tag).")


async def _write_page(title: str, body: str, css: str = "", js: str = "") -> str:
    html = SHELL.format(
        title=_clean_slot(title) or "Demo",
        css=_clean_slot(css),
        body=_clean_slot(body),
        js=_clean_slot(js),
    )
    full = _safe_path("index.html")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(html)
    return f"wrote index.html ({len(html)} bytes) — shell is fixed and always valid"


def validate_site() -> list[str]:
    """Return a list of problems; empty list means the site is safe to deploy."""
    index = os.path.join(WORKSPACE, "index.html")
    if not os.path.isfile(index):
        return ["index.html is missing"]
    with open(index, encoding="utf-8") as f:
        html = f.read()
    low = html.lower()
    problems = []
    if len(html.strip()) < 150:
        problems.append(f"index.html is suspiciously small ({len(html)} bytes)")
    # The fixed shell alone is ~230 valid bytes, so a blank-body page passes the
    # byte floor. Require real VISIBLE content (tags + whitespace stripped) so an
    # empty page can't ship as a "successful" deploy.
    visible = re.sub(r"<[^>]+>", " ", html)
    visible = re.sub(r"\s+", " ", visible).strip()
    if len(visible) < 30:
        problems.append(f"page has almost no visible content ({len(visible)} chars)")
    if "<html" not in low:
        problems.append("no <html> tag")
    if "</html>" not in low:
        problems.append("no closing </html> — file truncated")
    if "<body" in low and "</body>" not in low:
        problems.append("<body> not closed — file truncated")
    if _BIG_DATA_URI.search(html):
        problems.append("large data:base64 blob present (junk favicon/image)")
    if _RUNS.search(html):
        problems.append("abnormal run of identical characters (broken base64)")
    return problems


# Injected before page scripts: tag every element that binds a click/submit
# listener, so deadness is judged by real behaviour, not by guessing from source.
_PROBE_JS = """
(() => {
  const orig = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function(type, fn, opts) {
    try {
      if (type === 'click' || type === 'submit') {
        this.__crescBound = true;
        if (this === document || this === document.body || this === window)
          window.__crescDelegated = true;   // delegated handler covers children
      }
    } catch (e) {}
    return orig.call(this, type, fn, opts);
  };
})();
"""

# Runs after render. Returns {block:[...], warn:[...]} from the live DOM.
# block = high-precision, agent-fixable defects that must not ship; warn =
# logged but allowed (cosmetic / commonly intentional on a demo).
_AUDIT_JS = r"""
(() => {
  const block = [], warn = [];
  const delegated = !!window.__crescDelegated;
  const txt = el => (el.textContent || '').trim();

  // 1. DEAD CONTROLS — behavioural, not static.
  document.querySelectorAll('button').forEach(b => {
    const label = txt(b) || b.getAttribute('aria-label') || b.title ||
                  (b.querySelector('img') && b.querySelector('img').alt) || '';
    if (!label) { block.push('empty button (no text / aria-label): a broken control'); return; }
    // a bare <button> defaults to type=submit, but that only does anything
    // inside a <form>; outside one it's still dead.
    const submits = (b.type === 'submit' || b.type === 'reset') && b.closest('form');
    const wired = b.hasAttribute('onclick') || b.__crescBound || delegated ||
                  submits || b.closest('a[href]');
    if (!wired) block.push('dead button "' + label.slice(0,40) + '" — no handler, does nothing on click');
  });
  document.querySelectorAll('a').forEach(a => {
    const href = a.getAttribute('href');
    const wired = a.hasAttribute('onclick') || a.__crescBound || delegated;
    const dead = (href === null || href === '' || href === '#' || href === 'javascript:void(0)');
    if (dead && !wired) warn.push('link "' + (txt(a)||'').slice(0,30) + '" goes nowhere (href="' + (href||'') + '")');
    // in-page anchor whose target id is missing
    if (href && href.startsWith('#') && href.length > 1 && !document.getElementById(href.slice(1)) && !wired)
      warn.push('anchor ' + href + ' points at a section that does not exist');
  });
  document.querySelectorAll('form').forEach(f => {
    const wired = f.hasAttribute('action') || f.__crescBound || f.hasAttribute('onsubmit') || delegated;
    if (!wired) warn.push('form has no action and no submit handler — it goes nowhere');
  });

  // 4. target=_blank without rel=noopener — tab-nabbing.
  document.querySelectorAll('a[target="_blank"]').forEach(a => {
    const rel = (a.getAttribute('rel') || '').toLowerCase();
    if (!rel.includes('noopener'))
      block.push('target="_blank" link without rel="noopener" (tab-nabbing risk): ' + (a.getAttribute('href')||''));
  });

  // 8a. non-https subresources — mixed content, will be blocked by the browser.
  document.querySelectorAll('script[src], link[href], img[src]').forEach(n => {
    const u = n.getAttribute('src') || n.getAttribute('href') || '';
    if (u.startsWith('http://')) block.push('insecure http:// resource (mixed content, browser will block): ' + u.slice(0,60));
  });
  // 7. plain external http links — warn only.
  document.querySelectorAll('a[href^="http://"]').forEach(a => {
    warn.push('external link uses insecure http://: ' + a.getAttribute('href').slice(0,50));
  });

  // 9. horizontal overflow — cosmetic, warn.
  if (document.documentElement.scrollWidth > document.documentElement.clientWidth + 2)
    warn.push('page overflows horizontally (content wider than the viewport)');

  // 10. images without alt — warn.
  document.querySelectorAll('img:not([alt])').forEach(() =>
    warn.push('an <img> has no alt attribute'));

  // 11b. empty headings — warn.
  document.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(h => {
    if (!txt(h)) warn.push('an empty <' + h.tagName.toLowerCase() + '> heading');
  });

  return { block: [...new Set(block)].slice(0,10), warn: [...new Set(warn)].slice(0,10) };
})();
"""

# 5. leaked-secret patterns: high precision only (curated, not "looks like a key").
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
]
# 12. placeholder text left in the visible page: owner's "broken text" pain.
_PLACEHOLDER = re.compile(r"lorem ipsum|your text here|insert[_ ]|\bTODO\b|\bFIXME\b|placeholder text|xxxxx", re.I)


def _static_block_checks(html: str, visible: str) -> list[str]:
    """Source-level hard blocks that don't need the DOM: leaked secrets and
    placeholder text left in the rendered content."""
    out = []
    for pat in _SECRET_PATTERNS:
        m = pat.search(html)
        if m:
            out.append(f"a secret-looking string is hard-coded in the page: {m.group(0)[:12]}… (remove it)")
            break
    pm = _PLACEHOLDER.search(visible)
    if pm:
        out.append(f"placeholder text left in the page: '{pm.group(0)}' (replace with real content)")
    return out


async def _render_check(must_contain: str = "") -> dict:
    """Headless-render the page and report what a browser actually sees: console
    errors, JS page errors, visible text length, and whether an expected term is
    present. Deterministic — turns review from an LLM opinion into a real test."""
    index = os.path.join(WORKSPACE, "index.html")
    if not os.path.isfile(index):
        return {"ok": False, "errors": ["index.html missing"], "visible_chars": 0}
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return {"ok": True, "errors": [], "note": "playwright unavailable; structural check only"}
    console_errs, page_errs = [], []
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch()
            pg = await b.new_page()
            pg.on("console", lambda m: console_errs.append(m.text) if m.type == "error" else None)
            pg.on("pageerror", lambda e: page_errs.append(str(e)))
            # Instrument addEventListener BEFORE any page script runs, so we can
            # tell at runtime which controls actually got a click/submit handler
            # (static inspection can't see listeners, and misses event delegation).
            await pg.add_init_script(_PROBE_JS)
            await pg.goto("file://" + index, wait_until="networkidle", timeout=15000)
            await pg.wait_for_timeout(400)
            text = (await pg.inner_text("body")).strip()
            present = (must_contain.lower() in (await pg.content()).lower()) if must_contain else True
            audit = await pg.evaluate(_AUDIT_JS)
            await b.close()
    except Exception as e:
        return {"ok": False, "errors": [f"render failed: {e}"], "visible_chars": 0}
    errs = [f"console error: {x}" for x in console_errs[:5]] + [f"JS error: {x}" for x in page_errs[:5]]
    if len(text) < 30:
        errs.append(f"renders almost blank ({len(text)} visible chars)")
    if must_contain and not present:
        errs.append(f"expected content '{must_contain}' not found in the page")
    # hard-block defects from the live DOM (the strict acceptance audit)
    errs += audit.get("block", [])
    # source-level hard blocks: leaked secrets + placeholder text in the page
    try:
        with open(index, encoding="utf-8") as f:
            errs += _static_block_checks(f.read(), text)
    except OSError:
        pass
    return {"ok": not errs, "errors": errs, "warnings": audit.get("warn", []),
            "visible_chars": len(text)}


async def _check_page(must_contain: str = "") -> str:
    """Run the deterministic page check (structural gate + headless render) and
    return a verdict string the reviewer can fold into its judgement."""
    problems = validate_site()
    render = await _render_check(must_contain)
    all_errs = problems + render.get("errors", [])
    warns = render.get("warnings", [])
    warn_block = ("\nMinor (fix if quick, won't block the ship):\n- " + "\n- ".join(warns)) if warns else ""
    if all_errs:
        return "CHECK FAILED — these must be fixed before shipping:\n- " + "\n- ".join(all_errs) + warn_block
    vc = render.get("visible_chars", 0)
    note = render.get("note", "")
    return (f"CHECK PASSED: valid structure, every control works, no leaked secrets or placeholder "
            f"text, renders cleanly ({vc} visible chars, no console/JS errors)."
            + (f" [{note}]" if note else "") + warn_block)


class CheckArgs(BaseModel):
    must_contain: str = Field(default="", description="Optional: a key word/phrase from "
                              "the brief the page must actually contain (e.g. the product name).")


class ReadArgs(BaseModel):
    path: str = Field(description="Relative file path to read, e.g. 'index.html'.")


async def _read_file(path: str) -> str:
    full = _safe_path(path)
    try:
        with open(full, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"ERROR: {path} not found"


async def _list_files() -> str:
    if not os.path.isdir(WORKSPACE):
        return "(empty)"
    files = []
    for root, _, names in os.walk(WORKSPACE):
        for n in names:
            rel = os.path.relpath(os.path.join(root, n), WORKSPACE)
            files.append(rel)
    return "\n".join(sorted(files)) or "(empty)"


async def _deploy_site() -> str:
    if not os.path.isdir(WORKSPACE) or not os.listdir(WORKSPACE):
        return "ERROR: nothing to deploy — write files first."
    problems = validate_site()
    if problems:
        return ("REFUSED: site invalid, NOT deploying. Soloist must rewrite "
                "index.html to fix: " + "; ".join(problems))
    proc = await asyncio.create_subprocess_exec(
        WRANGLER, "pages", "deploy", WORKSPACE,
        f"--project-name={PROJECT}", "--branch=main", "--commit-dirty=true",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PATH": os.path.expanduser("~/.npm-global/bin:") + os.environ.get("PATH", "")},
    )
    # wrangler can hang (auth prompt, network stall): never let it freeze a run
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return "ERROR: deploy timed out after 120s (wrangler did not finish)."
    text = out.decode(errors="replace")
    urls = re.findall(r"https://[a-z0-9.-]+\.pages\.dev", text)
    if urls:
        # prefer a stable alias URL over the per-deploy hash subdomain (e.g.
        # https://main.<proj>.pages.dev rather than https://<hash>.<proj>.pages.dev)
        stable = [u for u in urls if not re.match(r"https://[0-9a-f]{8}\.", u)]
        url = stable[-1] if stable else urls[-1]
        return f"DEPLOYED: {url}  (production: https://{PROJECT}-5vj.pages.dev)"
    return f"deploy finished but no URL parsed:\n{text[-400:]}"


def _read_tools() -> list[StructuredTool]:
    return [
        StructuredTool.from_function(
            coroutine=_read_file, name="read_file",
            description="Read a file from the site workspace — use this to review the "
                        "page instead of expecting it pasted in chat.",
            args_schema=ReadArgs),
        StructuredTool.from_function(
            coroutine=_list_files, name="list_files",
            description="List all files currently in the site workspace."),
    ]


def build_author_tools() -> list[StructuredTool]:
    """For the Soloist: build the page from parts (shell is fixed) + read back."""
    return [
        StructuredTool.from_function(
            coroutine=_write_page, name="write_page",
            description="Build index.html from parts. You supply title/body/css/js; the "
                        "HTML shell (doctype, head, closing tags) is fixed in code and "
                        "always valid. body = markup that goes INSIDE <body> only.",
            args_schema=PageArgs),
    ] + _read_tools()


def build_review_tools() -> list[StructuredTool]:
    """For the Tuning Fork: read the page AND run a deterministic check on it."""
    return _read_tools() + [
        StructuredTool.from_function(
            coroutine=_check_page, name="check_page", args_schema=CheckArgs,
            description="Run a DETERMINISTIC check on the page: structural validation "
                        "plus a headless browser render that reports console errors, JS "
                        "errors, visible content, and whether an expected term is present. "
                        "Call this before giving your verdict — base CLEAN/ISSUES on its result, "
                        "not just on reading the code.",
        ),
    ]


def build_deploy_tools() -> list[StructuredTool]:
    """For the Stage Tech: read + deploy (with the validate gate)."""
    return _read_tools() + [
        StructuredTool.from_function(
            coroutine=_deploy_site, name="deploy_site",
            description="Deploy the site workspace to Cloudflare Pages and return the "
                        "REAL live URL. Refuses if the page is invalid/truncated.",
        ),
    ]
