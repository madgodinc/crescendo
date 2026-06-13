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


# Fixed HTML shell — assembled in Python so a weak model can NEVER break the
# structure, truncate </html>, or smuggle base64 into the document skeleton.
SHELL = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
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
    out, _ = await proc.communicate()
    text = out.decode(errors="replace")
    urls = re.findall(r"https://[a-z0-9.-]+\.pages\.dev", text)
    if urls:
        return f"DEPLOYED: {urls[-1]}  (production: https://{PROJECT}-5vj.pages.dev)"
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
    """For the Tuning Fork: read the page to review it."""
    return _read_tools()


def build_deploy_tools() -> list[StructuredTool]:
    """For the Stage Tech: read + deploy (with the validate gate)."""
    return _read_tools() + [
        StructuredTool.from_function(
            coroutine=_deploy_site, name="deploy_site",
            description="Deploy the site workspace to Cloudflare Pages and return the "
                        "REAL live URL. Refuses if the page is invalid/truncated.",
        ),
    ]
