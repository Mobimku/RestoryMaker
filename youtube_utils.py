import os
from pathlib import Path

# Lightweight wrapper around yt-dlp to fetch info, audio-only, and best-quality video

def _make_opts(output_dir: str, out_tmpl: str = "%(title)s.%(ext)s"):
    return {
        "outtmpl": str(Path(output_dir) / out_tmpl),
        "quiet": True,
        "noprogress": True,
        "nocheckcertificate": True,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
    }


def get_video_info(url: str) -> dict:
    try:
        import yt_dlp
    except Exception as e:
        raise RuntimeError("yt-dlp not installed. Please install it to use YouTube features.") from e
    ydl_opts = _make_opts(".")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info or {}


def download_audio(url: str, output_dir: str, progress_callback=None) -> str:
    """Download audio-only (best) as WAV or M4A for transcription."""
    try:
        import yt_dlp
    except Exception as e:
        raise RuntimeError("yt-dlp not installed. Please install it to use YouTube features.") from e
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    # Prefer m4a for speed, we can still upload to Gemini
    ydl_opts = _make_opts(output_dir, out_tmpl="%(id)s.%(ext)s")
    ydl_opts.update({
        "format": "bestaudio/best",
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "0"},
        ],
    })
    # Optional progress
    if progress_callback:
        state = {"last_pct": -1}
        def _hook(d: dict):
            try:
                status = d.get('status')
                if status == 'downloading':
                    pct_str = (d.get('_percent_str') or '').strip()
                    if pct_str.endswith('%'):
                        try:
                            pct = int(float(pct_str[:-1]))
                        except Exception:
                            pct = None
                    else:
                        pct = None
                    if pct is not None and pct != state.get('last_pct'):
                        state['last_pct'] = pct
                        spd = d.get('_speed_str') or ''
                        eta = d.get('_eta_str') or ''
                        msg = f"[Download Audio] {pct}%" + (f" | {spd}" if spd else "") + (f" | ETA {eta}" if eta else "")
                        progress_callback(msg)
                elif status == 'finished':
                    progress_callback("[Download Audio] Selesai mengunduh audio. Memproses...")
            except Exception:
                pass
        ydl_opts['progress_hooks'] = [_hook]
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # After postprocessing, ext becomes m4a
        out = Path(output_dir) / f"{info['id']}.m4a"
    return str(out)


def download_video_best(url: str, output_dir: str, progress_callback=None) -> str:
    """Download best mp4 (highest quality) if available, fallback to best video+audio."""
    try:
        import yt_dlp
    except Exception as e:
        raise RuntimeError("yt-dlp not installed. Please install it to use YouTube features.") from e
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    # Try best mp4 video+audio merged
    ydl_opts = _make_opts(output_dir, out_tmpl="%(title)s.%(ext)s")
    ydl_opts.update({
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
    })
    # Progress hook for logging download progress
    state = {"last_pct": -1}
    def _hook(d: dict):
        if not progress_callback:
            return
        try:
            status = d.get('status')
            if status == 'downloading':
                pct_str = (d.get('_percent_str') or '').strip()
                if pct_str.endswith('%'):
                    try:
                        pct = int(float(pct_str[:-1]))
                    except Exception:
                        pct = None
                else:
                    pct = None
                if pct is not None and pct != state.get('last_pct'):
                    state['last_pct'] = pct
                    spd = d.get('_speed_str') or ''
                    eta = d.get('_eta_str') or ''
                    msg = f"[Download] {pct}%" + (f" | {spd}" if spd else "") + (f" | ETA {eta}" if eta else "")
                    progress_callback(msg)
            elif status == 'finished':
                progress_callback("[Download] Selesai mengunduh. Menggabungkan/merapikan berkas...")
        except Exception:
            pass
    ydl_opts['progress_hooks'] = [_hook]
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # Determine output path based on title and chosen ext
        title = info.get("title") or info.get("id") or "video"
        # yt-dlp handles sanitization. Find the file in the output_dir that matches info id.
        # The exact filename may vary; best effort: prefer .mp4
        out_dir = Path(output_dir)
        candidates = list(out_dir.glob("*.mp4"))
        if candidates:
            # choose the newest file
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return str(candidates[0])
        # fallback: return any created file
        all_files = sorted(out_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if all_files:
            return str(all_files[0])
    raise RuntimeError("Failed to locate downloaded video file.")


def download_subtitles(
    url: str,
    output_dir: str,
    languages: list[str] | None = None,
    allow_auto: bool = True,
    progress_callback=None,
) -> tuple[str | None, str | None]:
    """Download subtitles only using yt-dlp.
    Returns (subtitle_path, ext) or (None, None) when not available.
    Prefers json3 > srv3 > vtt > srt.
    """
    try:
        import yt_dlp
    except Exception as e:
        raise RuntimeError("yt-dlp not installed. Please install it to use YouTube features.") from e
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    langs = languages or ["id", "en", "en-*", "id-*", "en-orig"]
    ydl_opts = _make_opts(output_dir, out_tmpl="%(title)s.%(ext)s")
    ydl_opts.update({
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": allow_auto,
        "subtitleslangs": langs,
        # Minta format yang tersedia, lalu konversi ke SRT via postprocessor FFmpeg
        "subtitlesformat": "best",
    })
    # Konversi subtitle ke SRT langsung (butuh ffmpeg)
    pp = ydl_opts.get("postprocessors", [])
    pp.append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})
    ydl_opts["postprocessors"] = pp
    if progress_callback:
        def _hook(d: dict):
            try:
                if d.get('status') == 'finished':
                    progress_callback("[Subtitle] Unduh subtitle selesai.")
            except Exception:
                pass
        ydl_opts['progress_hooks'] = [_hook]
    created_before = set(Path(output_dir).glob("*"))
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    created_after = set(Path(output_dir).glob("*"))
    # Cari SRT hasil konversi terlebih dahulu
    srt_files = sorted([p for p in (created_after - created_before) if p.suffix.lower() == ".srt"], key=lambda p: p.stat().st_mtime, reverse=True)
    if srt_files:
        p = srt_files[0]
        return str(p), "srt"
    # Jika tidak ada SRT, cek format lain sebagai fallback
    new_files = sorted([p for p in (created_after - created_before) if p.suffix.lower() in (".json3", ".srv3", ".vtt", ".srt")], key=lambda p: p.stat().st_mtime, reverse=True)
    if not new_files:
        # try to find by known title
        title = (info or {}).get("title") or (info or {}).get("id")
        if title:
            srt_cand = Path(output_dir) / f"{title}.srt"
            if srt_cand.exists():
                return str(srt_cand), "srt"
            for ext in (".json3", ".srv3", ".vtt", ".srt"):
                cand = Path(output_dir) / f"{title}{ext}"
                if cand.exists():
                    return str(cand), cand.suffix.lstrip('.')
        return None, None
    p = new_files[0]
    return str(p), p.suffix.lstrip('.')
