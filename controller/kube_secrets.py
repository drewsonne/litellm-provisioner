"""The Secret that hands a service its LiteLLM key (field api_key)."""

from __future__ import annotations

import base64

import kopf
import kubernetes

FIELD = "api_key"


def _api() -> kubernetes.client.CoreV1Api:
    return kubernetes.client.CoreV1Api()


def read_api_key(namespace: str, name: str) -> str | None:
    """Return the key stored in the Secret, or None if there is none."""
    try:
        secret = _api().read_namespaced_secret(name=name, namespace=namespace)
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status == 404:
            return None
        msg = f"reading secret {namespace}/{name}: HTTP {exc.status}"
        raise kopf.TemporaryError(msg, delay=15) from None
    raw = (secret.data or {}).get(FIELD)
    return base64.b64decode(raw).decode() if raw else None


def write_api_key(
    namespace: str, name: str, body: kopf.Body, key: str, logger: kopf.Logger
) -> None:
    """Create or replace the Secret; it is owned by the LiteLLMKey."""
    secret = kubernetes.client.V1Secret(
        metadata=kubernetes.client.V1ObjectMeta(name=name),
        string_data={FIELD: key},
    )
    kopf.adopt(secret, owner=body)
    try:
        _api().create_namespaced_secret(namespace=namespace, body=secret)
        logger.info("Created secret %s/%s", namespace, name)
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status != 409:
            msg = f"creating secret {namespace}/{name}: HTTP {exc.status}"
            raise kopf.TemporaryError(msg, delay=15) from None
        _api().patch_namespaced_secret(
            name=name, namespace=namespace, body={"stringData": {FIELD: key}}
        )
        logger.info("Updated secret %s/%s", namespace, name)


def delete_secret(namespace: str, name: str, logger: kopf.Logger) -> None:
    """Delete the Secret; a missing Secret is fine."""
    try:
        _api().delete_namespaced_secret(name=name, namespace=namespace)
        logger.info("Deleted secret %s/%s", namespace, name)
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status != 404:
            msg = f"deleting secret {namespace}/{name}: HTTP {exc.status}"
            raise kopf.TemporaryError(msg, delay=15) from None
