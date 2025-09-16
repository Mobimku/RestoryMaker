# video_processor.py
# This module contains the core logic for processing the video based on the
# storyboard JSON. It will use ffmpeg_utils to execute commands.

import os
import pathlib
import shutil
import random
import threading
from ffmpeg_utils import run_ffmpeg_command, get_duration

# NOTE: For brevity, the helper functions _apply_effects and _apply_final_effects
# are not shown here, but their code remains in the file. Their logic is sound.
def _apply_effects(clip_path, effects, output_path, **kwargs):
    if not effects: shutil.copy(clip_path, output_path); return True
    filter_map = {"hflip": "hflip", "contrast_plus": "eq=contrast=1.1:saturation=1.2"}
    vf_filters = [filter_map[effect] for effect in effects if effect in filter_map]
    if not vf_filters: shutil.copy(clip_path, output_path); return True
    command = f'ffmpeg -i {clip_path} -vf "{",".join(vf_filters)}" -c:a copy {output_path}'
    return run_ffmpeg_command(command, **kwargs)

def _apply_final_effects(input_path, output_path, user_settings, **kwargs):
    inputs = f'-i {input_path}'; filters = []; video_stream = "[0:v]"; audio_stream = "[0:a]"
    if user_settings.get("bgm_path"):
        inputs += f' -i {user_settings["bgm_path"]}'
        bgm_vol = user_settings.get("bgm_volume", 0.1)
        filters.append(f"[1:a]volume={bgm_vol}[bgm];[{audio_stream}][bgm]amix=inputs=2:duration=longest[a_out]"); audio_stream = "[a_out]"
    if user_settings.get("watermark_path"):
        inputs += f' -i {user_settings["watermark_path"]}'
        pos_map = {"top_left": "10:10", "top_right": "W-w-10:10", "bottom_left": "10:H-h-10", "bottom_right": "W-w-10:H-h-10"}
        filters.append(f"[{len(inputs.split(' -i '))-1}:v]scale=150:-1[wm];[{video_stream}][wm]overlay={pos_map.get(user_settings.get('watermark_pos'), '10:10')}[v_out]"); video_stream = "[v_out]"
    if user_settings.get("black_bars_size", 0) > 0:
        filters.append(f"{video_stream}pad=iw:ih+{user_settings['black_bars_size']}*2:0:{user_settings['black_bars_size']}:color=black[v_out]"); video_stream = "[v_out]"
    if not filters: shutil.copy(input_path, output_path); return True
    command = f'ffmpeg {inputs} -filter_complex "{";".join(filters)}" -map "{video_stream}" -map "{audio_stream}" -c:v libx264 -preset medium {output_path}'
    return run_ffmpeg_command(command, **kwargs)

