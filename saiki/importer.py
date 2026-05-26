"""Generate TTS audio and add sentence notes to Anki."""

from __future__ import annotations

import os
import csv
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Callable

from .ankiconnect import anki_request
from .config import Config


@dataclass(frozen=True)
class ImportResult:
    processed: int
    added: int
    failed: int


def parse_tags(value: str | None) -> list[str]:
    tags = ["text-to-speech"]
    if value:
        tags.extend(tag.strip() for tag in value.split(",") if tag.strip())
    else:
        tags.append("AI-generated")
    return tags


def require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Required command not found: {name}")


def generate_tts(sentence: str, raw_output: str, lang_code: str, tld: str) -> None:
    subprocess.run(["gtts-cli", sentence, "--lang", lang_code, "--tld", tld, "--output", raw_output], check=True)


def speed_audio(raw_output: str, output_path: str, tempo: float) -> None:
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", raw_output, "-filter:a", f"atempo={tempo}", "-y", output_path],
        stdin=subprocess.DEVNULL,
        check=True,
    )


def read_sentences(path: str) -> list[str]:
    expanded = os.path.expanduser(path)
    if expanded.lower().endswith((".tsv", ".csv")):
        delimiter = "\t" if expanded.lower().endswith(".tsv") else ","
        with open(expanded, "r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh, delimiter=delimiter)
            if reader.fieldnames and "sentence" in reader.fieldnames:
                return [row["sentence"].strip() for row in reader if row.get("sentence", "").strip()]
        raise RuntimeError("TSV/CSV sentence imports must include a 'sentence' header.")

    with open(expanded, "r", encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip()]


def import_sentences(
    config: Config,
    lang: str,
    sentence_file: str | None = None,
    tags_value: str | None = None,
    request: Callable = anki_request,
) -> ImportResult:
    require_command("gtts-cli")
    require_command("ffmpeg")

    language = config.language(lang)
    decks = list(language.get("decks", []))
    if not decks:
        raise RuntimeError(f"No deck configured for language: {lang}")
    deck = decks[0]

    source = os.path.expanduser(sentence_file) if sentence_file else config.sentence_file_for(lang)
    sentences = read_sentences(source)
    tags = parse_tags(tags_value)
    front_field = config.fields.get("front", "Front")
    back_field = config.fields.get("back", "Back")
    added = 0
    failed = 0

    with tempfile.TemporaryDirectory() as temp_dir:
        for sentence in sentences:
            basename = f"tts_{time.strftime('%Y%m%d_%H%M%S')}_{lang}_{os.getpid()}_{added + failed}"
            raw_output = os.path.join(temp_dir, f"{basename}_original.mp3")
            output_path = os.path.join(temp_dir, f"{basename}.mp3")
            try:
                generate_tts(sentence, raw_output, str(language["tts_code"]), str(language["tts_tld"]))
                speed_audio(raw_output, output_path, float(language["tts_tempo"]))
                request(
                    "addNote",
                    url=config.anki_connect_url,
                    note={
                        "deckName": deck,
                        "modelName": config.note_model,
                        "fields": {front_field: "", back_field: sentence},
                        "options": {"allowDuplicate": False},
                        "tags": tags,
                        "audio": [{"path": output_path, "filename": f"{basename}.mp3", "fields": [front_field]}],
                    },
                )
                added += 1
            except Exception:
                failed += 1
    return ImportResult(processed=len(sentences), added=added, failed=failed)
