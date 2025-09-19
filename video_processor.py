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

def _apply_effects(clip_path, effects, output_path, **kwargs):
    """Applies a list of effects to a single clip using FFmpeg's filter_complex."""
    if not effects:
        shutil.copy(clip_path, output_path)
        return True
    filter_map = {"hflip": "hflip", "contrast_plus": "eq=contrast=1.1:saturation=1.2"}
    vf_filters = [filter_map[effect] for effect in effects if effect in filter_map]
    if not vf_filters:
        shutil.copy(clip_path, output_path)
        return True
    command = f'ffmpeg -i "{clip_path}" -vf "{",".join(vf_filters)}" -c:a copy "{output_path}"'
    return run_ffmpeg_command(command, **kwargs)

def _apply_final_effects(input_path, output_path, user_settings, **kwargs):
    """Applies final user-defined effects like BGM, volume changes, etc."""
    inputs = f'-i "{input_path}"'
    video_filters = []; audio_filters = []
    video_stream = "[0:v]"; audio_stream = "[0:a]"

    main_vol = user_settings.get("main_vo_volume", 1.0)
    if main_vol != 1.0:
        audio_filters.append(f"[{audio_stream}]volume={main_vol}[a_main_vol]")
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
    # OPTIMASI KECEPATAN
    command = (f'ffmpeg {inputs} -filter_complex "{filter_complex}" '
               f'-map "{video_stream}" -map "{audio_stream}" -c:v libx264 -preset veryfast {output_path}')
    return run_ffmpeg_command(command, **kwargs)

def auto_extend_beats_with_gaps(segment, film_duration_sec):
    """Auto-extend beats dengan gap pattern ketika beats habis"""
    beats = segment.get("beats", [])
    target_duration_sec = segment.get("target_vo_duration_sec", 0)

    if not beats:
        return segment

    # Hitung durasi beats existing
    current_duration = sum(beat.get("src_length_ms", 0) for beat in beats) / 1000.0

    if current_duration >= target_duration_sec:
        return segment

    # Kumpulkan posisi yang sudah digunakan untuk wrap-around
    used_positions = [beat.get("src_at_ms", 0) for beat in beats]

    # Cari posisi terakhir di video sumber
    last_beat = beats[-1]
    last_src_end_ms = last_beat.get("src_at_ms", 0) + last_beat.get("src_length_ms", 0)

    # Timeline position untuk beat baru
    current_timeline_ms = int(current_duration * 1000)
    current_src_ms = last_src_end_ms

    # Generate beats sampai durasi tercukupi
    import random
    while current_duration < target_duration_sec:
        # Gunakan `calculate_dynamic_gap`
        gap_sec = calculate_dynamic_gap(current_src_ms / 1000.0, film_duration_sec)
        gap_duration_ms = int(gap_sec * 1000)
        current_src_ms += gap_duration_ms

        # Gunakan `handle_film_end_wraparound`
        current_src_ms = handle_film_end_wraparound(current_src_ms, film_duration_sec, used_positions)

        # Duration klip berikutnya
        remaining_sec = target_duration_sec - current_duration
        clip_duration_ms = min(random.randint(3000, 4000), int(remaining_sec * 1000))

        if clip_duration_ms < 1000:
            break

        # Tambah beat baru
        new_beat = {
            "at_ms": current_timeline_ms,
            "src_at_ms": current_src_ms,
            "src_length_ms": clip_duration_ms,
            "note": f"Auto-extended with gap from {current_src_ms/1000:.1f}s"
        }

        beats.append(new_beat)

        # Tambahkan posisi baru ke used_positions
        used_positions.append(current_src_ms)

        # Update tracking
        current_timeline_ms += clip_duration_ms
        current_src_ms += clip_duration_ms
        current_duration += clip_duration_ms / 1000.0

    segment["beats"] = beats
    return segment

def calculate_dynamic_gap(current_position_sec, film_duration_sec):
    """Hitung gap yang dinamis berdasarkan posisi"""
    import random

    # Base gap 5-15 detik
    base_gap = random.randint(5, 15)

    # Adjust berdasarkan posisi di film
    if current_position_sec < film_duration_sec * 0.3:  # Awal film
        return random.randint(8, 20)  # Gap lebih besar
    elif current_position_sec > film_duration_sec * 0.7:  # Akhir film
        return random.randint(5, 12)  # Gap lebih kecil
    else:  # Middle
        return random.randint(6, 18)  # Gap medium

