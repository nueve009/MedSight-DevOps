"""Who may use the review console. Tokens are signed here with test keys; no network."""
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app import config, review_auth

SECRET = "test-secret-that-is-long-enough-for-hs256-signing"
URL = "https://project.test"
USER = str(uuid.uuid4())


def claims(**changes) -> dict:
    base = {"sub": USER, "aud": "authenticated", "exp": int(time.time()) + 600, "iss": f"{URL}/auth/v1",
            "aal": "aal2", "is_anonymous": False}
    return {k: v for k, v in {**base, **changes}.items() if v is not ...}


def token(secret: str = SECRET, **changes) -> str:
    return jwt.encode(claims(**changes), secret, algorithm="HS256")


def lookup(user_id):
    return "Ana" if user_id == USER else None


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_URL", URL)
    monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setattr(config, "REVIEW_REQUIRE_MFA", True)


def test_a_good_token_gives_the_name_from_the_reviewers_table():
    good = token()
    reviewer = review_auth.authenticate(f"Bearer {good}", lookup)
    assert (reviewer.name, reviewer.user_id, reviewer.token) == ("Ana", USER, good)


@pytest.mark.parametrize("header, status, reason", [
    (None, 401, "no_token"),
    ("", 401, "no_token"),
    ("Basic abc", 401, "no_token"),
    ("Bearer not-a-token", 401, "invalid_token"),
])
def test_missing_or_malformed_headers_are_refused(header, status, reason):
    with pytest.raises(review_auth.Refused) as e:
        review_auth.authenticate(header, lookup)
    assert (e.value.status, e.value.reason) == (status, reason)


@pytest.mark.parametrize("changes, status, reason", [
    ({"secret": "another-secret-that-is-long-enough-to-sign-with"}, 401, "invalid_token"),
    ({"exp": int(time.time()) - 60}, 401, "expired"),
    ({"exp": ...}, 401, "invalid_token"),
    ({"aud": "anon"}, 401, "invalid_token"),
    ({"iss": "https://other.test/auth/v1"}, 401, "invalid_token"),
    ({"is_anonymous": True}, 403, "anonymous"),
    ({"is_anonymous": ...}, 403, "anonymous"),
    ({"aal": "aal1"}, 403, "two_factor_required"),
    ({"aal": ...}, 403, "two_factor_required"),
    ({"sub": "not-a-uuid"}, 401, "invalid_subject"),
    ({"sub": str(uuid.uuid4())}, 403, "not_a_reviewer"),
])
def test_refused_tokens(changes, status, reason):
    with pytest.raises(review_auth.Refused) as e:
        review_auth.authenticate(f"Bearer {token(**changes)}", lookup)
    assert (e.value.status, e.value.reason) == (status, reason)


@pytest.mark.parametrize("changes", [{}, {"aal": "aal1"}, {"aal": ...}])
def test_with_two_step_off_a_password_session_is_enough(monkeypatch, changes):
    monkeypatch.setattr(config, "REVIEW_REQUIRE_MFA", False)
    assert review_auth.authenticate(f"Bearer {token(**changes)}", lookup).name == "Ana"


@pytest.mark.parametrize("changes, status, reason", [
    ({"secret": "another-secret-that-is-long-enough-to-sign-with"}, 401, "invalid_token"),
    ({"exp": int(time.time()) - 60}, 401, "expired"),
    ({"aud": "anon"}, 401, "invalid_token"),
    ({"is_anonymous": True}, 403, "anonymous"),
    ({"sub": str(uuid.uuid4())}, 403, "not_a_reviewer"),
])
def test_with_two_step_off_every_other_check_stays(monkeypatch, changes, status, reason):
    monkeypatch.setattr(config, "REVIEW_REQUIRE_MFA", False)
    with pytest.raises(review_auth.Refused) as e:
        review_auth.authenticate(f"Bearer {token(**{'aal': 'aal1', **changes})}", lookup)
    assert (e.value.status, e.value.reason) == (status, reason)


def test_inactive_reviewer_is_refused():
    # The store only returns active reviewers, so an inactive one looks up as None.
    with pytest.raises(review_auth.Refused) as e:
        review_auth.authenticate(f"Bearer {token()}", lambda _user: None)
    assert e.value.reason == "not_a_reviewer"


def test_an_unsigned_token_is_refused():
    unsigned = jwt.encode(claims(), "", algorithm="none")
    with pytest.raises(review_auth.Refused) as e:
        review_auth.authenticate(f"Bearer {unsigned}", lookup)
    assert e.value.reason == "invalid_token"


class FakeJwkClient:
    def __init__(self, key):
        self.key = key

    def get_signing_key_from_jwt(self, _token):
        return self


def ec_keys():
    private = ec.generate_private_key(ec.SECP256R1())
    return private, private.public_key()


def test_project_keys_are_used_when_no_secret_is_set(monkeypatch):
    private, public = ec_keys()
    monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", "")
    monkeypatch.setattr(review_auth, "_jwk_client", lambda: FakeJwkClient(public))
    good = jwt.encode(claims(), private, algorithm="ES256")
    assert review_auth.authenticate(f"Bearer {good}", lookup).name == "Ana"


def test_a_token_cannot_switch_to_a_shared_secret_signed_with_the_public_key(monkeypatch):
    _, public = ec_keys()
    pem = public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", "")
    monkeypatch.setattr(review_auth, "_jwk_client", lambda: FakeJwkClient(public))
    import base64
    import hashlib
    import hmac
    import json

    def part(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    head = part(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = part(json.dumps(claims()).encode())
    signature = part(hmac.new(pem, f"{head}.{body}".encode(), hashlib.sha256).digest())
    with pytest.raises(review_auth.Refused) as e:
        review_auth.authenticate(f"Bearer {head}.{body}.{signature}", lookup)
    assert e.value.reason == "invalid_token"


def test_nothing_configured_is_refused_not_open(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", "")
    monkeypatch.setattr(config, "SUPABASE_URL", "")
    with pytest.raises(review_auth.Refused) as e:
        review_auth.authenticate(f"Bearer {token()}", lookup)
    assert (e.value.status, e.value.reason) == (503, "auth_not_configured")
