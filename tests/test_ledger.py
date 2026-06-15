"""Unit tests for the grounding pass (ledger.py).

The grounding pass is deterministic and report-only: it classifies every audit
event as grounded / broken / attested and never raises. These tests pin that
logic with no network and no real files — a stub verifier supplies URL results.

Run:  uv run python -m pytest tests/test_ledger.py -q
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ledger import ground_event, ground_run  # noqa: E402

LIVE = lambda u: "live"
DEAD = lambda u: "unreachable"
OFFLINE = lambda u: "format-valid"


# ── a deploy claim is grounded only when its URL actually resolves ────────────

class TestDeployGrounding:
    def test_live_url_is_grounded(self):
        ev = {"kind": "deploy", "meta": {"url": "https://x.pages.dev"}}
        assert ground_event(ev, verify_url=LIVE)["status"] == "grounded"

    def test_dead_url_is_broken(self):
        ev = {"kind": "deploy", "meta": {"url": "https://x.pages.dev"}}
        assert ground_event(ev, verify_url=DEAD)["status"] == "broken"

    def test_offline_falls_back_to_format_valid(self):
        # no network: a well-formed Pages URL still grounds, but the detail says so
        ev = {"kind": "deploy", "meta": {"url": "https://x.pages.dev"}}
        r = ground_event(ev, verify_url=OFFLINE)
        assert r["status"] == "grounded" and "network check skipped" in r["detail"]

    def test_failed_deploy_with_no_url_is_broken(self):
        ev = {"kind": "deploy", "meta": {"failed": True}}
        assert ground_event(ev, verify_url=LIVE)["status"] == "broken"

    def test_url_pulled_from_text_when_meta_missing(self):
        ev = {"kind": "deploy", "text": "Deployed. Live URL: https://y.pages.dev", "meta": {}}
        assert ground_event(ev, verify_url=LIVE)["status"] == "grounded"


# ── a page-write grounds transitively against a verified deploy ───────────────

class TestPageGrounding:
    def test_page_grounds_against_live_deploy(self):
        ev = {"kind": "code", "meta": {}}
        assert ground_event(ev, deploy_live=True)["status"] == "grounded"

    def test_page_without_file_or_deploy_is_broken(self, monkeypatch):
        # point the site path at a file that can't exist, so the test doesn't
        # depend on whether a real run left a page in the workspace.
        monkeypatch.setenv("CRESCENDO_SITE_PATH", "/nonexistent/crescendo/index.html")
        ev = {"kind": "code", "meta": {}}
        assert ground_event(ev, deploy_live=False)["status"] == "broken"


# ── a review grounds on a recorded deterministic check verdict ────────────────

class TestReviewGrounding:
    def test_review_with_verdict_is_grounded(self):
        ev = {"kind": "review", "meta": {"verdict": "clean"}}
        assert ground_event(ev)["status"] == "grounded"
        ev2 = {"kind": "review", "meta": {"verdict": "issues"}}
        assert ground_event(ev2)["status"] == "grounded"

    def test_review_without_verdict_is_attested(self):
        ev = {"kind": "review", "meta": {}}
        assert ground_event(ev)["status"] == "attested"


# ── internal decisions are attested, never counted against the ratio ──────────

class TestAttested:
    def test_internal_kinds_are_attested(self):
        for k in ("brief", "rider", "plan", "archive", "recall", "learn", "skills"):
            assert ground_event({"kind": k, "meta": {}})["status"] == "attested"


# ── the whole-run summary ─────────────────────────────────────────────────────

class TestGroundRun:
    def _run(self):
        return {"timeline": [
            {"kind": "brief", "meta": {}},
            {"kind": "plan", "meta": {}},
            {"kind": "code", "meta": {}},
            {"kind": "review", "meta": {"verdict": "clean"}},
            {"kind": "deploy", "meta": {"url": "https://z.pages.dev"}},
            {"kind": "archive", "meta": {}},
        ]}

    def test_clean_run_is_fully_grounded(self):
        r = ground_run(self._run(), verify_url=LIVE)
        # external claims: code (transitive via deploy) + review + deploy = 3
        assert r["total_claims"] == 3
        assert r["grounded"] == 3 and r["broken"] == 0
        assert r["ratio"] == 1.0 and r["all_grounded"] is True
        assert r["attested"] == 3   # brief, plan, archive

    def test_dead_deploy_breaks_grounding(self):
        r = ground_run(self._run(), verify_url=DEAD)
        # deploy is broken, and the page can no longer ground transitively
        assert r["all_grounded"] is False
        assert r["broken"] >= 1 and r["ratio"] < 1.0

    def test_ratio_is_one_when_no_external_claims(self):
        r = ground_run({"timeline": [{"kind": "plan", "meta": {}}]}, verify_url=LIVE)
        assert r["total_claims"] == 0 and r["ratio"] == 1.0 and r["all_grounded"] is True

    def test_reads_events_key_too(self):
        # maestro stores under "events"; the dashboard renames to "timeline"
        r = ground_run({"events": [{"kind": "deploy", "meta": {"url": "https://q.pages.dev"}}]},
                       verify_url=LIVE)
        assert r["grounded"] == 1
