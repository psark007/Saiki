from __future__ import annotations

import os
import tempfile
import unittest

from saiki.audio import build_playlist, resolve_media_paths
from saiki.config import DEFAULT_CONFIG, deep_merge
from saiki.importer import parse_tags, read_sentences
from saiki.text import extract_first_visible_line, extract_visible_text
from saiki.words import build_query_from_decks, compare_word_files, read_word_file
from saiki.youtube import TranscriptLine, extract_video_id, sentence_vocab, write_sentence_export


class ConfigTests(unittest.TestCase):
    def test_deep_merge_preserves_nested_defaults(self):
        merged = deep_merge(DEFAULT_CONFIG, {"languages": {"es": {"decks": ["Spanish"]}}})
        self.assertEqual(merged["languages"]["es"]["decks"], ["Spanish"])
        self.assertEqual(merged["languages"]["es"]["transcript_code"], "es")
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


if __name__ == "__main__":
    unittest.main()
