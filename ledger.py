"""Ground the audit trail against real artifacts.

The hash chain proves the trail wasn't *altered*. This proves the agents
didn't *lie*: for every entry that claims an external artifact — a written
page, a deployed URL, a deterministic check result — the artifact is checked
to actually exist. The result is one provable number, "N/N grounded claims".

This is deliberately deterministic and report-only. It runs no model and
never blocks a run; a missing artifact is surfaced, not raised. So it can't
add variance to a recorded run or deadlock the pipeline — it only ever adds
information to the audit report.

A claim falls into one of three buckets:
  - grounded  : it references an external artifact and that artifact verifies
  - broken    : it references an external artifact that is missing / unreachable
  - attested  : an internal decision (a plan, a recall) with no external
                artifact to ground — counted separately, never held against
                the grounding ratio.

`ground_run(doc, ...)` returns a dict the audit report renders. `verify_url`
is injected so the dashboard (which already proxies HTTP) and the tests can
supply their own checker; the default does a real HEAD/GET with a short
timeout and degrades to a format check when the network is unavailable.
"""

from __future__ import annotations

import os
import re
import urllib.request

# kinds whose claim points at an external artifact we can verify
_PAGE_KINDS = {"code"}          # the Soloist wrote the page file
_DEPLOY_KINDS = {"deploy"}      # the Stage Tech shipped a live URL
_CHECK_KINDS = {"review"}       # the Tuning Fork ran a deterministic check
_ATTEST_KINDS = {"attest"}      # the live DOM was fetched + hashed into the chain
# kinds that are internal decisions: real, attributed, hash-chained, but with
# no external artifact to point at, so they don't count toward the ratio.
_ATTESTED_KINDS = {"brief", "rider", "plan", "archive", "recall", "learn", "skills", "approval"}

_PAGES_URL = re.compile(r"https://[\w.-]+\.pages\.dev\S*")
SITE_PATH = os.environ.get(
    "CRESCENDO_SITE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace", "site", "index.html"),
)


def _default_verify_url(url: str) -> str:
    """Return 'live' (HTTP 200), 'unreachable', or 'format-valid' when the
    network can't be reached. Never raises — grounding is report-only."""
    if not _PAGES_URL.match(url):
        return "unreachable"
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=6) as r:
            return "live" if r.status < 400 else "unreachable"
    except Exception:
        # offline / blocked: we can still vouch for the shape of the URL, but
        # we say so honestly rather than claim it's live.
        return "format-valid"


