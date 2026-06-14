"""mgi-mind memory exposed as LangChain tools for Crescendo agents.

Each agent gets tools bound to its own bearer token, so every write is
attributed to that agent in the audit trail (Track-3). The Archivist relies on
these; other agents may use them too.

build_memory_tools(token) -> [remember, recall]
"""

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


_STATE_MARKER = "CRESCENDO_RUNSTATE"  # KV key prefix for run checkpoints


async def save_checkpoint(token: str, run_id: str, state: dict) -> bool:
    """Persist a run's full state to mgi-mind's KV store so it survives a crash.

    Uses the raw /kv/set blob surface — NOT searchable memory. Searchable memory
    chunks and embeds, which mangles a large JSON checkpoint into pieces that
    don't round-trip; KV stores the value verbatim under one key."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{MGIMIND_URL}/kv/set", headers=headers,
                             json={"key": f"{_STATE_MARKER}:{run_id}", "value": state})
            r.raise_for_status()
            return True
    except Exception:
        return False


async def load_checkpoint(token: str, run_id: str) -> dict | None:
    """Load the checkpoint for a run_id, or None.

    Returns None when no checkpoint exists OR it's already marked finished —
    so a re-run of a brief that already shipped starts clean."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{MGIMIND_URL}/kv/get", headers=headers,
                             json={"key": f"{_STATE_MARKER}:{run_id}"})
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None
    if not data.get("found"):
        return None
    state = data.get("value")
    if not isinstance(state, dict) or state.get("_finished"):
        return None
    return state


# ── Live dashboard trail ──────────────────────────────────────────────────────
# The dashboard reads a run's live state from mgi-mind (single source of truth),
# so it survives a maestro restart and stays a vitrine on top of Band. Each run
# has a per-run live document under CRESCENDO_LIVE:<run_id>, plus one pointer key
# CRESCENDO_ACTIVE holding the current run id and a capped history list.
_LIVE_MARKER = "CRESCENDO_LIVE"
_ACTIVE_KEY = "CRESCENDO_ACTIVE"
_TOK_MARKER = "CRESCENDO_TOK"   # per-agent running token total (each agent owns its key)


async def set_token_total(token: str, agent: str, total: int) -> bool:
    """Publish an agent's running token total to its OWN KV key (no cross-agent
    race — each agent only writes its own). Maestro reads the delta per turn."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{MGIMIND_URL}/kv/set", headers=headers,
                             json={"key": f"{_TOK_MARKER}:{agent}", "value": int(total)})
            r.raise_for_status()
            return True
    except Exception:
        return False


async def get_token_total(token: str, agent: str) -> int:
    """Read an agent's running token total (0 if none yet)."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{MGIMIND_URL}/kv/get", headers=headers,
                             json={"key": f"{_TOK_MARKER}:{agent}"})
            r.raise_for_status()
            data = r.json()
        return int(data.get("value") or 0) if data.get("found") else 0
    except Exception:
        return 0


async def save_live(token: str, run_id: str, doc: dict) -> bool:
    """Overwrite the per-run live document. Whole-doc write (no read-modify-
    write) so a concurrent dashboard read sees either the old or the new
    complete blob, never a torn one. The timeline is small (tens of events)."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{MGIMIND_URL}/kv/set", headers=headers,
                             json={"key": f"{_LIVE_MARKER}:{run_id}", "value": doc})
            r.raise_for_status()
            return True
    except Exception:
        return False


async def update_active(token: str, run_id: str, brief: str, status: str,
                        updated: str, cap: int = 15) -> bool:
    """Set the active-run pointer and upsert this run into the history list.

    Read-modify-write on CRESCENDO_ACTIVE, but called only at run start/end (and
    on failure) — twice per run, single maestro process, so no real contention."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            g = await c.post(f"{MGIMIND_URL}/kv/get", headers=headers,
                             json={"key": _ACTIVE_KEY})
            g.raise_for_status()
            cur = g.json().get("value") if g.json().get("found") else None
            doc = cur if isinstance(cur, dict) else {}
            recent = [r for r in (doc.get("recent") or []) if r.get("run_id") != run_id]
            recent.insert(0, {"run_id": run_id, "brief": brief,
                              "status": status, "updated": updated})
            doc = {"current": run_id, "recent": recent[:cap]}
            s = await c.post(f"{MGIMIND_URL}/kv/set", headers=headers,
                             json={"key": _ACTIVE_KEY, "value": doc})
            s.raise_for_status()
            return True
    except Exception:
        return False


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
