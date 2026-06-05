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
    "tts_model_dir": "~/.local/share/saiki/models",
    "note_model": "Basic",
    "fields": {"front": "Front", "back": "Back"},
    "languages": {
        "jp": {
            "name": "japanese",
            "transcript_code": "ja",
            "tts_backend": "edge-tts",
            "tts_voice": "ja-JP-NanamiNeural",
            "tts_tempo": 1.15,
            "decks": ["日本語"],
            "word_model": "ja_core_news_lg",
            "field": "Back",
            "sentence_file": "sentences_jp.txt",
        },
        "es": {
            "name": "spanish",
            "transcript_code": "es",
            "tts_backend": "edge-tts",
            "tts_voice": "es-ES-ElviraNeural",
            "tts_tempo": 1,
            "decks": ["Español"],
            "word_model": "es_core_news_sm",
            "field": "Back",
            "sentence_file": "sentences_es.txt",
        },
    },
}


@dataclass(frozen=True)
class Config:
    """Typed convenience wrapper around the merged YAML configuration.

    The underlying ``data`` mapping remains available for simple serialization
    and tests, while properties and helpers provide normalized paths and common
    language-specific lookups for the rest of the application.
    """

    data: dict[str, Any]

    @property
    def anki_connect_url(self) -> str:
        """URL for the local AnkiConnect HTTP server."""
        return str(self.data["anki_connect_url"])

    @property
    def media_dir(self) -> str:
        """Expanded path to Anki's collection.media directory."""
        return expand_path(str(self.data["media_dir"]))

    @property
    def audio_output_root(self) -> str:
        """Expanded root directory for exported listening audio."""
        return expand_path(str(self.data["audio_output_root"]))

    @property
    def word_output_root(self) -> str:
        """Expanded root directory for generated vocabulary lists."""
        return expand_path(str(self.data["word_output_root"]))

    @property
    def sentence_dir(self) -> str:
        """Expanded directory used for relative sentence import files."""
        return expand_path(str(self.data["sentence_dir"]))

    @property
    def tts_model_dir(self) -> str:
        """Expanded directory used to resolve local TTS model paths."""
        return expand_path(str(self.data["tts_model_dir"]))

    @property
    def note_model(self) -> str:
        """Anki note type used when importing generated sentence cards."""
        return str(self.data.get("note_model", "Basic"))

    @property
    def fields(self) -> dict[str, str]:
        """Configured logical field names, currently front and back."""
        return dict(self.data.get("fields", {}))

    @property
    def languages(self) -> dict[str, dict[str, Any]]:
        """Language configurations keyed by CLI language code."""
        return dict(self.data.get("languages", {}))

    def language(self, lang: str) -> dict[str, Any]:
        """Return one language config with shared TTS defaults applied.

        A fresh dict is returned so callers may layer CLI overrides onto it
        without mutating the loaded configuration.
        """
        try:
            language = dict(self.languages[lang])
            language.setdefault("tts_model_dir", self.tts_model_dir)
            return language
        except KeyError as e:
            available = ", ".join(sorted(self.languages))
            raise ValueError(f"Unsupported language '{lang}'. Available: {available}") from e

    def language_name(self, lang: str) -> str:
        """Return the long language bucket name for output directories."""
        return str(self.language(lang)["name"])

    def transcript_code(self, lang: str) -> str:
        """Return the language code expected by transcript providers."""
        return str(self.language(lang)["transcript_code"])

    def decks_for(self, lang: str) -> list[str]:
        """Return configured Anki deck names for a language."""
        return list(self.language(lang).get("decks", []))

    def field_for(self, lang: str) -> str:
        """Return the Anki field to mine for vocabulary."""
        return str(self.language(lang).get("field", self.fields.get("back", "Back")))

    def sentence_file_for(self, lang: str) -> str:
        """Resolve the sentence import file for a language."""
        value = str(self.language(lang).get("sentence_file", f"sentences_{lang}.txt"))
        return expand_path(value if os.path.isabs(value) or value.startswith("~") else os.path.join(self.sentence_dir, value))


def expand_path(path: str) -> str:
    """Expand ``~`` and environment variables in a configured path."""
    return os.path.expanduser(os.path.expandvars(path))


def default_config_path() -> str:
    """Return the conventional user config path."""
    return expand_path("~/.config/saiki/config.yaml")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge a user config mapping over default config values."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | None = None) -> Config:
    """Load defaults plus an optional YAML config file."""
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
    """Return sorted language codes suitable for argparse choices."""
    return sorted(config.languages.keys())
