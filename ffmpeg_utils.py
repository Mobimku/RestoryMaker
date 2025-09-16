# ffmpeg_utils.py
# A helper module to execute FFmpeg commands reliably.
# It will handle running subprocesses, capturing output for the GUI log,
# and potentially monitoring progress.

import subprocess
import json

def get_duration(media_path: str):
    """
    Returns the duration of a media file in seconds using ffprobe.
    """
    command = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{media_path}"'
    try:
        # Using shell=True is more robust for paths with spaces on Windows.
        result = subprocess.check_output(command, stderr=subprocess.STDOUT, shell=True)
        return float(result)
    except Exception as e:
        # The user will see this print in the console, which is fine for this level of error.
        print(f"Error getting duration for {media_path}: {e}")
        return None

def run_ffmpeg_command(command: str, **kwargs):
    """
    Executes an FFmpeg command using subprocess.
    Now uses shell=True for robust path handling.
    """
    progress_callback = kwargs.get("progress_callback")

    # Add -y flag to automatically overwrite output files
    if "ffmpeg" in command and "-y" not in command:
        command = command.replace("ffmpeg", "ffmpeg -y")

    if progress_callback:
        progress_callback(f"Executing: {command}")
    else:
        print(f"Executing: {command}")

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding='utf-8',
            shell=True # Use shell=True for better path handling
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
            return False

    except Exception as e:
        if progress_callback:
            progress_callback(f"An error occurred while running FFmpeg: {e}")
        return False
