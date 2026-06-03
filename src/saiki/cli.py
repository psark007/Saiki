"""Unified command-line interface for Saiki."""

from __future__ import annotations

import argparse
import sys

from .audio import extract_audio
from .config import Config, language_choices, load_config
from .importer import (
    format_tts_error,
    import_sentences,
    list_tts_voices,
    supported_tts_backends,
    synthesize_tts_sample,
)
from .words import compare_word_files, extract_words
from .youtube import run_youtube


def add_config_arg(parser: argparse.ArgumentParser) -> None:
    """Attach the shared ``--config`` option to a parser."""
    parser.add_argument("--config", help="Path to YAML config file.")


def add_tts_override_args(parser: argparse.ArgumentParser, tts_backends: list[str]) -> None:
    """Attach per-command TTS override flags.

    These options intentionally mirror config keys so command-line overrides
    can be collected mechanically and merged over the selected language.
    """
    parser.add_argument("--tts-backend", choices=tts_backends, help="Override the configured TTS backend.")
    parser.add_argument("--tts-voice", help="Override the configured backend voice.")
    parser.add_argument("--tts-voices", help="Override the configured backend voice bundle path.")
    parser.add_argument("--tts-model", help="Override the configured backend model or local model path.")
    parser.add_argument("--tts-model-dir", help="Override the directory used for relative TTS model paths.")
    parser.add_argument("--tts-config", help="Override the configured backend model config path.")
    parser.add_argument("--tts-vocab-config", help="Override the configured backend vocab config path.")
    parser.add_argument("--tts-code", help="Override the configured backend language code.")
    parser.add_argument("--tts-tld", help="Override the configured gTTS top-level domain.")
    parser.add_argument("--tts-tempo", type=float, help="Override the post-processing tempo multiplier.")
    parser.add_argument("--tts-speed", type=float, help="Override backend-native speech speed when supported.")


def collect_tts_overrides(args: argparse.Namespace) -> dict[str, object]:
    """Collect TTS override attributes from an argparse namespace."""
    return {
        "tts_backend": getattr(args, "tts_backend", None),
        "tts_voice": getattr(args, "tts_voice", None),
        "tts_voices": getattr(args, "tts_voices", None),
        "tts_model": getattr(args, "tts_model", None),
        "tts_model_dir": getattr(args, "tts_model_dir", None),
        "tts_config": getattr(args, "tts_config", None),
        "tts_vocab_config": getattr(args, "tts_vocab_config", None),
        "tts_code": getattr(args, "tts_code", None),
        "tts_tld": getattr(args, "tts_tld", None),
        "tts_tempo": getattr(args, "tts_tempo", None),
        "tts_speed": getattr(args, "tts_speed", None),
    }


