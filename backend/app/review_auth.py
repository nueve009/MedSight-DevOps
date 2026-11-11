"""
Who is calling the review console (app/review.py).

A request needs Authorization: Bearer <Supabase access token>. It is accepted
only if the signature is valid, it hasn't expired, its audience is
"authenticated", it isn't an anonymous session, and the account is active in
`reviewers`. The name in that table is the actor: a name in a request body is
never used. With config.REVIEW_REQUIRE_MFA on (the default), the authenticator
code must have been entered too (aal2); only the local launcher turns it off.

A token says how it was signed, and each method is tied to its own key, so a
token can't pick an easier one: HS256 only ever uses SUPABASE_JWT_SECRET, and
RS256 or ES256 only ever use the project's published keys. A project that has
moved to published keys can still have an old secret in its settings.
"""
import logging
import uuid
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import Header, HTTPException, Request
from jwt.algorithms import has_crypto

from app import config

log = logging.getLogger(__name__)

AUDIENCE = "authenticated"
JWKS_CACHE_S = 600
LEEWAY_S = 30   # a laptop's clock may be a little off from Supabase's
PUBLISHED_KEY_ALGORITHMS = ("RS256", "ES256")
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
        _jwk_clients[url] = jwt.PyJWKClient(url, cache_keys=True, lifespan=JWKS_CACHE_S, timeout=5,
                                            headers={"apikey": config.SUPABASE_ANON_KEY})
    return _jwk_clients[url]


def _verified_claims(token: str) -> dict:
    if not config.SUPABASE_JWT_SECRET and not config.SUPABASE_URL:
        raise Refused(503, "auth_not_configured")
    try:
        algorithm = jwt.get_unverified_header(token).get("alg")
    except jwt.InvalidTokenError as e:
        raise Refused(401, "invalid_token") from e

    if algorithm == "HS256" and config.SUPABASE_JWT_SECRET:
        key = config.SUPABASE_JWT_SECRET
    elif algorithm in PUBLISHED_KEY_ALGORITHMS and config.SUPABASE_URL:
        key = _published_key(token)
    else:
        raise Refused(401, "alg_not_allowed")

    issuer = f"{config.SUPABASE_URL}/auth/v1" if config.SUPABASE_URL else None
    options = {"require": ["exp", "sub", "aud"]}
    try:
        return jwt.decode(token, key, algorithms=[algorithm], audience=AUDIENCE, issuer=issuer, options=options,
                          leeway=LEEWAY_S)
    except jwt.InvalidTokenError as e:
        raise Refused(401, _reason(e)) from e


def _published_key(token: str):
    if not has_crypto:
        raise Refused(503, "missing_crypto_backend")
    try:
        return _jwk_client().get_signing_key_from_jwt(token).key
    except jwt.PyJWKClientConnectionError as e:
        raise Refused(503, "signing_keys_unavailable") from e
    except (jwt.PyJWKClientError, jwt.InvalidTokenError) as e:
        raise Refused(401, "no_matching_kid") from e


def _reason(error: jwt.InvalidTokenError) -> str:
    """Why a token failed, for the log. Reviewers only ever see "Please sign in again."."""
    for kind, reason in ((jwt.ExpiredSignatureError, "expired"), (jwt.InvalidSignatureError, "bad_signature"),
                         (jwt.InvalidAudienceError, "bad_audience"), (jwt.InvalidIssuerError, "bad_issuer"),
                         (jwt.ImmatureSignatureError, "not_yet_valid"), (jwt.InvalidIssuedAtError, "not_yet_valid"),
                         (jwt.MissingRequiredClaimError, "missing_claim"),
                         (jwt.InvalidAlgorithmError, "alg_not_allowed")):
        if isinstance(error, kind):
            return reason
    return "invalid_token"


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
