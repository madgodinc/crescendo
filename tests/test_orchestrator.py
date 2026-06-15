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
