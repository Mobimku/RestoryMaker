# video_processor.py
# This module contains the core logic for processing the video based on the
# storyboard JSON. It will use ffmpeg_utils to execute commands.

import os
import pathlib
import shutil
import random
import threading
from ffmpeg_utils import run_ffmpeg_command, get_duration
import math

def _ts_to_seconds(ts: str) -> float:
    try:
        ts = (ts or "").strip().replace(',', '.')
        h, m, s = ts.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        return 0.0

def _ffconcat_escape(path_obj: pathlib.Path) -> str:
    """Escape path for ffmpeg concat demuxer (single quotes)."""
    s = path_obj.resolve().as_posix()
    return s.replace("'", "'\\''")

def _apply_effects(clip_path, effects, output_path, **kwargs):
    """Applies a list of effects to a single clip using FFmpeg's filter_complex."""
    if not effects:
        shutil.copy(clip_path, output_path)
        return True
    # Build visual filters based on effect names
    filter_map = {
        "hflip": "hflip",
        # Slight color boost
        "contrast_plus": "eq=contrast=1.08:saturation=1.15",
        "sat_plus": "eq=saturation=1.15",
        # Slight static zoom-in (no speed change)
        "zoom_light": "scale=iw*1.06:ih*1.06,crop=iw:ih",
        # Gentle horizontal pan with small zoom to avoid black borders (uses time t)
        "crop_pan_light": "scale=iw*1.06:ih*1.06,crop=iw:ih:x='(in_w-iw)/2 - 20*t':y='(in_h-ih)/2'",
    }
    vf_filters = [filter_map[effect] for effect in effects if effect in filter_map]
    if not vf_filters:
        shutil.copy(clip_path, output_path)
        return True
    # Re-encode audio to ensure concat compatibility
    command = (
        f'ffmpeg -i "{clip_path}" -vf "{",".join(vf_filters)}" '
        f'-c:a aac -b:a 128k -ar 48000 -ac 2 "{output_path}"'
    )
    return run_ffmpeg_command(command, **kwargs)

def _apply_final_effects(input_path, output_path, user_settings, **kwargs):
    """Applies final user-defined effects like BGM, volume changes, etc."""
    inputs = f'-i "{input_path}"'
    video_filters = []; audio_filters = []
    video_stream = "[0:v]"; audio_stream = "[0:a]"

    main_vol = user_settings.get("main_vo_volume", 1.0)
    if main_vol != 1.0:
        # audio_stream already includes brackets, e.g., "[0:a]"
        audio_filters.append(f"{audio_stream}volume={main_vol}[a_main_vol]")
        audio_stream = "[a_main_vol]"

    if user_settings.get("bgm_path"):
        bgm_path = user_settings["bgm_path"]
        bgm_vol = user_settings.get("bgm_volume", 0.1)
        bgm_segment = user_settings.get("bgm_segment", "")
        timing = user_settings.get("bgm_timing")

        if bgm_segment and timing and "start_sec" in timing and "duration_sec" in timing:
            # BGM hanya pada segmen tertentu: loop, trim ke durasi, lalu delay ke offset
            inputs += f' -stream_loop -1 -i "{bgm_path}"'
            start_ms = int(round(timing["start_sec"] * 1000))
            duration_sec = max(0.0, float(timing["duration_sec"]))
            audio_filters.append(
                f"[1:a]atrim=0:{duration_sec},asetpts=PTS-STARTPTS,volume={bgm_vol},adelay={start_ms}|{start_ms}[bgm]"
            )
            audio_filters.append(f"{audio_stream}[bgm]amix=inputs=2:duration=longest[a_out]")
            audio_stream = "[a_out]"
        else:
            # Global BGM sepanjang video (fallback lama)
            inputs += f' -i "{bgm_path}"'
            audio_filters.append(f"[1:a]volume={bgm_vol}[bgm]")
            audio_filters.append(f"{audio_stream}[bgm]amix=inputs=2:duration=longest[a_out]")
            audio_stream = "[a_out]"

    # Selalu tambahkan letterbox (movie bars) default di atas & bawah sebagai overlay
    # Menggunakan 12% tinggi frame untuk tiap bar.
    video_filters.append(
        f"{video_stream}drawbox=x=0:y=0:w=iw:h=ih*0.12:color=black:t=fill,drawbox=x=0:y=ih-ih*0.12:w=iw:h=ih*0.12:color=black:t=fill[v_out]"
    )
    video_stream = "[v_out]"

    if not video_filters and not audio_filters:
        shutil.copy(input_path, output_path)
        return True

    filter_complex = ";".join(video_filters + audio_filters)
    # Tentukan pemetaan audio: jika tidak ada audio filter, map langsung 0:a (bukan label filter)
    used_audio_filters = len(audio_filters) > 0
    video_map = f'"{video_stream}"'  # video_stream adalah label filter seperti [v_out]
    audio_map = f'"{audio_stream}"' if used_audio_filters else '0:a'
    # OPTIMASI KECEPATAN
    command = (f'ffmpeg {inputs} -filter_complex "{filter_complex}" '
               f'-map {video_map} -map {audio_map} -r 25 -c:v libx264 -preset veryfast '
               f'-c:a aac -b:a 128k -ar 48000 -ac 2 "{output_path}"')
    return run_ffmpeg_command(command, **kwargs)

