"""Keep a LiteLLMKey's key in LiteLLM and its Secret in step with its spec."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import kopf
import kube_secrets
from litellm_api import KeySettings

if TYPE_CHECKING:
    from collections.abc import Mapping

    from litellm_api import LiteLLMClient


def token_for(key: str) -> str:
    """LiteLLM's id for a key: the sha256 of its value."""
    return hashlib.sha256(key.encode()).hexdigest()


def owner_of(namespace: str, name: str) -> str:
    """Return the owner marker written into each key's metadata."""
    return f"{namespace}/{name}"


def reconcile(
    spec: Mapping[str, Any],
    namespace: str,
    name: str,
    body: Any,
    client: LiteLLMClient,
    logger: Any,
) -> None:
    """Make LiteLLM and the Secret match the spec."""
    alias: str = spec["alias"]
    secret_name: str = spec["secretName"]
    owner = owner_of(namespace, name)
    want = KeySettings.from_spec(spec)

    existing = client.find_key(alias)
    if existing is not None and not existing.managed:
        msg = (
            f"alias {alias!r} is already used by a key "
            "not managed by litellm-provisioner"
        )
        raise kopf.PermanentError(msg)
    if existing is not None and existing.owner != owner:
        msg = f"alias {alias!r} belongs to {existing.owner}"
        raise kopf.PermanentError(msg)

    stored = kube_secrets.read_api_key(namespace, secret_name)
    if existing is not None and (stored is None or token_for(stored) != existing.token):
        logger.warning("Secret for %r is missing or stale; replacing the key", alias)
        client.delete(existing.token)
        existing = None

    if existing is None:
        value, _token = client.generate(alias, want, owner)
        kube_secrets.write_api_key(namespace, secret_name, body, value, logger)
        logger.info("Created LiteLLM key %r", alias)
        return

    if not want.matches(existing):
        client.update(existing.token, want)
        logger.info("Updated LiteLLM key %r", alias)


def revoke(
    alias: str,
    secret_name: str,
    namespace: str,
    name: str,
    client: LiteLLMClient,
    logger: Any,
) -> None:
    """Revoke our key for alias (if it is ours), then delete the Secret."""
    existing = client.find_key(alias)
    if existing is None:
        logger.info("LiteLLM key %r already gone", alias)
    elif existing.managed and existing.owner == owner_of(namespace, name):
        client.delete(existing.token)
        logger.info("Revoked LiteLLM key %r", alias)
    else:
        logger.warning("Key %r is not ours; leaving it in LiteLLM", alias)
    kube_secrets.delete_secret(namespace, secret_name, logger)
