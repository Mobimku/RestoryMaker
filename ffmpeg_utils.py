# ffmpeg_utils.py
# A helper module to execute FFmpeg commands reliably.
# It will handle running subprocesses, capturing output for the GUI log,
# and potentially monitoring progress.

import subprocess
import shlex
import json

def get_duration(media_path: str):
    """
    Returns the duration of a media file in seconds using ffprobe.
    """
    command = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{media_path}"'
    try:
        result = subprocess.check_output(shlex.split(command), stderr=subprocess.STDOUT)
        return float(result)
    except Exception as e:
        print(f"Error getting duration for {media_path}: {e}")
        return None

def run_ffmpeg_command(command: str, progress_callback=None):
    """
    Executes an FFmpeg command using subprocess.

    Args:
        command (str): The FFmpeg command string.
        progress_callback (function, optional): A callback to update the GUI with progress.

    Returns:
        bool: True for success, False for failure.
    """
    # Add -y flag to automatically overwrite output files
    if "ffmpeg" in command and "-y" not in command:
        command = command.replace("ffmpeg", "ffmpeg -y")

    if progress_callback:
        progress_callback(f"Executing: {command}")
    else:
        print(f"Executing: {command}")

    try:
        process = subprocess.Popen(
            shlex.split(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding='utf-8'
        )

        for line in process.stdout:
            if progress_callback:
                progress_callback(line.strip())
            else:
                print(line.strip())

        process.wait()

        if process.returncode == 0:
            return True
        else:
            if progress_callback:
                progress_callback(f"FFmpeg command failed with return code {process.returncode}")
            else:
                print(f"FFmpeg command failed with return code {process.returncode}")
            return False

    except Exception as e:
        if progress_callback:
            progress_callback(f"An error occurred while running FFmpeg: {e}")
        else:
            print(f"An error occurred while running FFmpeg: {e}")
        return False
