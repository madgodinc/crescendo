"""mgi-mind memory exposed as LangChain tools for Crescendo agents.

Each agent gets tools bound to its own bearer token, so every write is
attributed to that agent in the audit trail (Track-3). The Archivist relies on
these; other agents may use them too.

build_memory_tools(token) -> [remember, recall]
"""

import json
import os
import re

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

MGIMIND_URL = os.environ.get("MGIMIND_URL", "http://127.0.0.1:8765")
LIBRARY = os.environ.get("MGIMIND_LIBRARY", "crescendo")


async def recall_playbook(token: str, error: str, context: str = "", limit: int = 3) -> list[dict]:
    """Recall error->fix playbooks for a failure, newest-verified first.

    This is the self-learning loop's READ side: before re-grinding a known
    failure, the orchestra asks memory whether it has solved this before. The
    HTTP layer maps a bare `query` onto `context`; we send both fields so the
    lexical error match and the semantic context match both fire.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body: dict = {"error": error, "limit": limit}
    if context:
        body["context"] = context
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{MGIMIND_URL}/procedure/recall", headers=headers, json=body)
            r.raise_for_status()
            data = r.json()
    except Exception:
        return []
    # Newer versions may return structured rows; honor those first.
    if isinstance(data, dict) and (data.get("results") or data.get("procedures")):
        return data.get("results") or data.get("procedures")
    if isinstance(data, list):
        return data
    # The HTTP layer otherwise returns a human-formatted string under "result".
    # Parse each block ([... verified] id: ... / error: / fix: / when:) into a dict.
    text = data.get("result", "") if isinstance(data, dict) else ""
    return _parse_recall_text(text)


def _parse_recall_text(text: str) -> list[dict]:
    """Parse the CLI-formatted procedure dump into structured playbook dicts."""
    out: list[dict] = []
    cur: dict | None = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        m = re.match(r"\[(.+?)\].*\bid:\s*(\S+)", line)
        if m:
            if cur:
                out.append(cur)
            cur = {"id": m.group(2), "verified": "verified" in m.group(1).lower(),
                   "error": "", "fix": "", "context": ""}
            continue
        if cur is None:
            continue
        if line.startswith("error:"):
            cur["error"] = line[len("error:"):].strip()
        elif line.startswith("fix:"):
            cur["fix"] = line[len("fix:"):].strip()
        elif line.startswith("when:"):
            cur["context"] = line[len("when:"):].strip()
    if cur:
        out.append(cur)
    return [p for p in out if p.get("fix")]


async def learn_playbook(token: str, error: str, fix: str, context: str = "",
                         provenance: str = "", verified: bool = False) -> bool:
    """Write an error->fix playbook back to procedural memory (the WRITE side).

    `verified=True` only when a deterministic signal confirmed the fix worked
    (here: the deploy gate accepted the rebuilt page). Unverified lessons are
    stored with low weight and surface quietly until a later run confirms them.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"error": error, "fix": fix, "verified": verified}
    if context:
        body["context"] = context
    if provenance:
        body["provenance"] = provenance
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{MGIMIND_URL}/procedure/learn", headers=headers, json=body)
            r.raise_for_status()
            return True
    except Exception:
        return False


def summarize_playbooks(playbooks: list[dict]) -> str:
    """Render recalled playbooks as a compact 'apply this known fix' block."""
    lines = []
    for p in playbooks:
        fix = (p.get("fix") or "").strip()
        if not fix:
            continue
        ctx = (p.get("context") or p.get("error") or "").strip()
        tag = " (verified)" if p.get("verified") else ""
        lines.append(f"- {fix}{tag}" + (f"  [{ctx[:60]}]" if ctx else ""))
    if not lines:
        return ""
    return ("Known fixes for this kind of failure (apply directly, don't "
            "rediscover):\n" + "\n".join(lines))


