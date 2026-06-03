"""Extract and compare language-learning vocabulary."""

from __future__ import annotations

import logging
import os
from collections import Counter
from typing import Callable

import regex as re

from .ankiconnect import anki_request
from .config import Config
from .text import extract_first_visible_line, extract_visible_text, normalize_word_key

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


def setup_logging(logfile: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(logfile)), exist_ok=True)
    logging.basicConfig(filename=logfile, level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def build_query_from_decks(decks: list[str]) -> str:
    return " OR ".join(f'deck:"{d}"' for d in decks)


def japanese_filter(token) -> bool:
    text = (token.text or "").strip()
    lemma = (token.lemma_ or "").strip()
    if not text or not JAPANESE_CHAR_RE.fullmatch(text):
        return False
    if lemma in JAPANESE_GRAMMAR_EXCLUDE or text in JAPANESE_PARTICLES:
        return False
    if getattr(token, "pos_", None) not in JAPANESE_ALLOWED_POS:
        return False
    if getattr(token, "is_stop", False) or getattr(token, "like_url", False) or getattr(token, "like_email", False):
        return False
    if any(c in text for c in "<>=/\\:&%"):
        return False
    return text not in {"ruby", "rt", "div", "br", "nbsp", "href", "strong", "a"}


def spanish_filter(token) -> bool:
    return bool(getattr(token, "is_alpha", False)) and not bool(getattr(token, "is_stop", False))


def spanish_format(token) -> str:
    return (token.lemma_ or token.text or "").lower().strip()


def japanese_format(token) -> str:
    lemma = (token.lemma_ or "").strip()
    surface = (token.text or "").strip()
    if lemma and surface and lemma != surface:
        return f"{lemma} ({surface})"
    return lemma or surface


LANGUAGE_PROFILES = {
    "spanish": {"token_filter": spanish_filter, "output_format": spanish_format},
    "japanese": {"token_filter": japanese_filter, "output_format": japanese_format},
}


def load_spacy_model(model_name: str):
    try:
        import spacy  # type: ignore
    except Exception as e:
        raise RuntimeError("Failed to import spaCy. Use a Python version supported by spaCy.") from e
    try:
        return spacy.load(model_name)
    except Exception as e:
        raise RuntimeError(f"Failed to load spaCy model '{model_name}'. Try: python -m spacy download {model_name}") from e


def get_notes(query: str, config: Config, request: Callable = anki_request) -> list[dict]:
    note_ids = request("findNotes", url=config.anki_connect_url, query=query) or []
    if not note_ids:
        return []
    return request("notesInfo", url=config.anki_connect_url, notes=note_ids) or []


def extract_counts(
    notes: list[dict],
    field_name: str,
    nlp,
    token_filter: Callable,
    output_format: Callable,
    use_full_field: bool,
) -> Counter:
    counter: Counter = Counter()
    for note in notes:
        fields = note.get("fields", {}) or {}
        raw_val = (fields.get(field_name, {}) or {}).get("value", "") or ""
        text = extract_visible_text(raw_val) if use_full_field else extract_first_visible_line(raw_val)
        if not text:
            continue
        for token in nlp(text):
            if token_filter(token):
                key = output_format(token)
                if key:
                    counter[key] += 1
    return counter


def write_counts(counter: Counter, out_path: str, min_freq: int) -> int:
    items = [(w, c) for (w, c) in counter.items() if c >= min_freq]
    items.sort(key=lambda x: (-x[1], x[0]))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for word, freq in items:
            f.write(f"{word} {freq}\n")
    return len(items)


def read_word_file(path: str) -> set[str]:
    words: set[str] = set()
    with open(os.path.expanduser(path), "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            word = stripped.rsplit(" ", 1)[0]
            words.add(normalize_word_key(word))
    return words


def compare_word_files(source_path: str, known_path: str) -> list[str]:
    known = read_word_file(known_path)
    new_words: list[str] = []
    with open(os.path.expanduser(source_path), "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            word = stripped.rsplit(" ", 1)[0]
            if normalize_word_key(word) not in known:
                new_words.append(stripped)
    return new_words


def extract_words(
    config: Config,
    lang: str,
    query: str | None = None,
    decks: list[str] | None = None,
    field: str | None = None,
    min_freq: int = 2,
    outdir: str | None = None,
    out: str | None = None,
    full_field: bool = False,
    spacy_model: str | None = None,
    request: Callable = anki_request,
) -> dict[str, object]:
    language_bucket = config.language_name(lang)
    profile = LANGUAGE_PROFILES[language_bucket]
    search_query = query or build_query_from_decks(decks or config.decks_for(lang))
    out_dir = os.path.expanduser(outdir) if outdir else os.path.join(config.word_output_root, language_bucket)
    out_path = os.path.expanduser(out) if out else os.path.join(out_dir, f"words_{lang}.txt")
    model_name = spacy_model or str(config.language(lang).get("word_model"))
    nlp = load_spacy_model(model_name)
    notes = get_notes(search_query, config, request=request)
    if notes:
        fields0 = (notes[0].get("fields", {}) or {})
        field_name = field or config.field_for(lang)
        if field_name not in fields0:
            raise RuntimeError(f"Field '{field_name}' not found. Available fields: {list(fields0.keys())}")
    else:
        field_name = field or config.field_for(lang)
    counter = extract_counts(notes, field_name, nlp, profile["token_filter"], profile["output_format"], full_field)
    written = write_counts(counter, out_path, min_freq)
    return {"query": search_query, "notes": len(notes), "unique": len(counter), "written": written, "out": out_path}

