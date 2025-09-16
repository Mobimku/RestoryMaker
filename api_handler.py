# api_handler.py
# This module will handle all interactions with external APIs,
# namely the Google Gemini API for storyboard generation and the
# Google Cloud TTS API for creating voice-overs.

import google.generativeai as genai
from google.cloud import texttospeech
from google.api_core import client_options
import json
import pathlib

PROMPT_FILE = pathlib.Path(__file__).parent / "prompt_storyboard.md"

def get_storyboard_from_srt(srt_content: str, api_key: str, film_duration: int, language: str = "en", progress_callback=None):
    """
    Takes SRT content, constructs a prompt, and returns the storyboard JSON from Gemini.
    """
    def log(msg):
        if progress_callback:
            progress_callback(msg)
        else:
            print(msg)

    try:
        genai.configure(api_key=api_key)
        generation_config = {
            "temperature": 0.9, "top_p": 1, "top_k": 1, "max_output_tokens": 8192,
        }
        model = genai.GenerativeModel(model_name="gemini-1.0-pro", generation_config=generation_config)

        system_prompt = PROMPT_FILE.read_text(encoding='utf-8')
        system_prompt = system_prompt.replace("{durasi_film}", str(film_duration // 60)).replace("{lang}", language)

        prompt_parts = [system_prompt, "\n\n---\n\n## SRT CONTENT INPUT:\n", srt_content]

        log("Sending prompt to Gemini API...")
        response = model.generate_content(prompt_parts)

        raw_json = response.text
        if raw_json.strip().startswith("```json"):
            raw_json = raw_json.strip()[7:-4]

        log("Successfully received storyboard from Gemini.")
        return json.loads(raw_json)

    except Exception as e:
        log(f"An error occurred while calling the Gemini API: {e}")
        return None

def generate_vo_audio(vo_script: str, api_key: str, output_path: str, language_code: str = "en-US", progress_callback=None):
    """
    Takes a voice-over script and generates a WAV audio file using Google TTS.
    """
    def log(msg):
        if progress_callback:
            progress_callback(msg)
        else:
            print(msg)

    try:
        opts = client_options.ClientOptions(api_key=api_key)
        client = texttospeech.TextToSpeechClient(client_options=opts)
        synthesis_input = texttospeech.SynthesisInput(text=vo_script)
        voice = texttospeech.VoiceSelectionParams(
            language_code=language_code, ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16
        )

        log(f"Requesting TTS for script, outputting to {output_path}...")
        response = client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )

        with open(output_path, "wb") as out:
            out.write(response.audio_content)
        log(f"Audio content written to file {output_path}")

        return True

    except Exception as e:
        log(f"An error occurred during Text-to-Speech generation: {e}")
        return False
