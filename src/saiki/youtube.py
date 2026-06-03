"""YouTube transcript mining and Anki-ready exports."""

from __future__ import annotations

import csv
import os
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import YouTubeTranscriptApi

from .config import Config
from .text import normalize_word_key
from .words import read_word_file

STOPWORDS = {
    "es": {
        "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por",
        "un", "para", "con", "no", "una", "su", "al", "lo", "como",
    },
    "en": {"the", "is", "and", "of", "to", "in", "it", "that", "on", "you", "this", "for", "with"},
    "ja": {"の", "に", "は", "を", "た", "が", "で", "て", "です", "ます", "する", "ある", "いる"},
}


@dataclass(frozen=True)
class TranscriptLine:
    """One cleaned transcript line with its start timestamp in seconds."""

    start: float
    text: str


def extract_video_id(url_or_id: str) -> str:
    """Extract a YouTube video id from a URL, or pass an id through unchanged."""
    if "youtube" in url_or_id or "youtu.be" in url_or_id:
        query = urlparse(url_or_id)
        if query.hostname == "youtu.be":
            return query.path.lstrip("/")
        if query.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
            values = parse_qs(query.query).get("v", [])
            if values:
                return values[0]
    return url_or_id


def video_url(video_or_id: str) -> str:
    """Return a canonical watch URL for a YouTube id or URL."""
    video_id = extract_video_id(video_or_id)
    return f"https://www.youtube.com/watch?v={video_id}"


def fetch_transcript(video_id: str, lang_code: str):
    """Fetch a transcript while supporting old and new library APIs."""
    if hasattr(YouTubeTranscriptApi, "fetch"):
        api = YouTubeTranscriptApi()
        return api.fetch(video_id, languages=[lang_code])
    if hasattr(YouTubeTranscriptApi, "get_transcript"):
        return YouTubeTranscriptApi.get_transcript(video_id, languages=[lang_code])
    raise RuntimeError("Unsupported youtube-transcript-api version.")


def snippet_text(entry) -> str:
    """Read transcript text from either dict-like or object-like entries."""
    if isinstance(entry, dict):
        return entry.get("text", "") or ""
    return getattr(entry, "text", "") or ""


def snippet_start(entry) -> float:
    """Read transcript start time from either dict-like or object-like entries."""
    if isinstance(entry, dict):
        return float(entry.get("start", 0.0) or 0.0)
    return float(getattr(entry, "start", 0.0) or 0.0)


def transcript_lines(entries) -> list[TranscriptLine]:
    """Normalize raw transcript entries into non-empty transcript lines."""
    lines: list[TranscriptLine] = []
    for entry in entries:
        text = snippet_text(entry).replace("\n", " ").strip()
        if text:
            lines.append(TranscriptLine(snippet_start(entry), text))
    return lines


def tokenize_japanese(text: str) -> list[str]:
    """Tokenize Japanese text with fugashi."""
    try:
        from fugashi import Tagger
    except ImportError as e:
        raise RuntimeError('Japanese requires fugashi. Install: pip install "fugashi[unidic-lite]"') from e
    tagger = Tagger()
    return [w.surface for w in tagger(text)]


def tokenize_spanish(text: str, raw: bool = False) -> list[str]:
    """Tokenize Spanish-ish text with a lightweight word regex."""
    tokens = re.findall(r"\b[\wáéíóúñü]+\b", text)
    return tokens if raw else [t.lower() for t in tokens]


def tokenize_text(text: str, lang_code: str, raw: bool = False) -> list[str]:
    """Dispatch transcript tokenization by language code."""
    return tokenize_japanese(text) if lang_code == "ja" else tokenize_spanish(text, raw=raw)


def count_words(tokens: list[str], lang_code: str, remove_stopwords: bool = True) -> Counter:
    """Count tokens, optionally excluding the built-in stopword list."""
    if remove_stopwords:
        stopwords = STOPWORDS.get(lang_code, set())
        tokens = [t for t in tokens if t not in stopwords]
    return Counter(tokens)


def sentence_vocab(sentence: str, lang_code: str, known_words: set[str] | None = None) -> list[str]:
    """Guess distinct useful vocabulary for one transcript sentence."""
    words: list[str] = []
    seen: set[str] = set()
    for token in tokenize_text(sentence, lang_code):
        key = normalize_word_key(token)
        if key in seen or key in STOPWORDS.get(lang_code, set()):
            continue
        if known_words is not None and key in known_words:
            continue
        seen.add(key)
        words.append(token)
    return words


def write_sentence_export(
    lines: list[TranscriptLine],
    out_path: str,
    video: str,
    lang_code: str,
    delimiter: str = "\t",
    known_words_path: str | None = None,
    only_new: bool = False,
) -> int:
    """Write transcript lines as Anki-importable sentence rows."""
    known = read_word_file(known_words_path) if known_words_path else None
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    written = 0
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=delimiter)
        writer.writerow(["sentence", "timestamp", "video_url", "vocab_guess"])
        for line in lines:
            vocab = sentence_vocab(line.text, lang_code, known)
            if only_new and not vocab:
                continue
            writer.writerow([line.text, f"{line.start:.2f}", video_url(video), ", ".join(vocab)])
            written += 1
    return written


def run_youtube(
    config: Config,
    lang: str,
    video: str,
    mode: str = "vocab",
    top: int | None = None,
    no_stopwords: bool = False,
    raw: bool = False,
    out: str | None = None,
    fmt: str = "tsv",
    known_words: str | None = None,
    only_new: bool = False,
) -> dict[str, object]:
    """Run transcript mining in either vocabulary or sentence-export mode."""
    lang_code = config.transcript_code(lang)
    video_id = extract_video_id(video)
    entries = fetch_transcript(video_id, lang_code)
    lines = transcript_lines(entries)

    if mode == "sentences":
        if out:
            delimiter = "," if fmt == "csv" else "\t"
            written = write_sentence_export(lines, out, video_id, lang_code, delimiter, known_words, only_new)
            return {"mode": mode, "lines": len(lines), "written": written, "out": out}
        return {"mode": mode, "lines": lines}

    text = " ".join(line.text for line in lines)
    tokens = tokenize_text(text, lang_code, raw=raw)
    counts = count_words(tokens, lang_code, remove_stopwords=not no_stopwords)
    items = counts.most_common(top) if top else counts.most_common()
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            for word, count in items:
                fh.write(f"{word} {count}\n")
    return {"mode": mode, "items": items, "out": out}
