"""Text cleanup helpers shared by tools."""

from __future__ import annotations

from html import unescape

import regex as re


def extract_first_visible_line(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"</?(br|div|p)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.strip()
    return text.splitlines()[0] if text else ""


def extract_visible_text(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"</?(br|div|p)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def normalize_word_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())

