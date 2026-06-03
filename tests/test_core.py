from __future__ import annotations

import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from saiki.audio import build_playlist, resolve_media_paths
from saiki.config import Config, DEFAULT_CONFIG, deep_merge
from saiki.importer import (
    PreparedTtsBackend,
    import_sentences,
    list_tts_voices,
    parse_tags,
    prepare_tts_backend,
    read_sentences,
    synthesize_tts_sample,
    supported_tts_backends,
)
from saiki.text import extract_first_visible_line, extract_visible_text
from saiki.words import build_query_from_decks, compare_word_files, read_word_file
from saiki.youtube import TranscriptLine, extract_video_id, sentence_vocab, write_sentence_export


class ConfigTests(unittest.TestCase):
    def test_deep_merge_preserves_nested_defaults(self):
        merged = deep_merge(DEFAULT_CONFIG, {"languages": {"es": {"decks": ["Spanish"]}}})
        self.assertEqual(merged["languages"]["es"]["decks"], ["Spanish"])
        self.assertEqual(merged["languages"]["es"]["transcript_code"], "es")
        self.assertEqual(merged["tts_model_dir"], "~/.local/share/saiki/models")
        self.assertIn("jp", merged["languages"])


class TextTests(unittest.TestCase):
    def test_visible_text_helpers_strip_html(self):
        raw = "<div>Hola&nbsp;mundo</div><br><p>segunda linea</p>"
        self.assertEqual(extract_first_visible_line(raw), "Hola\xa0mundo")
        self.assertEqual(extract_visible_text(raw), "Hola\xa0mundo\nsegunda linea")


class AudioTests(unittest.TestCase):
    def test_resolve_media_paths_rejects_unsafe_names(self):
        self.assertIsNone(resolve_media_paths("/media", "/out", "../secret.mp3"))
        self.assertIsNone(resolve_media_paths("/media", "/out", "/tmp/secret.mp3"))
        self.assertEqual(
            resolve_media_paths("/media", "/out", "nested/audio.mp3"),
            ("/media/nested/audio.mp3", "/out/nested/audio.mp3"),
        )

    def test_build_playlist_includes_audio_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "nested"))
            for rel in ["b.mp3", "nested/a.ogg", "note.txt", "spanish_concat.mp3"]:
                with open(os.path.join(tmp, rel), "w", encoding="utf-8") as fh:
                    fh.write("x")
            playlist = build_playlist(tmp, "spanish")
            with open(playlist, "r", encoding="utf-8") as fh:
                self.assertEqual(fh.read().splitlines(), ["b.mp3", "nested/a.ogg"])


class WordsTests(unittest.TestCase):
    def test_build_query_from_decks(self):
        self.assertEqual(build_query_from_decks(["A", "B"]), 'deck:"A" OR deck:"B"')

    def test_compare_word_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "source.txt")
            known = os.path.join(tmp, "known.txt")
            with open(source, "w", encoding="utf-8") as fh:
                fh.write("comer 3\nhablar 2\n")
            with open(known, "w", encoding="utf-8") as fh:
                fh.write("Comer 10\n")
            self.assertEqual(read_word_file(known), {"comer"})
            self.assertEqual(compare_word_files(source, known), ["hablar 2"])


class YoutubeTests(unittest.TestCase):
    def test_extract_video_id(self):
        self.assertEqual(extract_video_id("https://youtu.be/abc123"), "abc123")
        self.assertEqual(extract_video_id("https://www.youtube.com/watch?v=abc123&t=5"), "abc123")
        self.assertEqual(extract_video_id("abc123"), "abc123")

    def test_sentence_vocab_filters_known_words(self):
        self.assertEqual(sentence_vocab("Hola hola mundo", "es", {"hola"}), ["mundo"])

    def test_write_sentence_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "sentences.tsv")
            written = write_sentence_export(
                [TranscriptLine(12.3, "Hola mundo")],
                out,
                "abc123",
                "es",
            )
            self.assertEqual(written, 1)
            with open(out, "r", encoding="utf-8") as fh:
                rows = fh.read().splitlines()
            self.assertEqual(rows[0], "sentence\ttimestamp\tvideo_url\tvocab_guess")
            self.assertEqual(rows[1], "Hola mundo\t12.30\thttps://www.youtube.com/watch?v=abc123\thola, mundo")


