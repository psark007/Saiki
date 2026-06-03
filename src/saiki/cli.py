"""Unified command-line interface for Saiki."""

from __future__ import annotations

import argparse
import sys

from .audio import extract_audio
from .config import Config, language_choices, load_config
from .importer import import_sentences
from .words import compare_word_files, extract_words
from .youtube import run_youtube


def add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="Path to YAML config file.")


def build_parser(config: Config | None = None) -> argparse.ArgumentParser:
    choices = language_choices(config or load_config())
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

    return parser


def main(argv: list[str] | None = None) -> int:
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
        result = import_sentences(config, args.lang, args.sentence_file, args.tags)
        print(f"Done. Added {result.added}/{result.processed} cards. Failed: {result.failed}")
        return 0 if result.failed == 0 else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
