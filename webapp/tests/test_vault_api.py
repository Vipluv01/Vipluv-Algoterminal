"""Vault -- app/routers/vault.py. STORE-ONLY: a decrypted secret must
never come back from any endpoint, appear in a log line, or appear in an
error body. See tests/test_crypto.py's own _fresh_key fixture for why the
same pattern is needed here -- crypto._fernet() is lru_cache'd process-wide."""

import json

import pytest
from cryptography.fernet import Fernet

from app import crypto


@pytest.fixture(autouse=True)
def _fresh_key(monkeypatch):
    monkeypatch.setenv("BROKER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    crypto._fernet.cache_clear()
    yield
    crypto._fernet.cache_clear()


def test_get_credential_is_null_when_none_stored(client):
    resp = client.get("/vault/credential")
    assert resp.status_code == 200
    assert resp.json() is None


def test_store_and_read_back_a_credential_never_returns_the_secret(client):
    create = client.post("/vault/credential", json={
        "broker": "zerodha", "api_key": "kite-live-key-abcdef1234", "api_secret": "kite-live-secret-zyxwvu9876",
    })
    assert create.status_code == 200, create.text
    body = create.json()
    assert body["broker"] == "zerodha"
    assert body["api_key_last4"] == "1234"
    assert body["api_secret_last4"] == "9876"
    assert body["has_access_token"] is False
    assert "rotated_at" in body

    # The full raw response body -- not just the modeled fields -- must
    # never contain the actual secrets, in case a future field addition
    # accidentally leaks one outside the declared response_model.
    raw = create.text
    assert "kite-live-key-abcdef1234" not in raw
    assert "kite-live-secret-zyxwvu9876" not in raw

    fetched = client.get("/vault/credential")
    assert fetched.json()["api_key_last4"] == "1234"
    assert "kite-live-key-abcdef1234" not in fetched.text
    assert "kite-live-secret-zyxwvu9876" not in fetched.text


def test_credential_with_access_token_reports_has_access_token(client):
    create = client.post("/vault/credential", json={
        "broker": "groww", "api_key": "groww-key-1111", "api_secret": "groww-secret-2222",
        "access_token": "groww-access-token-secretvalue",
    })
    body = create.json()
    assert body["has_access_token"] is True
    assert "groww-access-token-secretvalue" not in create.text


def test_posting_again_rotates_in_place_not_a_second_row(client):
    first = client.post("/vault/credential", json={
        "broker": "zerodha", "api_key": "old-key-0001", "api_secret": "old-secret-0002",
    }).json()

    second = client.post("/vault/credential", json={
        "broker": "zerodha", "api_key": "new-key-9999", "api_secret": "new-secret-8888",
    }).json()

    assert second["api_key_last4"] == "9999"
    fetched = client.get("/vault/credential").json()
    assert fetched["api_key_last4"] == "9999"  # the rotation stuck, not the original


def test_empty_api_key_or_secret_is_rejected(client):
    resp = client.post("/vault/credential", json={"broker": "zerodha", "api_key": "", "api_secret": "x"})
    assert resp.status_code == 400


def test_delete_credential(client):
    client.post("/vault/credential", json={"broker": "zerodha", "api_key": "key-0001", "api_secret": "secret-0002"})
    delete = client.delete("/vault/credential")
    assert delete.status_code == 200
    assert client.get("/vault/credential").json() is None


def test_deleting_a_nonexistent_credential_is_a_404(client):
    resp = client.delete("/vault/credential")
    assert resp.status_code == 404


def test_a_bad_request_body_error_does_not_echo_a_secret(client):
    """Real risk with FastAPI/Pydantic's default validation error handler:
    a 422 for an invalid field can echo the submitted value back in the
    error body. Sending a non-string api_key (which DOES fail validation,
    unlike a normal string secret) and confirming the actual secret text
    never appears in the response, even in this failure path."""
    resp = client.post("/vault/credential", json={
        "broker": "zerodha", "api_key": {"nested": "not-a-string-should-fail-validation"}, "api_secret": "x",
    })
    assert resp.status_code == 422
    assert "not-a-string-should-fail-validation" not in resp.text
