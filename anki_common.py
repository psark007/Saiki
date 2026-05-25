#!/usr/bin/env python3
"""Shared configuration and AnkiConnect helpers for the toolkit scripts."""

from __future__ import annotations

import os
from typing import Dict

import requests

ANKI_CONNECT_URL = "http://localhost:8765"

LANG_MAP: Dict[str, str] = {
    "jp": "japanese",
    "es": "spanish",
}

TRANSCRIPT_LANG_MAP: Dict[str, str] = {
    "jp": "ja",
    "es": "es",
}

DECK_TO_LANGUAGE: Dict[str, str] = {
    "日本語": "japanese",
    "Español": "spanish",
}

DEFAULT_ANKI_MEDIA_DIR = os.path.expanduser(
    "~/.var/app/net.ankiweb.Anki/data/Anki2/User 1/collection.media"
)

DEFAULT_AUDIO_OUTPUT_ROOT = os.path.expanduser("~/Languages/Anki/anki-audio")
DEFAULT_WORD_OUTPUT_ROOT = os.path.expanduser("~/Languages/Anki/anki-words")


def anki_request(action: str, **params):
    """Make an AnkiConnect request and return the result payload."""
    resp = requests.post(
        ANKI_CONNECT_URL,
        json={"action": action, "version": 6, "params": params},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("error") is not None:
        raise RuntimeError(f"AnkiConnect error for {action}: {data['error']}")
    return data["result"]
