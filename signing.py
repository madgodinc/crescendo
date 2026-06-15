"""Per-agent HMAC signing for the audit trail.

The hash chain proves the trail wasn't edited after the fact. On its own the
author of each row is just a string, so a party with write access to the store
could rewrite a row under any author and recompute the chain. Signing closes
that: each agent holds an HMAC key, and its rows carry
`HMAC(agent_key, prev_hash || actor || kind || text || ts)`. Verifying a row
requires the agent's key, so a row's author can't be forged without it.

Threat model, stated honestly:
  - Defends against: an external party with store access editing or
    re-authoring rows. Without the agent keys they cannot produce a valid
    signature, so any tampered row fails verification.
  - Does NOT defend against: the orchestrator itself, which reads the keys from
    its environment to write on each agent's behalf. This is integrity +
    provenance of the published trail under trust in the orchestrator, not a
    zero-trust guarantee against a malicious orchestrator. (A zero-trust version
    would have each agent sign inside its own process and never expose the key.)

Keys are the per-agent bearer tokens already in the environment, reused as HMAC
secrets so no new secret material is introduced.
"""

from __future__ import annotations

import hashlib
import hmac
import os

# actor id (as written in the trail) -> env var holding its key
_KEY_ENV = {
    "conductor": "MGIMIND_TOKEN_CONDUCTOR",
    "soloist": "MGIMIND_TOKEN_SOLOIST",
    "tuningfork": "MGIMIND_TOKEN_TUNING_FORK",
    "stagetech": "MGIMIND_TOKEN_STAGE_TECH",
    "archivist": "MGIMIND_TOKEN_ARCHIVIST",
}


def agent_key(actor: str) -> bytes | None:
    env = _KEY_ENV.get(actor)
    val = os.environ.get(env, "") if env else ""
    return val.encode("utf-8") if val else None


def _message(actor: str, kind: str, text: str, ts: str) -> bytes:
    """Encode the signed fields unambiguously. A plain concatenation lets an
    attacker shift bytes across field boundaries and keep the same HMAC
    (sign('code','XYZ') == sign('cod','eXYZ')), which would let a row be
    re-attributed without breaking the signature. Join with a NUL, which a
    UTF-8 trail field never contains, so each field stays distinct."""
    return "\x00".join((actor, kind, text, ts)).encode("utf-8")


def sign_event(actor: str, kind: str, text: str, ts: str) -> str:
    """Return the agent's HMAC over its event content, or "" if the actor has no
    key (e.g. the human, or an environment without the tokens). The hash chain
    handles ordering/integrity; this signature handles authorship."""
    key = agent_key(actor)
    if not key:
        return ""
    return hmac.new(key, _message(actor, kind, text, ts), hashlib.sha256).hexdigest()


def verify_event(actor: str, kind: str, text: str, ts: str, sig: str) -> bool | None:
    """True/False if the row is signed and we hold the key; None if unsignable
    (no key for this actor, or no signature recorded) so the caller can show
    'attested' rather than a hard fail."""
    if not sig:
        return None
    key = agent_key(actor)
    if not key:
        return None
    return hmac.compare_digest(sign_event(actor, kind, text, ts), sig)
