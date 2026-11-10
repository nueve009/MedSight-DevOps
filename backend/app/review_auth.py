"""
Who is calling the review console (app/review.py).

A request needs Authorization: Bearer <Supabase access token>. It is accepted
only if the signature is valid, it hasn't expired, its audience is
"authenticated", it isn't an anonymous session, and the account is active in
`reviewers`. The name in that table is the actor: a name in a request body is
never used. With config.REVIEW_REQUIRE_MFA on (the default), the authenticator
code must have been entered too (aal2); only the local launcher turns it off.

The signing method follows the settings, never the token's own header, so a
token can't choose an easier algorithm: HS256 when SUPABASE_JWT_SECRET is set,
otherwise RS256 or ES256 with the project's published keys.
"""
import logging
import uuid
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import Header, HTTPException, Request

from app import config

log = logging.getLogger(__name__)

AUDIENCE = "authenticated"
JWKS_CACHE_S = 600
_jwk_clients: dict[str, jwt.PyJWKClient] = {}


@dataclass(frozen=True)
class Reviewer:
    user_id: str
    name: str
    token: str   # passed on to Supabase Storage, so it applies this person's permissions


class Refused(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _jwk_client() -> jwt.PyJWKClient:
    url = f"{config.SUPABASE_URL}/auth/v1/.well-known/jwks.json"
    if url not in _jwk_clients:
        _jwk_clients[url] = jwt.PyJWKClient(url, cache_keys=True, lifespan=JWKS_CACHE_S, timeout=5)
    return _jwk_clients[url]


def _verified_claims(token: str) -> dict:
    options = {"require": ["exp", "sub", "aud"]}
    issuer = f"{config.SUPABASE_URL}/auth/v1" if config.SUPABASE_URL else None
    if config.SUPABASE_JWT_SECRET:
        key, algorithms = config.SUPABASE_JWT_SECRET, ["HS256"]
    elif config.SUPABASE_URL:
        try:
            key, algorithms = _jwk_client().get_signing_key_from_jwt(token).key, ["RS256", "ES256"]
        except jwt.PyJWKClientConnectionError as e:
            raise Refused(503, "signing_keys_unavailable") from e
        except (jwt.PyJWKClientError, jwt.InvalidTokenError) as e:
            raise Refused(401, "unknown_signing_key") from e
    else:
        raise Refused(503, "auth_not_configured")
    try:
        return jwt.decode(token, key, algorithms=algorithms, audience=AUDIENCE, issuer=issuer, options=options)
    except jwt.ExpiredSignatureError as e:
        raise Refused(401, "expired") from e
    except jwt.InvalidTokenError as e:
        raise Refused(401, "invalid_token") from e


def authenticate(authorization: Optional[str], reviewer_name) -> Reviewer:
    """The reviewer behind this header. reviewer_name(user_id) returns an active reviewer's name or None."""
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise Refused(401, "no_token")
    claims = _verified_claims(token)
    if claims.get("is_anonymous") is not False:
        raise Refused(403, "anonymous")
    if config.REVIEW_REQUIRE_MFA and claims.get("aal") != "aal2":
        raise Refused(403, "two_factor_required")
    try:
        user_id = str(uuid.UUID(str(claims["sub"])))
    except ValueError as e:
        raise Refused(401, "invalid_subject") from e
    name = reviewer_name(user_id)
    if not name:
        raise Refused(403, "not_a_reviewer")
    return Reviewer(user_id, name, token)


_MESSAGES = {
    401: "Please sign in again.",
    403: "This account can't use the review console.",
    503: "Sign-in can't be checked right now.",
}


def require_reviewer(request: Request, authorization: Optional[str] = Header(None)) -> Reviewer:
    if not config.ADMIN_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    from app.review_store import get_store  # here, so importing this module needs no database driver

    try:
        return authenticate(authorization, get_store(request).reviewer_name)
    except Refused as e:
        log.warning("review console refused", extra={"fields": {"reason": e.reason, "path": request.url.path}})
        detail = "Enter your two-factor code first." if e.reason == "two_factor_required" else _MESSAGES[e.status]
        raise HTTPException(status_code=e.status, detail=detail) from e
