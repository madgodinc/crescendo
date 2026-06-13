"""mgi-mind memory exposed as LangChain tools for Crescendo agents.

Each agent gets tools bound to its own bearer token, so every write is
attributed to that agent in the audit trail (Track-3). The Archivist relies on
these; other agents may use them too.

build_memory_tools(token) -> [remember, recall]
"""

import os

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

MGIMIND_URL = os.environ.get("MGIMIND_URL", "http://127.0.0.1:8765")
LIBRARY = os.environ.get("MGIMIND_LIBRARY", "crescendo")


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
