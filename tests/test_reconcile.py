"""reconcile() and revoke() against a fake LiteLLM and fake Secrets."""

from __future__ import annotations

import logging
from typing import Any, cast

import keys
import kopf
import pytest
from litellm_api import KeySettings, LiteLLMClient, ManagedKey

LOGGER = logging.getLogger("test")
SPEC = {
    "alias": "claude-memory",
    "models": ["claude-haiku-4-5"],
    "maxBudget": 25,
    "budgetDuration": "7d",
    "secretName": "litellm-key",
}
OWNER = "claude-memory/claude-memory"


class FakeClient:
    """In-memory stand-in for LiteLLMClient."""

    def __init__(self, existing: ManagedKey | None = None) -> None:
        """Start with an optional pre-existing key."""
        self.keys: dict[str, ManagedKey] = {}
        if existing:
            self.keys[cast("str", SPEC["alias"])] = existing
        self.calls: list[str] = []

    def find_key(self, alias: str) -> ManagedKey | None:
        """Return the stored key for alias."""
        return self.keys.get(alias)

    def generate(
        self, alias: str, settings: KeySettings, owner: str
    ) -> tuple[str, str]:
        """Store a new key and return (value, token)."""
        self.calls.append("generate")
        value = f"sk-{alias}-{len(self.calls)}"
        self.keys[alias] = ManagedKey(
            keys.token_for(value),
            True,  # noqa: FBT003
            owner,
            settings.models,
            settings.max_budget,
            settings.budget_duration,
        )
        return value, keys.token_for(value)

    def update(self, token: str, settings: KeySettings) -> None:  # noqa: ARG002
        """Record an update."""
        self.calls.append("update")

    def delete(self, token: str) -> None:
        """Remove the key with this token."""
        self.calls.append("delete")
        self.keys = {a: k for a, k in self.keys.items() if k.token != token}


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    store: dict[str, str] = {}
    monkeypatch.setattr(
        keys.kube_secrets, "read_api_key", lambda ns, n, _body: store.get(f"{ns}/{n}")
    )
    monkeypatch.setattr(
        keys.kube_secrets,
        "write_api_key",
        lambda ns, n, _body, key, _logger: store.__setitem__(f"{ns}/{n}", key),
    )
    monkeypatch.setattr(
        keys.kube_secrets,
        "delete_secret",
        lambda ns, n, _body, _logger: store.pop(f"{ns}/{n}", None),
    )
    return store


def managed(value: str, **overrides: object) -> ManagedKey:
    fields: dict[str, Any] = {
        "token": keys.token_for(value),
        "managed": True,
        "owner": OWNER,
        "models": ("claude-haiku-4-5",),
        "max_budget": 25.0,
        "budget_duration": "7d",
    }
    fields.update(overrides)
    return ManagedKey(**fields)


def run(client: FakeClient) -> None:
    keys.reconcile(
        SPEC,
        "claude-memory",
        "claude-memory",
        {},
        cast("LiteLLMClient", client),
        LOGGER,
    )


def test_new_key_is_generated_and_stored(secrets: dict[str, str]) -> None:
    client = FakeClient()
    run(client)
    assert client.calls == ["generate"]
    assert secrets["claude-memory/litellm-key"].startswith("sk-claude-memory")


def test_key_in_sync_is_left_alone(secrets: dict[str, str]) -> None:
    secrets["claude-memory/litellm-key"] = "sk-current"
    client = FakeClient(managed("sk-current"))
    run(client)
    assert client.calls == []


def test_changed_settings_update_in_place(secrets: dict[str, str]) -> None:
    secrets["claude-memory/litellm-key"] = "sk-current"
    client = FakeClient(managed("sk-current", max_budget=5.0))
    run(client)
    assert client.calls == ["update"]
    assert secrets["claude-memory/litellm-key"] == "sk-current"


def test_missing_secret_replaces_the_key(secrets: dict[str, str]) -> None:
    client = FakeClient(managed("sk-lost"))
    run(client)
    assert client.calls == ["delete", "generate"]
    assert "claude-memory/litellm-key" in secrets


def test_stale_secret_replaces_the_key(secrets: dict[str, str]) -> None:
    secrets["claude-memory/litellm-key"] = "sk-old-revoked"
    client = FakeClient(managed("sk-other"))
    run(client)
    assert client.calls == ["delete", "generate"]


@pytest.mark.usefixtures("secrets")
def test_unmanaged_alias_is_refused() -> None:
    client = FakeClient(managed("sk-ui", managed=False, owner=None))
    with pytest.raises(kopf.PermanentError, match="not managed"):
        run(client)
    assert client.calls == []


@pytest.mark.usefixtures("secrets")
def test_alias_owned_by_another_resource_is_refused() -> None:
    client = FakeClient(managed("sk-x", owner="other-ns/other"))
    with pytest.raises(kopf.PermanentError, match="other-ns/other"):
        run(client)
    assert client.calls == []


def test_integer_budget_matches_float_budget(secrets: dict[str, str]) -> None:
    secrets["claude-memory/litellm-key"] = "sk-current"
    client = FakeClient(managed("sk-current", max_budget=25.0))
    run(client)  # SPEC says maxBudget: 25 (an int)
    assert "update" not in client.calls


def test_revoke_deletes_our_key_and_secret(secrets: dict[str, str]) -> None:
    secrets["claude-memory/litellm-key"] = "sk-current"
    client = FakeClient(managed("sk-current"))
    keys.revoke(
        "claude-memory",
        "litellm-key",
        "claude-memory",
        "claude-memory",
        {},
        cast("LiteLLMClient", client),
        LOGGER,
    )
    assert client.calls == ["delete"]
    assert secrets == {}


@pytest.mark.usefixtures("secrets")
def test_revoke_leaves_foreign_key_alone() -> None:
    client = FakeClient(managed("sk-ui", managed=False, owner=None))
    keys.revoke(
        "claude-memory",
        "litellm-key",
        "claude-memory",
        "claude-memory",
        {},
        cast("LiteLLMClient", client),
        LOGGER,
    )
    assert client.calls == []


@pytest.mark.usefixtures("secrets")
def test_key_value_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client = FakeClient(managed("sk-lost"))
    run(client)
    assert "sk-" not in caplog.text
