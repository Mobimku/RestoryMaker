# api_handler.py
# This module will handle all interactions with external APIs,
# specifically the Google Gemini API for storyboard and speech generation.

import google.generativeai as genai
from google.generativeai import types
import json
import pathlib

PROMPT_FILE = pathlib.Path(__file__).parent / "prompt_storyboard.md"

def get_storyboard_from_srt(srt_content: str, api_key: str, film_duration: int, output_folder: str, language: str = "en", progress_callback=None):
    """
    Takes SRT content, constructs a prompt, and returns the storyboard JSON from Gemini.
    Also saves the raw response for debugging.
    """
    def log(msg):
        if progress_callback: progress_callback(msg)
        else: print(msg)

    raw_json = ""
    try:
        genai.configure(api_key=api_key)
        generation_config = {"temperature": 0.9, "top_p": 1, "top_k": 1, "max_output_tokens": 8192}
        model = genai.GenerativeModel(model_name="gemini-2.5-pro", generation_config=generation_config)

        system_prompt = PROMPT_FILE.read_text(encoding='utf-8')
        system_prompt = system_prompt.replace("{durasi_film}", str(film_duration // 60)).replace("{lang}", language)

        prompt_parts = [system_prompt, "\n\n---\n\n## SRT CONTENT INPUT:\n", srt_content]

        log("Sending storyboard prompt to Gemini API (model: gemini-2.5-pro)...")
        response = model.generate_content(prompt_parts)

        raw_json = response.text

        # Save the raw response for debugging BEFORE trying to parse it.
        debug_json_path = pathlib.Path(output_folder) / "storyboard_output.txt"
        with open(debug_json_path, "w", encoding="utf-8") as f:
            f.write(raw_json)
        log(f"Saved raw Gemini response to {debug_json_path}")

        if raw_json.strip().startswith("```json"):
            raw_json = raw_json.strip()[7:-4]

        log("Successfully received storyboard from Gemini. Parsing JSON...")
        return json.loads(raw_json)

    except Exception as e:
        log(f"An error occurred while calling the Gemini API for storyboard: {e}")
        # The raw_json is already saved, so the user can inspect it.
        return None

def generate_vo_audio(vo_script: str, api_key: str, output_path: str, language_code: str = "en-US", progress_callback=None):
    # ... (This function remains the same) ...
    def log(msg):
        if progress_callback: progress_callback(msg)
        else: print(msg)

    try:
        genai.configure(api_key=api_key)
        client = genai.GenerativeModel(model_name="gemini-2.5-flash-preview-tts")
        log(f"Requesting TTS from Gemini for script (voice: schedar)...")
        response = client.generate_content(
            contents=[vo_script],
            generation_config=types.GenerationConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name='schedar')
                    )
                )
            )
        )
        audio_data = response.candidates[0].content.parts[0].inline_data.data
        with open(output_path, "wb") as out:
            out.write(audio_data)
        log(f"Audio content written to file {output_path}")
        return True
    except Exception as e:
        log(f"An error occurred during Gemini TTS generation: {e}")
        import traceback
        log(traceback.format_exc())
        return False
