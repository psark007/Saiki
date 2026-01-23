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
from typing import Dict, List

import requests


# Map deck name -> language bucket
deck_to_language: Dict[str, str] = {
    "日本語": "japanese",
    "Español": "spanish",
    # Add more mappings here
}

# Map CLI lang code -> language bucket
lang_map: Dict[str, str] = {
    "jp": "japanese",
    "es": "spanish",
}

# If Anki is installed as a flatpak, media dir is typically:
media_dir = os.path.expanduser("~/.var/app/net.ankiweb.Anki/data/Anki2/User 1/collection.media")

# Default export root (can be overridden by --outdir)
output_root = os.path.expanduser("~/Documents/anki-audio")

AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".flac")


def anki_request(action: str, **params):
    """Make an AnkiConnect request and return 'result'. Raise on error."""
    resp = requests.post(
        "http://localhost:8765",
        json={"action": action, "version": 6, "params": params},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("error") is not None:
        raise RuntimeError(f"AnkiConnect error for {action}: {data['error']}")
    return data["result"]


def ensure_ffmpeg_available() -> None:
    """Raise a helpful error if ffmpeg isn't installed."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found in PATH. Install ffmpeg to use --concat.")


def build_playlist(out_dir: str, language: str) -> str:
    """
    Create an .m3u playlist listing audio files in out_dir (sorted by filename).
    Returns the playlist path.
    """
    m3u_path = os.path.join(out_dir, f"{language}.m3u")
    files = sorted(
        f for f in os.listdir(out_dir)
        if f.lower().endswith(AUDIO_EXTS) and os.path.isfile(os.path.join(out_dir, f))
    )

    with open(m3u_path, "w", encoding="utf-8") as fh:
        for fname in files:
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
        choices=sorted(lang_map.keys()),
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
        help="Output directory. Default: ~/Documents/anki-audio/<language>",
    )

    # Keep your existing useful behavior
    parser.add_argument(
        "--copy-only-new",
        action="store_true",
        help="Skip overwriting existing files.",
    )

    args = parser.parse_args()

    language = lang_map[args.lang]

    # Find all decks whose mapped language matches
    selected_decks = [deck for deck, lang in deck_to_language.items() if lang == language]
    if not selected_decks:
        print(f"No decks found for language: {language}", file=sys.stderr)
        return 1

    # Output folder: either user-specified --outdir or default output_root/<language>
    out_dir = os.path.expanduser(args.outdir) if args.outdir else os.path.join(output_root, language)
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
                src = os.path.join(media_dir, match)
                dst = os.path.join(out_dir, match)

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

    # Create playlist (top-level audio only; if you have subfolders, you can extend this)
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

