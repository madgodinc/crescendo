"""Unit tests for the orchestrator's deterministic decision logic.

These are the pure functions that decide whether a clean page ships, how a
deploy failure is classified for the self-learning loop, and how the resource
contract is parsed. No network, no LLM, no Band — fast and offline.

Run:  uv run python -m pytest tests/ -q
"""

import os

# maestro.py reads a few env vars at import time (agent ids, tokens). Set dummies
# so the import succeeds without a real .env — we only test the pure functions.
os.environ.setdefault("MGIMIND_TOKEN_ARCHIVIST", "t")
for _k in ("CONDUCTOR", "SOLOIST", "TUNING_FORK", "STAGE_TECH", "ARCHIVIST", "MAESTRO"):
    os.environ.setdefault(f"{_k}_AGENT_ID", "id")
os.environ.setdefault("MAESTRO_API_KEY", "k")

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from maestro import Maestro, _deploy_error_signature, _is_system_echo  # noqa: E402
import deploy_tools  # noqa: E402


# ── a weak model parroting a Band system block must not count as a reply ──────

class TestSystemEcho:
    def test_real_captured_echo_is_detected(self):
        echo = ("[[System]: ## Current Participants\n"
                "- @trolltina1/archivist — Archivist (Agent)\n"
                "- @trolltina1/soloist — Soloist (Agent)")
        assert _is_system_echo(echo) is True

    def test_participants_block_only(self):
        assert _is_system_echo("## Current Participants\n- @a — A\n- @b — B") is True

    def test_real_verdict_is_not_an_echo(self):
        assert _is_system_echo("CLEAN") is False
        assert _is_system_echo("ISSUES:\n1. email field does not validate") is False

    def test_prose_mentioning_system_is_not_an_echo(self):
        # a real review that happens to use the word "system" must pass through
        assert _is_system_echo("CLEAN. The login system works.") is False


# ── review verdict: the headline gate (broken page must not ship as clean) ────

class TestIsClean:
    def test_plain_clean(self):
        assert Maestro._is_clean("CLEAN") is True
        assert Maestro._is_clean("CLEAN — looks good") is True
        assert Maestro._is_clean("The page is great. LGTM") is True
        assert Maestro._is_clean("Looks good to me") is True

    def test_negated_negatives_are_clean(self):
        # "no problems" must read as clean, not as containing PROBLEM
        assert Maestro._is_clean("No problems found, CLEAN") is True
        assert Maestro._is_clean("no bugs, no errors. LGTM") is True
        assert Maestro._is_clean("Zero issues. Looks good.") is True
        assert Maestro._is_clean("Nothing missing. CLEAN.") is True

    def test_real_issues_are_not_clean(self):
        # the whole point: a finding without the literal word ISSUE still blocks
        assert Maestro._is_clean("I found one problem with the layout") is False
        assert Maestro._is_clean("there is a concern about contrast") is False
        assert Maestro._is_clean("PROBLEM: the button does not work") is False
        assert Maestro._is_clean("The button is broken") is False
        assert Maestro._is_clean("missing aria-label on the form") is False
        assert Maestro._is_clean("ISSUES:\n1. font not linked") is False

    def test_clean_word_with_an_issue_still_blocks(self):
        # "CLEAN ... but there is an ISSUE" must NOT pass
        assert Maestro._is_clean("CLEAN - although there is a minor ISSUE with spacing") is False

    def test_adjacent_negated_negatives_are_clean(self):
        # regression: a reviewer confirming a page is fine writes the negative
        # right next to NO ("no truncation", "no broken links") — these are
        # POSITIVE and must read as clean, or the review loop spins to max rounds
        # and ships a clean page as "shipped-with-issues".
        assert Maestro._is_clean(
            "CLEAN\n\nThe page is complete:\n- Ends with </html>, no truncation.\n"
            "- The Start button works correctly.") is True
        assert Maestro._is_clean("CLEAN, no broken links, no missing fonts. LGTM") is True
        assert Maestro._is_clean("CLEAN — not broken, nothing incomplete") is True

    def test_truncated_page_still_blocks(self):
        # but a REAL truncation (not negated) must still block
        assert Maestro._is_clean("the file is truncated, does not end with </html>") is False

    def test_cleanup_substring_does_not_ship(self):
        # "CLEANUP needed" must NOT match the CLEAN positive marker — a substring
        # match here would silently ship a page the reviewer asked to fix.
        assert Maestro._is_clean("cleanup needed on the header") is False
        assert Maestro._is_clean("the cleanliness is fine but spacing is off") is False

    def test_other_signoff_phrasings_are_clean(self):
        # models sign off without the literal CLEAN; these are positive too
        assert Maestro._is_clean("Zero issues. Ship it.") is True
        assert Maestro._is_clean("Approved.") is True
        assert Maestro._is_clean("Passes review, good to go.") is True