async def fetch_skills(token: str, query: str, libraries: list[str], per_lib: int = 3) -> str:
    """Pull the most relevant skills for a task from the given skill libraries.

    This is the Archivist's core service: weak models get expert guidance pulled
    from memory before they work, so they punch above bare-model quality.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    found: list[str] = []
    async with httpx.AsyncClient(timeout=20) as c:
        for lib in libraries:
            try:
                r = await c.post(f"{MGIMIND_URL}/memory/search", headers=headers,
                                 json={"query": query, "library": lib, "limit": per_lib})
                for m in (r.json().get("results") or []):
                    text = (m.get("content") or "").strip()
                    if text:
                        found.append(f"- {text}")
            except Exception:
                continue
    if not found:
        return ""
    return "Relevant skills from memory (apply these):\n" + "\n".join(found)


class RememberArgs(BaseModel):
    content: str = Field(description="A fact, decision, or piece of context to store in memory.")


class RecallArgs(BaseModel):
    query: str = Field(description="What to look for — a topic, decision, or question.")
    limit: int = Field(default=5, description="Max results to return.")


STATE_LIBRARY = os.environ.get("MGIMIND_STATE_LIBRARY", "crescendo-state")
_STATE_MARKER = "CRESCENDO_RUNSTATE"


async def save_checkpoint(token: str, run_id: str, state: dict) -> bool:
    """Persist a run's full state to mgi-mind so it survives a crash.

    Stored as one memory line: '<marker> <run_id> <json>'. The newest line for
    a run_id is the live checkpoint; older ones are stale history (cheap, and
    avoids needing an update/delete API)."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    blob = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    content = f"{_STATE_MARKER} {run_id} {blob}"
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{MGIMIND_URL}/memory/add", headers=headers,
                             json={"library": STATE_LIBRARY, "content": content})
            # On a fresh server the state library may not exist yet — create it once
            # and retry, so resume works out of the box.
            if r.status_code == 400 and "not found" in r.text.lower():
                await c.post(f"{MGIMIND_URL}/library/create", headers=headers,
                             json={"name": STATE_LIBRARY})
                r = await c.post(f"{MGIMIND_URL}/memory/add", headers=headers,
                                 json={"library": STATE_LIBRARY, "content": content})
            r.raise_for_status()
            return True
    except Exception:
        return False


async def load_checkpoint(token: str, run_id: str) -> dict | None:
    """Load the newest unfinished checkpoint for a run_id, or None.

    Returns None when no checkpoint exists OR the latest is already marked
    done — so a re-run of a finished brief starts clean."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{MGIMIND_URL}/memory/search", headers=headers,
                             json={"query": f"{_STATE_MARKER} {run_id}",
                                   "library": STATE_LIBRARY, "limit": 25})
            r.raise_for_status()
            results = r.json().get("results") or []
    except Exception:
        return None
    # Parse every matching state line for this exact run_id, keep the newest by
    # phase progress (more completed phases = more recent). Search isn't ordered
    # by time, so we rank by how far each checkpoint got.
    best, best_score = None, -1
    prefix = f"{_STATE_MARKER} {run_id} "
    for m in results:
        text = (m.get("content") or "").strip()
        if not text.startswith(prefix):
            continue
        try:
            state = json.loads(text[len(prefix):])
        except Exception:
            continue
        score = len(state.get("_done_phases", []))
        if score > best_score:
            best, best_score = state, score
    if best is None or best.get("_finished"):
        return None
    return best


def build_memory_tools(token: str) -> list[StructuredTool]:
    """Return [remember, recall] tools wired to mgi-mind with this agent's token."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def remember(content: str) -> str:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{MGIMIND_URL}/memory/add", headers=headers,
                             json={"library": LIBRARY, "content": content})
            r.raise_for_status()
            return r.json().get("result", "stored")

    async def recall(query: str, limit: int = 5) -> str:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{MGIMIND_URL}/memory/search", headers=headers,
                             json={"query": query, "limit": limit})
            r.raise_for_status()
            results = r.json().get("results", [])
        if not results:
            return "No relevant memory found."
        lines = []
        for m in results:
            who = m.get("author") or "?"
            lines.append(f"- ({who}, score {m.get('score', 0):.2f}) {m.get('content', '')}")
        return "Relevant memory:\n" + "\n".join(lines)

    return [
        StructuredTool.from_function(
            coroutine=remember,
            name="remember",
            description="Store a fact, decision, or context into shared project memory. "
                        "Use after any meaningful decision or result so it persists across the run.",
            args_schema=RememberArgs,
        ),
        StructuredTool.from_function(
            coroutine=recall,
            name="recall",
            description="Search shared project memory for relevant prior context, decisions, "
                        "or facts. Use before starting work to pull only what's relevant.",
            args_schema=RecallArgs,
        ),
    ]