def handle_film_end_wraparound(current_src_ms, film_duration_sec, used_positions):
    """Handle ketika mendekati akhir film"""
    film_duration_ms = film_duration_sec * 1000

    if current_src_ms >= film_duration_ms - 30000:  # 30 detik dari akhir
        # Cari posisi yang belum terpakai di awal/tengah film
        import random

        # Buat list kandidat posisi (dalam chunk 30 detik)
        candidates = []
        for i in range(0, int(film_duration_ms), 30000):  # Setiap 30 detik
            chunk_start = i
            chunk_end = min(i + 30000, film_duration_ms)

            # Cek apakah chunk ini sudah banyak terpakai
            usage_count = sum(1 for pos in used_positions
                            if chunk_start <= pos <= chunk_end)

            if usage_count < 3:  # Max 3 klip per chunk 30 detik
                candidates.append(chunk_start)

        if candidates:
            return random.choice(candidates) + random.randint(0, 25000)

    return current_src_ms

def validate_gap_pattern(beats):
    """Validasi pattern gap untuk copyright safety"""
    violations = []

    for i in range(len(beats) - 1):
        current_end = beats[i]['src_at_ms'] + beats[i]['src_length_ms']
        next_start = beats[i+1]['src_at_ms']

        gap_sec = (next_start - current_end) / 1000.0

        if gap_sec < 4:  # Gap terlalu kecil
            violations.append({
                'beat_index': i,
                'gap_duration': gap_sec,
                'issue': 'Gap too small for copyright safety'
            })
        elif gap_sec > 60:  # Gap terlalu besar
            violations.append({
                'beat_index': i,
                'gap_duration': gap_sec,
                'issue': 'Gap too large, may break narrative flow'
            })

    return violations

def finalize_storyboard_with_auto_extension(storyboard, film_duration_sec):
    """Finalisasi storyboard dengan auto-extension"""

    for segment in storyboard.get("segments", []):
        # Auto-extend beats jika kurang
        segment = auto_extend_beats_with_gaps(segment, film_duration_sec)

        # Validate pattern
        violations = validate_gap_pattern(segment.get("beats", []))

        if violations:
            print(f"Segment {segment['label']}: {len(violations)} gap violations")
            # Log violations tapi lanjut (gap violations tidak fatal)

        # Final validation durasi
        total_beats_duration = sum(
            beat.get("src_length_ms", 0) for beat in segment.get("beats", [])
        ) / 1000.0

        target = segment.get("target_vo_duration_sec", 0)
        if abs(total_beats_duration - target) > 5:  # Tolerance 5 detik
            print(f"WARNING: {segment['label']} beats duration {total_beats_duration:.1f}s != target {target}s")

    return storyboard


