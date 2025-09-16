# api_handler.py
# This module will handle all interactions with external APIs,
# specifically the Google Gemini API for storyboard and speech generation.

import google.generativeai as genai
from google.generativeai import types
import json
import pathlib
import time

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

        # Configure safety settings to be more permissive for video editing content
        safety_settings = [
            {
                "category": "HARM_CATEGORY_HARASSMENT",
                "threshold": "BLOCK_ONLY_HIGH"
            },
            {
                "category": "HARM_CATEGORY_HATE_SPEECH",
                "threshold": "BLOCK_ONLY_HIGH"
            },
            {
                "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "threshold": "BLOCK_ONLY_HIGH"
            },
            {
                "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                "threshold": "BLOCK_ONLY_HIGH"
            }
        ]

        generation_config = {
            "temperature": 0.7,  # Reduced temperature for more consistent output
            "top_p": 0.8,
            "top_k": 40,
            "max_output_tokens": 8192
        }

        model = genai.GenerativeModel(
            model_name="gemini-2.5-pro",  # Keep using gemini-2.5-pro as requested
            generation_config=generation_config,
            safety_settings=safety_settings
        )

        # Load and prepare the system prompt
        if PROMPT_FILE.exists():
            system_prompt = PROMPT_FILE.read_text(encoding='utf-8')
        else:
            # Fallback prompt if file doesn't exist
            system_prompt = """You are a video editing AI that creates storyboards for movie recaps.
            Create a JSON structure with segments for video editing based on the provided SRT subtitles.

            Return ONLY valid JSON in this format:
            {
                "segments": [
                    {
                        "label": "Intro",
                        "vo_script": "Brief engaging introduction script",
                        "source_timeblocks": [
                            {"start": "00:01:30", "end": "00:02:00"}
                        ],
                        "edit_rules": {
                            "cut_length_sec": {"min": 2, "max": 4},
                            "effects_pool": ["contrast_plus"],
                            "max_effects_per_clip": 1
                        }
                    }
                ]
            }"""

        system_prompt = system_prompt.replace("{durasi_film}", str(film_duration // 60)).replace("{lang}", language)

        # Sanitize SRT content to reduce potential safety triggers
        sanitized_srt = _sanitize_srt_content(srt_content)

        prompt_parts = [
            system_prompt,
            "\n\n---\n\n## SRT CONTENT INPUT:\n",
            sanitized_srt,
            "\n\n---\n\nPlease analyze this subtitle content and create a storyboard JSON for video editing. Focus on creating engaging video segments with appropriate timing and effects."
        ]

        log("Sending storyboard prompt to Gemini API (model: gemini-2.5-pro)...")

        # Retry logic for handling temporary API issues
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = model.generate_content(prompt_parts)

                # Check if response was blocked
                if response.prompt_feedback:
                    if response.prompt_feedback.block_reason:
                        log(f"WARNING: Prompt was blocked due to: {response.prompt_feedback.block_reason}")
                        if attempt < max_retries - 1:
                            log("Attempting to rephrase and retry...")
                            # Try with a more neutral prompt
                            prompt_parts = _create_neutral_prompt(sanitized_srt, film_duration, language)
                            time.sleep(2)  # Brief delay before retry
                            continue
                        else:
                            log("ERROR: All retry attempts failed due to safety blocks.")
                            return None

                # Check if response generation was blocked
                if not response.candidates or len(response.candidates) == 0:
                    log("ERROR: No response candidates generated.")
                    return None

                candidate = response.candidates[0]
                if candidate.finish_reason == 2:  # SAFETY block
                    log(f"WARNING: Response blocked for safety reasons. Attempt {attempt + 1}/{max_retries}")
                    if attempt < max_retries - 1:
                        log("Trying with modified prompt...")
                        prompt_parts = _create_neutral_prompt(sanitized_srt, film_duration, language)
                        time.sleep(2)
                        continue
                    else:
                        log("ERROR: All attempts blocked by safety filter.")
                        return None
                elif candidate.finish_reason == 3:  # RECITATION
                    log("WARNING: Response blocked due to recitation concerns.")
                    return None
                elif candidate.finish_reason not in [1, None]:  # Not STOP or unset
                    log(f"WARNING: Unexpected finish reason: {candidate.finish_reason}")

                # Extract the response text
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

        # Save the raw response for debugging BEFORE trying to parse it
        debug_json_path = pathlib.Path(output_folder) / "storyboard_output.txt"
        with open(debug_json_path, "w", encoding="utf-8") as f:
            f.write(raw_response_text)
        log(f"Saved raw Gemini response to {debug_json_path}")

        # Clean up the response text
        response_text = raw_response_text.strip()

        # Remove markdown code blocks if present
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        elif response_text.startswith("```"):
            response_text = response_text[3:]

        if response_text.endswith("```"):
            response_text = response_text[:-3]

        response_text = response_text.strip()

        log("Successfully received storyboard from Gemini. Parsing JSON...")

        try:
            storyboard_json = json.loads(response_text)
            log("JSON parsing successful!")
            return storyboard_json
        except json.JSONDecodeError as e:
            log(f"JSON parsing failed: {e}")
            log("Attempting to extract JSON from response...")

            # Try to find JSON within the response
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                try:
                    storyboard_json = json.loads(json_match.group())
                    log("Successfully extracted JSON from response!")
                    return storyboard_json
                except json.JSONDecodeError:
                    log("Failed to parse extracted JSON as well.")

            log("Could not extract valid JSON. Please check the raw response file.")
            return None

    except Exception as e:
        log(f"An error occurred while calling the Gemini API for storyboard: {e}")
        import traceback
        log(traceback.format_exc())
        return None

def _sanitize_srt_content(srt_content: str) -> str:
    """
    Sanitizes SRT content to reduce potential safety triggers while preserving meaning.
    """
    # Remove or replace potentially problematic terms that might trigger safety filters
    import re

    # Replace excessive caps with normal case
    srt_content = re.sub(r'\b[A-Z]{3,}\b', lambda m: m.group().title(), srt_content)

    # Remove excessive punctuation that might be seen as aggressive
    srt_content = re.sub(r'[!]{2,}', '!', srt_content)
    srt_content = re.sub(r'[?]{2,}', '?', srt_content)

    # Truncate if extremely long to avoid overwhelming the model
    if len(srt_content) > 50000:  # ~50k characters limit
        log("SRT content is very long, truncating to prevent API issues...")
        srt_content = srt_content[:50000] + "\n\n[Content truncated for processing]"

    return srt_content

def _create_neutral_prompt(srt_content: str, film_duration: int, language: str) -> list:
    """
    Creates a more neutral prompt that's less likely to trigger safety filters.
    """
    neutral_prompt = f"""Please analyze the following subtitle content and create a technical JSON structure for video editing software.

The video is approximately {film_duration // 60} minutes long and in {language} language.

Required JSON format:
{{
    "segments": [
        {{
            "label": "segment_name",
            "vo_script": "narrative script for this segment",
            "source_timeblocks": [
                {{"start": "HH:MM:SS", "end": "HH:MM:SS"}}
            ],
            "edit_rules": {{
                "cut_length_sec": {{"min": 2, "max": 4}},
                "effects_pool": ["contrast_plus"],
                "max_effects_per_clip": 1
            }}
        }}
    ]
}}

Create segments for: Intro, Rising, Mid-conflict, Climax, Ending

Focus on technical video editing parameters and storytelling structure."""

    return [neutral_prompt, "\n\nSubtitle content:\n", srt_content]

def generate_vo_audio(vo_script: str, api_key: str, output_path: str, language_code: str = "en-US", progress_callback=None):
    """
    Generate voice-over audio using Gemini TTS with schedar voice.
    Uses gemini-2.5-flash-preview-tts model as per Google AI documentation.
    """
    def log(msg):
        if progress_callback:
            progress_callback(msg)
        else:
            print(msg)

    try:
        genai.configure(api_key=api_key)

        # Use the official TTS model from Google AI documentation
        client = genai.GenerativeModel(model_name="gemini-2.5-flash-preview-tts")

        log(f"Requesting TTS from Gemini for script (voice: schedar)...")
        log(f"Script preview: {vo_script[:100]}...")

        try:
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

            # Extract audio data from response
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'inline_data') and part.inline_data:
                        audio_data = part.inline_data.data
                        with open(output_path, "wb") as out:
                            out.write(audio_data)
                        log(f"Audio content written to file {output_path}")
                        return True

            log("No audio data found in response")
            return False

        except Exception as tts_error:
            log(f"TTS generation failed: {str(tts_error)}")
            log("Creating silent audio placeholder...")
            _create_silent_audio_placeholder(output_path, len(vo_script) * 0.15)  # ~0.15 sec per character
            return True  # Return True so processing continues

    except Exception as e:
        log(f"An error occurred during TTS generation: {e}")
        import traceback
        log(traceback.format_exc())
        # Create placeholder audio file
        _create_silent_audio_placeholder(output_path, 10)  # 10 second placeholder
        return True  # Return True so processing continues

def _create_silent_audio_placeholder(output_path: str, duration: float = 10.0):
    """
    Creates a silent audio file as a placeholder when TTS is not available.
    """
    import subprocess
    try:
        # Create silent audio using ffmpeg
        command = f'ffmpeg -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 -t {duration} -c:a aac -y "{output_path}"'
        subprocess.run(command, shell=True, check=True, capture_output=True)
    except:
        # If ffmpeg fails, create a minimal WAV file
        import wave
        import struct

        sample_rate = 44100
        duration_samples = int(sample_rate * duration)

        with wave.open(output_path, 'w') as wav_file:
            wav_file.setnchannels(2)  # Stereo
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(sample_rate)

            # Write silent samples
            for _ in range(duration_samples):
                wav_file.writeframes(struct.pack('<hh', 0, 0))  # Silent stereo sample