class TestCountIssues:
    def test_clean_review_counts_zero(self):
        # _count_issues only runs after _is_clean is already False, so it sees a
        # review that genuinely has no negative markers.
        assert Maestro._count_issues("CLEAN, all good") == 0
        assert Maestro._count_issues("Looks great, shipping it") == 0

    def test_numbered_list(self):
        assert Maestro._count_issues("ISSUES:\n1. font\n2. contrast\n3. no alt text") == 3

    def test_bulleted_list(self):
        assert Maestro._count_issues("ISSUES:\n- one thing\n- another") == 2

    def test_prose_issue_without_list_counts_one(self):
        assert Maestro._count_issues("ISSUES: something is off but no list") == 1


# ── self-learning: a deploy failure must map to a STABLE signature so the loop ─
# ── recalls the same fix across runs (brief + URL differ every time) ──────────

class TestDeploySignature:
    def test_base64_junk(self):
        s = _deploy_error_signature("Refused: page contains base64 favicon junk")
        assert s == "deploy gate refused: base64/favicon junk in page"

    def test_truncated(self):
        s = _deploy_error_signature("file truncated, does not end with </html>")
        assert s == "deploy gate refused: page truncated / not valid HTML"

    def test_empty(self):
        s = _deploy_error_signature("page empty, no file written")
        assert s == "deploy gate refused: page empty / no file written"

    def test_generic(self):
        s = _deploy_error_signature("some other validation failure")
        assert s == "deploy gate refused: page failed validation"

    def test_signature_is_stable_across_briefs(self):
        # same failure class -> same signature regardless of the unique wording
        a = _deploy_error_signature("base64 junk in the pomodoro page")
        b = _deploy_error_signature("base64 junk in the weather page")
        assert a == b


# ── resource contract: parse the Conductor's inferred access list ─────────────

class TestParseRider:
    def test_em_dash_hyphen_colon_separators(self):
        out = Maestro._parse_rider(
            "RESOURCE: Cloudflare Pages — hosting the site\n"
            "RESOURCE: custom domain - branded URL\n"
            "RESOURCE: Stripe API key: payments")
        assert [r["name"] for r in out] == ["Cloudflare Pages", "custom domain", "Stripe API key"]
        assert out[0]["why"] == "hosting the site"

    def test_none_collapses_to_empty(self):
        assert Maestro._parse_rider("RESOURCE: none — ships on our Cloudflare Pages account") == []

    def test_ignores_noise(self):
        assert Maestro._parse_rider("here is some text with no resource lines") == []


# ── run id: stable per brief so a relaunch finds its checkpoint ───────────────

class TestRunId:
    def test_stable_for_same_brief(self):
        assert Maestro._run_id("build a timer") == Maestro._run_id("build a timer")

    def test_differs_across_briefs(self):
        assert Maestro._run_id("build a timer") != Maestro._run_id("build a gallery")

    def test_format(self):
        rid = Maestro._run_id("anything")
        assert rid.startswith("run_") and len(rid) == 16  # "run_" + 12 hex


# ── human approval gate: DENY must win, negated approval must not grant ───────