def _process_segment_from_beats(segment_data, vo_audio_path, source_video_path, work_dir, film_duration_sec, stop_event, **kwargs):
    """
    Processes a single video segment based on a list of 'beats'.
    This method supports auto-extending beats to match VO duration.
    """
    segment_label = segment_data['label']
    progress_callback = kwargs.get('progress_callback', lambda msg: print(msg))
    progress_callback(f"--- Memulai proses segmen (mode Beats): {segment_label} ---")

    segment_work_dir = work_dir / segment_label
    segment_work_dir.mkdir(exist_ok=True)

    # 1. Dapatkan durasi VO untuk target perpanjangan
    vo_duration = get_duration(vo_audio_path)
    if not vo_duration:
        progress_callback(f"ERROR: Tidak dapat membaca durasi dari VO: {vo_audio_path}")
        return None
    segment_data['target_vo_duration_sec'] = vo_duration

    # 2. Auto-extend beats jika perlu
    segment_data = auto_extend_beats_with_gaps(segment_data, film_duration_sec)

    # 3. Validasi pola gap
    violations = validate_gap_pattern(segment_data.get("beats", []))
    if violations:
        progress_callback(f"WARNING: Segment {segment_label} memiliki {len(violations)} pelanggaran pola gap.")
        for v in violations:
            progress_callback(f"  - Beat {v['beat_index']}: {v['issue']} (durasi: {v['gap_duration']:.2f}s)")

    beats = segment_data.get("beats", [])
    if not beats:
        progress_callback(f"ERROR: Tidak ada beats untuk diproses di segmen {segment_label}.")
        return None

    # 4. Potong, proses efek, dan kumpulkan klip dari setiap beat
    processed_clips = []
    clips_dir = segment_work_dir / "beat_clips"
    clips_dir.mkdir(exist_ok=True)
    effected_clips_dir = segment_work_dir / "effected_beat_clips"
    effected_clips_dir.mkdir(exist_ok=True)

    edit_rules = segment_data.get('edit_rules', {})
    effects_pool = edit_rules.get('effects_pool', [])
    max_effects = edit_rules.get('max_effects_per_clip', 0)

    for i, beat in enumerate(beats):
        if stop_event.is_set(): return None

        start_sec = beat['src_at_ms'] / 1000.0
        duration_sec = beat['src_length_ms'] / 1000.0

        clip_path = clips_dir / f"beat_{i:03d}.mp4"

        # Potong klip dari sumber
        command = (
            f"ffmpeg -ss {start_sec:.3f} -t {duration_sec:.3f} -i \"{source_video_path}\" "
            f"-c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac \"{clip_path}\""
        )
        if not run_ffmpeg_command(command, **kwargs):
            progress_callback(f"ERROR: Gagal memotong beat {i} untuk {segment_label}")
            continue

        # Terapkan efek
        num_effects = random.randint(0, max_effects)
        selected_effects = random.sample(effects_pool, num_effects) if num_effects > 0 and effects_pool else []
        effected_path = effected_clips_dir / f"effected_beat_{i:03d}.mp4"
        if _apply_effects(clip_path, selected_effects, effected_path, **kwargs):
            processed_clips.append(effected_path)

    if stop_event.is_set() or not processed_clips: return None

    # 5. Gabungkan semua klip yang telah diproses
    concat_list_path = segment_work_dir / "beats_concat_list.txt"
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for clip in processed_clips:
            f.write(f"file '{clip.resolve().as_posix()}'\n")

    seg_beats_joined_path = segment_work_dir / "seg_beats_joined.mp4"
    concat_command = (
        f"ffmpeg -f concat -safe 0 -i \"{concat_list_path}\" "
        f"-c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac \"{seg_beats_joined_path}\""
    )
    if not run_ffmpeg_command(concat_command, **kwargs):
        progress_callback(f"ERROR: Gagal menggabungkan klip dari beats untuk {segment_label}")
        return None

    # 6. Gabungkan video hasil gabungan dengan audio VO (tanpa peregangan waktu)
    final_segment_path = work_dir / f"seg_{segment_label.lower()}.mp4"
    command = (
        f'ffmpeg -i "{seg_beats_joined_path}" -i "{vo_audio_path}" '
        f'-c:v copy -c:a aac -map 0:v:0 -map 1:a:0 -shortest "{final_segment_path}"'
    )
    if not run_ffmpeg_command(command, **kwargs):
        progress_callback(f"ERROR: Gagal menggabungkan video beats dengan audio VO untuk {segment_label}")
        return None

    return final_segment_path


