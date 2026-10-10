"""Handlers report status and turn LiteLLM outages into retries."""

from __future__ import annotations

import logging
from typing import Any

import keys
import kopf
import pytest
from litellm_api import LiteLLMError

# kopf decorators type handlers as framework-called; tests call them directly.
HANDLERS: Any = keys
LOGGER = logging.getLogger("test")
SPEC = {
    "alias": "a",
    "models": ["m"],
    "maxBudget": 1,
    "budgetDuration": "7d",
    "secretName": "s",
}


@pytest.fixture(autouse=True)
def no_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop handlers building a real LiteLLM client."""
    monkeypatch.setattr(keys, "client_from_env", object)


def call(handler: str, **extra: object) -> kopf.Patch:
    """Invoke a handler the way kopf does and return its patch."""
    patch = kopf.Patch()
    getattr(keys, handler)(
        spec=SPEC,
        body={},
        name="n",
        namespace="ns",
        logger=LOGGER,
        patch=patch,
        **extra,
    )
    return patch


@pytest.mark.parametrize("handler", ["create_fn", "resume_fn", "check_drift"])
def test_success_sets_ready(handler: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean reconcile marks the key ready."""
    monkeypatch.setattr(keys, "reconcile", lambda *_a, **_k: None)
    assert call(handler).status == {"ready": True, "message": ""}


def test_litellm_down_sets_not_ready_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A LiteLLM outage is recorded and retried."""

    def down(*_a: object, **_k: object) -> None:
        msg = "GET /key/list: ConnectError"
        raise LiteLLMError(msg)

    monkeypatch.setattr(keys, "reconcile", down)
    patch = kopf.Patch()
    with pytest.raises(kopf.TemporaryError):
        HANDLERS.create_fn(
            spec=SPEC, body={}, name="n", namespace="ns", logger=LOGGER, patch=patch
        )
    assert patch.status["ready"] is False
    assert "ConnectError" in patch.status["message"]


def test_refusal_sets_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal is recorded and not retried."""

    def refuse(*_a: object, **_k: object) -> None:
        msg = "alias 'a' belongs to x/y"
        raise kopf.PermanentError(msg)

    monkeypatch.setattr(keys, "reconcile", refuse)
    patch = kopf.Patch()
    with pytest.raises(kopf.PermanentError):
        HANDLERS.create_fn(
            spec=SPEC, body={}, name="n", namespace="ns", logger=LOGGER, patch=patch
        )
    assert patch.status == {"ready": False, "message": "alias 'a' belongs to x/y"}


def test_update_with_new_alias_revokes_old_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renaming the alias or secret revokes the old key first."""
    revoked: list[tuple[str, str]] = []
    monkeypatch.setattr(keys, "reconcile", lambda *_a, **_k: None)
    monkeypatch.setattr(
        keys, "revoke", lambda alias, secret, *_a, **_k: revoked.append((alias, secret))
    )
    old = {**SPEC, "alias": "old-alias", "secretName": "old-secret"}
    call("update_fn", old=old)
    assert revoked == [("old-alias", "old-secret")]


def test_update_with_same_alias_revokes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Other spec changes just reconcile."""
    monkeypatch.setattr(keys, "reconcile", lambda *_a, **_k: None)
    monkeypatch.setattr(keys, "revoke", lambda *_a, **_k: pytest.fail("revoked"))
    call("update_fn", old={**SPEC, "maxBudget": 5})


def test_delete_waits_when_litellm_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deletion retries while LiteLLM is unreachable."""

    def down(*_a: object, **_k: object) -> None:
        msg = "POST /key/delete: HTTP 503"
        raise LiteLLMError(msg)

    monkeypatch.setattr(keys, "revoke", down)
    with pytest.raises(kopf.TemporaryError):
        HANDLERS.delete_fn(spec=SPEC, body={}, name="n", namespace="ns", logger=LOGGER)