class TestHumanVerdict:
    def test_plain_decisions(self):
        assert Maestro._human_verdict("APPROVE") is True
        assert Maestro._human_verdict("DENY") is False
        assert Maestro._human_verdict("reject") is False

    def test_negated_approval_does_not_grant(self):
        # the dangerous case: a bare-substring match read "do not approve" as a grant
        assert Maestro._human_verdict("do not approve") is False
        assert Maestro._human_verdict("don't approve, deny") is False
        assert Maestro._human_verdict("disapprove") is False

    def test_deny_wins_on_ambiguity(self):
        assert Maestro._human_verdict("approve? no, deny this") is False

    def test_no_decision_keeps_waiting(self):
        assert Maestro._human_verdict("not sure yet") is None
        assert Maestro._human_verdict("what access does it need?") is None

    def test_affirmatives_grant(self):
        assert Maestro._human_verdict("yes, go ahead") is True
        assert Maestro._human_verdict("authorize the deploy") is True


class TestWaitHumanDecision:
    """The gate reads Band's inserted_at field. Reading created_at (which Band
    messages don't have) made every human reply invisible, so a typed DENY was
    silently ignored and the gate always auto-granted by timeout."""

    class _Msg:
        def __init__(self, content, inserted_at, sender_type="User"):
            self.content = content
            self.inserted_at = inserted_at
            self.sender_type = sender_type
            self.id = content

    def _maestro_with_messages(self, msgs):
        import maestro
        m = maestro.Maestro.__new__(maestro.Maestro)
        m.room = "room"

        async def _room_messages(room_id):
            return msgs
        m._room_messages = _room_messages
        return m

    def test_deny_is_read_and_blocks(self):
        import asyncio
        from datetime import datetime, timezone
        since = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        later = datetime(2026, 6, 15, 12, 0, 5, tzinfo=timezone.utc)
        m = self._maestro_with_messages([self._Msg("DENY", later)])
        assert asyncio.run(m._wait_human_decision(since)) is False

    def test_approve_is_read_and_grants(self):
        import asyncio
        from datetime import datetime, timezone
        since = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        later = datetime(2026, 6, 15, 12, 0, 5, tzinfo=timezone.utc)
        m = self._maestro_with_messages([self._Msg("APPROVE", later)])
        assert asyncio.run(m._wait_human_decision(since)) is True


# ── resume safety: the page lives in the workspace, not the checkpoint ────────

class TestResumeArtifactGuard:
    """The crash-resume guard rebuilds the page if code-review was checkpointed
    done but the local artifact has vanished. It hinges on _site_bytes()
    reporting 0 for a missing/empty file, so lock that invariant."""

    def test_site_bytes_zero_when_missing(self, monkeypatch):
        import maestro
        monkeypatch.setattr(maestro, "SITE_PATH", "/nonexistent/crescendo/index.html")
        assert Maestro._site_bytes() == 0

    def test_site_bytes_zero_when_empty(self, monkeypatch, tmp_path):
        import maestro
        f = tmp_path / "index.html"
        f.write_text("")
        monkeypatch.setattr(maestro, "SITE_PATH", str(f))
        assert Maestro._site_bytes() == 0

    def test_site_bytes_positive_when_written(self, monkeypatch, tmp_path):
        import maestro
        f = tmp_path / "index.html"
        f.write_text("<!doctype html><h1>hi</h1>")
        monkeypatch.setattr(maestro, "SITE_PATH", str(f))
        assert Maestro._site_bytes() > 0


# ── deploy gate sanitizer + validator (junk from weak models must not ship) ───

class TestCleanSlot:
    def test_strips_big_base64_data_uri(self):
        # a real junk blob is 200+ base64 chars; that's what gets stripped
        blob = "A" * 250
        out = deploy_tools._clean_slot(f'<img src="data:image/png;base64,{blob}">')
        assert blob not in out

    def test_runs_of_identical_chars_collapsed(self):
        # 40+ identical chars (broken base64 padding) get collapsed
        out = deploy_tools._clean_slot("x" * 60)
        assert len(out) < 60

    def test_strips_structural_tags(self):
        # slots must not carry html/head/body/script/style tags
        out = deploy_tools._clean_slot("<html><body>hi</body></html>")
        assert "<html" not in out.lower() and "<body" not in out.lower()

    def test_keeps_normal_markup(self):
        out = deploy_tools._clean_slot("<h1>Hello</h1><p>world</p>")
        assert "Hello" in out and "world" in out


