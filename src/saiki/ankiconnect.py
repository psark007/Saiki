"""Small AnkiConnect client."""

from __future__ import annotations

import requests


def anki_request(action: str, url: str = "http://localhost:8765", **params):
    resp = requests.post(
        url,
        json={"action": action, "version": 6, "params": params},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("error") is not None:
        raise RuntimeError(f"AnkiConnect error for {action}: {data['error']}")
    return data["result"]

