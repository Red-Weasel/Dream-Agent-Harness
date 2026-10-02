"""Connector secrets (DREAM-160): the system keyring (the optional `keyring` package), else an environment variable.

A secret is never written to a JSON file and never sent back to the page: the page only learns where it is set."""
from __future__ import annotations

import os

SERVICE = "dream-sleepwalk"


def _keyring():
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def env_name(connector: str, field: str) -> str:
    return f"DREAM_SLEEPWALK_{connector}_{field}".upper().replace("-", "_")


def _stored(connector: str, field: str) -> str | None:
    ring = _keyring()
    try:
        return ring.get_password(SERVICE, f"{connector}.{field}") if ring else None
    except Exception:              # a locked or missing keyring backend: fall back to the environment
        return None


def get(connector: str, field: str) -> str | None:
    return _stored(connector, field) or os.environ.get(env_name(connector, field)) or None


def where(connector: str, field: str) -> str | None:
    if _stored(connector, field):
        return "keyring"
    return "environment" if os.environ.get(env_name(connector, field)) else None


def put(connector: str, field: str, value: str) -> None:
    ring = _keyring()
    if ring is None:
        raise ValueError(f"Saving a secret needs the keyring package (pip install keyring); "
                         f"or set {env_name(connector, field)} in Dream's environment")
    try:
        ring.set_password(SERVICE, f"{connector}.{field}", value)
    except Exception as exc:
        raise ValueError(f"The system keyring did not store the secret ({type(exc).__name__}); "
                         f"or set {env_name(connector, field)} in Dream's environment") from None
