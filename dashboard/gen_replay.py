"""Build dashboard/replay.json from the live mgi-mind audit trail.

Turns the raw audit events for the `crescendo` library into a replay the static
dashboard can render with no backend — the judged artifact.

Run: uv run python dashboard/gen_replay.py
"""

import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv("/home/madgodinc/code/crescendo/.env")

MGIMIND_URL = os.environ.get("MGIMIND_URL", "http://127.0.0.1:8765")
TOKEN = os.environ["MGIMIND_TOKEN_CONDUCTOR"]
OUT = os.path.join(os.path.dirname(__file__), "replay.json")

# Fixed roster the dashboard draws as the orchestra graph.
AGENTS = [
    {"id": "conductor", "label": "Conductor", "role": "plans & routes", "tier": "smart"},
    {"id": "soloist", "label": "Soloist", "role": "writes code", "tier": "coder"},
    {"id": "tuningfork", "label": "Tuning Fork", "role": "reviews", "tier": "smart"},
    {"id": "stagetech", "label": "Stage Tech", "role": "deploys", "tier": "cheap"},
    {"id": "archivist", "label": "Archivist", "role": "memory & audit", "tier": "cheap"},
]


def main() -> None:
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    with httpx.Client(timeout=20) as c:
        r = c.post(f"{MGIMIND_URL}/audit", headers=headers, json={"limit": 200})
        r.raise_for_status()
        events = r.json().get("events", [])

    # keep only crescendo-library events, oldest first, normalized for the UI
    timeline = []
    for e in reversed(events):
        if e.get("library") and e["library"] != "crescendo":
            continue
        timeline.append({
            "ts": e.get("ts", ""),
            "actor": e.get("actor", "?"),
            "op": e.get("op", ""),
            "text": (e.get("after") or "")[:500],
            "note": e.get("note", ""),
        })

    replay = {"agents": AGENTS, "timeline": timeline}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(replay, f, ensure_ascii=False, indent=2)
    print(f"wrote {OUT}: {len(timeline)} events, {len(AGENTS)} agents")


if __name__ == "__main__":
    main()
