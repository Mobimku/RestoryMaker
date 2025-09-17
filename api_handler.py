# api_handler.py
# This module will handle all interactions with external APIs,
# specifically the Google Gemini API for storyboard and speech generation.

import google.generativeai as genai
import os
import json
import pathlib
import time
import re
import traceback
import wave
import struct
import subprocess

PROMPT_FILE = pathlib.Path(__file__).parent / "prompt_storyboard.md"

def get_storyboard_from_srt(srt_path: str, api_key: str, film_duration: int, output_folder: str, language: str = "en", progress_callback=None):
    """
    Uploads the original SRT file, constructs a prompt using the file reference,
    and returns the storyboard JSON from Gemini.
    """
    def log(msg):
        if progress_callback: progress_callback(msg)
        else: print(msg)

    uploaded_file = None
    try:
        genai.configure(api_key=api_key)

        log(f"Uploading SRT file: {srt_path}...")
        uploaded_file = genai.upload_file(path=srt_path)
        log(f"Successfully uploaded file: {uploaded_file.name}")

        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"}
        ]
        generation_config = {"temperature": 0.7, "top_p": 0.8, "top_k": 40, "max_output_tokens": 8192}

        model = genai.GenerativeModel(
            model_name="gemini-2.5-pro",
            generation_config=generation_config,
            safety_settings=safety_settings
        )

        system_prompt = PROMPT_FILE.read_text(encoding='utf-8') if PROMPT_FILE.exists() else "You are a helpful AI."
        system_prompt = system_prompt.replace("{durasi_film}", str(film_duration // 60)).replace("{lang}", language)

        prompt_parts = [system_prompt, "\n\n---\n\n## SRT FILE INPUT:\n", uploaded_file]

        log("Sending storyboard prompt to Gemini API with file reference...")
        response = model.generate_content(prompt_parts, request_options={'timeout': 600})

        if not response.candidates:
            log(f"ERROR: Prompt blocked. Feedback: {response.prompt_feedback}")
            return None

        raw_response_text = response.text

        debug_json_path = pathlib.Path(output_folder) / "storyboard_output.txt"
        with open(debug_json_path, "w", encoding="utf-8") as f: f.write(raw_response_text)
        log(f"Saved raw Gemini response to {debug_json_path}")

        response_text = raw_response_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        log("Parsing JSON...")
        return json.loads(response_text)

    except Exception as e:
        log(f"An error occurred calling Gemini API: {e}")
        log(traceback.format_exc())
        return None
    finally:
        if uploaded_file:
            log(f"Deleting uploaded file from service: {uploaded_file.name}")
            genai.delete_file(name=uploaded_file.name)

# --- The rest of the file is unchanged ---
def generate_vo_audio(vo_script: str, api_key: str, output_path: str, language_code: str = "en-US", progress_callback=None):
    def log(msg):
        if progress_callback: progress_callback(msg)
        else: print(msg)
    try:
        genai.configure(api_key=api_key)
        client = genai.GenerativeModel(model_name="gemini-2.5-flash-preview-tts")
        log(f"Requesting TTS from Gemini (voice: schedar)...")
        response = client.generate_content(
            contents=[vo_script],
            generation_config={
                "response_modalities": ["AUDIO"],
                "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": "schedar"}}}
            }
        )
        if response.candidates and response.candidates[0].content.parts:
            audio_data = response.candidates[0].content.parts[0].inline_data.data
            with open(output_path, "wb") as out: out.write(audio_data)
            log(f"Audio content written to file {output_path}")
            return True
        log("No audio data found in TTS response.")
        _create_silent_audio_placeholder(output_path, log_func=log)
        return True
    except Exception as e:
        log(f"An error occurred during TTS generation: {e}")
        log(traceback.format_exc())
        _create_silent_audio_placeholder(output_path, log_func=log)
        return True

def _create_silent_audio_placeholder(output_path: str, duration: float = 5.0, log_func=print):
    try:
        command = f'ffmpeg -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 -t {duration} -c:a aac -y "{output_path}"'
        subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        log_func(f"Created silent audio placeholder of {duration}s at {output_path}")
    except Exception as e:
        log_func(f"FFmpeg failed to create silent audio: {e}. Falling back to manual WAV creation.")
        sample_rate = 44100; duration_samples = int(sample_rate * duration)
        with wave.open(output_path, 'w') as wav_file:
            wav_file.setnchannels(1); wav_file.setsampwidth(2); wav_file.setframerate(sample_rate)
            for _ in range(duration_samples): wav_file.writeframes(struct.pack('<h', 0))
        log_func("Manual WAV placeholder created.")
