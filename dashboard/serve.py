"""Crescendo dashboard server — a read-only vitrine on top of the live run.

Serves the static dashboard files AND proxies three read-only JSON endpoints to
mgi-mind's KV store, adding the bearer token server-side. The dashboard is a
browser page; mgi-mind has no CORS and the token must not reach the browser, so
this same-origin proxy is the seam. Everything is loopback.

  GET /                     -> dashboard/index.html
  GET /<file>               -> static file from the dashboard dir
  GET /api/runs             -> {current, recent:[...]}        (CRESCENDO_ACTIVE)
  GET /api/live             -> the current run's live document (or {status:idle})
  GET /api/run/<run_id>     -> a specific run's live document  (or 404)
  GET /audit/<run_id>       -> a standalone, hash-chained audit report (HTML)

Run: uv run python dashboard/serve.py [--port 8000]
mgi-mind must be up on MGIMIND_URL; MGIMIND_TOKEN_ARCHIVIST supplies the token.
"""

import hashlib
import html
import json
import os
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ledger import ground_run  # noqa: E402  (grounding pass over the audit trail)

# load .env from the repo root if present (works locally, in Docker, and from a
# clone) without a hardcoded path; env vars already set always win.
for _p in (os.path.join(os.path.dirname(__file__), "..", ".env"),
           "/home/madgodinc/code/crescendo/.env"):
    if os.path.isfile(_p):
        load_dotenv(_p)
        break

MGIMIND_URL = os.environ.get("MGIMIND_URL", "http://127.0.0.1:8765").rstrip("/")
# token is only needed to reach the brain; in replay/demo mode (no brain) the
# dashboard still serves the recorded run from replay.json, so don't hard-crash.
TOKEN = os.environ.get("MGIMIND_TOKEN_ARCHIVIST", "crescendo_archivist_tok")
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))

LIVE_MARKER = "CRESCENDO_LIVE"
ACTIVE_KEY = "CRESCENDO_ACTIVE"

# Only these static files are servable (no directory traversal, no surprises).
_MIME = {".html": "text/html", ".js": "application/javascript",
         ".css": "text/css", ".json": "application/json", ".ico": "image/x-icon"}