# ── acceptance audit: leaked secrets + placeholder text must hard-block ───────

class TestStaticBlockChecks:
    def test_openai_key_blocks(self):
        out = deploy_tools._static_block_checks("var k='sk-abcdefghij1234567890XYZ';", "visible")
        assert any("secret" in o for o in out)

    def test_github_and_aws_keys_block(self):
        assert deploy_tools._static_block_checks("ghp_abcdefghij1234567890abcd", "v")
        assert deploy_tools._static_block_checks("AKIAIOSFODNN7EXAMPLE", "v")

    def test_provider_keys_block(self):
        # the project wires several LLM providers; their key formats must block too
        assert deploy_tools._static_block_checks("xai-abcdefghij1234567890abcd", "v")
        assert deploy_tools._static_block_checks("hf_abcdefghij1234567890abcd", "v")
        assert deploy_tools._static_block_checks("xoxb-1234567890-abcdefABCDEF", "v")
        assert deploy_tools._static_block_checks("sk_live_abcdefghij1234567890ABCD", "v")

    def test_hyphenated_and_fine_grained_keys_block(self):
        # the CURRENT default key formats use hyphens in the body; the old
        # [A-Za-z0-9]{20,} class terminated at the first hyphen and missed them
        assert deploy_tools._static_block_checks("sk-ant-api03-aBcDeFgHiJkLmNoPqRsTuV", "v")
        assert deploy_tools._static_block_checks("sk-proj-aBcDeFgHiJkLmNoPqRsTuVwXyZ", "v")
        assert deploy_tools._static_block_checks(
            "github_pat_11ABCDEF0aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789", "v")

    def test_secret_scan_runs_in_static_gate(self, tmp_path, monkeypatch):
        # the secret scan must fire in validate_site() (the always-run static
        # gate), NOT only on the headless-render path — otherwise a key ships
        # unscanned whenever Playwright is unavailable.
        monkeypatch.setattr(deploy_tools, "WORKSPACE", str(tmp_path))
        html = ("<!doctype html><html><head></head><body><h1>Hi</h1><p>"
                + "real content here. " * 5
                + "sk-ant-api03-aBcDeFgHiJkLmNoPqRsTuV</p></body></html>")
        (tmp_path / "index.html").write_text(html)
        problems = deploy_tools.validate_site()
        assert any("secret" in p for p in problems)

    def test_provider_key_prefixes_do_not_false_positive(self):
        # ordinary words that share a prefix must not trip the patterns
        assert deploy_tools._static_block_checks("<p>See our skills and hf demos</p>", "skills hf demos") == []

    def test_clean_page_has_no_secret_finding(self):
        # ordinary markup with no key-shaped string passes
        assert deploy_tools._static_block_checks("<h1>Ledger</h1><p>Budget app</p>", "Ledger Budget app") == []

    def test_placeholder_text_blocks(self):
        assert any("placeholder" in o for o in
                   deploy_tools._static_block_checks("<h1>x</h1>", "Lorem ipsum dolor sit"))
        assert any("placeholder" in o for o in
                   deploy_tools._static_block_checks("<h1>x</h1>", "TODO finish this"))

    def test_real_content_is_not_placeholder(self):
        out = deploy_tools._static_block_checks("<h1>Vigor</h1>", "Vigor is a fitness app with a hero")
        assert out == []


# ── deploy gate runs the render check, not just static validation ─────────────

