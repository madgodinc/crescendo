"""Unit tests for per-author HMAC signing of the audit trail.

Signing proves a row's author can't be forged without that agent's key. These
tests set dummy per-agent keys, then check that a valid signature verifies and
that tampering with the content or the author both fail.

Run:  uv run python -m pytest tests/test_signing.py -q
"""

import os
import sys

# per-agent keys signing.py reads
os.environ.setdefault("MGIMIND_TOKEN_SOLOIST", "soloist-key")
os.environ.setdefault("MGIMIND_TOKEN_CONDUCTOR", "conductor-key")
os.environ.setdefault("MGIMIND_TOKEN_TUNING_FORK", "tf-key")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from signing import sign_event, verify_event, chain_hash  # noqa: E402


class TestChainHash:
    ROOT = "0" * 64

    def test_deterministic(self):
        a = chain_hash(self.ROOT, "soloist", "code", "XYZ", "t")
        b = chain_hash(self.ROOT, "soloist", "code", "XYZ", "t")
        assert a == b and len(a) == 64

    def test_boundary_shift_does_not_collide(self):
        # the same vulnerability the signature had: a plain concat would let
        # (kind='code', text='XYZ') and (kind='cod', text='eXYZ') chain equal.
        a = chain_hash(self.ROOT, "soloist", "code", "XYZ", "t")
        b = chain_hash(self.ROOT, "soloist", "cod", "eXYZ", "t")
        assert a != b

    def test_prev_links_the_chain(self):
        # a different previous hash yields a different link for identical fields
        h1 = chain_hash(self.ROOT, "soloist", "code", "X", "t")
        h2 = chain_hash("f" * 64, "soloist", "code", "X", "t")
        assert h1 != h2

    def test_any_field_change_breaks_it(self):
        base = chain_hash(self.ROOT, "soloist", "code", "X", "t")
        assert chain_hash(self.ROOT, "conductor", "code", "X", "t") != base
        assert chain_hash(self.ROOT, "soloist", "plan", "X", "t") != base
        assert chain_hash(self.ROOT, "soloist", "code", "Y", "t") != base
        assert chain_hash(self.ROOT, "soloist", "code", "X", "u") != base


class TestSign:
    def test_signs_with_a_key(self):
        sig = sign_event("soloist", "code", "Built the page", "2026-06-14T12:00:00")
        assert sig and len(sig) == 64   # hex sha256

    def test_no_token_set_returns_empty(self):
        # an actor whose key env var is unset has no signature
        assert sign_event("nobody", "brief", "anything", "2026-06-14T12:00:00") == ""

    def test_human_row_signs_with_orchestrator_key(self, monkeypatch):
        # human rows (brief, approval) are signed by the orchestrator key so an
        # injected/forged human row no longer renders as unsigned-but-clean.
        monkeypatch.setenv("MGIMIND_TOKEN_MAESTRO", "maestro-key")
        sig = sign_event("human", "approval", "human authorised the deploy", "2026-06-14T12:00:00")
        assert sig and len(sig) == 64
        assert verify_event("human", "approval", "human authorised the deploy", "2026-06-14T12:00:00", sig) is True
        # a forged approval text fails verification
        assert verify_event("human", "approval", "DIFFERENT approval", "2026-06-14T12:00:00", sig) is False

    def test_human_falls_back_to_archivist_token(self, monkeypatch):
        monkeypatch.delenv("MGIMIND_TOKEN_MAESTRO", raising=False)
        monkeypatch.setenv("MGIMIND_TOKEN_ARCHIVIST", "arch-key")
        sig = sign_event("human", "brief", "build a page", "2026-06-14T12:00:00")
        assert sig and len(sig) == 64


class TestVerify:
    def _sig(self):
        return sign_event("soloist", "code", "Built the page", "2026-06-14T12:00:00")

    def test_valid_signature_verifies(self):
        assert verify_event("soloist", "code", "Built the page", "2026-06-14T12:00:00", self._sig()) is True

    def test_tampered_content_fails(self):
        assert verify_event("soloist", "code", "Built a DIFFERENT page", "2026-06-14T12:00:00", self._sig()) is False

    def test_forged_author_fails(self):
        # a different agent cannot present another agent's signature as its own
        assert verify_event("conductor", "code", "Built the page", "2026-06-14T12:00:00", self._sig()) is False

    def test_tampered_timestamp_fails(self):
        assert verify_event("soloist", "code", "Built the page", "2026-06-14T13:00:00", self._sig()) is False

    def test_no_signature_is_none(self):
        # unsignable rows (human, or pre-signing data) read as attested, not forged
        assert verify_event("soloist", "code", "Built the page", "2026-06-14T12:00:00", "") is None

    def test_no_key_actor_is_none(self, monkeypatch):
        # an actor with no key at all can't be verified -> None (attested), not forged.
        # human now resolves to the orchestrator key, so use a truly keyless actor.
        monkeypatch.delenv("MGIMIND_TOKEN_MAESTRO", raising=False)
        assert verify_event("nobody", "brief", "x", "t", "anysig") is None

    def test_field_boundary_collision_fails(self):
        # a plain concat would let bytes shift across field boundaries with the
        # same HMAC: sign(kind='code', text='XYZ') == sign(kind='cod', text='eXYZ').
        # The NUL-delimited encoding must reject that so a row can't be re-split.
        sig = sign_event("soloist", "code", "XYZ", "2026-06-14T12:00:00")
        assert verify_event("soloist", "code", "XYZ", "2026-06-14T12:00:00", sig) is True
        assert verify_event("soloist", "cod", "eXYZ", "2026-06-14T12:00:00", sig) is False
        # boundary between text and timestamp, too
        sig2 = sign_event("soloist", "deploy", "done", "T")
        assert verify_event("soloist", "deploy", "don", "eT", sig2) is False
        # boundary between actor and kind: a re-split actor isn't a known agent,
        # so it has no key and can never verify as that signature (None, not a
        # valid True). Re-splitting INTO a real actor is blocked by the NUL too.
        sig3 = sign_event("soloist", "code", "x", "t")
        assert verify_event("soloi", "stcode", "x", "t", sig3) is None
        # and a real actor with a shifted kind boundary still fails
        sig4 = sign_event("conductor", "plan", "ab", "t")
        assert verify_event("conductor", "pla", "nab", "t", sig4) is False
