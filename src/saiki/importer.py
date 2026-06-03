"""Generate TTS audio and add sentence notes to Anki.

This module owns the TTS backend abstraction used by both ``import`` and
``tts-test``. Backends synthesize their native output format first, then ffmpeg
normalizes the result to MP3 and applies the configured tempo multiplier.
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .ankiconnect import anki_request
from .config import Config, expand_path


@dataclass(frozen=True)
class ImportResult:
    """Summary of one sentence import run."""

    processed: int
    added: int
    failed: int
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PreparedTtsBackend:
    """Runtime-ready TTS backend callable plus its native audio extension."""

    name: str
    raw_ext: str
    synthesize: Callable[[str, str], None]


@dataclass(frozen=True)
class TtsBackendSpec:
    """Static metadata needed to validate and build a TTS backend."""

    raw_ext: str
    build: Callable[[dict[str, Any]], Callable[[str, str], None]]
    required_keys: tuple[str, ...] = ()
    command: str | None = None
    list_voices: Callable[[dict[str, Any]], list[str]] | None = None


def parse_tags(value: str | None) -> list[str]:
    """Parse comma-separated tag text and add Saiki's default TTS tags."""
    tags = ["text-to-speech"]
    if value:
        tags.extend(tag.strip() for tag in value.split(",") if tag.strip())
    else:
        tags.append("AI-generated")
    return tags


def require_command(name: str) -> None:
    """Raise a friendly error if an external command is not on PATH."""
    if shutil.which(name) is None:
        raise RuntimeError(f"Required command not found: {name}")


_TTS_PATH_KEYS = ("tts_model", "tts_voices", "tts_vocab_config", "tts_config")
_MAX_ERROR_DETAILS = 5
_DEFAULT_TEST_TEXT = {
    "jp": "これはテストです。",
    "es": "Esta es una prueba.",
}


def _generate_gtts(cfg: dict[str, Any]) -> Callable[[str, str], None]:
    """Build a gTTS synthesizer using the command-line wrapper."""
    lang_code = str(cfg["tts_code"])
    tld = str(cfg["tts_tld"])

    def synthesize(sentence: str, output: str) -> None:
        subprocess.run(
            ["gtts-cli", sentence, "--lang", lang_code, "--tld", tld, "--output", output],
            stdin=subprocess.DEVNULL,
            check=True,
        )

    return synthesize


def _generate_edge_tts(cfg: dict[str, Any]) -> Callable[[str, str], None]:
    """Build an edge-tts synthesizer for a configured neural voice."""
    voice = str(cfg["tts_voice"])

    def synthesize(sentence: str, output: str) -> None:
        subprocess.run(
            ["edge-tts", "--voice", voice, "--text", sentence, "--write-media", output],
            stdin=subprocess.DEVNULL,
            check=True,
        )

    return synthesize


def _generate_piper(cfg: dict[str, Any]) -> Callable[[str, str], None]:
    """Build a Piper synthesizer around a local ONNX voice model."""
    model = str(cfg["tts_model"])
    _require_file("piper", "tts_model", model)
    config = str(cfg["tts_config"]) if cfg.get("tts_config") else None
    if config:
        _require_file("piper", "tts_config", config)

    def synthesize(sentence: str, output: str) -> None:
        command = ["piper", "--model", model]
        if config:
            command.extend(["--config", config])
        command.extend(["--output_file", output])
        subprocess.run(
            command,
            input=f"{sentence}\n".encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    return synthesize


def _generate_espeak(cfg: dict[str, Any]) -> Callable[[str, str], None]:
    """Build an espeak-ng synthesizer for a configured voice code."""
    voice = str(cfg["tts_voice"])

    def synthesize(sentence: str, output: str) -> None:
        subprocess.run(["espeak-ng", "-v", voice, "-w", output, sentence], stdin=subprocess.DEVNULL, check=True)

    return synthesize


def _generate_kokoro(cfg: dict[str, Any]) -> Callable[[str, str], None]:
    """Build a Kokoro ONNX synthesizer from local model and voice files."""
    _require_file("kokoro", "tts_model", str(cfg["tts_model"]))
    _require_file("kokoro", "tts_voices", str(cfg["tts_voices"]))
    if cfg.get("tts_vocab_config"):
        _require_file("kokoro", "tts_vocab_config", str(cfg["tts_vocab_config"]))

    try:
        from kokoro_onnx import Kokoro  # type: ignore
        import soundfile as sf  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "kokoro backend requires 'kokoro-onnx' and 'soundfile'. Install them first."
        ) from exc

    kokoro_kwargs = {}
    if cfg.get("tts_vocab_config"):
        kokoro_kwargs["vocab_config"] = str(cfg["tts_vocab_config"])
    kokoro = Kokoro(str(cfg["tts_model"]), str(cfg["tts_voices"]), **kokoro_kwargs)
    voice = str(cfg["tts_voice"])
    lang_code = str(cfg["tts_code"])
    speed = _optional_float(cfg, "tts_speed", 1.0)

    def synthesize(sentence: str, output: str) -> None:
        samples, sample_rate = kokoro.create(sentence, voice=voice, speed=speed, lang=lang_code)
        sf.write(output, samples, sample_rate)

    return synthesize


