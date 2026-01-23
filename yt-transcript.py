#!/usr/bin/env python3
"""
yt-transcript.py

Extract vocab or timestamped lines from a YouTube transcript.

Howto:
  ./yt-transcript.py {jp,es} <video_url_or_id> [options]

Examples:
  ./yt-transcript.py es https://youtu.be/SLgVwNulYhc --mode vocab --top 50
  ./yt-transcript.py jp SLgVwNulYhc --mode sentences

Requirements:
  pip install youtube-transcript-api

Japanese tokenization (recommended "Option 1"):
  pip install "fugashi[unidic-lite]"
"""

from __future__ import annotations

import re
import sys
import argparse
from collections import Counter
from urllib.parse import urlparse, parse_qs

from youtube_transcript_api import YouTubeTranscriptApi


# -------------------------
# Language mapping
# -------------------------
LANG_MAP = {
    "jp": "ja",
    "es": "es",
}

# Small starter stopword lists (you can grow these over time)
STOPWORDS = {
    "es": {
        "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por",
        "un", "para", "con", "no", "una", "su", "al", "lo", "como",
    },
    "en": {"the", "is", "and", "of", "to", "in", "it", "that", "on", "you", "this", "for", "with"},
    "ja": {"の", "に", "は", "を", "た", "が", "で", "て", "です", "ます", "する", "ある", "いる"},
}


# -------------------------
# URL / transcript helpers
# -------------------------
def extract_video_id(url_or_id: str) -> str:
    """Accept full YouTube URLs (including youtu.be) or raw video IDs."""
    if "youtube" in url_or_id or "youtu.be" in url_or_id:
        query = urlparse(url_or_id)

        # youtu.be/<id>
        if query.hostname == "youtu.be":
            return query.path.lstrip("/")

        # youtube.com/watch?v=<id>
        if query.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
            qs = parse_qs(query.query)
            v = qs.get("v", [])
            if v:
                return v[0]

    return url_or_id


def fetch_transcript(video_id: str, lang_code: str):
    """
    Support both youtube-transcript-api v1.x and older v0.x.

    - v1.x: instance method .fetch(video_id, languages=[...]) -> list of snippet objects
    - v0.x: class method .get_transcript(video_id, languages=[...]) -> list of dicts
    """
    # Newer API (v1.x)
    if hasattr(YouTubeTranscriptApi, "fetch"):
        api = YouTubeTranscriptApi()
        return api.fetch(video_id, languages=[lang_code])

    # Older API (v0.x)
    if hasattr(YouTubeTranscriptApi, "get_transcript"):
        return YouTubeTranscriptApi.get_transcript(video_id, languages=[lang_code])

    raise RuntimeError("Unsupported youtube-transcript-api version (missing fetch/get_transcript).")


def snippet_text(entry) -> str:
    """Entry can be a dict (old API) or a snippet object (new API)."""
    if isinstance(entry, dict):
        return (entry.get("text", "") or "")
    return (getattr(entry, "text", "") or "")


def snippet_start(entry) -> float:
    """Entry can be a dict (old API) or a snippet object (new API)."""
    if isinstance(entry, dict):
        return float(entry.get("start", 0.0) or 0.0)
    return float(getattr(entry, "start", 0.0) or 0.0)


# -------------------------
# Tokenization
# -------------------------
def tokenize_japanese(text: str) -> list[str]:
    """
    Japanese tokenization using fugashi (MeCab wrapper).
    Recommended install: pip install "fugashi[unidic-lite]"
    """
    try:
        from fugashi import Tagger
    except ImportError as e:
        raise RuntimeError('Japanese requires fugashi. Install: pip install "fugashi[unidic-lite]"') from e

    tagger = Tagger()
    return [w.surface for w in tagger(text)]


def tokenize_spanish(text: str, raw: bool = False) -> list[str]:
    """
    Lightweight Spanish tokenization (keeps accented letters).
    If raw=False, lowercases everything.
    """
    tokens = re.findall(r"\b[\wáéíóúñü]+\b", text)
    return tokens if raw else [t.lower() for t in tokens]


def count_words(tokens: list[str], lang_code: str, remove_stopwords: bool = True) -> Counter:
    if remove_stopwords:
        sw = STOPWORDS.get(lang_code, set())
        tokens = [t for t in tokens if t not in sw]
    return Counter(tokens)


# -------------------------
# Main
# -------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract vocab or timestamped lines from a YouTube transcript."
    )
    parser.add_argument("lang", choices=["jp", "es"], help="Language code (jp or es).")
    parser.add_argument("video", help="YouTube video URL or ID")
    parser.add_argument(
        "--mode",
        choices=["vocab", "sentences"],
        default="vocab",
        help="Mode: vocab (word counts) or sentences (timestamped lines)",
    )
    parser.add_argument("--top", type=int, default=None, help="Top N words (vocab mode only)")
    parser.add_argument("--no-stopwords", action="store_true", help="Don't remove common words")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="(Spanish only) Do not lowercase tokens",
    )

    args = parser.parse_args()
    lang_code = LANG_MAP[args.lang]
    video_id = extract_video_id(args.video)

    try:
        transcript = fetch_transcript(video_id, lang_code)
    except Exception as e:
        print(f"Error fetching transcript: {e}", file=sys.stderr)
        return 1

    if args.mode == "sentences":
        for entry in transcript:
            start = snippet_start(entry)
            text = snippet_text(entry).replace("\n", " ").strip()
            if text:
                print(f"[{start:.2f}s] {text}")
        return 0

    # vocab mode
    text = " ".join(snippet_text(entry) for entry in transcript).replace("\n", " ")

    if lang_code == "ja":
        tokens = tokenize_japanese(text)
    else:
        tokens = tokenize_spanish(text, raw=args.raw)

    counts = count_words(tokens, lang_code, remove_stopwords=not args.no_stopwords)
    items = counts.most_common(args.top) if args.top else counts.most_common()

    for word, count in items:
        print(f"{word}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