def build_parser(config: Config | None = None) -> argparse.ArgumentParser:
    """Build the full CLI parser.

    Passing a loaded config lets argparse choices reflect user-defined language
    codes. When no config is supplied, defaults are loaded so the parser remains
    usable in tests and help-generation contexts.
    """
    choices = language_choices(config or load_config())
    tts_backends = supported_tts_backends()
    parser = argparse.ArgumentParser(description="Saiki: sentence mining and listening tools for Anki.")
    add_config_arg(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    audio = sub.add_parser("audio", help="Extract Anki audio into playlists.")
    audio.add_argument("lang", choices=choices)
    audio.add_argument("--concat", action="store_true")
    audio.add_argument("--outdir")
    audio.add_argument("--media-dir")
    audio.add_argument("--copy-only-new", action="store_true")

    words = sub.add_parser("words", help="Extract frequent words from Anki.")
    words.add_argument("lang", choices=choices)
    group = words.add_mutually_exclusive_group()
    group.add_argument("--query")
    group.add_argument("--deck", action="append")
    words.add_argument("--field")
    words.add_argument("--min-freq", type=int, default=2)
    words.add_argument("--outdir")
    words.add_argument("--out")
    words.add_argument("--full-field", action="store_true")
    words.add_argument("--spacy-model")

    compare = sub.add_parser("compare-words", help="Print words in source that are not in known.")
    compare.add_argument("source")
    compare.add_argument("known")

    youtube = sub.add_parser("youtube", help="Mine a YouTube transcript.")
    youtube.add_argument("lang", choices=choices)
    youtube.add_argument("video")
    youtube.add_argument("--mode", choices=["vocab", "sentences"], default="vocab")
    youtube.add_argument("--top", type=int)
    youtube.add_argument("--no-stopwords", action="store_true")
    youtube.add_argument("--raw", action="store_true")
    youtube.add_argument("--out")
    youtube.add_argument("--format", choices=["tsv", "csv"], default="tsv")
    youtube.add_argument("--known-words", help="Word list to filter vocab_guess against.")
    youtube.add_argument("--only-new", action="store_true", help="Only export sentences with unknown vocab.")

    importer = sub.add_parser("import", help="Generate TTS and import sentence cards.")
    importer.add_argument("lang", choices=choices)
    importer.add_argument("sentence_file", nargs="?")
    importer.add_argument("--tags", help="Comma-separated tags. text-to-speech is always included.")
    add_tts_override_args(importer, tts_backends)

    test_tts = sub.add_parser("tts-test", help="Synthesize one TTS sample without importing into Anki.")
    test_tts.add_argument("lang", choices=choices)
    test_tts.add_argument("text", nargs="?")
    test_tts.add_argument("--out", help="Output MP3 path. Defaults to ./tts_test_<lang>_<backend>.mp3.")
    add_tts_override_args(test_tts, tts_backends)

    voices = sub.add_parser("tts-voices", help="List voices or voice-listing hints for a TTS backend.")
    voices.add_argument("lang", nargs="?", choices=choices)
    voices.add_argument("--backend", choices=tts_backends, help="Backend to list instead of the language default.")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit status."""
    # Parse --config first so subcommand language choices can come from the
    # user's config file instead of only the built-in defaults.
    pre = argparse.ArgumentParser(add_help=False)
    add_config_arg(pre)
    known, _ = pre.parse_known_args(argv)
    config = load_config(known.config)
    parser = build_parser(config)
    args = parser.parse_args(argv)

    if args.command == "audio":
        result = extract_audio(config, args.lang, args.outdir, args.media_dir, args.copy_only_new, args.concat)
        print(f"Copied {result['copied']} files")
        print(f"Playlist: {result['playlist']}")
        print(f"Output directory: {result['outdir']}")
        if result["concat"]:
            print(f"Concatenated file: {result['concat']}")
        return 0

    if args.command == "words":
        result = extract_words(
            config, args.lang, args.query, args.deck, args.field, args.min_freq,
            args.outdir, args.out, args.full_field, args.spacy_model,
        )
        print(f"Query: {result['query']}")
        print(f"Found {result['notes']} notes")
        print(f"Extracted {result['unique']} unique entries")
        print(f"Wrote {result['written']} entries to: {result['out']}")
        return 0

    if args.command == "compare-words":
        for line in compare_word_files(args.source, args.known):
            print(line)
        return 0

    if args.command == "youtube":
        result = run_youtube(
            config, args.lang, args.video, args.mode, args.top, args.no_stopwords,
            args.raw, args.out, args.format, args.known_words, args.only_new,
        )
        if args.mode == "sentences" and not args.out:
            for line in result["lines"]:
                print(f"[{line.start:.2f}s] {line.text}")
        elif args.mode == "sentences":
            print(f"Wrote {result['written']} rows to: {result['out']}")
        else:
            for word, count in result["items"]:
                print(f"{word}: {count}")
        return 0

    if args.command == "import":
        tts_overrides = collect_tts_overrides(args)
        result = import_sentences(config, args.lang, args.sentence_file, args.tags, tts_overrides=tts_overrides)
        print(f"Done. Added {result.added}/{result.processed} cards. Failed: {result.failed}")
        for error in result.errors:
            print(f"Error: {error}", file=sys.stderr)
        return 0 if result.failed == 0 else 1

    if args.command == "tts-test":
        try:
            output = synthesize_tts_sample(config, args.lang, args.text, args.out, collect_tts_overrides(args))
            print(f"Wrote TTS sample: {output}")
            return 0
        except Exception as exc:
            print(f"Error: {format_tts_error(exc)}", file=sys.stderr)
            return 1

    if args.command == "tts-voices":
        for line in list_tts_voices(config, args.lang, args.backend):
            print(line)
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