class TestDeployGate:
    """_deploy_site must hard-block on render-time errors (a missing local image,
    a JS error) so a broken page can't ship just because the LLM reviewer didn't
    call check_page on the final version. The static validate_site() alone misses
    these — they only surface in the headless render's console errors."""

    def _write_valid_page(self, body):
        import deploy_tools
        os.makedirs(deploy_tools.WORKSPACE, exist_ok=True)
        path = os.path.join(deploy_tools.WORKSPACE, "index.html")
        html = ("<!doctype html><html><head></head><body>" + body +
                "</body></html>")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return path

    def test_render_errors_refuse_deploy(self, monkeypatch):
        import asyncio
        import deploy_tools
        self._write_valid_page("<h1>Mira</h1><p>portfolio with a gallery here</p>")

        async def fake_render(must_contain=""):
            return {"ok": False, "errors": ["console error: ERR_FILE_NOT_FOUND"],
                    "warnings": [], "visible_chars": 40}

        monkeypatch.setattr(deploy_tools, "_render_check", fake_render)
        out = asyncio.run(deploy_tools._deploy_site())
        assert out.startswith("REFUSED")
        assert "ERR_FILE_NOT_FOUND" in out

    def test_clean_render_does_not_refuse_on_the_gate(self, monkeypatch):
        # with no render errors the gate passes; deploy proceeds to wrangler
        # (which we don't run here — we only assert it didn't REFUSE on the gate).
        import asyncio
        import deploy_tools
        self._write_valid_page("<h1>Mira</h1><p>portfolio with a real gallery</p>")

        async def fake_render(must_contain=""):
            return {"ok": True, "errors": [], "warnings": [], "visible_chars": 40}

        # pad past validate_site()'s byte/visible floors so only the render gate
        # is under test here
        self._write_valid_page(
            "<h1>Mira</h1><p>" + "A real illustrator portfolio with a gallery. " * 6 + "</p>")

        class _FakeProc:
            async def communicate(self):
                return (b"https://abcd1234.example.pages.dev\n", b"")

        async def fake_exec(*a, **k):
            return _FakeProc()

        monkeypatch.setattr(deploy_tools, "_render_check", fake_render)
        # reaching wrangler means the gate passed; return a fake URL instead of
        # actually shelling out, so we assert "not REFUSED" deterministically.
        monkeypatch.setattr(deploy_tools.asyncio, "create_subprocess_exec", fake_exec)
        out = asyncio.run(deploy_tools._deploy_site())
        assert not out.startswith("REFUSED")
        assert "pages.dev" in out


class TestRenderSSRFBlock:
    """The render is of attacker-controlled HTML. Every non-file:// request must
    be blocked so a page can't SSRF into cloud metadata / internal services or
    exfiltrate over an image beacon at render time."""

    def _has_playwright(self):
        try:
            import playwright  # noqa: F401
            return True
        except Exception:
            return False

    def test_external_requests_are_blocked_and_flagged(self, monkeypatch):
        import asyncio
        import deploy_tools
        if not self._has_playwright():
            import pytest
            pytest.skip("playwright not installed")
        os.makedirs(deploy_tools.WORKSPACE, exist_ok=True)
        html = ("<!doctype html><html><head></head><body><h1>Page</h1><p>"
                + "plenty of real visible content here padding. " * 4 + "</p>"
                "<img src='http://169.254.169.254/latest/meta-data/'>"
                "<script>fetch('http://127.0.0.1:8765/audit')</script>"
                "</body></html>")
        with open(os.path.join(deploy_tools.WORKSPACE, "index.html"), "w") as f:
            f.write(html)
        r = asyncio.run(deploy_tools._render_check())
        # the page failed the gate AND the external loads were surfaced as blocked
        assert r["ok"] is False
        assert any("blocked" in e and "external resource" in e for e in r["errors"])
        assert any("169.254.169.254" in e or "127.0.0.1" in e for e in r["errors"])

    def test_inline_only_page_still_passes(self, monkeypatch):
        import asyncio
        import deploy_tools
        if not self._has_playwright():
            import pytest
            pytest.skip("playwright not installed")
        os.makedirs(deploy_tools.WORKSPACE, exist_ok=True)
        html = ("<!doctype html><html><head><style>body{color:#fff}</style></head>"
                "<body><h1>Real</h1><p>" + "genuine inline content here. " * 4 + "</p>"
                "<button id=b>Go</button>"
                "<script>document.getElementById('b').addEventListener('click',()=>{})</script>"
                "</body></html>")
        with open(os.path.join(deploy_tools.WORKSPACE, "index.html"), "w") as f:
            f.write(html)
        r = asyncio.run(deploy_tools._render_check())
        assert r["ok"] is True