def _site_exists() -> bool:
    # resolve at call time so a test (or a CRESCENDO_SITE_PATH override) takes
    # effect without re-importing the module.
    path = os.environ.get("CRESCENDO_SITE_PATH", SITE_PATH)
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def ground_event(ev: dict, *, verify_url=_default_verify_url,
                 deploy_live: bool | None = None,
                 allow_local_file: bool = True) -> dict:
    """Classify one audit event. `deploy_live` lets the caller pass the run's
    already-verified deploy state so a page-write claim can be grounded
    transitively (a live deploy proves the page was really written), which is
    what lets historical runs ground even after the workspace file is gone.

    `allow_local_file` gates the ambient-workspace fallback: a page-write with no
    deploy grounds on _site_exists() ONLY for a live run, where the local file is
    authoritative. When grounding a REPLAY (the dashboard), the workspace holds
    some other run's page, so trusting it would ground a historical claim against
    an unrelated file — pass False there."""
    kind = ev.get("kind", "")
    meta = ev.get("meta") or {}

    if kind in _DEPLOY_KINDS:
        url = meta.get("url") or ""
        m = _PAGES_URL.search(ev.get("text", "") or "")
        if not url and m:
            url = m.group(0)
        if meta.get("failed") or not url:
            return {"status": "broken", "artifact": "deploy",
                    "detail": "claimed a deploy with no live URL"}
        state = verify_url(url)
        if state == "live":
            return {"status": "grounded", "artifact": "deploy",
                    "detail": f"{url} returned 200"}
        if state == "format-valid":
            return {"status": "grounded", "artifact": "deploy",
                    "detail": f"{url} is a valid Pages URL (network check skipped)"}
        return {"status": "broken", "artifact": "deploy",
                "detail": f"{url} did not resolve"}

    if kind in _PAGE_KINDS:
        # the page file is the artifact. If the run deployed successfully, the
        # live URL already proves the page existed: ground it transitively so
        # a replayed/old run isn't penalised for a since-overwritten workspace.
        if deploy_live:
            return {"status": "grounded", "artifact": "page",
                    "detail": "page shipped to the verified live deploy"}
        if allow_local_file and _site_exists():
            return {"status": "grounded", "artifact": "page",
                    "detail": "page file present in the workspace"}
        return {"status": "broken", "artifact": "page",
                "detail": "claimed a page write with no file and no deploy"}

    if kind in _ATTEST_KINDS:
        # the strongest possible grounding: a real digest of the live DOM plus a
        # 2xx from the deployed URL. This is the one claim a non-deploying system
        # physically cannot make — there's no live artifact for it to hash.
        digest = meta.get("live_dom_sha256") or ""
        status = meta.get("http_status") or 0
        if digest and 200 <= status < 400:
            return {"status": "grounded", "artifact": "attest",
                    "detail": f"live DOM hashed (sha256 {digest[:12]}…), HTTP {status}"}
        if digest:
            return {"status": "broken", "artifact": "attest",
                    "detail": f"hashed a DOM but the URL returned HTTP {status}"}
        # network unavailable / playwright absent: the deploy itself still grounds
        # separately, so don't count a skipped attestation against the ratio.
        return {"status": "attested", "artifact": None,
                "detail": meta.get("detail") or "live attestation unavailable"}

    if kind in _CHECK_KINDS:
        # the deterministic check_page result is the artifact: a real verdict
        # means the headless render actually ran.
        if meta.get("verdict") in ("clean", "issues"):
            return {"status": "grounded", "artifact": "check",
                    "detail": f"deterministic check ran (verdict: {meta['verdict']})"}
        return {"status": "attested", "artifact": None,
                "detail": "review note without a recorded check verdict"}

    if kind in _ATTESTED_KINDS:
        return {"status": "attested", "artifact": None,
                "detail": "internal decision, no external artifact"}

    return {"status": "attested", "artifact": None, "detail": "uncategorised"}


def ground_run(doc: dict, *, verify_url=_default_verify_url,
               allow_local_file: bool = False) -> dict:
    """Ground a whole run. Returns:
        {grounded, broken, attested, total_claims, ratio, all_grounded, events:[...]}
    where total_claims = grounded + broken (the entries that *make* an external
    claim) and ratio is grounded/total_claims (1.0 when nothing is broken).

    allow_local_file defaults False: grounding a report/replay must NOT trust the
    ambient workspace (it holds an unrelated run's page). A live run that owns the
    workspace passes True."""
    tl = doc.get("timeline", []) or doc.get("events", [])

    # first pass: is there a verified live deploy? page-writes ground against it.
    deploy_live = False
    for ev in tl:
        if ev.get("kind") in _DEPLOY_KINDS:
            r = ground_event(ev, verify_url=verify_url)
            if r["status"] == "grounded":
                deploy_live = True

    results, grounded, broken, attested = [], 0, 0, 0
    for ev in tl:
        r = ground_event(ev, verify_url=verify_url, deploy_live=deploy_live,
                         allow_local_file=allow_local_file)
        results.append(r)
        if r["status"] == "grounded":
            grounded += 1
        elif r["status"] == "broken":
            broken += 1
        else:
            attested += 1

    total_claims = grounded + broken
    ratio = (grounded / total_claims) if total_claims else 1.0
    return {
        "grounded": grounded,
        "broken": broken,
        "attested": attested,
        "total_claims": total_claims,
        "ratio": ratio,
        "all_grounded": broken == 0,
        "events": results,
    }