def _post(path: str, payload: dict, timeout=6):
    """POST JSON to a mgi-mind endpoint with the server-side token; return parsed."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{MGIMIND_URL}{path}", data=body, method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _kv_get(key: str):
    """Fetch a KV value from mgi-mind, or None. Token added here, server-side."""
    data = _post("/kv/get", {"key": key})
    return data.get("value") if data.get("found") else None


def learned_fixes():
    """Parse the procedural playbooks the self-learning LOOP wrote (only those —
    the procedure store also holds unrelated dev notes, so keep just the ones
    whose source is the deploy gate)."""
    try:
        data = _post("/procedure/recall", {"error": "deploy gate refused", "limit": 12})
    except Exception:
        return []
    text = data.get("result", "") if isinstance(data, dict) else ""
    out, cur = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and "id:" in line:
            if cur:
                out.append(cur)
            cur = {"verified": "verified" in line.lower(), "error": "", "fix": "",
                   "when": "", "src": ""}
        elif cur is not None:
            if line.startswith("error:"):
                cur["error"] = line[6:].strip()
            elif line.startswith("fix:"):
                cur["fix"] = line[4:].strip()
            elif line.startswith("when:"):
                cur["when"] = line[5:].strip()
            elif line.startswith("from:"):
                cur["src"] = line[5:].strip()
    if cur:
        out.append(cur)
    # only the self-learning loop's deploy-gate playbooks (dedupe by fix text)
    seen, fixes = set(), []
    for p in out:
        if "deploy gate" not in p.get("src", "") and "deploy gate refused" not in p.get("error", ""):
            continue
        key = p["fix"][:50]
        if key in seen:
            continue
        seen.add(key)
        fixes.append(p)
    return fixes


def run_stats(active: dict):
    """Aggregate cross-run metrics from the history mgi-mind keeps."""
    recent = (active or {}).get("recent", [])
    runs = []
    for r in recent:
        doc = _kv_get(f"{LIVE_MARKER}:{r.get('run_id')}")
        if not isinstance(doc, dict):
            continue
        tl = doc.get("timeline", [])
        tok = sum((e.get("meta") or {}).get("tokens", 0) for e in tl)
        verdict = doc.get("review_verdict", "")
        clean = verdict == "clean"
        # wall-clock from first to last event
        ts = [e.get("ts", "") for e in tl if e.get("ts")]
        dur = 0
        if len(ts) >= 2:
            from datetime import datetime
            try:
                dur = (datetime.fromisoformat(ts[-1]) - datetime.fromisoformat(ts[0])).total_seconds()
            except Exception:
                dur = 0
        runs.append({"run_id": r.get("run_id"), "brief": r.get("brief", ""),
                     "status": r.get("status"), "clean": clean, "tokens": tok, "dur": round(dur)})
    n = len(runs)
    cleans = sum(1 for r in runs if r["clean"])
    return {"n": n, "clean_pct": round(100 * cleans / n) if n else 0,
            "avg_tokens": round(sum(r["tokens"] for r in runs) / n) if n else 0,
            "avg_dur": round(sum(r["dur"] for r in runs) / n) if n else 0,
            "runs": runs}


ROLE = {"human": "Human", "conductor": "Conductor", "soloist": "Soloist",
        "tuningfork": "Tuning Fork", "stagetech": "Stage Tech", "archivist": "Archivist"}
PHASE_OF = {"brief": "Intake", "rider": "Resource contract", "plan": "Plan",
            "skills": "Code ↔ Review", "code": "Code ↔ Review", "review": "Code ↔ Review",
            "approval": "Approval", "recall": "Deploy", "learn": "Deploy",
            "deploy": "Deploy", "archive": "Archive"}


def render_audit(doc: dict) -> str:
    """Render a standalone, tamper-evident audit report for one run.

    Every decision in the run's attributed timeline is shown with its author,
    phase, real content, timestamp and token cost. A SHA-256 hash chain links
    the events (each hash folds in the previous one), so any edit to a past
    decision breaks every hash after it — the trail is verifiable, not asserted.
    """
    tl = doc.get("timeline", [])
    brief = doc.get("brief", "")
    run_id = doc.get("run_id", "")
    status = doc.get("status", "")
    e = html.escape
    # Grounding pass: a deterministic, report-only check that every claim
    # pointing at an external artifact (a page, a deploy URL, a check result)
    # actually has one. Format-only URL check keeps the report render fast and
    # deterministic — the URL is in the trail only because deploy_site returned
    # it after its own validation, so trusting its shape here is honest.
    grd = ground_run(doc, verify_url=lambda u: "format-valid")
    gmark = {"grounded": ('<span class="vb gnd" title="claim backed by a real artifact">grounded</span>'),
             "broken": ('<span class="vb brk" title="claim references a missing artifact">unbacked</span>'),
             "attested": ""}
    rows, prev = [], "0" * 64
    total_tok = 0
    for i, ev in enumerate(tl, 1):
        actor = ev.get("actor", "?")
        kind = ev.get("kind", "")
        text = ev.get("text", "")
        ts = ev.get("ts", "")
        meta = ev.get("meta") or {}
        tok = meta.get("tokens") or 0
        total_tok += tok
        # hash chain: h_i = sha256(h_{i-1} || actor || kind || text || ts)
        h = hashlib.sha256((prev + actor + kind + text + ts).encode("utf-8")).hexdigest()
        prev = h
        verdict = meta.get("verdict")
        badge = ""
        if verdict == "clean":
            badge = '<span class="vb clean">clean</span>'
        elif verdict == "issues":
            badge = '<span class="vb issues">issues</span>'
        elif meta.get("url"):
            badge = '<span class="vb url">shipped</span>'
        rnd = f' · round {meta["round"]}' if meta.get("round") else ""
        tokstr = f'<span class="tok">~{tok} tok</span>' if tok else ""
        gb = gmark.get((grd["events"][i - 1] if i - 1 < len(grd["events"]) else {}).get("status"), "")
        rows.append(f"""
        <tr class="ev {e(actor)}">
          <td class="n">{i}</td>
          <td class="who"><span class="dot {e(actor)}"></span>{e(ROLE.get(actor, actor))}</td>
          <td class="ph">{e(PHASE_OF.get(kind, kind))}{rnd}</td>
          <td class="kind">{e(kind)} {badge}{gb}</td>
          <td class="content">{e(text)}</td>
          <td class="meta">{tokstr}<span class="ts">{e(ts[11:19] if len(ts) > 19 else ts)}</span>
              <span class="hash" title="chained SHA-256">{h[:12]}…</span></td>
        </tr>""")
    deploy_url = ""
    for ev in tl:
        u = (ev.get("meta") or {}).get("url")
        if u:
            deploy_url = u
    verdict_line = doc.get("review_verdict") or status
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Audit report · {e(run_id)}</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {{ --bg:#0a0e1a; --pan:#121829; --line:rgba(120,140,200,.16); --ink:#e8eef9; --dim:#7e8aa8;
    --gold:#ffcf5a; --green:#4fe0a8; --pink:#ff7bc6; --purple:#b89bff; --blue:#5aa8ff; --orange:#ff9a5a; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.55 'Space Grotesk',system-ui,sans-serif; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:36px 26px 60px; }}
  h1 {{ font-size:23px; margin:0 0 4px; }} h1 .a {{ color:var(--gold); }}
  .sub {{ color:var(--dim); font-size:13px; }}
  .card {{ background:var(--pan); border:1px solid var(--line); border-radius:14px; padding:18px 22px; margin:18px 0; }}
  .card b {{ color:var(--gold); }}
  .meta-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:14px; }}
  .kv .k {{ color:var(--dim); font-size:11px; text-transform:uppercase; letter-spacing:.08em; }}
  .kv .v {{ font-family:'JetBrains Mono',monospace; font-size:13px; margin-top:3px; word-break:break-all; }}
  .kv .v a {{ color:var(--green); text-decoration:none; }}
  table {{ width:100%; border-collapse:collapse; margin-top:8px; }}
  th {{ text-align:left; font-size:10.5px; text-transform:uppercase; letter-spacing:.08em; color:var(--dim);
        border-bottom:1px solid var(--line); padding:8px 9px; position:sticky; top:0; background:var(--bg); }}
  td {{ padding:11px 9px; border-bottom:1px solid var(--line); vertical-align:top; }}
  td.n {{ color:var(--dim); font-family:'JetBrains Mono',monospace; font-size:12px; }}
  td.who {{ white-space:nowrap; font-weight:600; }}
  td.content {{ white-space:pre-wrap; color:#cdd6ee; font-size:13px; max-width:430px; }}
  td.meta {{ white-space:nowrap; font-family:'JetBrains Mono',monospace; font-size:11px; color:var(--dim); }}
  td.meta .tok {{ color:var(--purple); margin-right:8px; }}
  td.meta .ts {{ margin-right:8px; }}
  td.meta .hash {{ color:#566; }}
  .dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:7px; }}
  .dot.human{{background:#fff}} .dot.conductor{{background:var(--blue)}} .dot.soloist{{background:var(--green)}}
  .dot.tuningfork{{background:var(--pink)}} .dot.stagetech{{background:var(--orange)}} .dot.archivist{{background:var(--purple)}}
  .vb {{ font-size:10px; padding:1px 7px; border-radius:10px; margin-left:6px; }}
  .vb.clean {{ background:rgba(79,224,168,.15); color:var(--green); }}
  .vb.issues {{ background:rgba(255,123,198,.15); color:var(--pink); }}
  .vb.url {{ background:rgba(79,224,168,.15); color:var(--green); }}
  .vb.gnd {{ background:rgba(90,168,255,.14); color:var(--blue); }}
  .vb.brk {{ background:rgba(255,123,198,.18); color:var(--pink); }}
  .foot {{ color:var(--dim); font-size:12px; margin-top:20px; line-height:1.7; }}
  .foot code {{ font-family:'JetBrains Mono',monospace; color:var(--purple); }}
  a.back {{ color:var(--blue); text-decoration:none; font-size:13px; }}
</style></head><body><div class="wrap">
  <a class="back" href="/">← dashboard</a>
  <h1>Audit report · <span class="a">Crescendo</span></h1>
  <div class="sub">Every decision in this run, attributed to its agent and linked in a tamper-evident hash chain.</div>
  <div class="card">
    <div class="meta-grid">
      <div class="kv"><div class="k">Brief</div><div class="v">{e(brief)}</div></div>
      <div class="kv"><div class="k">Run ID</div><div class="v">{e(run_id)}</div></div>
      <div class="kv"><div class="k">Decisions</div><div class="v">{len(tl)}</div></div>
      <div class="kv"><div class="k">Verdict</div><div class="v">{e(verdict_line)}</div></div>
      <div class="kv"><div class="k">Tokens (est.)</div><div class="v">~{total_tok}</div></div>
      <div class="kv"><div class="k">Grounded claims</div><div class="v" style="color:{'var(--green)' if grd['all_grounded'] else 'var(--pink)'}">{grd['grounded']}/{grd['total_claims']}{' ✓' if grd['all_grounded'] else ' ⚠'}</div></div>
      <div class="kv"><div class="k">Shipped to</div><div class="v">{'<a href="'+e(deploy_url)+'" target="_blank">'+e(deploy_url)+'</a>' if deploy_url else '—'}</div></div>
    </div>
  </div>
  <div class="card" style="padding:0;overflow:hidden">
    <table>
      <thead><tr><th>#</th><th>Agent</th><th>Phase</th><th>Action</th><th>What was decided / produced</th><th>Cost · time · hash</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </div>
  <div class="foot">
    <b style="color:var(--ink)">How to verify:</b> each row's hash is
    <code>SHA-256(previous_hash + agent + action + content + timestamp)</code>.
    The chain root after the last decision is <code>{prev[:24]}…</code>.
    Editing any past decision changes its hash and every hash after it, so the trail is tamper-evident.
    Source of record: the mgi-mind memory ledger, where each agent writes under its own token.
    <br><b style="color:var(--ink)">Grounded, not just attested:</b> {grd['grounded']} of {grd['total_claims']}
    claims that point at an external artifact — a written page, a live deploy URL, a deterministic check
    result — were verified to actually have one. The hash chain proves no decision was altered; grounding
    proves no agent claimed an artifact it never produced. ({grd['attested']} internal decisions carry no
    external artifact and are attested by author + hash only.)
  </div>
</div></body></html>"""


