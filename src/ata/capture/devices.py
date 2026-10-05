"""Listagem de dispositivos para ``ata doctor`` e o dashboard. Nunca levanta exceção.

``list_devices(config) -> dict`` no formato de :func:`ata.capture.base.device_listing`::

    {"backend": "linux-pipewire", "platform": "linux", "available": bool,
     "far": {"id", "description", "source"} | None, "mic": {...} | None, "problems": [str, ...]}
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from .base import CaptureError, backend_for_platform, device_listing

if TYPE_CHECKING:
    from ..config import Config


def list_devices(config: Config | None = None, platform: str | None = None) -> dict[str, Any]:
    try:
        backend = backend_for_platform(platform, config)
    except CaptureError as exc:
        return device_listing("none", available=False, problems=[str(exc)],
                              extra={"platform": platform or sys.platform})
    try:
        return backend.devices()
    except Exception as exc:  # noqa: BLE001 - doctor precisa de resposta, não de traceback
        return device_listing(getattr(backend, "name", "?"), available=False,
                              problems=[f"falha ao listar dispositivos: {type(exc).__name__}"])