def _process_segment(segment_data, vo_audio_path, source_video_path, work_dir, stop_event, **kwargs):
    """
    Processes a single video segment by dispatching to the appropriate
    method based on the segment_data structure.
    """
    # Check if this segment should be processed using the new "beats" logic
    if segment_data.get("beats"):
        film_duration_sec = kwargs.get('film_duration_sec')
        if not film_duration_sec:
            # Fallback to getting duration if not passed, for safety
            film_duration_sec = get_duration(source_video_path)

        return _process_segment_from_beats(
            segment_data, vo_audio_path, source_video_path, work_dir,
            film_duration_sec, stop_event, **kwargs
        )

    # --- Fallback to original timeblock-based processing ---
    segment_label = segment_data['label']
    progress_callback = kwargs.get('progress_callback', lambda msg: print(msg))
    progress_callback(f"--- Memulai proses segmen (mode Timeblocks): {segment_label} ---")

    segment_work_dir = work_dir / segment_label
    segment_work_dir.mkdir(exist_ok=True)

    timeblock_clips = []
    for i, tb in enumerate(segment_data.get('source_timeblocks', [])):
        if stop_event.is_set(): return None
        start_time = tb['start'].replace(',', '.')
        end_time = tb['end'].replace(',', '.')
        start_sec = _ts_to_seconds(start_time)
        end_sec = _ts_to_seconds(end_time)
        duration = max(0.01, end_sec - start_sec)
        clip_path = segment_work_dir / f"tb_{i}.mp4"
        command = (
            f"ffmpeg -ss {start_sec:.3f} -t {duration:.3f} -i \"{source_video_path}\" "
            f"-c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac \"{clip_path}\""
        )
        if not run_ffmpeg_command(command, **kwargs):
            progress_callback(f"ERROR: Gagal memotong timeblock {i} untuk {segment_label}")
            return None
        timeblock_clips.append(clip_path)

    if not timeblock_clips:
        progress_callback(f"Warning: Tidak ada timeblocks yang valid untuk segmen {segment_label}. Melewati.")
        return None

    if stop_event.is_set(): return None
    concat_list_path = segment_work_dir / "concat_list.txt"
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for clip in timeblock_clips: f.write(f"file '{clip.resolve().as_posix()}'\n")

    seg_raw_path = segment_work_dir / "seg_raw.mp4"
    concat_command_1 = (f"ffmpeg -f concat -safe 0 -i \"{concat_list_path}\" "
                        f"-c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac \"{seg_raw_path}\"")
    if not run_ffmpeg_command(concat_command_1, **kwargs): return None

    if stop_event.is_set(): return None
    cut_rules = segment_data.get('edit_rules', {})
    split_time = (cut_rules.get('cut_length_sec', {}).get('min', 3) + cut_rules.get('cut_length_sec', {}).get('max', 4)) / 2
    raw_clips_dir = segment_work_dir / "raw_clips"; raw_clips_dir.mkdir(exist_ok=True)
    if not run_ffmpeg_command(f"ffmpeg -i \"{seg_raw_path}\" -c copy -map 0 -segment_time {split_time} -f segment -reset_timestamps 1 \"{raw_clips_dir / 'clip_%03d.mp4'}\"", **kwargs): return None

    raw_clips = sorted(list(raw_clips_dir.glob("*.mp4"))); random.shuffle(raw_clips)
    effected_clips_dir = segment_work_dir / "effected_clips"; effected_clips_dir.mkdir(exist_ok=True)
    effect_rules = segment_data.get('edit_rules', {}); effects_pool, max_effects = effect_rules.get('effects_pool', []), effect_rules.get('max_effects_per_clip', 0)
    effected_clips = []
    for i, clip_path in enumerate(raw_clips):
        if stop_event.is_set(): return None
        num_effects = random.randint(0, max_effects); selected_effects = random.sample(effects_pool, num_effects) if num_effects > 0 else []
        output_path = effected_clips_dir / f"effected_{i:03d}.mp4"
        if _apply_effects(clip_path, selected_effects, output_path, **kwargs): effected_clips.append(output_path)

    if not effected_clips:
        progress_callback(f"Warning: Tidak ada klip yang berhasil diproses dengan efek untuk {segment_label}. Melewati.")
        return None

    if stop_event.is_set(): return None
    concat_list_path_2 = segment_work_dir / "concat_list_2.txt"
    with open(concat_list_path_2, "w", encoding="utf-8") as f:
        for clip in effected_clips: f.write(f"file '{clip.resolve().as_posix()}'\n")
    seg_joined_path = segment_work_dir / "seg_joined.mp4"
    concat_command_2 = (f"ffmpeg -f concat -safe 0 -i \"{concat_list_path_2}\" "
                        f"-c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac \"{seg_joined_path}\"")
    if not run_ffmpeg_command(concat_command_2, **kwargs): return None

    if stop_event.is_set(): return None
    vo_duration = get_duration(vo_audio_path); video_duration = get_duration(seg_joined_path)
    if not vo_duration or not video_duration: return None

    final_segment_path = work_dir / f"seg_{segment_label.lower()}.mp4" # FIX: Outputting mp4
    command = (f'ffmpeg -i \"{seg_joined_path}\" -i \"{vo_audio_path}\" '
               f'-filter_complex "[0:v]setpts=({vo_duration}/{video_duration})*PTS[v]" '
               f'-map "[v]" -map 1:a -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 192k "{final_segment_path}"')
    if not run_ffmpeg_command(command, **kwargs): return None
    return final_segment_path

def process_video(storyboard: dict, source_video_path: str, vo_audio_map: dict, user_settings: dict, film_duration_sec: float, stop_event: threading.Event, progress_callback=None):
    base_dir = pathlib.Path(source_video_path).parent
    work_dir = base_dir / "temp_restory_work"
    kwargs = {"progress_callback": progress_callback}

    try:
        if work_dir.exists(): shutil.rmtree(work_dir)
        work_dir.mkdir()
        progress_callback(f"Created temporary working directory at: {work_dir}")

        processed_segment_paths = []
        segment_order = []
        selected_segments = user_settings.get("selected_segments", [])

        # Pass film_duration_sec to kwargs to be available in _process_segment
        kwargs['film_duration_sec'] = film_duration_sec

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
                    for p in processed_segment_paths: f.write(f"file '{p.resolve().as_posix()}'\n")
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
