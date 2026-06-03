"""Configuration loading for Saiki.

Defaults mirror the original scripts. Users can override them with YAML at
~/.config/saiki/config.yaml or by passing --config to the CLI.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - handled when config files are loaded
    yaml = None


DEFAULT_CONFIG: dict[str, Any] = {
    "anki_connect_url": "http://localhost:8765",
    "media_dir": "~/.var/app/net.ankiweb.Anki/data/Anki2/User 1/collection.media",
    "audio_output_root": "~/Languages/Anki/anki-audio",
    "word_output_root": "~/Languages/Anki/anki-words",
    "sentence_dir": "~/Languages/Anki",
    "note_model": "Basic",
    "fields": {"front": "Front", "back": "Back"},
    "languages": {
        "jp": {
            "name": "japanese",
            "transcript_code": "ja",
            "tts_code": "ja",
            "tts_tld": "com",
            "tts_tempo": 1.35,
            "decks": ["日本語"],
            "word_model": "ja_core_news_lg",
            "field": "Back",
            "sentence_file": "sentences_jp.txt",
        },
        "es": {
            "name": "spanish",
            "transcript_code": "es",
            "tts_code": "es",
            "tts_tld": "es",
            "tts_tempo": 1.25,
            "decks": ["Español"],
            "word_model": "es_core_news_sm",
            "field": "Back",
            "sentence_file": "sentences_es.txt",
        },
    },
}


@dataclass(frozen=True)
class Config:
    data: dict[str, Any]

    @property
    def anki_connect_url(self) -> str:
        return str(self.data["anki_connect_url"])

    @property
    def media_dir(self) -> str:
        return expand_path(str(self.data["media_dir"]))

    @property
    def audio_output_root(self) -> str:
        return expand_path(str(self.data["audio_output_root"]))

    @property
    def word_output_root(self) -> str:
        return expand_path(str(self.data["word_output_root"]))

    @property
    def sentence_dir(self) -> str:
        return expand_path(str(self.data["sentence_dir"]))

    @property
    def note_model(self) -> str:
        return str(self.data.get("note_model", "Basic"))

    @property
    def fields(self) -> dict[str, str]:
        return dict(self.data.get("fields", {}))

    @property
    def languages(self) -> dict[str, dict[str, Any]]:
        return dict(self.data.get("languages", {}))

    def language(self, lang: str) -> dict[str, Any]:
        try:
            return dict(self.languages[lang])
        except KeyError as e:
            available = ", ".join(sorted(self.languages))
            raise ValueError(f"Unsupported language '{lang}'. Available: {available}") from e

    def language_name(self, lang: str) -> str:
        return str(self.language(lang)["name"])

    def transcript_code(self, lang: str) -> str:
        return str(self.language(lang)["transcript_code"])

    def decks_for(self, lang: str) -> list[str]:
        return list(self.language(lang).get("decks", []))

    def field_for(self, lang: str) -> str:
        return str(self.language(lang).get("field", self.fields.get("back", "Back")))

    def sentence_file_for(self, lang: str) -> str:
        value = str(self.language(lang).get("sentence_file", f"sentences_{lang}.txt"))
        return expand_path(value if os.path.isabs(value) or value.startswith("~") else os.path.join(self.sentence_dir, value))


def expand_path(path: str) -> str:
    return os.path.expanduser(os.path.expandvars(path))


def default_config_path() -> str:
    return expand_path("~/.config/saiki/config.yaml")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | None = None) -> Config:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config_path = expand_path(path) if path else default_config_path()
    if os.path.exists(config_path):
        if yaml is None:
            raise RuntimeError("Loading config files requires PyYAML. Install pyyaml.")
        with open(config_path, "r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        if not isinstance(loaded, dict):
            raise RuntimeError(f"Config must be a YAML mapping: {config_path}")
        config = deep_merge(config, loaded)
    return Config(config)


def language_choices(config: Config) -> list[str]:
    return sorted(config.languages.keys())
