#!/usr/bin/env python3
"""
audio_extractor.py

Extract all Anki media referenced by [sound:...] tags from one or more decks (grouped by language),
copy them into a language-specific output folder, write an .m3u playlist, and optionally concatenate
all audio into a single MP3 file.

Howto:
  ./audio_extractor.py jp [--concat] [--outdir DIR] [--copy-only-new]
  ./audio_extractor.py es [--concat] [--outdir DIR] [--copy-only-new]

Requirements:
  - Anki running + AnkiConnect enabled at http://localhost:8765
  - Python package: requests
  - OPTIONAL (for --concat): ffmpeg

Notes:
  - This scans all fields of each note and extracts filenames inside [sound:...]
  - It copies referenced media files out of Anki's collection.media folder
  - It preserves filenames (and subfolders if they exist)
"""

import os
import re
import sys
import argparse
import shutil
import subprocess
import tempfile
from typing import List

from anki_common import (
    DEFAULT_ANKI_MEDIA_DIR,
    DEFAULT_AUDIO_OUTPUT_ROOT,
    DECK_TO_LANGUAGE,
    LANG_MAP,
    anki_request,
)

AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".flac")


def ensure_ffmpeg_available() -> None:
    """Raise a helpful error if ffmpeg isn't installed."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found in PATH. Install ffmpeg to use --concat.")


def resolve_media_paths(media_dir: str, out_dir: str, media_name: str) -> tuple[str, str] | None:
    """Return safe source/destination paths for an Anki media filename."""
    normalized = os.path.normpath(media_name)
    if os.path.isabs(normalized) or normalized.startswith(".."):
        return None
    return os.path.join(media_dir, normalized), os.path.join(out_dir, normalized)


def build_playlist(out_dir: str, language: str) -> str:
    """
    Create an .m3u playlist listing audio files under out_dir (sorted by filename).
    Returns the playlist path.
    """
    m3u_path = os.path.join(out_dir, f"{language}.m3u")
    concat_name = f"{language}_concat.mp3"
    files: List[str] = []
    for root, _, filenames in os.walk(out_dir):
        for fname in filenames:
            abs_path = os.path.join(root, fname)
            rel_path = os.path.relpath(abs_path, out_dir)
            if rel_path == os.path.basename(m3u_path):
                continue
            if rel_path == concat_name:
                continue
            if fname.lower().endswith(AUDIO_EXTS) and os.path.isfile(abs_path):
                files.append(rel_path)

    with open(m3u_path, "w", encoding="utf-8") as fh:
        for fname in sorted(files):
            fh.write(f"{fname}\n")

    return m3u_path


def concat_audio_from_m3u(out_dir: str, m3u_path: str, out_path: str) -> None:
    """
    Concatenate audio files in the order listed in the .m3u.
    Uses ffmpeg concat demuxer and re-encodes to MP3 for reliability.

    Keeps original files untouched.
    """
    ensure_ffmpeg_available()

    # Read playlist entries (filenames, one per line)
    with open(m3u_path, "r", encoding="utf-8") as fh:
        rel_files = [line.strip() for line in fh if line.strip()]

    # Filter to existing audio files
    abs_files: List[str] = []
    for rel in rel_files:
        p = os.path.join(out_dir, rel)
        if os.path.isfile(p) and rel.lower().endswith(AUDIO_EXTS):
            abs_files.append(os.path.abspath(p))

    if not abs_files:
        raise RuntimeError("No audio files found to concatenate (playlist is empty?).")

    # ffmpeg concat demuxer expects a file with lines like: file '/abs/path/to/file'
    # Use a temp file so we don't leave junk behind if ffmpeg fails.
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
        concat_list_path = tmp.name
        for p in abs_files:
            # Escape single quotes for ffmpeg concat list
            safe = p.replace("'", "'\\''")
            tmp.write(f"file '{safe}'\n")

    # Re-encode to MP3 to avoid header/codec mismatches across files
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_path,
        "-c:a", "libmp3lame",
        "-q:a", "4",
        "-y",
        out_path,
    ]

    try:
        subprocess.run(cmd, check=True)
    finally:
        try:
            os.remove(concat_list_path)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract Anki audio by language."
    )

    # REQUIRED positional language code: jp / es 
    parser.add_argument(
        "lang",
        choices=sorted(LANG_MAP.keys()),
        help="Language code (jp or es).",
    )

    # Match bash-style flags
    parser.add_argument(
        "--concat",
        action="store_true",
        help="Also output a single concatenated MP3 file (in playlist order).",
    )
    parser.add_argument(
        "--outdir",
        help="Output directory. Default: ~/Languages/Anki/anki-audio/<language>",
    )
    parser.add_argument(
        "--media-dir",
        default=DEFAULT_ANKI_MEDIA_DIR,
        help="Anki collection.media directory. Defaults to the common Flatpak profile path.",
    )

    # Keep your existing useful behavior
    parser.add_argument(
        "--copy-only-new",
        action="store_true",
        help="Skip overwriting existing files.",
    )

    args = parser.parse_args()

    language = LANG_MAP[args.lang]
    media_dir = os.path.expanduser(args.media_dir)

    # Find all decks whose mapped language matches
    selected_decks = [deck for deck, lang in DECK_TO_LANGUAGE.items() if lang == language]
    if not selected_decks:
        print(f"No decks found for language: {language}", file=sys.stderr)
        return 1

    # Output folder: either user-specified --outdir or default output root/<language>
    out_dir = os.path.expanduser(args.outdir) if args.outdir else os.path.join(DEFAULT_AUDIO_OUTPUT_ROOT, language)
    os.makedirs(out_dir, exist_ok=True)

    # Collect note IDs across selected decks
    all_ids: List[int] = []
    for deck in selected_decks:
        ids = anki_request("findNotes", query=f'deck:"{deck}"')
        all_ids.extend(ids)

    if not all_ids:
        print(f"No notes found in decks for language: {language}")
        return 0

    # Fetch notes info (fields contain [sound:...] references)
    notes = anki_request("notesInfo", notes=all_ids)

    # Copy referenced audio files into out_dir
    copied: List[str] = []
    for note in notes:
        fields = note.get("fields", {})
        for field in fields.values():
            val = field.get("value", "") or ""
            for match in re.findall(r"\[sound:(.+?)\]", val):
                paths = resolve_media_paths(media_dir, out_dir, match)
                if paths is None:
                    print(f"Skipping unsafe media reference: {match}", file=sys.stderr)
                    continue
                src, dst = paths

                if not os.path.exists(src):
                    continue

                # If Anki stored media in subfolders, ensure the subfolder exists in out_dir
                dst_parent = os.path.dirname(dst)
                if dst_parent:
                    os.makedirs(dst_parent, exist_ok=True)

                if args.copy_only_new and os.path.exists(dst):
                    continue

                shutil.copy2(src, dst)
                copied.append(match)

    # Create playlist, including audio in subfolders.
    m3u_path = build_playlist(out_dir, language)

    print(f"\n✅ Copied {len(copied)} files for {language}")
    print(f"🎵 Playlist created at: {m3u_path}")
    print(f"📁 Output directory: {out_dir}")

    # Optional: concatenate all audio into one MP3 (order = playlist order)
    if args.concat:
        concat_out = os.path.join(out_dir, f"{language}_concat.mp3")
        try:
            concat_audio_from_m3u(out_dir, m3u_path, concat_out)
            print(f"🎧 Concatenated file created at: {concat_out}")
        except Exception as e:
            print(f"❌ Concatenation failed: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