def _list_gtts_voices(cfg: dict[str, Any]) -> list[str]:
    """Return voice-listing guidance for gTTS."""
    return [
        "gtts does not expose named voices.",
        f"Current settings: tts_code={cfg.get('tts_code', '<unset>')}, tts_tld={cfg.get('tts_tld', '<unset>')}",
    ]


def _list_edge_voices(cfg: dict[str, Any]) -> list[str]:
    """Return the configured edge-tts voice or ask the CLI to list online voices."""
    if cfg.get("tts_voice"):
        return [
            f"Configured edge-tts voice: {cfg['tts_voice']}",
            "Run `edge-tts --list-voices` directly to browse the full online voice catalog.",
        ]
    return _run_voice_command(["edge-tts", "--list-voices"])


def _list_espeak_voices(cfg: dict[str, Any]) -> list[str]:
    """List espeak-ng voices, narrowed by the configured language when possible."""
    voice_filter = str(cfg.get("tts_voice") or cfg.get("tts_code") or "")
    arg = f"--voices={voice_filter}" if voice_filter else "--voices"
    return _run_voice_command(["espeak-ng", arg])


def _list_piper_voices(cfg: dict[str, Any]) -> list[str]:
    """Return Piper model guidance instead of pretending it has a voice catalog."""
    model = cfg.get("tts_model")
    if model:
        return [f"Configured Piper model: {model}"]
    return ["Piper voices are model files. Set tts_model to a downloaded .onnx voice model."]


def _list_kokoro_voices(cfg: dict[str, Any]) -> list[str]:
    """Return Kokoro voice-bundle guidance from the configured files."""
    voice = cfg.get("tts_voice")
    voices = cfg.get("tts_voices")
    if voice or voices:
        return [f"Configured Kokoro voice: {voice or '<unset>'}", f"Voice bundle: {voices or '<unset>'}"]
    return ["Kokoro voices come from the configured tts_voices bundle. Set tts_voice to one voice from it."]


# Registry entries describe validation, dependency checks, synthesis, and
# optional voice-listing behavior in one place so new free backends can be added
# without changing the CLI or import workflows.
_TTS_BACKENDS: dict[str, TtsBackendSpec] = {
    "gtts": TtsBackendSpec(
        raw_ext=".mp3",
        command="gtts-cli",
        required_keys=("tts_code", "tts_tld"),
        build=_generate_gtts,
        list_voices=_list_gtts_voices,
    ),
    "edge-tts": TtsBackendSpec(
        raw_ext=".mp3",
        command="edge-tts",
        required_keys=("tts_voice",),
        build=_generate_edge_tts,
        list_voices=_list_edge_voices,
    ),
    "piper": TtsBackendSpec(
        raw_ext=".wav",
        command="piper",
        required_keys=("tts_model",),
        build=_generate_piper,
        list_voices=_list_piper_voices,
    ),
    "espeak-ng": TtsBackendSpec(
        raw_ext=".wav",
        command="espeak-ng",
        required_keys=("tts_voice",),
        build=_generate_espeak,
        list_voices=_list_espeak_voices,
    ),
    "kokoro": TtsBackendSpec(
        raw_ext=".wav",
        required_keys=("tts_model", "tts_voices", "tts_voice", "tts_code"),
        build=_generate_kokoro,
        list_voices=_list_kokoro_voices,
    ),
}


def supported_tts_backends() -> list[str]:
    """Return supported backend names for argparse choices and error messages."""
    return sorted(_TTS_BACKENDS)