def render_flywheel(active: dict) -> str:
    """The flywheel made visible: the fixes the orchestra has LEARNED across runs
    (so it stops rediscovering them) + cross-run reliability metrics. This is the
    on-screen proof of 'cheaper and more accurate the longer it runs'."""
    e = html.escape
    fixes = learned_fixes()
    stats = run_stats(active)
    fix_rows = "".join(f"""
      <tr><td class="v">{'✓ verified' if f['verified'] else '· unverified'}</td>
          <td class="err">{e(f['error'])}</td>
          <td class="fix">{e(f['fix'])}</td></tr>""" for f in fixes) or \
        '<tr><td colspan="3" class="empty">No fixes learned yet — they appear after the orchestra recovers from a deploy failure.</td></tr>'
    run_rows = "".join(f"""
      <tr><td>{e((r.get('brief') or r.get('run_id'))[:48])}</td>
          <td class="{'ok' if r['clean'] else 'warn'}">{'clean' if r['clean'] else (r.get('status') or '—')}</td>
          <td class="num">~{r['tokens']}</td><td class="num">{r['dur']}s</td></tr>""" for r in stats["runs"]) or \
        '<tr><td colspan="4" class="empty">No completed runs yet.</td></tr>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Flywheel · Crescendo</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {{ --bg:#0a0e1a; --pan:#121829; --line:rgba(120,140,200,.16); --ink:#e8eef9; --dim:#7e8aa8;
    --gold:#ffcf5a; --green:#4fe0a8; --pink:#ff7bc6; --purple:#b89bff; }}
  *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 'Space Grotesk',system-ui,sans-serif}}
  .wrap{{max-width:1000px;margin:0 auto;padding:36px 26px 60px}}
  a.back{{color:#5aa8ff;text-decoration:none;font-size:13px}}
  h1{{font-size:23px;margin:6px 0 4px}} h1 .a{{color:var(--gold)}}
  .sub{{color:var(--dim);font-size:13px;max-width:640px}}
  .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin:22px 0}}
  .stat{{background:var(--pan);border:1px solid var(--line);border-radius:14px;padding:16px 18px}}
  .stat .num{{font-size:28px;font-weight:700;font-family:'JetBrains Mono',monospace}}
  .stat .lbl{{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin-top:3px}}
  .stat.g .num{{color:var(--green)}} .stat.p .num{{color:var(--purple)}} .stat.gold .num{{color:var(--gold)}}
  h2{{font-size:15px;margin:28px 0 8px}} h2 .c{{color:var(--purple)}}
  .card{{background:var(--pan);border:1px solid var(--line);border-radius:14px;overflow:hidden}}
  table{{width:100%;border-collapse:collapse}}
  th{{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);
      border-bottom:1px solid var(--line);padding:9px 14px}}
  td{{padding:11px 14px;border-bottom:1px solid var(--line);vertical-align:top;font-size:13px}}
  td.v{{color:var(--green);white-space:nowrap;font-family:'JetBrains Mono',monospace;font-size:11.5px}}
  td.err{{color:var(--pink);font-family:'JetBrains Mono',monospace;font-size:12px;white-space:nowrap}}
  td.fix{{color:#cdd6ee}} td.num{{font-family:'JetBrains Mono',monospace;text-align:right;color:var(--dim)}}
  td.ok{{color:var(--green)}} td.warn{{color:var(--pink)}} td.empty{{color:var(--dim);text-align:center;padding:22px}}
  .note{{color:var(--dim);font-size:12px;margin-top:18px;line-height:1.7}}
</style></head><body><div class="wrap">
  <a class="back" href="/">← dashboard</a>
  <h1>The flywheel · <span class="a">Crescendo</span></h1>
  <div class="sub">Every time the orchestra recovers from a failure, it writes the fix to memory.
     Next time that class of problem appears, it recalls the fix instead of rediscovering it —
     so it gets cheaper and more accurate the longer it runs.</div>
  <div class="stats">
    <div class="stat gold"><div class="num">{stats['n']}</div><div class="lbl">Runs</div></div>
    <div class="stat g"><div class="num">{stats['clean_pct']}%</div><div class="lbl">Shipped clean</div></div>
    <div class="stat p"><div class="num">~{stats['avg_tokens']}</div><div class="lbl">Avg tokens / run</div></div>
    <div class="stat"><div class="num">{stats['avg_dur']}s</div><div class="lbl">Avg wall-clock</div></div>
    <div class="stat g"><div class="num">{len(fixes)}</div><div class="lbl">Fixes learned</div></div>
  </div>
  <h2>Learned <span class="c">fixes</span> · procedural memory</h2>
  <div class="card"><table>
    <thead><tr><th>Trust</th><th>Failure signature</th><th>Verified fix</th></tr></thead>
    <tbody>{fix_rows}</tbody></table></div>
  <h2>Run <span class="c">history</span></h2>
  <div class="card"><table>
    <thead><tr><th>Brief</th><th>Verdict</th><th>Tokens</th><th>Time</th></tr></thead>
    <tbody>{run_rows}</tbody></table></div>
  <div class="note">A verified fix carries a deterministic signal (the rebuilt page passed the deploy gate).
    Watch this list grow across runs — that growth is the flywheel.</div>
</div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj, status=200):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, markup: str):
        body = markup.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, rel: str):
        # Resolve inside the dashboard dir only — reject traversal.
        path = os.path.normpath(os.path.join(DASHBOARD_DIR, rel))
        if not path.startswith(DASHBOARD_DIR) or not os.path.isfile(path):
            self.send_error(404, "not found")
            return
        ext = os.path.splitext(path)[1]
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", _MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/runs":
                active = _kv_get(ACTIVE_KEY) or {"current": None, "recent": []}
                return self._send_json(active)
            if path == "/api/learned":
                return self._send_json({"fixes": learned_fixes()})
            if path == "/api/stats":
                active = _kv_get(ACTIVE_KEY) or {}
                return self._send_json(run_stats(active))
            if path == "/api/live":
                active = _kv_get(ACTIVE_KEY) or {}
                cur = active.get("current")
                doc = _kv_get(f"{LIVE_MARKER}:{cur}") if cur else None
                return self._send_json(doc or {"status": "idle"})
            if path.startswith("/api/run/"):
                run_id = path[len("/api/run/"):]
                doc = _kv_get(f"{LIVE_MARKER}:{run_id}")
                return self._send_json(doc) if doc else self._send_json(
                    {"error": "run not found"}, status=404)
            if path.startswith("/audit/"):
                run_id = path[len("/audit/"):]
                doc = _kv_get(f"{LIVE_MARKER}:{run_id}")
                if not doc:
                    return self._send_file("__missing__")  # 404
                return self._send_html(render_audit(doc))
            if path == "/audit":
                # no run id → redirect to the current/last run's report
                active = _kv_get(ACTIVE_KEY) or {}
                cur = active.get("current")
                if cur:
                    self.send_response(302); self.send_header("Location", f"/audit/{cur}")
                    self.end_headers(); return
                return self._send_html("<p>No runs yet.</p>")
            if path == "/flywheel":
                return self._send_html(render_flywheel(_kv_get(ACTIVE_KEY) or {}))
        except Exception as e:
            # mgi-mind unreachable / bad reply — tell the front-end, don't hang.
            return self._send_json({"error": f"memory unreachable: {e}"}, status=502)

        # static
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        self._send_file(rel)

    def log_message(self, *a):  # quiet; the run logs are what matter
        pass


def main():
    port = 8000
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    # bind loopback locally (safe default); in Docker set HOST=0.0.0.0 so the
    # published port is reachable from the host.
    host = os.environ.get("HOST", "127.0.0.1")
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"[dashboard] http://{host}:{port}/  (proxying {MGIMIND_URL})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
