"""The Secret that hands a service its LiteLLM key (field api_key)."""

from __future__ import annotations

import base64

import kopf
import kubernetes

FIELD = "api_key"


def _api() -> kubernetes.client.CoreV1Api:
    return kubernetes.client.CoreV1Api()


def _read(namespace: str, name: str) -> kubernetes.client.V1Secret | None:
    """Return the Secret, or None if there is none."""
    try:
        return _api().read_namespaced_secret(name=name, namespace=namespace)
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status == 404:
            return None
        msg = f"reading secret {namespace}/{name}: HTTP {exc.status}"
        raise kopf.TemporaryError(msg, delay=15) from None


def _owned_by(secret: kubernetes.client.V1Secret, body: kopf.Body) -> bool:
    """Whether one of the Secret's ownerReferences is this LiteLLMKey."""
    refs = secret.metadata.owner_references or []
    return any(ref.uid == body["metadata"]["uid"] for ref in refs)


def _refuse_unowned(namespace: str, name: str) -> None:
    msg = f"secret {namespace}/{name} exists and is not owned by this LiteLLMKey"
    raise kopf.PermanentError(msg)


def read_api_key(namespace: str, name: str, body: kopf.Body) -> str | None:
    """Return the key stored in our Secret, or None if there is none.

    A Secret of that name that this LiteLLMKey does not own is refused.
    """
    secret = _read(namespace, name)
    if secret is None:
        return None
    if not _owned_by(secret, body):
        _refuse_unowned(namespace, name)
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
        existing = _read(namespace, name)
        if existing is not None and not _owned_by(existing, body):
            _refuse_unowned(namespace, name)
        _api().patch_namespaced_secret(
            name=name, namespace=namespace, body={"stringData": {FIELD: key}}
        )
        logger.info("Updated secret %s/%s", namespace, name)


def delete_secret(
    namespace: str, name: str, body: kopf.Body, logger: kopf.Logger
) -> None:
    """Delete the Secret if this LiteLLMKey owns it; a missing Secret is fine."""
    secret = _read(namespace, name)
    if secret is None:
        return
    if not _owned_by(secret, body):
        logger.warning("Secret %s/%s is not ours; leaving it", namespace, name)
        return
    try:
        _api().delete_namespaced_secret(name=name, namespace=namespace)
        logger.info("Deleted secret %s/%s", namespace, name)
    except kubernetes.client.exceptions.ApiException as exc:
        if exc.status != 404:
            msg = f"deleting secret {namespace}/{name}: HTTP {exc.status}"
            raise kopf.TemporaryError(msg, delay=15) from None
