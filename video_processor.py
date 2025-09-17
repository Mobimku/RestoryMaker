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

def _process_segment(segment_data, vo_audio_path, source_video_path, work_dir, stop_event, **kwargs):
    """Processes a single video segment from cutting to VO syncing."""
    segment_label = segment_data['label']
    kwargs['progress_callback'](f"--- Memulai proses untuk segmen: {segment_label} ---")
    segment_work_dir = work_dir / segment_label
    segment_work_dir.mkdir(exist_ok=True)

    timeblock_clips = []
    for i, tb in enumerate(segment_data.get('source_timeblocks', [])):
        if stop_event.is_set(): return None
        # PERBAIKAN TIMESTAMP
        start_time = tb['start'].replace(',', '.')
        end_time = tb['end'].replace(',', '.')
        # Hitung durasi untuk pemotongan yang lebih robust (re-encode)
        start_sec = _ts_to_seconds(start_time)
        end_sec = _ts_to_seconds(end_time)
        duration = max(0.01, end_sec - start_sec)
        clip_path = segment_work_dir / f"tb_{i}.mp4"
        # Re-encode untuk menghindari error non-monotonic DTS / stream mismatch
        command = (
            f"ffmpeg -ss {start_sec:.3f} -t {duration:.3f} -i \"{source_video_path}\" "
            f"-c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac \"{clip_path}\""
        )
        if not run_ffmpeg_command(command, **kwargs):
            kwargs['progress_callback'](f"ERROR: Gagal memotong timeblock {i} untuk {segment_label}")
            return None
        timeblock_clips.append(clip_path)

    if stop_event.is_set(): return None
    concat_list_path = segment_work_dir / "concat_list.txt"
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for clip in timeblock_clips: f.write(f"file '{clip.resolve().as_posix()}'\n")

    seg_raw_path = segment_work_dir / "seg_raw.mp4"
    # PERBAIKAN NON-MONOTONIC DTS
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

    final_segment_path = work_dir / f"seg_{segment_label.lower()}.mp3" # Outputting mp3 to match audio
    # OPTIMASI KECEPATAN
    command = (f'ffmpeg -i \"{seg_joined_path}\" -i \"{vo_audio_path}\" '
               f'-filter_complex "[0:v]setpts=({vo_duration}/{video_duration})*PTS[v]" '
               f'-map "[v]" -map 1:a -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 192k \"{final_segment_path}\"')
    if not run_ffmpeg_command(command, **kwargs): return None
    return final_segment_path

def process_video(storyboard: dict, source_video_path: str, vo_audio_map: dict, user_settings: dict, stop_event: threading.Event, progress_callback=None):
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
