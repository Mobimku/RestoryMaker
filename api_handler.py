# api_handler.py
# This module will handle all interactions with external APIs,
# specifically the Google Gemini API for storyboard and speech generation.

import google.generativeai as genai
import json
import pathlib
import time
import re
import traceback
import wave
import struct
import subprocess

PROMPT_FILE = pathlib.Path(__file__).parent / "prompt_storyboard.md"

def get_storyboard_from_srt(srt_content: str, api_key: str, film_duration: int, output_folder: str, language: str = "en", progress_callback=None):
    """
    Takes SRT content, constructs a prompt, and returns the storyboard JSON from Gemini.
    Also saves the raw response for debugging.
    Includes improved error handling for safety blocks and other API issues.
    """
    def log(msg):
        if progress_callback:
            progress_callback(msg)
        else:
            print(msg)

    raw_response_text = ""
    try:
        genai.configure(api_key=api_key)

        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"}
        ]

        generation_config = {
            "temperature": 0.7, "top_p": 0.8, "top_k": 40, "max_output_tokens": 8192
        }

        model = genai.GenerativeModel(
            model_name="gemini-2.5-pro",
            generation_config=generation_config,
            safety_settings=safety_settings
        )

        system_prompt = PROMPT_FILE.read_text(encoding='utf-8') if PROMPT_FILE.exists() else "You are a helpful AI."
        system_prompt = system_prompt.replace("{durasi_film}", str(film_duration // 60)).replace("{lang}", language)

        sanitized_srt = _sanitize_srt_content(srt_content, log)

        prompt_parts = [
            system_prompt,
            "\n\n---\n\n## SRT CONTENT INPUT:\n",
            sanitized_srt,
            "\n\n---\n\nPlease analyze this subtitle content and create a storyboard JSON for video editing."
        ]

        log("Sending storyboard prompt to Gemini API (model: gemini-2.5-pro)...")

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = model.generate_content(prompt_parts)

                if not response.candidates:
                    log(f"Prompt blocked. Feedback: {response.prompt_feedback}")
                    if attempt < max_retries - 1:
                        log("Attempting to rephrase and retry...")
                        prompt_parts = _create_neutral_prompt(sanitized_srt, film_duration, language)
                        time.sleep(2)
                        continue
                    else:
                        log("ERROR: All retry attempts failed due to safety blocks.")
                        return None

                candidate = response.candidates[0]
                if candidate.finish_reason.name != "STOP":
                    log(f"WARNING: Generation finished with reason: {candidate.finish_reason.name}")
                    if candidate.finish_reason.name == "MAX_TOKENS":
                        log("ERROR: The model ran out of tokens. The input SRT is likely too long.")
                    # Continue anyway to save the partial output

                if candidate.content and candidate.content.parts:
                    raw_response_text = candidate.content.parts[0].text
                    break
                else:
                    log("ERROR: No content in response.")
                    return None

            except Exception as e:
                log(f"Attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                else:
                    raise

        if not raw_response_text:
            log("ERROR: Failed to get valid response after all retries.")
            return None

        debug_json_path = pathlib.Path(output_folder) / "storyboard_output.txt"
        with open(debug_json_path, "w", encoding="utf-8") as f:
            f.write(raw_response_text)
        log(f"Saved raw Gemini response to {debug_json_path}")

        response_text = raw_response_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        log("Parsing JSON...")
        try:
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            log(f"JSON parsing failed: {e}. Attempting to extract from text.")
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    log("Failed to parse extracted JSON.")
            return None

    except Exception as e:
        log(f"An error occurred while calling the Gemini API for storyboard: {e}")
        log(traceback.format_exc())
        return None

def _sanitize_srt_content(srt_content: str, log_func) -> str:
    """Sanitizes SRT content to reduce potential safety triggers."""
    srt_content = re.sub(r'\b[A-Z]{3,}\b', lambda m: m.group().title(), srt_content)
    srt_content = re.sub(r'[!]{2,}', '!', srt_content)
    srt_content = re.sub(r'[?]{2,}', '?', srt_content)
    if len(srt_content) > 50000:
        log_func("SRT content is very long, truncating...")
        srt_content = srt_content[:50000] + "\n\n[Content truncated]"
    return srt_content

def _create_neutral_prompt(srt_content: str, film_duration: int, language: str) -> list:
    """Creates a more neutral prompt."""
    neutral_prompt = f"""Analyze the following subtitles and create a JSON structure for video editing.
The video is {film_duration // 60} minutes long, in {language}.
Format: {{"segments": [{{"label": "...", "vo_script": "...", "source_timeblocks": [{{"start": "...", "end": "..."}}]}}]}}
Create segments for: Intro, Rising, Mid-conflict, Climax, Ending."""
    return [neutral_prompt, "\n\nSubtitle content:\n", srt_content]

def generate_vo_audio(vo_script: str, api_key: str, output_path: str, language_code: str = "en-US", progress_callback=None):
    """Generate voice-over audio using Gemini TTS."""
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
                "speech_config": {
                    "voice_config": {"prebuilt_voice_config": {"voice_name": "schedar"}}
                }
            }
        )

        if response.candidates and response.candidates[0].content.parts:
            audio_data = response.candidates[0].content.parts[0].inline_data.data
            with open(output_path, "wb") as out:
                out.write(audio_data)
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
    """Creates a silent audio file as a placeholder."""
    try:
        command = f'ffmpeg -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 -t {duration} -c:a aac -y "{output_path}"'
        subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        log_func(f"Created silent audio placeholder of {duration}s at {output_path}")
    except Exception as e:
        log_func(f"FFmpeg failed to create silent audio: {e}. Falling back to manual WAV creation.")
        sample_rate = 44100
        duration_samples = int(sample_rate * duration)
        with wave.open(output_path, 'w') as wav_file:
            wav_file.setnchannels(1); wav_file.setsampwidth(2); wav_file.setframerate(sample_rate)
            for _ in range(duration_samples):
                wav_file.writeframes(struct.pack('<h', 0))
        log_func("Manual WAV placeholder created.")