def prepare_tts_backend(lang_cfg: dict[str, Any]) -> PreparedTtsBackend:
    """Validate config and return a callable backend for one language.

    Path-like config values are expanded before validation, required keys are
    checked per backend, and external command dependencies are verified when a
    backend shells out to a CLI tool.
    """
    backend = str(lang_cfg.get("tts_backend", "gtts")).strip()
    spec = _TTS_BACKENDS.get(backend)
    if spec is None:
        raise ValueError(_unknown_backend_message(backend))

    cfg = _expand_tts_paths(lang_cfg)
    _require_backend_keys(backend, cfg, spec.required_keys)
    if spec.command:
        require_command(spec.command)
    return PreparedTtsBackend(name=backend, raw_ext=spec.raw_ext, synthesize=spec.build(cfg))


def list_tts_voices(config: Config, lang: str | None = None, backend: str | None = None) -> list[str]:
    """Return voice names or backend-specific hints for the selected TTS backend."""
    lang_cfg = config.language(lang) if lang else {}
    if backend:
        lang_cfg = {**lang_cfg, "tts_backend": backend}
    name = str(lang_cfg.get("tts_backend", "gtts")).strip()
    spec = _TTS_BACKENDS.get(name)
    if spec is None:
        raise ValueError(_unknown_backend_message(name))
    cfg = _expand_tts_paths(lang_cfg)
    if spec.list_voices is None:
        return [f"{name} does not support voice listing."]
    return spec.list_voices(cfg)


def default_tts_test_text(lang: str) -> str:
    """Return a short built-in phrase for ``tts-test``."""
    return _DEFAULT_TEST_TEXT.get(lang, "This is a test.")


def synthesize_tts_sample(
    config: Config,
    lang: str,
    text: str | None = None,
    output: str | None = None,
    tts_overrides: Mapping[str, Any] | None = None,
) -> str:
    """Generate one TTS sample without touching Anki.

    This is the safest way to verify backend configuration. It uses the same
    backend preparation and ffmpeg normalization path as real imports.
    """
    language = _language_config(config, lang, tts_overrides)
    backend = prepare_tts_backend(language)
    tempo = _tts_tempo(language)
    require_command("ffmpeg")

    output_path = expand_path(output) if output else _default_tts_output(lang, backend.name)
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    sentence = text or default_tts_test_text(lang)
    with tempfile.TemporaryDirectory() as temp_dir:
        raw_output = os.path.join(temp_dir, f"tts_test_original{backend.raw_ext}")
        backend.synthesize(sentence, raw_output)
        speed_audio(raw_output, output_path, tempo)
    return output_path


def _raw_ext(backend: str) -> str:
    """Return a backend's raw extension, defaulting to MP3 for unknown names."""
    spec = _TTS_BACKENDS.get(backend)
    return spec.raw_ext if spec else ".mp3"


