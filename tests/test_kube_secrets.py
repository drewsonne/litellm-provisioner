"""The Secret helpers only touch Secrets this LiteLLMKey owns."""

from __future__ import annotations

import logging
from typing import Any

import kopf
import kube_secrets
import kubernetes
import pytest

LOGGER: Any = logging.getLogger("test")
BODY: Any = {
    "apiVersion": "litellm.drewsonne.github.io/v1",
    "kind": "LiteLLMKey",
    "metadata": {"name": "k", "namespace": "ns", "uid": "uid-ours"},
}


def secret(owner_uid: str | None) -> kubernetes.client.V1Secret:
    refs = (
        [
            kubernetes.client.V1OwnerReference(
                api_version="v1", kind="LiteLLMKey", name="x", uid=owner_uid
            )
        ]
        if owner_uid
        else None
    )
    return kubernetes.client.V1Secret(
        metadata=kubernetes.client.V1ObjectMeta(name="s", owner_references=refs),
        data={"api_key": "c2stdmFsdWU="},  # sk-value
    )


class FakeCoreApi:
    """Holds at most one Secret and records writes."""

    def __init__(self, existing: kubernetes.client.V1Secret | None) -> None:
        """Start with an optional existing Secret."""
        self.existing = existing
        self.calls: list[str] = []

    def read_namespaced_secret(self, **_: object) -> kubernetes.client.V1Secret:
        """Return the Secret or raise 404."""
        if self.existing is None:
            raise kubernetes.client.exceptions.ApiException(status=404)
        return self.existing

    def create_namespaced_secret(self, **_: object) -> None:
        """Create, or raise 409 if one exists."""
        if self.existing is not None:
            raise kubernetes.client.exceptions.ApiException(status=409)
        self.calls.append("create")

    def patch_namespaced_secret(self, **_: object) -> None:
        """Record a patch."""
        self.calls.append("patch")

    def delete_namespaced_secret(self, **_: object) -> None:
        """Record a delete."""
        self.calls.append("delete")


@pytest.fixture
def fake(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> FakeCoreApi:
    api = FakeCoreApi(request.param)
    monkeypatch.setattr(kube_secrets, "_api", lambda: api)
    return api


@pytest.mark.parametrize("fake", [secret("uid-ours")], indirect=True)
def test_owned_secret_is_read_patched_and_deleted(fake: FakeCoreApi) -> None:
    assert kube_secrets.read_api_key("ns", "s", BODY) == "sk-value"
    kube_secrets.write_api_key("ns", "s", BODY, "sk-new", LOGGER)
    kube_secrets.delete_secret("ns", "s", BODY, LOGGER)
    assert fake.calls == ["patch", "delete"]


@pytest.mark.parametrize("fake", [secret("uid-other"), secret(None)], indirect=True)
def test_unowned_secret_is_never_touched(fake: FakeCoreApi) -> None:
    with pytest.raises(kopf.PermanentError, match="not owned"):
        kube_secrets.read_api_key("ns", "s", BODY)
    with pytest.raises(kopf.PermanentError, match="not owned"):
        kube_secrets.write_api_key("ns", "s", BODY, "sk-new", LOGGER)
    kube_secrets.delete_secret("ns", "s", BODY, LOGGER)
    assert fake.calls == []


@pytest.mark.parametrize("fake", [None], indirect=True)
def test_missing_secret_is_created_and_delete_is_a_no_op(fake: FakeCoreApi) -> None:
    assert kube_secrets.read_api_key("ns", "s", BODY) is None
    kube_secrets.write_api_key("ns", "s", BODY, "sk-new", LOGGER)
    kube_secrets.delete_secret("ns", "s", BODY, LOGGER)
    assert fake.calls == ["create"]
