import os

import pytest
from cryptography.fernet import Fernet

from app import crypto


@pytest.fixture(autouse=True)
def _fresh_key(monkeypatch):
    """Each test gets its own key and a cleared cache -- crypto._fernet()
    is lru_cache'd, which would otherwise leak one test's key/env into the
    next test that runs in the same process."""
    monkeypatch.setenv("BROKER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    crypto._fernet.cache_clear()
    yield
    crypto._fernet.cache_clear()


def test_encrypt_decrypt_round_trips():
    secret = "kite-connect-api-secret-abc123"
    ciphertext = crypto.encrypt(secret)
    assert ciphertext != secret.encode()
    assert crypto.decrypt(ciphertext) == secret


def test_ciphertext_is_not_plaintext_and_not_reused_across_calls():
    """Fernet includes a random IV -- encrypting the same secret twice must
    NOT produce identical ciphertext, or a stolen database dump would let
    an attacker spot which users share a broker password/API key."""
    a = crypto.encrypt("same-secret")
    b = crypto.encrypt("same-secret")
    assert a != b
    assert crypto.decrypt(a) == crypto.decrypt(b) == "same-secret"


def test_missing_key_refuses_rather_than_falling_back_to_a_default(monkeypatch):
    monkeypatch.delenv("BROKER_CREDENTIAL_KEY", raising=False)
    crypto._fernet.cache_clear()
    with pytest.raises(crypto.MissingCredentialKeyError):
        crypto.encrypt("anything")