def _process_segment(segment_data, vo_audio_path, source_video_path, work_dir, stop_event, **kwargs):
    """Processes a single video segment from cutting to VO syncing."""
    segment_label = segment_data['label']
    kwargs['progress_callback'](f"--- Memulai proses untuk segmen: {segment_label} ---")
    segment_work_dir = work_dir / segment_label
    segment_work_dir.mkdir(exist_ok=True)

    # Build selected clips either from beats (preferred) or fallback 3-4s sequence
    selected = []
    beats = segment_data.get('beats', []) or []
    source_tbs = segment_data.get('source_timeblocks', []) or []
    # Build readable names for timeblocks to improve logs
    tb_names = []
    for i, tb in enumerate(source_tbs):
        try:
            s = str(tb.get('start', '00:00:00,000')).replace('.', ',')
            e = str(tb.get('end', '00:00:00,000')).replace('.', ',')
            rs = (tb.get('reason') or '').strip().replace('\n', ' ')
            if len(rs) > 48: rs = rs[:45] + '...'
            tb_names.append(f"TB{i:02d} {s}-{e} | {rs}")
        except Exception:
            tb_names.append(f"TB{i:02d}")
    if kwargs.get("progress_callback") and tb_names:
        try:
            kwargs["progress_callback"](f"[Timeblocks] {segment_label}: {len(tb_names)} items")
            for name in tb_names[:12]:
                kwargs["progress_callback"](f"  - {name}")
            if len(tb_names) > 12:
                kwargs["progress_callback"](f"  ... ({len(tb_names)-12} more)")
        except Exception:
            pass
    if beats and source_tbs:
        beat_clips_dir = segment_work_dir / "beat_clips"; beat_clips_dir.mkdir(exist_ok=True)
        try:
            beats_sorted = sorted(beats, key=lambda b: b.get('at_ms', 0))
        except Exception:
            beats_sorted = beats
        if kwargs.get("progress_callback"):
            try:
                kwargs["progress_callback"](f"[Beats] {segment_label}: beats present — using beats as primary anchors")
            except Exception:
                pass
        rng = random.Random()
        acc = 0.0
        for bi, b in enumerate(beats_sorted):
            if stop_event.is_set(): return None
            try:
                block_index = int(b.get('block_index', 0))
                src_off = max(0.0, float(b.get('src_at_ms', 0)) / 1000.0)
                src_len = max(0.01, float(b.get('src_length_ms', 0)) / 1000.0)
            except Exception:
                continue
            if block_index < 0 or block_index >= len(source_tbs):
                continue
            tb = source_tbs[block_index]
            tb_start = _ts_to_seconds(str(tb['start']).replace(',', '.'))
            tb_end = _ts_to_seconds(str(tb['end']).replace(',', '.'))
            cur = tb_start + src_off
            remaining = min(src_len, max(0.01, tb_end - cur))
            # Log beat mapping
            if kwargs.get("progress_callback"):
                try:
                    tb_name = tb_names[block_index] if block_index < len(tb_names) else f"TB{block_index:02d}"
                    kwargs["progress_callback"](f"[Beats] {segment_label} beat {bi:03d} → {tb_name} @{src_off:.2f}s len={src_len:.2f}s")
                except Exception:
                    pass
            # Satu potongan saja per beat: durasi acak 3–4s (atau sisa di timeblock/beat)
            if remaining > 0.1:
                desired = rng.uniform(3.0, 4.0)
                dur = min(remaining, desired)
                if dur >= 0.5:
                    out_clip = beat_clips_dir / f"beat_{bi:03d}_{len(selected):03d}.mp4"
                    cmd = (
                        f"ffmpeg -ss {cur:.3f} -t {dur:.3f} -i \"{source_video_path}\" "
                        f"-r 25 -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -b:a 128k -ar 48000 -ac 2 "
                        f"{_meta_flags()} \"{out_clip}\""
                    )
                    if not run_ffmpeg_command(cmd, **kwargs): return None
                    selected.append(out_clip)
                    if kwargs.get("progress_callback"):
                        try:
                            kwargs["progress_callback"](f"  · clip @{cur-tb_start:.2f}s dur={dur:.2f}s from {tb_name}")
                        except Exception:
                            pass
                    acc += float(get_duration(str(out_clip)) or dur)
                if remaining < 0.5:
                    break
        # Jika total dari beats masih kurang dari VO, tambahkan filler dari timeblocks lain
        vo_duration = get_duration(vo_audio_path)
        if not vo_duration or vo_duration <= 0:
            return None
        if acc < float(vo_duration):
            if kwargs.get("progress_callback"):
                try:
                    kwargs["progress_callback"](f"[Beats] Filler needed: {float(vo_duration)-acc:.2f}s; adding extra 3-4s cuts from timeblocks")
                except Exception:
                    pass
            # iterate all TBs to create additional random cuts until VO covered
            for i, tb in enumerate(source_tbs):
                if stop_event.is_set(): return None
                start_sec = _ts_to_seconds(str(tb['start']).replace(',', '.'))
                end_sec = _ts_to_seconds(str(tb['end']).replace(',', '.'))
                pos = start_sec
                while pos < end_sec - 0.05 and acc < float(vo_duration):
                    dur = rng.uniform(3.0, 4.0)
                    if pos + dur > end_sec:
                        dur = max(0.1, end_sec - pos)
                    if dur < 0.5:
                        break
                    out_clip = segment_work_dir / f"filler_tb{i:02d}_{len(selected):03d}.mp4"
                    cmd = (
                        f"ffmpeg -ss {pos:.3f} -t {dur:.3f} -i \"{source_video_path}\" "
                        f"-r 25 -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -b:a 128k -ar 48000 -ac 2 "
                        f"{_meta_flags()} \"{out_clip}\""
                    )
                    if not run_ffmpeg_command(cmd, **kwargs): return None
                    selected.append(out_clip)
                    acc += float(get_duration(str(out_clip)) or dur)
                    pos += dur
                    if kwargs.get("progress_callback"):
                        try:
                            tb_name = tb_names[i] if i < len(tb_names) else f"TB{i:02d}"
                            kwargs["progress_callback"](f"  · filler from {tb_name} @{pos-start_sec:.2f}s dur={dur:.2f}s")
                        except Exception:
                            pass
        # Trim klip terakhir agar pas VO
        if selected:
            overshoot = acc - float(vo_duration)
            if overshoot > 0.05:
                last_clip = selected[-1]
                last_dur = get_duration(str(last_clip)) or 0.0
                keep = max(0.1, last_dur - overshoot)
                trimmed = segment_work_dir / f"{last_clip.stem}_trim.mp4"
                trim_cmd = (
                    f"ffmpeg -y -i \"{last_clip}\" -t {keep:.3f} -r 25 -c:v libx264 -preset ultrafast -pix_fmt yuv420p "
                    f"-c:a aac -b:a 128k -ar 48000 -ac 2 \"{trimmed}\""
                )
                if run_ffmpeg_command(trim_cmd, **kwargs):
                    selected[-1] = trimmed
                    acc = acc - last_dur + (get_duration(str(trimmed)) or keep)
                    if kwargs.get("progress_callback"):
                        try:
                            kwargs["progress_callback"](f"[Sync] Trimmed last beat/filler clip by {overshoot:.2f}s to fit VO ({vo_duration:.2f}s total)")
                        except Exception:
                            pass
        if not selected:
            return None
        # Log ringkas
        if kwargs.get("progress_callback"):
            try:
                kwargs["progress_callback"](f"[Clips] {segment_label}: {len(selected)} clips (beats-first) totaling {acc:.2f}s vs VO {vo_duration:.2f}s")
            except Exception:
                pass
        if not selected:
            return None
    else:
        # Fallback: timeblocks → join → segment → split 3-4s → select sequentially until VO duration
        timeblock_clips = []
        for i, tb in enumerate(source_tbs):
            if stop_event.is_set(): return None
            start_sec = _ts_to_seconds(str(tb['start']).replace(',', '.'))
            end_sec = _ts_to_seconds(str(tb['end']).replace(',', '.'))
            duration = max(0.01, end_sec - start_sec)
            clip_path = segment_work_dir / f"tb_{i}.mp4"
            cmd = (
                f"ffmpeg -ss {start_sec:.3f} -t {duration:.3f} -i \"{source_video_path}\" "
                f"-r 25 -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -b:a 128k -ar 48000 -ac 2 \"{clip_path}\""
            )
            if not run_ffmpeg_command(cmd, **kwargs): return None
            timeblock_clips.append(clip_path)

        if stop_event.is_set(): return None
        concat_list_path = segment_work_dir / "concat_list.txt"
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for clip in timeblock_clips:
                f.write(f"file '{_ffconcat_escape(clip)}'\n")

        seg_raw_path = segment_work_dir / "seg_raw.mp4"
        concat_command_1 = (f"ffmpeg -f concat -safe 0 -i \"{concat_list_path}\" "
                            f"-c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac \"{seg_raw_path}\"")
        if not run_ffmpeg_command(concat_command_1, **kwargs): return None

        if stop_event.is_set(): return None
        # Bangun kandidat klip 3-4 detik dengan durasi acak (bukan nilai tengah)
        raw_clips_dir = segment_work_dir / "raw_clips"; raw_clips_dir.mkdir(exist_ok=True)
        seg_raw_dur = get_duration(str(seg_raw_path)) or 0.0
        if seg_raw_dur <= 0.1: return None
        rng = random.Random()
        pos = 0.0; cand_paths = []; cand_durs = []
        while pos < seg_raw_dur - 0.05:
            dur = rng.uniform(3.0, 4.0)
            if pos + dur > seg_raw_dur:
                dur = max(0.1, seg_raw_dur - pos)
            if dur < 0.5:
                break
            cand = raw_clips_dir / f"cand_{len(cand_paths):03d}.mp4"
            cut_cmd = (
                f"ffmpeg -ss {pos:.3f} -t {dur:.3f} -i \"{seg_raw_path}\" "
                f"-r 25 -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -b:a 128k -ar 48000 -ac 2 "
                f"{_meta_flags()} \"{cand}\""
            )
            if not run_ffmpeg_command(cut_cmd, **kwargs): return None
            cand_paths.append(cand); cand_durs.append(dur)
            pos += dur

        vo_duration = get_duration(vo_audio_path)
        if not vo_duration or vo_duration <= 0: return None
        avail = len(cand_paths)
        avg_dur = (sum(cand_durs)/avail) if avail else 3.5
        needed = min(avail, max(1, int(math.ceil(float(vo_duration) / max(0.1, avg_dur)))))
        stride = (avail / float(needed)) if needed > 0 else 1.0
        indices = sorted({min(avail-1, int(math.floor(i*stride))) for i in range(needed)})
        # Akumulasi sampai >= VO, jika kurang tambahkan indeks berikutnya bertetangga
        acc = 0.0
        for idx in indices:
            if stop_event.is_set(): return None
            selected.append(cand_paths[idx])
            acc += float(get_duration(str(cand_paths[idx])) or cand_durs[idx])
            if acc >= float(vo_duration): break
        next_idx = indices[-1] + 1 if indices else 0
        while acc < float(vo_duration) and next_idx < avail:
            if stop_event.is_set(): return None
            selected.append(cand_paths[next_idx])
            acc += float(get_duration(str(cand_paths[next_idx])) or cand_durs[next_idx])
            next_idx += 1
        # Trim last clip to fit VO exactly
        if selected:
            overshoot = acc - float(vo_duration)
            if overshoot > 0.05:
                last_clip = selected[-1]
                last_dur = get_duration(str(last_clip)) or 0.0
                keep = max(0.1, last_dur - overshoot)
                trimmed = raw_clips_dir / f"{last_clip.stem}_trim.mp4"
                trim_cmd = (
                    f"ffmpeg -y -i \"{last_clip}\" -t {keep:.3f} -r 25 -c:v libx264 -preset ultrafast -pix_fmt yuv420p "
                    f"-c:a aac -b:a 128k -ar 48000 -ac 2 \"{trimmed}\""
                )
                if run_ffmpeg_command(trim_cmd, **kwargs):
                    selected[-1] = trimmed
                    acc = acc - last_dur + (get_duration(str(trimmed)) or keep)
                    if kwargs.get("progress_callback"):
                        try:
                            kwargs["progress_callback"](f"[Sync] Trimmed last clip by {overshoot:.2f}s to fit VO ({vo_duration:.2f}s total)")
                        except Exception:
                            pass
        if not selected: return None
        # Log rencana seleksi
        if kwargs.get("progress_callback"):
            try:
                kwargs["progress_callback"](f"[ClipsPlan] available={avail}, needed~={needed}, stride={stride:.2f}, selected={len(selected)}")
                sel_total = sum([get_duration(str(c)) or 0.0 for c in selected])
                kwargs["progress_callback"](f"[Clips] Selected {len(selected)} clips totaling {sel_total:.2f}s to match VO {vo_duration:.2f}s")
            except Exception:
                pass

    effected_clips_dir = segment_work_dir / "effected_clips"; effected_clips_dir.mkdir(exist_ok=True)
    effect_rules = segment_data.get('edit_rules', {})
    effected_clips = []
    for i, clip_path in enumerate(selected):
        if stop_event.is_set(): return None
        # Mandatory effects: color boost + random zoom OR pan (as requested)
        selected_effects = ["contrast_plus", random.choice(["zoom_light", "crop_pan_light"]) ]
        output_path = effected_clips_dir / f"effected_{i:03d}.mp4"
        if _apply_effects(clip_path, selected_effects, output_path, **kwargs):
            effected_clips.append(output_path)
            # Log applied effects per clip
            if kwargs.get("progress_callback"):
                try:
                    d = get_duration(str(output_path)) or 0.0
                    kwargs["progress_callback"](f"[Effects] Clip {i:03d}: {output_path.name} | effects={selected_effects} | dur={d:.2f}s")
                except Exception:
                    pass

    if stop_event.is_set(): return None
    concat_list_path_2 = segment_work_dir / "concat_list_2.txt"
    with open(concat_list_path_2, "w", encoding="utf-8") as f:
        for clip in effected_clips:
            f.write(f"file '{_ffconcat_escape(clip)}'\n")
    seg_joined_path = segment_work_dir / "seg_joined.mp4"
        concat_command_2 = (f"ffmpeg -f concat -safe 0 -i \"{concat_list_path_2}\" "
                        f"-r 25 -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -b:a 128k -ar 48000 -ac 2 "
                        f"{_meta_flags()} \"{seg_joined_path}\"")
    if not run_ffmpeg_command(concat_command_2, **kwargs): return None

    if stop_event.is_set(): return None
    final_segment_path = work_dir / f"seg_{segment_label.lower()}.mp4"
    # Gabungkan video + VO, potong ke stream terpendek (VO) tanpa mengubah kecepatan
    main_vol = kwargs.get("main_vo_volume", 1.0)
    if main_vol and main_vol != 1.0:
        cb = kwargs.get("progress_callback")
        if cb:
            try:
                cb(f"Applying VO gain x{main_vol:.2f} for segment '{segment_label}'")
            except Exception:
                pass
    if main_vol and main_vol != 1.0:
        command = (f'ffmpeg -i \"{seg_joined_path}\" -i \"{vo_audio_path}\" '
                   f'-filter_complex "[1:a]volume={main_vol}[a1]" '
                   f'-map 0:v -map "[a1]" -shortest -r 25 -c:v libx264 -preset veryfast -crf 23 '
                   f'-c:a aac -b:a 128k -ar 48000 -ac 2 \"{final_segment_path}\"')
    else:
        command = (f'ffmpeg -i \"{seg_joined_path}\" -i \"{vo_audio_path}\" '
                   f'-map 0:v -map 1:a -shortest -r 25 -c:v libx264 -preset veryfast -crf 23 '
                   f'-c:a aac -b:a 128k -ar 48000 -ac 2 \"{final_segment_path}\"')
    if not run_ffmpeg_command(command, **kwargs): return None
    return final_segment_path

