"""Extract Anki audio media into playlists."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable

from .ankiconnect import anki_request
from .config import Config

AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".flac")


def resolve_media_paths(media_dir: str, out_dir: str, media_name: str) -> tuple[str, str] | None:
    """Return safe source and destination paths for one Anki media filename.

    Anki stores audio references as media names, not arbitrary filesystem
    paths. Absolute paths and parent-directory traversal are rejected so a
    malformed card cannot make the export read or write outside the configured
    media/output directories.
    """
    normalized = os.path.normpath(media_name)
    if os.path.isabs(normalized) or normalized.startswith(".."):
        return None
    return os.path.join(media_dir, normalized), os.path.join(out_dir, normalized)


def build_playlist(out_dir: str, language: str) -> str:
    """Write an M3U playlist containing exported audio files for a language."""
    m3u_path = os.path.join(out_dir, f"{language}.m3u")
    concat_name = f"{language}_concat.mp3"
    files: list[str] = []
    for root, _, filenames in os.walk(out_dir):
        for fname in filenames:
            abs_path = os.path.join(root, fname)
            rel_path = os.path.relpath(abs_path, out_dir)
            if rel_path in {os.path.basename(m3u_path), concat_name}:
                continue
            if fname.lower().endswith(AUDIO_EXTS) and os.path.isfile(abs_path):
                files.append(rel_path)

    with open(m3u_path, "w", encoding="utf-8") as fh:
        for fname in sorted(files):
            fh.write(f"{fname}\n")
    return m3u_path


def concat_audio_from_m3u(out_dir: str, m3u_path: str, out_path: str) -> None:
    """Concatenate playlist entries into a single MP3 with ffmpeg."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found in PATH. Install ffmpeg to use --concat.")

    with open(m3u_path, "r", encoding="utf-8") as fh:
        rel_files = [line.strip() for line in fh if line.strip()]

    abs_files = [
        os.path.abspath(os.path.join(out_dir, rel))
        for rel in rel_files
        if os.path.isfile(os.path.join(out_dir, rel)) and rel.lower().endswith(AUDIO_EXTS)
    ]
    if not abs_files:
        raise RuntimeError("No audio files found to concatenate.")

    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
        concat_list_path = tmp.name
        for path in abs_files:
            # ffmpeg's concat demuxer uses single-quoted paths. Escape literal
            # apostrophes so media filenames from Anki remain valid entries.
            tmp.write(f"file '{path.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n")

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", concat_list_path, "-c:a", "libmp3lame", "-q:a", "4", "-y", out_path,
    ]
    try:
        subprocess.run(cmd, check=True)
    finally:
        try:
            os.remove(concat_list_path)
        except OSError:
            pass


def extract_audio(
    config: Config,
    lang: str,
    outdir: str | None = None,
    media_dir: str | None = None,
    copy_only_new: bool = False,
    concat: bool = False,
    request: Callable = anki_request,
) -> dict[str, object]:
    """Copy audio from configured Anki decks and build a playlist.

    The return value is intentionally CLI-friendly: it reports the number of
    copied files, the playlist path, the output directory, and the optional
    concatenated MP3 path. ``request`` is injectable so tests can exercise the
    workflow without a running Anki instance.
    """
    language = config.language_name(lang)
    selected_decks = config.decks_for(lang)
    if not selected_decks:
        raise RuntimeError(f"No decks configured for language: {lang}")

    media_root = media_dir or config.media_dir
    out_dir = os.path.expanduser(outdir) if outdir else os.path.join(config.audio_output_root, language)
    os.makedirs(out_dir, exist_ok=True)

    all_ids: list[int] = []
    for deck in selected_decks:
        all_ids.extend(request("findNotes", url=config.anki_connect_url, query=f'deck:"{deck}"') or [])

    if not all_ids:
        return {"copied": 0, "playlist": build_playlist(out_dir, language), "outdir": out_dir, "concat": None}

    notes = request("notesInfo", url=config.anki_connect_url, notes=all_ids) or []
    copied: list[str] = []
    for note in notes:
        for field in (note.get("fields", {}) or {}).values():
            val = field.get("value", "") or ""
            for match in re.findall(r"\[sound:(.+?)\]", val):
                paths = resolve_media_paths(media_root, out_dir, match)
                if paths is None:
                    continue
                src, dst = paths
                if not os.path.exists(src):
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if copy_only_new and os.path.exists(dst):
                    continue
                shutil.copy2(src, dst)
                copied.append(match)

    m3u_path = build_playlist(out_dir, language)
    concat_path = None
    if concat:
        concat_path = os.path.join(out_dir, f"{language}_concat.mp3")
        concat_audio_from_m3u(out_dir, m3u_path, concat_path)
    return {"copied": len(copied), "playlist": m3u_path, "outdir": out_dir, "concat": concat_path}
