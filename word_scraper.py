#!/usr/bin/env python3
"""
word_extractor.py

Extract frequent words/lemmas from Anki notes via AnkiConnect.

Howto:
  ./word_extractor.py jp [--deck "日本語"] [--field Back] [--min-freq 2] [--outdir DIR] [--out FILE]
  ./word_extractor.py es [--deck "Español"] [--field Back] [--min-freq 2] [--outdir DIR] [--out FILE]

By default, this:
  - chooses decks based on the lang code (jp/es) using deck_to_language mappings
  - pulls notes from Anki via AnkiConnect (http://localhost:8765)
  - reads a single field (default: Back)
  - extracts the first visible line (HTML stripped) from that field
  - tokenizes with spaCy and counts words
  - writes "token count" lines sorted by descending count

Notes:
  - spaCy currently may not work on Python 3.14 in your environment.
    If spaCy import/load fails, create a Python 3.12 venv for this script.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from html import unescape
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import requests
import regex as re


# -------------------------
# Shared “language plumbing”
# -------------------------
# Match the idea used in audio_extractor.py: CLI lang code -> language bucket. :contentReference[oaicite:2]{index=2}
LANG_MAP: Dict[str, str] = {
    "jp": "japanese",
    "es": "spanish",
}

# Map deck name -> language bucket (same pattern as audio_extractor.py). :contentReference[oaicite:3]{index=3}
DECK_TO_LANGUAGE: Dict[str, str] = {
    "日本語": "japanese",
    "Español": "spanish",
    # Add more deck mappings here
}

# Default output root (mirrors the “one folder per language” idea)
DEFAULT_OUTPUT_ROOT = os.path.expanduser("~/Documents/anki-words")


# -------------------------
# Logging
# -------------------------
def setup_logging(logfile: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(logfile)), exist_ok=True)
    logging.basicConfig(
        filename=logfile,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


# -------------------------
# HTML cleanup helpers
# -------------------------
def extract_first_visible_line(text: str) -> str:
    """Remove common HTML and return only the first visible line."""
    text = unescape(text or "")
    text = re.sub(r"</?(br|div|p)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.strip()
    return text.splitlines()[0] if text else ""


def extract_visible_text(text: str) -> str:
    """Remove common HTML and return all visible text as a single string."""
    text = unescape(text or "")
    text = re.sub(r"</?(br|div|p)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    # Normalize whitespace a bit
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


# -------------------------
# AnkiConnect helper
# -------------------------
def anki_request(action: str, **params):
    """
    Make an AnkiConnect request and return 'result'.
    Raises a helpful error if the HTTP call fails or AnkiConnect returns an error.
    """
    resp = requests.post(
        "http://localhost:8765",
        json={"action": action, "version": 6, "params": params},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("error") is not None:
        raise RuntimeError(f"AnkiConnect error for {action}: {data['error']}")
    return data["result"]


def get_notes(query: str) -> List[dict]:
    """
    Query Anki for notes and return notesInfo payload.
    """
    note_ids = anki_request("findNotes", query=query) or []
    if not note_ids:
        return []
    return anki_request("notesInfo", notes=note_ids) or []


# -------------------------
# Language-specific token rules (spaCy-based)
# -------------------------
JAPANESE_CHAR_RE = re.compile(r"[\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Han}ー]+")

JAPANESE_PARTICLES = {
    "は", "が", "を", "に", "へ", "で", "と", "や", "も", "から", "まで", "より", "ば", "なら",
    "の", "ね", "よ", "ぞ", "ぜ", "さ", "わ", "か", "な", "って", "とき", "ってば", "けど", "けれど",
    "しかし", "でも", "ながら", "ほど", "し", "もの", "こと", "ところ", "よう", "らしい", "られる",
}

JAPANESE_GRAMMAR_EXCLUDE = {
    "て", "た", "ます", "れる", "てる", "ぬ", "ん", "しまう", "いる", "ない", "なる", "ある", "だ", "です",
}

JAPANESE_ALLOWED_POS = {"NOUN", "PROPN", "VERB", "ADJ"}


def japanese_filter(token) -> bool:
    """
    Filter Japanese tokens to keep “content-ish” words and avoid particles/grammar glue.
    Assumes a Japanese spaCy model that provides lemma_ and pos_ reasonably.
    """
    text = (token.text or "").strip()
    lemma = (token.lemma_ or "").strip()

    if not text:
        return False

    # Must look like Japanese script (hiragana/katakana/kanji/ー)
    if not JAPANESE_CHAR_RE.fullmatch(text):
        return False

    # Drop obvious grammar / particles
    if lemma in JAPANESE_GRAMMAR_EXCLUDE or text in JAPANESE_PARTICLES:
        return False

    # Keep only selected parts of speech
    if getattr(token, "pos_", None) not in JAPANESE_ALLOWED_POS:
        return False

    # Drop URLs/emails/stopwords when model flags them
    if getattr(token, "is_stop", False) or getattr(token, "like_url", False) or getattr(token, "like_email", False):
        return False

    # Defensive: drop tokens that look like HTML fragments or garbage
    if any(c in text for c in "<>=/\\:&%"):
        return False
    if text in {"ruby", "rt", "div", "br", "nbsp", "href", "strong", "a"}:
        return False

    return True


def spanish_filter(token) -> bool:
    """
    Keep alpha tokens that are not stopwords. (spaCy handles accent marks fine here.)
    """
    return bool(getattr(token, "is_alpha", False)) and not bool(getattr(token, "is_stop", False))


def spanish_format(token) -> str:
    return (token.lemma_ or token.text or "").lower().strip()


def japanese_format(token) -> str:
    # Keep both lemma and surface form (useful when lemma normalization is aggressive)
    lemma = (token.lemma_ or "").strip()
    surface = (token.text or "").strip()
    if not lemma and not surface:
        return ""
    if lemma and surface and lemma != surface:
        return f"{lemma} ({surface})"
    return lemma or surface


LANGUAGE_PROFILES = {
    "spanish": {
        "spacy_model": "es_core_news_sm",
        "token_filter": spanish_filter,
        "output_format": spanish_format,
    },
    "japanese": {
        "spacy_model": "ja_core_news_lg",
        "token_filter": japanese_filter,
        "output_format": japanese_format,
    },
}


def load_spacy_model(model_name: str):
    """
    Import spaCy lazily and load a model.
    This lets us show clearer errors when spaCy is missing/broken in the environment.
    """
    try:
        import spacy  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "Failed to import spaCy. If you're on Python 3.14, spaCy may not be compatible yet.\n"
            "Use a Python 3.12 venv for this script."
        ) from e

    try:
        return spacy.load(model_name)
    except Exception as e:
        raise RuntimeError(
            f"Failed to load spaCy model '{model_name}'.\n"
            f"Try: python -m spacy download {model_name}"
        ) from e


# -------------------------
# Core extraction
# -------------------------
def extract_counts(
    notes: List[dict],
    field_name: str,
    nlp,
    token_filter: Callable,
    output_format: Callable,
    use_full_field: bool,
) -> Counter:
    """
    For each note, take the specified field, strip HTML, tokenize, and count.
    """
    counter: Counter = Counter()

    for note in notes:
        fields = note.get("fields", {}) or {}
        raw_val = (fields.get(field_name, {}) or {}).get("value", "") or ""

        text = extract_visible_text(raw_val) if use_full_field else extract_first_visible_line(raw_val)
        if not text:
            continue

        doc = nlp(text)
        for token in doc:
            if token_filter(token):
                key = output_format(token)
                if key:
                    counter[key] += 1

    return counter


def write_counts(counter: Counter, out_path: str, min_freq: int) -> int:
    """
    Write "token count" lines sorted by descending count.
    Returns the number of written entries.
    """
    items = [(w, c) for (w, c) in counter.items() if c >= min_freq]
    items.sort(key=lambda x: (-x[1], x[0]))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for word, freq in items:
            f.write(f"{word} {freq}\n")

    return len(items)


def build_query_from_decks(decks: List[str]) -> str:
    """
    Build an Anki query that OR's multiple deck:"..." clauses.
    """
    # deck:"日本語" OR deck:"日本語::subdeck" is possible but we keep it simple.
    parts = [f'deck:"{d}"' for d in decks]
    return " OR ".join(parts)


# -------------------------
# Main CLI
# -------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract frequent words from Anki notes (CLI resembles other toolkit scripts)."
    )

    # Match "positional lang” style (jp/es) 
    parser.add_argument("lang", choices=sorted(LANG_MAP.keys()), help="Language code (jp or es).")

    # Let you override deck selection, but keep sane defaults:
    # - if --query is provided, we use that exactly
    # - else if --deck is provided (repeatable), we use those decks
    # - else we infer decks from DECK_TO_LANGUAGE mapping
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--query",
        help='Full Anki search query (e.g. \'deck:"Español" tag:foo\'). Overrides --deck.',
    )
    group.add_argument(
        "--deck",
        action="append",
        help='Deck name (repeatable). Example: --deck "日本語" --deck "日本語::Subdeck"',
    )

    # Similar “bashy” knobs
    parser.add_argument("--field", default="Back", help="Which note field to read (default: Back).")
    parser.add_argument("--min-freq", type=int, default=2, help="Minimum frequency to include (default: 2).")
    parser.add_argument("--outdir", help="Output directory (default: ~/Documents/anki-words/<language>).")
    parser.add_argument("--out", help="Output file path (default: <outdir>/words_<lang>.txt).")
    parser.add_argument(
        "--full-field",
        action="store_true",
        help="Use the full field text (HTML stripped) instead of only the first visible line.",
    )
    parser.add_argument(
        "--spacy-model",
        help="Override the spaCy model name (advanced).",
    )
    parser.add_argument(
        "--logfile",
        default=os.path.expanduser("~/Documents/anki-words/extract_words.log"),
        help="Log file path.",
    )

    args = parser.parse_args()

    setup_logging(args.logfile)

    language_bucket = LANG_MAP[args.lang]
    profile = LANGUAGE_PROFILES.get(language_bucket)
    if not profile:
        print(f"❌ Unsupported language bucket: {language_bucket}", file=sys.stderr)
        return 1

    # Resolve query / decks
    if args.query:
        query = args.query
    else:
        if args.deck:
            decks = args.deck
        else:
            decks = [d for d, lang in DECK_TO_LANGUAGE.items() if lang == language_bucket]
        if not decks:
            print(f"❌ No decks mapped for language: {language_bucket}", file=sys.stderr)
            return 1
        query = build_query_from_decks(decks)

    # Output paths
    out_dir = os.path.expanduser(args.outdir) if args.outdir else os.path.join(DEFAULT_OUTPUT_ROOT, language_bucket)
    default_outfile = os.path.join(out_dir, f"words_{args.lang}.txt")
    out_path = os.path.expanduser(args.out) if args.out else default_outfile

    logging.info("lang=%s bucket=%s query=%s field=%s", args.lang, language_bucket, query, args.field)
    print(f"🔎 Query: {query}")
    print(f"🧾 Field: {args.field}")

    # Load spaCy model
    model_name = args.spacy_model or profile["spacy_model"]
    try:
        nlp = load_spacy_model(model_name)
    except Exception as e:
        print(f"❌ {e}", file=sys.stderr)
        logging.exception("spaCy load failed")
        return 1

    # Fetch notes
    try:
        notes = get_notes(query)
    except Exception as e:
        print(f"❌ Failed to query AnkiConnect: {e}", file=sys.stderr)
        logging.exception("AnkiConnect query failed")
        return 1

    print(f"✅ Found {len(notes)} notes.")
    if not notes:
        print("⚠️  No notes found. Check your query/deck names.")
        return 0

    # Validate the field exists on at least one note
    fields0 = (notes[0].get("fields", {}) or {})
    if args.field not in fields0:
        available = list(fields0.keys())
        print(f"❌ Field '{args.field}' not found on sample note.", file=sys.stderr)
        print(f"   Available fields: {available}", file=sys.stderr)
        return 1

    # Extract + write
    counter = extract_counts(
        notes=notes,
        field_name=args.field,
        nlp=nlp,
        token_filter=profile["token_filter"],
        output_format=profile["output_format"],
        use_full_field=args.full_field,
    )

    print(f"🧠 Extracted {len(counter)} unique entries (before min-freq filter).")
    written = write_counts(counter, out_path, args.min_freq)

    print(f"📄 Wrote {written} entries to: {out_path}")
    logging.info("wrote=%s out=%s", written, out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

