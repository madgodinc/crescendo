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


class WriteArgs(BaseModel):
    path: str = Field(description="Relative file path inside the site, e.g. 'index.html'.")
    content: str = Field(description="Full file content to write.")


def _safe_path(rel: str) -> str:
    rel = rel.lstrip("/")
    if ".." in rel.split("/"):
        raise ValueError("path traversal not allowed")
    return os.path.join(WORKSPACE, rel)


async def _write_file(path: str, content: str) -> str:
    full = _safe_path(path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return f"wrote {path} ({len(content)} bytes)"


async def _deploy_site() -> str:
    if not os.path.isdir(WORKSPACE) or not os.listdir(WORKSPACE):
        return "ERROR: nothing to deploy — write files first."
    proc = await asyncio.create_subprocess_exec(
        WRANGLER, "pages", "deploy", WORKSPACE,
        f"--project-name={PROJECT}", "--branch=main", "--commit-dirty=true",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PATH": os.path.expanduser("~/.npm-global/bin:") + os.environ.get("PATH", "")},
    )
    out, _ = await proc.communicate()
    text = out.decode(errors="replace")
    m = re.search(r"https://[a-z0-9.-]+\.pages\.dev", text)
    if m:
        return f"DEPLOYED: {m.group(0)}  (production: https://{PROJECT}-5vj.pages.dev)"
    return f"deploy finished but no URL parsed:\n{text[-400:]}"


def build_deploy_tools() -> list[StructuredTool]:
    return [
        StructuredTool.from_function(
            coroutine=_write_file, name="write_file",
            description="Write a file of the product into the site workspace. "
                        "Use this to save the HTML/CSS/JS the Soloist produces.",
            args_schema=WriteArgs,
        ),
        StructuredTool.from_function(
            coroutine=_deploy_site, name="deploy_site",
            description="Deploy the site workspace to Cloudflare Pages and return the "
                        "REAL live URL. Only the Stage Tech calls this, after review passes.",
        ),
    ]