def _language_config(
    config: Config,
    lang: str,
    tts_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a language config with any non-None CLI overrides applied."""
    language = config.language(lang)
    if tts_overrides:
        language.update({key: value for key, value in tts_overrides.items() if value is not None})
    return language


def _default_tts_output(lang: str, backend: str) -> str:
    """Return the default sample output path for ``tts-test``."""
    safe_backend = backend.replace(os.sep, "_")
    return os.path.abspath(f"tts_test_{lang}_{safe_backend}.mp3")


def _expand_tts_paths(lang_cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Expand TTS paths and resolve relative model files under tts_model_dir."""
    cfg = dict(lang_cfg)
    if isinstance(cfg.get("tts_model_dir"), str):
        cfg["tts_model_dir"] = expand_path(str(cfg["tts_model_dir"]))
    for key in _TTS_PATH_KEYS:
        if isinstance(cfg.get(key), str):
            path = expand_path(str(cfg[key]))
            if not os.path.isabs(path) and cfg.get("tts_model_dir"):
                path = os.path.join(str(cfg["tts_model_dir"]), path)
            cfg[key] = path
    return cfg


def _require_backend_keys(backend: str, cfg: Mapping[str, Any], keys: tuple[str, ...]) -> None:
    """Ensure backend-specific required config keys are present and non-empty."""
    missing = [key for key in keys if cfg.get(key) is None or (isinstance(cfg.get(key), str) and not cfg[key].strip())]
    if missing:
        raise RuntimeError(f"{backend} backend requires config key(s): {', '.join(missing)}")


def _require_file(backend: str, key: str, path: str) -> None:
    """Ensure a configured model path exists before calling a backend."""
    if not os.path.isfile(path):
        raise RuntimeError(f"{backend} backend config key {key} points to a missing file: {path}")


def format_tts_error(exc: Exception) -> str:
    """Format backend and ffmpeg failures for concise CLI output."""
    return _error_message(exc)


def _unknown_backend_message(backend: str) -> str:
    """Build a consistent unknown-backend error message."""
    return f"Unknown TTS backend: {backend!r}. Choose from: {', '.join(supported_tts_backends())}"


def _optional_float(cfg: Mapping[str, Any], key: str, default: float | None) -> float | None:
    """Parse an optional numeric config value."""
    if cfg.get(key) is None:
        return default
    try:
        return float(cfg[key])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{key} must be a number.") from exc


def _run_voice_command(command: list[str], timeout: float = 30.0) -> list[str]:
    """Run an external voice-listing command and return printable lines."""
    if shutil.which(command[0]) is None:
        return [f"Required command not found: {command[0]}"]
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        return [f"{' '.join(command)} failed: {_subprocess_detail(exc)}"]
    except subprocess.TimeoutExpired:
        return [f"{' '.join(command)} timed out after {timeout:g}s."]
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return lines or ["No voices returned."]


def _short_text(value: str, limit: int = 500) -> str:
    """Collapse and truncate long subprocess output for display."""
    text = " ".join(value.strip().split())
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def _sentence_label(sentence: str, limit: int = 80) -> str:
    """Return a compact representation of a sentence for error lists."""
    text = " ".join(sentence.split())
    if len(text) > limit:
        text = f"{text[:limit - 3]}..."
    return repr(text)


def _subprocess_detail(exc: subprocess.CalledProcessError) -> str:
    """Extract useful stdout/stderr context from a failed subprocess."""
    stderr = exc.stderr
    stdout = exc.stdout
    detail = stderr if stderr else stdout
    if isinstance(detail, bytes):
        detail = detail.decode("utf-8", errors="replace")
    text = _short_text(str(detail or ""))
    command = exc.cmd if isinstance(exc.cmd, str) else " ".join(str(part) for part in exc.cmd)
    suffix = f": {text}" if text else ""
    return f"{command} exited with status {exc.returncode}{suffix}"


def _error_message(exc: Exception) -> str:
    """Convert an exception into a short user-facing string."""
    if isinstance(exc, subprocess.CalledProcessError):
        return _subprocess_detail(exc)
    return _short_text(str(exc) or exc.__class__.__name__)


def _tts_tempo(cfg: Mapping[str, Any]) -> float:
    """Validate and return the post-processing tempo multiplier."""
    tempo = _optional_float(cfg, "tts_tempo", 1.0)
    if tempo is None or tempo <= 0:
        raise RuntimeError("tts_tempo must be greater than 0.")
    return tempo


def speed_audio(raw_output: str, output_path: str, tempo: float) -> None:
    """Convert backend output to an MP3 and apply ffmpeg's atempo filter."""
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", raw_output, "-filter:a", f"atempo={tempo}", "-y", output_path],
        stdin=subprocess.DEVNULL,
        check=True,
    )


def read_sentences(path: str) -> list[str]:
    """Read sentences from plain text, CSV, or TSV input.

    CSV and TSV imports must contain a ``sentence`` header so exports from the
    YouTube sentence-mining command can be imported directly.
    """
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
    tts_overrides: Mapping[str, Any] | None = None,
) -> ImportResult:
    """Generate TTS for each sentence and add cards through AnkiConnect.

    The first configured deck for the language is used as the destination.
    Audio is attached to the front field so Anki imports the temporary MP3 into
    its media collection before the temporary directory is removed.
    """
    language = _language_config(config, lang, tts_overrides)
    backend = prepare_tts_backend(language)
    tempo = _tts_tempo(language)
    require_command("ffmpeg")

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
    errors: list[str] = []

    with tempfile.TemporaryDirectory() as temp_dir:
        for sentence in sentences:
            basename = f"tts_{time.strftime('%Y%m%d_%H%M%S')}_{lang}_{os.getpid()}_{added + failed}"
            raw_output = os.path.join(temp_dir, f"{basename}_original{backend.raw_ext}")
            output_path = os.path.join(temp_dir, f"{basename}.mp3")
            try:
                backend.synthesize(sentence, raw_output)
                speed_audio(raw_output, output_path, tempo)
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
            except Exception as exc:
                failed += 1
                if len(errors) < _MAX_ERROR_DETAILS:
                    errors.append(f"{_sentence_label(sentence)}: {_error_message(exc)}")
    return ImportResult(processed=len(sentences), added=added, failed=failed, errors=errors)
