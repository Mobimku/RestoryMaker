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

# The prompt is now embedded directly in the script.
STORYBOARD_PROMPT_TEMPLATE = """
# 🎬 Prompt: Storyboard Maker untuk Film Recap

##PROMPT FINAL

TUGAS:
Anda adalah Storyboard Maker berbasis SRT untuk membuat recap film dan rencana video (video plan) terstruktur.
Input saya adalah subtitle SRT lengkap dan akurat dari sebuah film berdurasi ± {durasi_film}.
Keluaran Anda HARUS mengikuti skema JSON di bawah ini.

LOGIC DASAR DURASI:
- Total recap harus berdurasi antara 18–25 menit (≈ 1080–1500 detik).
- Gunakan proporsi distribusi durasi per segmen sebagai berikut:
  * Intro: 10–12% dari total recap (≈ 2.5–3 menit)
  * Rising: 28–32% dari total recap (≈ 8–9 menit)
  * Mid-conflict: 20–22% dari total recap (≈ 6–7 menit)
  * Climax: 20–22% dari total recap (≈ 6–7 menit)
  * Ending: 12–15% dari total recap (≈ 4–5 menit)
- Jangan gunakan angka kecil seperti 30–60 detik. Selalu patuhi distribusi di atas.
- Hitung target_vo_duration_sec otomatis berdasarkan distribusi ini.
- Semua VO script harus ditulis agar durasi total recap sesuai target di atas.

LANGKAH ANALISIS:
1) Baca seluruh SRT. Identifikasi struktur naratif: Intro → Rising → Mid-conflict → Climax → Ending.
2) Temukan momen penting (establishing context, inciting incident, turning points, confrontation, climax, resolution).
3) Untuk setiap babak, pilih rentang timestamp SRT yang paling representatif (boleh discontinuous, 2–5 rentang).
4) Tulis recap singkat (3–5 kalimat) per babak.

PENULISAN VO (WAJIB):
- Kalimat pertama HARUS menjadi **HOOK punchy** sesuai konteks segmen (12–18 kata).
- Gunakan kata-kata berbeda dengan makna sama; ubah struktur kalimat dari SRT.
- Pertahankan SEMUA informasi penting (jangan buang detail inti).
- Jangan menambahkan keterangan ekstra atau karakter baru.
- Jika ada kalimat terlalu panjang, gabungkan/kompres supaya lebih ringkas.
- Jangan copy-paste dialog asli, VO harus hasil narasi ulang.

PACING & WORD BUDGET (WAJIB):
- Default speech_rate_wpm: Intro 150, Rising 160, Mid-conflict 165, Climax 175, Ending 150.
- Gunakan fill_ratio = 0.90 (90% waktu kata, 10% jeda).
- Rumus target kata:
  words_target ≈ target_vo_duration_sec * (speech_rate_wpm / 60) * fill_ratio
- Tuliskan VO agar jumlah katanya mendekati words_target (±2%).
- Setelah menulis VO, hitung:
  predicted_duration_sec ≈ (words_actual / (speech_rate_wpm/60)) + (sentences * 0.30) + (commas * 0.12)
  delta_sec = predicted_duration_sec - target_vo_duration_sec
- Jika |delta_sec| > 2% → revisi VO hingga fit=OK.

RENCANA VIDEO (per segmen):
- Gunakan `source_timeblocks` dari SRT sebagai bahan visual.
- Total durasi hasil edit HARUS sama dengan durasi VO.
- Pecah visual jadi klip 3–4 detik (acak namun logis).
- Terapkan 0–2 efek per klip, pilih dari pool:
  ["crop_pan_light","zoom_light","hflip","contrast_plus","sat_plus","pip_blur_bg"].
- Hindari zoom terus-menerus.
- Sisipkan 1 transisi lembut per 20–30 detik (crossfade 0.4–0.6s).
- Tambahkan `beats` opsional (milidetik) untuk menandai penempatan VO/key visuals.

VALIDASI (WAJIB):
- Laporkan word budget di `vo_meta`:
  * speech_rate_wpm
  * fill_ratio
  * words_target
  * words_actual
  * sentences
  * commas
  * predicted_duration_sec
  * delta_sec
  * fit ("OK" atau "REWRITE")
- Pastikan fit="OK" sebelum output final.

BATASAN:
- Output WAJIB berupa JSON sesuai skema, tanpa komentar tambahan.
- Bahasa keluaran: {lang}.
- Jangan memuat catatan, penjelasan, atau format lain selain JSON.

SKEMA JSON KELUARAN:
{
  "film_meta": {
    "title": "{judul|opsional}",
    "duration_sec": {durasi_total_detik}
  },
  "recap": {
    "intro": "…",
    "rising": "…",
    "mid_conflict": "…",
    "climax": "…",
    "ending": "…"
  },
  "segments": [
    {
      "label": "Intro",
      "vo_language": "{lang}",
      "target_vo_duration_sec": {intro_vo_sec},
      "vo_script": "… narasi panjang dengan HOOK punchy …",
      "vo_meta": {
        "speech_rate_wpm": 150,
        "fill_ratio": 0.90,
        "words_target": 0,
        "words_actual": 0,
        "sentences": 0,
        "commas": 0,
        "predicted_duration_sec": 0.0,
        "delta_sec": 0.0,
        "fit": "OK"
      },
      "source_timeblocks": [
        {"start": "HH:MM:SS.mmm", "end": "HH:MM:SS.mmm", "reason": "…"}
      ],
      "edit_rules": {
        "cut_length_sec": {"min": 3.0, "max": 4.0},
        "effects_pool": ["crop_pan_light","zoom_light","hflip","contrast_plus","sat_plus","pip_blur_bg"],
        "max_effects_per_clip": 2,
        "transition_every_sec": 25,
        "transition_type": "crossfade",
        "transition_duration_sec": 0.5
      },
      "beats": [
        {"at_ms": 0, "action": "logo/titlecard optional"}
      ]
    }
  ]
}
"""

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

        system_prompt = STORYBOARD_PROMPT_TEMPLATE.replace("{durasi_film}", str(film_duration // 60)).replace("{lang}", language)

        prompt_parts = [system_prompt, "\n\n---\n\n## SRT FILE INPUT:\n", uploaded_file]

        log("Sending storyboard prompt to Gemini API with file reference...")
        response = model.generate_content(prompt_parts, request_options={'timeout': 600})

        if not response.candidates:
            log(f"ERROR: Prompt was blocked by the API. Feedback: {response.prompt_feedback}")
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