def process_video(storyboard: dict, source_video_path: str, vo_audio_map: dict, user_settings: dict, stop_event: threading.Event, progress_callback=None):
    base_dir = pathlib.Path(source_video_path).parent
    work_dir = base_dir / "temp_restory_work"
    kwargs = {"progress_callback": progress_callback, "main_vo_volume": user_settings.get("main_vo_volume", 1.0)}

    try:
        if work_dir.exists(): shutil.rmtree(work_dir)
        work_dir.mkdir()
        progress_callback(f"Created temporary working directory at: {work_dir}")

        processed_segment_paths = []
        segment_order = []
        selected_segments = user_settings.get("selected_segments", [])

        for segment_data in storyboard.get('segments', []):
            if stop_event.is_set(): raise InterruptedError("Processing stopped by user.")
            segment_label = segment_data['label']
            if segment_label not in selected_segments:
                progress_callback(f"Skipping segment '{segment_label}' as it was not selected.")
                continue

            vo_path = vo_audio_map.get(segment_label)
            if vo_path:
                segment_path = _process_segment(segment_data, vo_path, source_video_path, work_dir, stop_event, **kwargs)
                if segment_path:
                    processed_segment_paths.append(segment_path)
                    segment_order.append(segment_label)
                    # **SOLUSI MANAJEMEN RUANG**
                    segment_work_dir = work_dir / segment_data['label']
                    if segment_work_dir.exists():
                        progress_callback(f"--- Membersihkan file sementara untuk segmen: {segment_data['label']} ---")
                        shutil.rmtree(segment_work_dir)
                else:
                    progress_callback(f"Segment {segment_label} failed or was stopped."); break
            else:
                progress_callback(f"Warning: No voice-over audio found for segment '{segment_label}'. Skipping.")

        if stop_event.is_set() or not processed_segment_paths:
            raise InterruptedError("Processing stopped or no segments were completed.")

        # Branch: concat all vs export per-segment
        if user_settings.get("process_all", True):
            concat_video_path = work_dir / "concatenated.mp4"
            if len(processed_segment_paths) > 1:
                concat_list_path = work_dir / "final_concat_list.txt"
                with open(concat_list_path, "w", encoding="utf-8") as f:
                    for p in processed_segment_paths:
                        f.write(f"file '{_ffconcat_escape(p)}'\n")
                command = f"ffmpeg -f concat -safe 0 -i \"{concat_list_path}\" -c copy \"{concat_video_path}\""
                if not run_ffmpeg_command(command, **kwargs): raise Exception("Final concatenation failed.")
            else:
                if processed_segment_paths: shutil.copy(processed_segment_paths[0], concat_video_path)
                else: raise Exception("No segments processed to create final video.")

            if stop_event.is_set(): raise InterruptedError("Processing stopped by user.")

            # Hitung timing BGM jika disetel untuk segmen tertentu
            bgm_segment = user_settings.get("bgm_segment")
            if user_settings.get("bgm_path") and bgm_segment and bgm_segment in segment_order:
                idx = segment_order.index(bgm_segment)
                start_sec = 0.0
                for p in processed_segment_paths[:idx]:
                    d = get_duration(str(p)) or 0.0
                    start_sec += float(d)
                vo_map = user_settings.get("_vo_audio_map") or {}
                target_vo_path = vo_map.get(bgm_segment)
                duration_sec = None
                if target_vo_path:
                    duration_sec = get_duration(target_vo_path)
                if not duration_sec:
                    duration_sec = get_duration(str(processed_segment_paths[idx]))
                if duration_sec:
                    user_settings["bgm_timing"] = {"start_sec": float(start_sec), "duration_sec": float(duration_sec)}

            final_video_path = user_settings.get("output_path")
            progress_callback("--- Applying final effects (BGM, Volume, etc.) ---")
            if not _apply_final_effects(concat_video_path, final_video_path, user_settings, **kwargs):
                raise Exception("Failed to apply final effects.")

            return final_video_path
        else:
            # Export per segment without concatenation
            out_paths = []
            out_dir = pathlib.Path(user_settings.get("output_path")).parent
            base_stem = pathlib.Path(user_settings.get("output_path")).stem.replace("_recap", "")
            vo_map = user_settings.get("_vo_audio_map") or {}

            for seg_label, seg_path in zip(segment_order, processed_segment_paths):
                if stop_event.is_set(): raise InterruptedError("Processing stopped by user.")
                per_out = out_dir / f"{base_stem}_{seg_label}.mp4"
                # Per-segment BGM timing: if bgm configured for this seg, start at 0, duration = VO length or segment length
                local_settings = dict(user_settings)
                bgm_segment = user_settings.get("bgm_segment")
                if user_settings.get("bgm_path") and bgm_segment == seg_label:
                    duration_sec = None
                    if vo_map.get(seg_label):
                        duration_sec = get_duration(vo_map.get(seg_label))
                    if not duration_sec:
                        duration_sec = get_duration(str(seg_path))
                    if duration_sec:
                        local_settings["bgm_timing"] = {"start_sec": 0.0, "duration_sec": float(duration_sec)}
                else:
                    # remove timing so global BGM (whole segment) applies, or none
                    local_settings.pop("bgm_timing", None)

                progress_callback(f"--- Applying final effects for segment '{seg_label}' ---")
                if not _apply_final_effects(seg_path, str(per_out), local_settings, **kwargs):
                    raise Exception(f"Failed to apply final effects for segment {seg_label}.")
                out_paths.append(str(per_out))

            return out_paths

    except InterruptedError as e:
        progress_callback(f"STOPPED: {e}")
        return None
    except Exception as e:
        progress_callback(f"ERROR in video processing: {e}")
        import traceback
        progress_callback(traceback.format_exc())
        return None
    finally:
        progress_callback("--- Cleaning up temporary files ---")
        if work_dir.exists():
            shutil.rmtree(work_dir)
            progress_callback("Cleanup complete.")