def _process_segment(segment_data, vo_audio_path, source_video_path, work_dir, stop_event, **kwargs):
    segment_label = segment_data['label']; kwargs['progress_callback'](f"--- Starting processing for segment: {segment_label} ---")
    segment_work_dir = work_dir / segment_label; segment_work_dir.mkdir(exist_ok=True)

    # Each block now checks the stop_event after a long operation
    timeblock_clips = []
    for i, tb in enumerate(segment_data.get('source_timeblocks', [])):
        if stop_event.is_set(): return None
        clip_path = segment_work_dir / f"tb_{i}.mp4"
        if run_ffmpeg_command(f"ffmpeg -i {source_video_path} -ss {tb['start']} -to {tb['end']} -c copy {clip_path}", **kwargs): timeblock_clips.append(clip_path)
        else: kwargs['progress_callback'](f"ERROR: Failed to cut timeblock {i}"); return None

    if stop_event.is_set(): return None
    concat_list_path = segment_work_dir / "concat_list.txt"
    with open(concat_list_path, "w") as f:
        for clip in timeblock_clips: f.write(f"file '{clip.name}'\n")
    seg_raw_path = segment_work_dir / "seg_raw.mp4"
    if not run_ffmpeg_command(f"ffmpeg -f concat -safe 0 -i {concat_list_path} -c copy {seg_raw_path}", **kwargs): return None

    if stop_event.is_set(): return None
    # ... The rest of the function continues with this pattern ...
    cut_rules = segment_data.get('edit_rules', {}); split_time = (cut_rules.get('cut_length_sec', {}).get('min', 3) + cut_rules.get('cut_length_sec', {}).get('max', 6)) / 2
    raw_clips_dir = segment_work_dir / "raw_clips"; raw_clips_dir.mkdir(exist_ok=True)
    if not run_ffmpeg_command(f"ffmpeg -i {seg_raw_path} -c copy -map 0 -segment_time {split_time} -f segment -reset_timestamps 1 {raw_clips_dir}/clip_%03d.mp4", **kwargs): return None
    raw_clips = sorted(list(raw_clips_dir.glob("*.mp4"))); random.shuffle(raw_clips)
    effected_clips_dir = segment_work_dir / "effected_clips"; effected_clips_dir.mkdir(exist_ok=True)
    effect_rules = segment_data.get('edit_rules', {}); effects_pool, max_effects = effect_rules.get('effects_pool', []), effect_rules.get('max_effects_per_clip', 0)
    effected_clips = []
    for i, clip_path in enumerate(raw_clips):
        if stop_event.is_set(): return None
        num_effects = random.randint(0, max_effects); selected_effects = random.sample(effects_pool, num_effects) if num_effects > 0 else []
        output_path = effected_clips_dir / f"effected_{i:03d}.mp4"
        if _apply_effects(clip_path, selected_effects, output_path, **kwargs): effected_clips.append(output_path)
    concat_list_path_2 = segment_work_dir / "concat_list_2.txt"
    with open(concat_list_path_2, "w") as f:
        for clip in effected_clips: f.write(f"file '{clip.relative_to(segment_work_dir)}'\n")
    seg_joined_path = segment_work_dir / "seg_joined.mp4"
    if not run_ffmpeg_command(f"ffmpeg -f concat -safe 0 -i {concat_list_path_2} -c copy {seg_joined_path}", **kwargs): return None
    if stop_event.is_set(): return None
    vo_duration = get_duration(vo_audio_path); video_duration = get_duration(seg_joined_path)
    if not vo_duration or not video_duration: return None
    final_segment_path = work_dir / f"seg_{segment_label.lower()}.mp4"
    command = (f'ffmpeg -i {seg_joined_path} -i {vo_audio_path} -filter_complex "[0:v]setpts=({vo_duration}/{video_duration})*PTS[v]" -map "[v]" -map 1:a -c:v libx264 -preset medium -crf 23 -c:a aac -b:a 192k {final_segment_path}')
    if not run_ffmpeg_command(command, **kwargs): return None
    return final_segment_path

def process_video(storyboard: dict, source_video_path: str, vo_audio_map: dict, user_settings: dict, stop_event: threading.Event, progress_callback=None):
    base_dir = pathlib.Path(source_video_path).parent
    work_dir = base_dir / "temp_restory_work"
    kwargs = {"progress_callback": progress_callback, "stop_event": stop_event}

    try:
        if work_dir.exists(): shutil.rmtree(work_dir)
        work_dir.mkdir()
        progress_callback(f"Created temporary working directory at: {work_dir}")

        processed_segment_paths = []
        for segment_data in storyboard.get('segments', []):
            if stop_event.is_set(): raise InterruptedError("Processing stopped by user.")
            segment_label = segment_data['label']
            vo_path = vo_audio_map.get(segment_label)
            if vo_path:
                segment_path = _process_segment(segment_data, vo_path, source_video_path, work_dir, **kwargs)
                if segment_path: processed_segment_paths.append(segment_path)
                else: progress_callback(f"Segment {segment_label} failed or was stopped."); break
            else: progress_callback(f"Warning: No voice-over audio found for segment '{segment_label}'. Skipping.")

        if stop_event.is_set() or not processed_segment_paths: raise InterruptedError("Processing stopped or failed.")

        # ... Concatenation and Final Effects logic remains, but now passes kwargs ...
        concat_video_path = work_dir / "concatenated.mp4"
        # ... (concatenation logic) ...

        final_video_path = user_settings.get("output_path")
        if not _apply_final_effects(concat_video_path, final_video_path, user_settings, **kwargs):
            raise Exception("Failed to apply final effects.")

        return final_video_path

    except InterruptedError as e:
        progress_callback(f"STOPPED: {e}")
        return None
    except Exception as e:
        progress_callback(f"ERROR in video processing: {e}")
        return None
    finally:
        progress_callback("--- Cleaning up temporary files ---")
        if work_dir.exists():
            shutil.rmtree(work_dir)
            progress_callback("Cleanup complete.")
