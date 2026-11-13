"""SC-UP-01: per-person Research access through Sustainable Catalyst sign-in (Workspace sessions).

The browser holds no Research service credential and chooses no owner. A person signs in to Sustainable Catalyst
(SC-AUTH-02: SC Identity OIDC with MFA, Workspace session) and the Research page asks Workspace for a short-lived
``sc-workspace-research-session/1.0`` document, signed with the Workspace Research key Research already pins
(``workspace_ingest.Keyring``). Research takes the person from that signature:

* signed by a current (neither retired nor revoked) Workspace key, issuer Workspace, audience Research;
* SC Identity authentication only (an OIDC session or SC access token), never the legacy service bearer;
* issued at most ``CLOCK_SKEW_SECONDS`` ahead, valid for at most ``SESSION_MAX_LIFETIME_SECONDS``, unexpired.

The person is the owner of their own records. Acting on another owner's records still needs that owner's Workspace
editor attestation, whose grantee must be the signed person (``editorial.resolve_actor``).

Off by default (``RESEARCH_USER_IDENTITY``). Staging and development only: production stays closed by constant until
separately authorised. While it is on, the browser routes refuse the staging service credential.
"""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException

from . import workspace_ingest as wi

MODE_ENV = "RESEARCH_USER_IDENTITY"
MODE = "workspace-session"
SESSION_SCHEMA = "sc-workspace-research-session/1.0"
SCHEME = "SC-Workspace"
SESSION_MAX_LIFETIME_SECONDS = 300
CLOCK_SKEW_SECONDS = 60
MAX_HEADER_BYTES = 8192
SC_IDENTITY_METHODS = frozenset({"sc-identity-oidc-session", "sc-identity-access-token"})
SUBJECT = re.compile(r"^workspace-user:[A-Za-z0-9._:@-]{1,128}$")
PRODUCTION_USER_SESSIONS_AUTHORIZED = False
LOOPBACK = {"127.0.0.1", "localhost", "[::1]"}


@dataclass(frozen=True)
class SessionState:
    enabled: bool
    reason: str


def session_state(env=None) -> SessionState:
    env = os.environ if env is None else env
    mode = (env.get(MODE_ENV) or "disabled").strip().lower()
    environment = (env.get("RESEARCH_ENV") or "").strip().lower()
    if mode == "disabled":
        return SessionState(False, "mode-disabled")
    if mode != MODE:
        return SessionState(False, "unknown-mode")
    if environment == "production" and not PRODUCTION_USER_SESSIONS_AUTHORIZED:
        return SessionState(False, "production-not-authorized")
    if environment not in {"staging", "development"}:
        return SessionState(False, "environment-not-staging")
    if not env.get("RESEARCH_WORKSPACE_PUBLIC_KEY_FILE"):
        return SessionState(False, "workspace-key-not-configured")
    if not workspace_url(env):
        return SessionState(False, "workspace-url-not-configured")
    return SessionState(True, "active")


def workspace_url(env=None) -> str:
    """The Workspace origin the browser signs in at: https, or http on a loopback host (local staging tunnels)."""
    env = os.environ if env is None else env
    value = (env.get("RESEARCH_WORKSPACE_PUBLIC_URL") or "").strip().rstrip("/")
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if parts.path or parts.query or parts.fragment or parts.username or parts.password or not parts.hostname:
        return ""
    if parts.scheme == "https" or (parts.scheme == "http" and parts.netloc.rsplit(":", 1)[0] in LOOPBACK):
        return value
    return ""


def public_status(env=None) -> dict:
    state = session_state(env)
    return {"mode": MODE if state.enabled else "closed", "reason": state.reason,
            "workspaceUrl": workspace_url(env) if state.enabled else None,
            "sessionSchema": SESSION_SCHEMA, "maxLifetimeSeconds": SESSION_MAX_LIFETIME_SECONDS,
            "serviceCredentialAccepted": not state.enabled}


def _refuse(message: str) -> HTTPException:
    return HTTPException(status_code=401, detail={"code": "SC_SIGN_IN_REQUIRED", "message": message},
                         headers={"WWW-Authenticate": SCHEME})


def _instant(value: Any) -> datetime:
    when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if when.tzinfo is None:
        raise ValueError("no time zone")
    return when


def verify_session(authorization: str | None, key=None, now: datetime | None = None) -> dict:
    """Return the verified session body, or raise PermissionError with a reason safe to show."""
    scheme, _, value = (authorization or "").strip().partition(" ")
    if scheme != SCHEME or not value.strip():
        raise PermissionError("Sign in to Sustainable Catalyst to use your Research workspace")
    if len(value) > MAX_HEADER_BYTES:
        raise PermissionError("The Sustainable Catalyst session is malformed")
    try:
        doc = json.loads(base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4)))
    except (ValueError, json.JSONDecodeError) as exc:
        raise PermissionError("The Sustainable Catalyst session is malformed") from exc
    if not isinstance(doc, dict):
        raise PermissionError("The Sustainable Catalyst session is malformed")
    keyring = key or wi.load_public_key()
    try:
        if isinstance(keyring, wi.Keyring):
            keyring.require_current((doc.get("signature") or {}).get("keyId"))
        body = wi.verify_document(keyring, SESSION_SCHEMA, doc)
    except ValueError as exc:
        raise PermissionError("The Sustainable Catalyst session could not be verified") from exc
    if not SUBJECT.fullmatch(str(body.get("subject") or "")):
        raise PermissionError("The Sustainable Catalyst session names no Workspace person")
    if body.get("authentication") not in SC_IDENTITY_METHODS:
        raise PermissionError("Research needs a Sustainable Catalyst sign-in, not a service credential")
    now = now or datetime.now(timezone.utc)
    try:
        issued, expires = _instant(body.get("issuedAt")), _instant(body.get("expiresAt"))
    except (TypeError, ValueError) as exc:
        raise PermissionError("The Sustainable Catalyst session has no valid issue time or expiry") from exc
    if (issued - now).total_seconds() > CLOCK_SKEW_SECONDS or \
            (expires - issued).total_seconds() > SESSION_MAX_LIFETIME_SECONDS + CLOCK_SKEW_SECONDS:
        raise PermissionError("The Sustainable Catalyst session is dated in the future or valid for too long")
    if expires <= now:
        raise PermissionError("The Sustainable Catalyst session has expired; sign in again")
    return body


def principal(authorization: str | None, owner_scope: str | None, actor_scope: str | None) -> tuple[str, str]:
    """(owner, acting person) for a browser route.

    With Workspace sessions on, the acting person is the signed subject and the staging credential is refused; the
    owner defaults to that person, and an editor names the owner they act for (``resolve_actor`` then requires that
    owner's attestation for the signed person). Otherwise the staging service credential applies as before.
    """
    if session_state().enabled:
        try:
            person = verify_session(authorization)["subject"]
        except PermissionError as exc:
            raise _refuse(str(exc)) from exc
        except (RuntimeError, OSError) as exc:  # the Workspace key is missing or unreadable: fail closed
            raise HTTPException(status_code=503, detail="Research cannot verify Sustainable Catalyst sessions") from exc
        owner = (owner_scope or "").strip() or person
        if len(owner) > 160:
            raise HTTPException(status_code=400, detail="X-SC-Owner-Scope is too long")
        return owner, person
    from . import convergence as cv
    return cv.internal_principal(authorization, owner_scope), (actor_scope or "").strip()[:160]
