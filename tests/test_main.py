"""The operator wires kopf the same way as the other provisioners."""

from __future__ import annotations

import kopf
import main


def test_startup_uses_own_annotation_prefix() -> None:
    settings = kopf.OperatorSettings()
    main.configure(settings=settings)  # ty: ignore[missing-argument]
    assert settings.persistence.finalizer == "litellm.drewsonne.github.io/finalizer"
    assert settings.peering.standalone is True


def test_liveness_probe_reports_alive() -> None:
    assert main.liveness_probe() is True  # ty: ignore[missing-argument]
