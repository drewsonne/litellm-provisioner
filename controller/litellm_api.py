"""Minimal LiteLLM admin API client for virtual keys.

The only place that talks to LiteLLM. Error messages carry the method, path
and HTTP status, never a key value or the master key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Mapping

MANAGED_BY = "litellm-provisioner"


class LiteLLMError(Exception):
    """LiteLLM was unreachable or rejected a request."""


@dataclass(frozen=True)
class ManagedKey:
    """A key as LiteLLM reports it. ``token`` is sha256 of the key value."""

    token: str
    managed: bool
    owner: str | None
    models: tuple[str, ...]
    max_budget: float | None
    budget_duration: str | None


@dataclass(frozen=True)
class KeySettings:
    """The settings a LiteLLMKey declares for its key."""

    models: tuple[str, ...]
    max_budget: float
    budget_duration: str

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any]) -> KeySettings:
        """Build settings from a LiteLLMKey spec."""
        return cls(
            models=tuple(spec["models"]),
            max_budget=float(spec["maxBudget"]),
            budget_duration=str(spec["budgetDuration"]),
        )

    def as_api(self) -> dict[str, Any]:
        """Return the fields LiteLLM's key endpoints take."""
        return {
            "models": list(self.models),
            "max_budget": self.max_budget,
            "budget_duration": self.budget_duration,
        }

    def matches(self, key: ManagedKey) -> bool:
        """Return True if LiteLLM already has these settings for the key."""
        return (
            sorted(key.models) == sorted(self.models)
            and key.max_budget is not None
            and float(key.max_budget) == self.max_budget
            and key.budget_duration == self.budget_duration
        )


class LiteLLMClient:
    """Calls LiteLLM's /key endpoints with the master key."""

    def __init__(self, base_url: str, master_key: str) -> None:
        """Connect to LiteLLM at base_url, authenticating as the master key."""
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {master_key}"},
            timeout=30.0,
        )

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            msg = f"{method} {path}: {type(exc).__name__}"
            raise LiteLLMError(msg) from None
        if resp.status_code >= 400:
            msg = f"{method} {path}: HTTP {resp.status_code}"
            raise LiteLLMError(msg)
        return resp.json()

    def find_key(self, alias: str) -> ManagedKey | None:
        """Return the key with this alias, or None if there is none."""
        body = self._call(
            "GET",
            "/key/list",
            params={"key_alias": alias, "return_full_object": "true"},
        )
        keys = body.get("keys") or []
        if not keys:
            return None
        if len(keys) > 1:
            msg = f"alias {alias!r} matches {len(keys)} keys in LiteLLM"
            raise LiteLLMError(msg)
        row = keys[0]
        meta = row.get("metadata") or {}
        managed = meta.get("managed_by") == MANAGED_BY
        return ManagedKey(
            token=row["token"],
            managed=managed,
            owner=meta.get("owner") if managed else None,
            models=tuple(row.get("models") or ()),
            max_budget=row.get("max_budget"),
            budget_duration=row.get("budget_duration"),
        )

    def generate(
        self, alias: str, settings: KeySettings, owner: str
    ) -> tuple[str, str]:
        """Create a key. Returns (key value, token)."""
        body = self._call(
            "POST",
            "/key/generate",
            json={
                "key_alias": alias,
                **settings.as_api(),
                "metadata": {"managed_by": MANAGED_BY, "owner": owner},
            },
        )
        return body["key"], body["token"]

    def update(self, token: str, settings: KeySettings) -> None:
        """Apply settings to an existing key; its value stays the same."""
        self._call("POST", "/key/update", json={"key": token, **settings.as_api()})

    def delete(self, token: str) -> None:
        """Revoke a key."""
        self._call("POST", "/key/delete", json={"keys": [token]})


def client_from_env() -> LiteLLMClient:
    """Build a client from LITELLM_URL and LITELLM_MASTER_KEY."""
    return LiteLLMClient(os.environ["LITELLM_URL"], os.environ["LITELLM_MASTER_KEY"])
