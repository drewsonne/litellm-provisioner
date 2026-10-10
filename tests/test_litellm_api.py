"""LiteLLMClient against a fake LiteLLM (pytest-httpx)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest
from litellm_api import (
    MANAGED_BY,
    KeySettings,
    LiteLLMClient,
    LiteLLMError,
    ManagedKey,
)

if TYPE_CHECKING:
    from pytest_httpx import HTTPXMock

URL = "http://litellm.test:4000"
MASTER = "sk-master-test"
SETTINGS = KeySettings(
    models=("claude-haiku-4-5",), max_budget=25.0, budget_duration="7d"
)


def client() -> LiteLLMClient:
    return LiteLLMClient(URL, MASTER)


def key_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "token": "abc123",
        "key_alias": "a",
        "models": ["claude-haiku-4-5"],
        "max_budget": 25.0,
        "budget_duration": "7d",
        "metadata": {"managed_by": MANAGED_BY, "owner": "ns/name"},
    }
    row.update(overrides)
    return row


def stored_key(models: tuple[str, ...], max_budget: float) -> ManagedKey:
    return ManagedKey(
        token="t",  # noqa: S106
        managed=True,
        owner="o",
        models=models,
        max_budget=max_budget,
        budget_duration="7d",
    )


def test_find_key_returns_none_when_alias_unused(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{URL}/key/list?key_alias=a&return_full_object=true", json={"keys": []}
    )
    assert client().find_key("a") is None


def test_find_key_reads_managed_marker(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"keys": [key_row()]})
    assert client().find_key("a") == ManagedKey(
        token="abc123",  # noqa: S106
        managed=True,
        owner="ns/name",
        models=("claude-haiku-4-5",),
        max_budget=25.0,
        budget_duration="7d",
    )


def test_find_key_marks_foreign_keys_unmanaged(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"keys": [key_row(metadata={})]})
    found = client().find_key("a")
    assert found is not None
    assert found.managed is False
    assert found.owner is None


def test_find_key_refuses_duplicate_aliases(httpx_mock: HTTPXMock) -> None:
    other = key_row(token="def")  # noqa: S106
    httpx_mock.add_response(json={"keys": [key_row(), other]})
    with pytest.raises(LiteLLMError, match="2 keys"):
        client().find_key("a")


def test_find_key_ignores_partial_alias_matches(httpx_mock: HTTPXMock) -> None:
    rows = [
        key_row(key_alias="openclaw", token="exact"),  # noqa: S106
        key_row(key_alias="openclaw-old", token="old"),  # noqa: S106
    ]
    httpx_mock.add_response(json={"keys": rows})
    found = client().find_key("openclaw")
    assert found is not None
    assert found.token == "exact"  # noqa: S105


def test_find_key_returns_none_when_only_partial_matches(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json={"keys": [key_row(key_alias="claude-memory")]})
    assert client().find_key("claude") is None


def test_find_key_alias_match_is_case_sensitive(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"keys": [key_row(key_alias="OpenClaw")]})
    assert client().find_key("openclaw") is None


def test_generate_sends_marker_and_returns_key_and_token(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json={"key": "sk-new", "token": "tok"})
    assert client().generate("a", SETTINGS, "ns/name") == ("sk-new", "tok")
    sent = json.loads(httpx_mock.get_requests()[0].content)
    assert sent == {
        "key_alias": "a",
        "models": ["claude-haiku-4-5"],
        "max_budget": 25.0,
        "budget_duration": "7d",
        "metadata": {"managed_by": MANAGED_BY, "owner": "ns/name"},
    }


def test_update_and_delete_use_token(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=f"{URL}/key/update", json={})
    httpx_mock.add_response(url=f"{URL}/key/delete", json={"deleted_keys": ["tok"]})
    client().update("tok", SETTINGS)
    client().delete("tok")
    update, delete = (json.loads(r.content) for r in httpx_mock.get_requests())
    assert update["key"] == "tok"
    assert update["models"] == ["claude-haiku-4-5"]
    assert delete == {"keys": ["tok"]}


def test_http_error_becomes_litellm_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=500, text="boom")
    with pytest.raises(LiteLLMError, match="HTTP 500"):
        client().delete("tok")


def test_network_error_becomes_litellm_error_without_master_key(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(LiteLLMError) as caught:
        client().delete("tok")
    assert MASTER not in str(caught.value)


def test_integer_budget_matches_float_budget() -> None:
    settings = KeySettings.from_spec(
        {"models": ["m"], "maxBudget": 25, "budgetDuration": "7d"}
    )
    stored = stored_key(("m",), 25.0)
    assert settings.matches(stored)


def test_model_order_does_not_count_as_drift() -> None:
    settings = KeySettings(("a", "b"), 1.0, "7d")
    assert settings.matches(stored_key(("b", "a"), 1.0))
    assert not settings.matches(stored_key(("a",), 1.0))