class ImporterTests(unittest.TestCase):
    def test_parse_tags(self):
        self.assertEqual(parse_tags(None), ["text-to-speech", "AI-generated"])
        self.assertEqual(parse_tags("youtube, manual "), ["text-to-speech", "youtube", "manual"])

    def test_read_sentences_from_tsv_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "youtube.tsv")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("sentence\ttimestamp\tvideo_url\tvocab_guess\n")
                fh.write("Hola mundo\t1.00\thttps://example.test\tmundo\n")
            self.assertEqual(read_sentences(path), ["Hola mundo"])

    def test_supported_tts_backends_are_free_options(self):
        self.assertEqual(supported_tts_backends(), ["edge-tts", "espeak-ng", "gtts", "kokoro", "piper"])

    def test_list_gtts_voice_hint(self):
        config = Config(deep_merge(deepcopy(DEFAULT_CONFIG), {"languages": {"es": {"tts_code": "es", "tts_tld": "es"}}}))
        voices = list_tts_voices(config, "es", backend="gtts")
        self.assertIn("gtts does not expose named voices.", voices)
        self.assertIn("tts_code=es", voices[1])

    def test_list_edge_voice_uses_configured_voice(self):
        voices = list_tts_voices(Config(deepcopy(DEFAULT_CONFIG)), "es")
        self.assertEqual(voices[0], "Configured edge-tts voice: es-ES-ElviraNeural")

    def test_prepare_tts_backend_validates_required_keys(self):
        with self.assertRaisesRegex(RuntimeError, "tts_voice"):
            prepare_tts_backend({"tts_backend": "edge-tts"})

    def test_prepare_tts_backend_expands_model_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = os.path.join(tmp, "voice.onnx")
            config = os.path.join(tmp, "voice.onnx.json")
            for path in [model, config]:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("{}")

            with patch("saiki.importer.require_command"):
                backend = prepare_tts_backend(
                    {
                        "tts_backend": "piper",
                        "tts_model_dir": tmp,
                        "tts_model": "voice.onnx",
                        "tts_config": "voice.onnx.json",
                    }
                )

            with patch("saiki.importer.subprocess.run") as run:
                backend.synthesize("Hola", "/tmp/out.wav")

        args = run.call_args.args[0]
        self.assertEqual(args[2], model)
        self.assertEqual(args[4], config)
        self.assertEqual(run.call_args.kwargs["input"], b"Hola\n")

    def test_synthesize_tts_sample_uses_backend_and_speed_audio(self):
        seen: dict[str, str] = {}

        def synthesize(sentence: str, output: str) -> None:
            seen["sentence"] = sentence
            seen["raw_output"] = output

        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "sample.mp3")
            with patch("saiki.importer.prepare_tts_backend") as prepare, patch(
                "saiki.importer.require_command"
            ), patch("saiki.importer.speed_audio") as speed:
                prepare.return_value = PreparedTtsBackend("fake", ".wav", synthesize)
                result = synthesize_tts_sample(Config(deepcopy(DEFAULT_CONFIG)), "es", output=output)

        self.assertEqual(result, output)
        self.assertEqual(seen["sentence"], "Esta es una prueba.")
        self.assertTrue(seen["raw_output"].endswith(".wav"))
        speed.assert_called_once()

    def test_import_sentences_returns_error_details(self):
        def fail_synthesis(sentence: str, output: str) -> None:
            raise RuntimeError("tts broke")

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sentences.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("Hola mundo\n")

            with patch("saiki.importer.prepare_tts_backend") as prepare, patch("saiki.importer.require_command"):
                prepare.return_value = PreparedTtsBackend("fake", ".mp3", fail_synthesis)
                result = import_sentences(Config(deepcopy(DEFAULT_CONFIG)), "es", path, request=lambda *a, **k: None)

        self.assertEqual(result.processed, 1)
        self.assertEqual(result.added, 0)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.errors, ["'Hola mundo': tts broke"])


if __name__ == "__main__":
    unittest.main()
