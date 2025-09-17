# api_handler.py
import google.generativeai as genai
import base64
import json
import pathlib
import traceback
import subprocess
import urllib.request
import urllib.error
import time
import re

STORYBOARD_PROMPT_TEMPLATE = """
# 🎬 Prompt: Storyboard Maker untuk Film Recap

##PROMPT FINAL

TUGAS:
Anda adalah Storyboard Maker berbasis SRT untuk membuat recap film dan rencana video (video plan) terstruktur.
Input saya adalah subtitle SRT lengkap dan akurat dari sebuah film berdurasi ± {durasi_film}.
Keluaran Anda HARUS mengikuti skema JSON di bawah ini.


- PARAMETER DURASI & KATA PER SEGMEN (WAJIB DIGUNAKAN):
{CONCRETE_DURASI_KATA}

LANGKAH ANALISIS:
1) Baca seluruh SRT. Identifikasi struktur naratif: Intro → Rising → Mid-conflict → Climax → Ending.
2) Temukan momen penting (establishing context, inciting incident, turning points, confrontation, climax, resolution).
3) Untuk setiap babak, pilih rentang timestamp SRT yang paling representatif (boleh discontinuous). Jumlah rentang tidak dibatasi angka tetap; cukup untuk menyusun BEATS klip 3-4 detik hingga menutup durasi VO segmen.
4) Tulis recap ringkas per babak yang konsisten dengan target words_target per segmen (lihat PARAMETER KONKRIT); hindari patokan jumlah kalimat tetap.

PENULISAN VO (WAJIB):
- Kalimat pertama HARUS menjadi **HOOK punchy** sesuai konteks segmen (12–18 kata).
- Gunakan kata-kata berbeda dengan makna sama; ubah struktur kalimat dari SRT.
- Pertahankan SEMUA informasi penting (jangan buang detail inti).
- Jangan menambahkan keterangan ekstra atau karakter baru.
- Jika ada kalimat terlalu panjang, gabungkan/kompres supaya lebih ringkas.
- Jangan copy-paste dialog asli, VO harus hasil narasi ulang.

PACING & WORD BUDGET (WAJIB):
- Gunakan fill_ratio = 0.90 (90% waktu kata, 10% jeda).
- PARAMETER KONKRIT PER SEGMEN (WAJIB DIGUNAKAN):
{CONCRETE_VO_PARAMS}
- Tuliskan VO agar jumlah katanya mendekati words_target (±2%).
- Setelah menulis VO, hitung:
  predicted_duration_sec = (words_actual / (speech_rate_wpm/60)) + (sentences * 0.30) + (commas * 0.12)
  delta_sec = predicted_duration_sec - target_vo_duration_sec
- Jika |delta_sec| > 2% → revisi VO hingga fit=OK.

RENCANA VIDEO (per segmen):
- Gunakan `source_timeblocks` dari SRT sebagai bahan visual.
- Total durasi hasil edit HARUS sama dengan durasi VO.
- Pecah visual menjadi klip 3–4 detik secara BERURUTAN (bukan potongan kontinu panjang).
- Urutan klip mengikuti urutan narasi/VO (ascending `at_ms`) dan menjaga progresi waktu sumber (gunakan `block_index` dan posisi di timeblock secara menaik) agar visual sinkron dengan VO.
- Terapkan 0–2 efek per klip, pilih dari pool:
  ["crop_pan_light","zoom_light","hflip","contrast_plus","sat_plus","pip_blur_bg"]. Hindari zoom terus-menerus.
- Wajib menandai BEATS sebagai tulang visual (bone) untuk SETIAP klip 3–4 detik (tepat satu beat per klip):
  - Struktur beat (JSON): { "at_ms": <waktu_segm_ms>, "block_index": <idx_timeblock>, "src_at_ms": <posisi_ms_dalam_timeblock>, "src_length_ms": <durasi_klip_ms>, "note": "opsional" }
  - `src_length_ms` WAJIB berada di rentang 3000–4000 ms. Jika butuh durasi lebih panjang, pecah menjadi beberapa beat beruntun pada timeblock sama atau berikutnya — JANGAN membuat satu beat/klip kontinu > 4000 ms.
  - `block_index` mengacu ke indeks pada array `source_timeblocks`.
  - `at_ms` adalah posisi penempatan klip di timeline segmen (mulai 0), urut dan berkelanjutan hingga menutup durasi VO.
  - Urutan beats wajib konsisten dan mengikuti urutan naratif timeblocks agar visual koheren.

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
        "speech_rate_wpm": 190,
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
        {"at_ms": 0, "block_index": 0, "src_at_ms": 0, "src_length_ms": 3500, "note": "opening context"}
      ]
    }
  ]
}
"""

def get_storyboard_from_srt(
    srt_path: str,
    api_key: str,
    film_duration: int,
    output_folder: str,
    language: str = "en",
    progress_callback=None,
    recap_minutes: int | None = None,
    fast_mode: bool = False,
    storyboard_model: str | None = None,
):
    def log(msg):
        if progress_callback: progress_callback(msg)

    uploaded_file = None
    try:
        genai.configure(api_key=api_key)
        log(f"Mengunggah file SRT: {srt_path}...")
        uploaded_file = genai.upload_file(path=srt_path)
        log(f"Berhasil mengunggah file: {uploaded_file.name}")

        safety_settings = [{"category": c, "threshold": "BLOCK_NONE"} for c in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]]
        # Mode cepat menggunakan model flash dengan output JSON dan batas token lebih kecil
        allowed_models = {"gemini-2.5-flash", "gemini-2.5-pro"}
        if storyboard_model and storyboard_model in allowed_models:
            model_name = storyboard_model
            log(f"Storyboard model dipilih: {model_name}")
        else:
            model_name = "gemini-2.5-flash" if fast_mode else "gemini-2.5-pro"
            if fast_mode:
                log("Mode cepat aktif: menggunakan Gemini 2.5 Flash untuk storyboard.")
        generation_config = {
            "temperature": 0.5 if fast_mode else 0.7,
            "top_p": 0.8,
            "top_k": 40,
            # Jangan set max_output_tokens agar model memakai kapasitas default penuh
            "response_mime_type": "application/json",
        }

        model = genai.GenerativeModel(
            model_name=model_name,
            generation_config=generation_config,
            safety_settings=safety_settings
        )
        # Hitung parameter VO konkret di luar prompt
        # Target total recap berdasarkan pilihan pengguna (default 22 menit)
        total_target_sec = int((recap_minutes or 22) * 60)
        # Distribusi tegas (sesuai rentang): Intro 11%, Rising 30%, Mid 22%, Climax 22%, Ending 15%
        dist = {
            "Intro": 0.11,
            "Rising": 0.30,
            "Mid-conflict": 0.22,
            "Climax": 0.22,
            "Ending": 0.15,
        }
        # WPM tegas per segmen (dalam rentang 185–210)
        wpm_map = {
            "Intro": 190,
            "Rising": 200,
            "Mid-conflict": 200,
            "Climax": 205,
            "Ending": 190,
        }
        fill_ratio = 0.90
        secs_map = {k: round(v * total_target_sec) for k, v in dist.items()}
        words_map = {k: round(secs_map[k] * (wpm_map[k] / 60.0) * fill_ratio) for k in dist.keys()}
        vo_lines = []
        vo_dw_lines = []
        order = ["Intro", "Rising", "Mid-conflict", "Climax", "Ending"]
        for k in order:
            vo_lines.append(f"- {k}: target_vo_duration_sec={secs_map[k]}, speech_rate_wpm={wpm_map[k]}, words_target={words_map[k]}")
            menit = secs_map[k] / 60.0
            vo_dw_lines.append(f"- {k}: {secs_map[k]} detik (~{menit:.2f} menit), words_target={words_map[k]}")
        concrete_params = "\n".join(vo_lines)
        concrete_durasi_kata = "\n".join(vo_dw_lines)

        # Log ringkas agar pengguna bisa melihat angka yang dipakai
        try:
            log("=== PARAMETER DURASI & KATA PER SEGMEN ===")
            for line in vo_dw_lines: log(line)
            log("=== PARAMETER KONKRIT VO (detik / wpm / words_target) ===")
            for line in vo_lines: log(line)
        except Exception:
            pass

        # Generate per segmen untuk menghindari MAX_TOKENS
        def build_segment_prompt(label: str, vo_sec: int, wpm: int, words: int) -> str:
            return (
                "# Segmen Storyboard (JSON saja)\n"
                f"Label: {label}\n"
                f"Bahasa VO: {language}\n"
                "Instruksi: Hanya keluarkan JSON untuk SATU segmen di bawah ini, tanpa catatan tambahan.\n"
                "Wajib isi: label, vo_language, target_vo_duration_sec, vo_script, vo_meta (speech_rate_wpm, fill_ratio=0.90, words_target, words_actual, sentences, commas, predicted_duration_sec, delta_sec, fit),\n"
                "source_timeblocks, edit_rules (cut_length_sec 3-4, efek dari pool), dan beats (satu beat per klip 3-4 detik).\n"
                "Kepatuhan durasi & kata WAJIB: gunakan angka eksplisit di bawah ini.\n\n"
                "PARAMETER SEGMENT (WAJIB DIGUNAKAN):\n"
                f"- target_vo_duration_sec={vo_sec}, speech_rate_wpm={wpm}, words_target={words}\n\n"
                "Format JSON yang diminta:\n"
                "{\n"
                f"  \"label\": \"{label}\",\n"
                f"  \"vo_language\": \"{language}\",\n"
                f"  \"target_vo_duration_sec\": {vo_sec},\n"
                "  \"vo_script\": \"...\",\n"
                "  \"vo_meta\": {\n"
                f"    \"speech_rate_wpm\": {wpm},\n"
                "    \"fill_ratio\": 0.90,\n"
                f"    \"words_target\": {words},\n"
                "    \"words_actual\": 0,\n"
                "    \"sentences\": 0,\n"
                "    \"commas\": 0,\n"
                "    \"predicted_duration_sec\": 0.0,\n"
                "    \"delta_sec\": 0.0,\n"
                "    \"fit\": \"OK\"\n"
                "  },\n"
                "  \"source_timeblocks\": [ {\"start\": \"HH:MM:SS.mmm\", \"end\": \"HH:MM:SS.mmm\", \"reason\": \"...\"} ],\n"
                "  \"edit_rules\": {\n"
                "    \"cut_length_sec\": {\"min\": 3.0, \"max\": 4.0},\n"
                "    \"effects_pool\": [\"crop_pan_light\",\"zoom_light\",\"hflip\",\"contrast_plus\",\"sat_plus\",\"pip_blur_bg\"],\n"
                "    \"max_effects_per_clip\": 2,\n"
                "    \"transition_every_sec\": 25,\n"
                "    \"transition_type\": \"crossfade\",\n"
                "    \"transition_duration_sec\": 0.5\n"
                "  },\n"
                "  \"beats\": [ {\"at_ms\": 0, \"block_index\": 0, \"src_at_ms\": 0, \"src_length_ms\": 3500, \"note\": \"...\"} ]\n"
                "}\n"
            )

        # Kembali ke permintaan prompt utuh (single call)
        system_prompt = STORYBOARD_PROMPT_TEMPLATE \
            .replace("{durasi_film}", str(film_duration // 60)) \
            .replace("{durasi_total_detik}", str(int(film_duration))) \
            .replace("{lang}", language) \
            .replace("{CONCRETE_VO_PARAMS}", concrete_params) \
            .replace("{CONCRETE_DURASI_KATA}", concrete_durasi_kata) \
            .replace("{intro_vo_sec}", str(secs_map["Intro"])) \
            .replace("{rising_vo_sec}", str(secs_map["Rising"])) \
            .replace("{mid_vo_sec}", str(secs_map["Mid-conflict"])) \
            .replace("{climax_vo_sec}", str(secs_map["Climax"])) \
            .replace("{ending_vo_sec}", str(secs_map["Ending"]))

        prompt_parts = [system_prompt, "\n\n---\n\n## SRT FILE INPUT:\n", uploaded_file]
        log("Mengirim prompt storyboard (single call) ke Gemini API...")
        response = model.generate_content(prompt_parts, request_options={'timeout': 600})

        if not response.candidates:
            log(f"ERROR: Prompt diblokir oleh API. Feedback: {response.prompt_feedback}")
            return None
        candidate = response.candidates[0]
        if candidate.finish_reason.name != "STOP":
            log(f"ERROR: Respons dihentikan dengan alasan: {candidate.finish_reason.name}.")
            return None

        raw_response_text = response.text
        raw_path = pathlib.Path(output_folder) / "storyboard_output_raw.txt"
        try:
            with open(raw_path, "w", encoding="utf-8") as f: f.write(raw_response_text)
            log(f"Menyimpan respons mentah Gemini ke {raw_path}")
        except Exception:
            pass

        response_text = raw_response_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        log("Mem-parsing JSON dari respons Gemini...")
        parsed = json.loads(response_text)
        json_path = pathlib.Path(output_folder) / "storyboard_output.json"
        with open(json_path, "w", encoding="utf-8") as jf:
            json.dump(parsed, jf, ensure_ascii=False, indent=2)
        log(f"Menyimpan storyboard JSON ke {json_path}")
        return parsed
    except Exception as e:
        log(f"Terjadi error saat memanggil Gemini API: {e}")
        log(traceback.format_exc())
        return None
    finally:
        if uploaded_file:
            log(f"Menghapus file yang diunggah dari layanan: {uploaded_file.name}")
            genai.delete_file(name=uploaded_file.name)

def generate_vo_audio(
    vo_script: str,
    api_key: str,
    output_path: str,
    language_code: str = "en-US",
    voice_name: str = "",
    progress_callback=None,
    tts_device: str = "cpu",
    voice_prompt_path: str = "",
    speech_rate_wpm: int | None = None,
    max_chunk_sec: int | None = None,
):
    def log(msg):
        if progress_callback: progress_callback(msg)

    # Pilih backend: gemini (default) atau lokal (Chatterbox)
    # Pakai Gemini TTS selalu (hapus backend lokal)

    # Backend: Gemini 2.5 Flash Preview TTS dan pecah per ~3 menit
    try:
        import os, time, re, wave, tempfile
        import api_manager as _am
        genai.configure(api_key=api_key, transport='rest')

        model = genai.GenerativeModel("gemini-2.5-flash-preview-tts")

        # Bagi teks menjadi chunk ~3 menit berdasarkan WPM jika tersedia; fallback ke panjang karakter
        chunks = _split_text_for_tts_by_duration(vo_script, speech_rate_wpm or 195, max_sec=(max_chunk_sec or 180))
        total = len(chunks)
        log(f"Menyiapkan TTS Gemini Flash: {total} potongan (~3 menit per potong)...")

        # Temp folder untuk WAV chunk
        tmp_dir = pathlib.Path(output_path).with_suffix('').with_name(pathlib.Path(output_path).stem + "_tts_chunks")
        try:
            if tmp_dir.exists():
                for f in tmp_dir.glob("*"): f.unlink()
            else:
                tmp_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        wav_paths = []
        generation_config_base = {"response_modalities": ["AUDIO"]}
        if voice_name:
            generation_config_base["speech_config"] = {
                "voice_config": {"prebuilt_voice_config": {"voice_name": voice_name}}
            }

        # Prepare key rotation (exclude cooldown keys)
        am = _am.APIManager()
        for idx, chunk in enumerate(chunks, 1):
            if not chunk.strip():
                continue
            _preview = chunk[:50].replace("\n", " ").replace("\r", " ")
            log(f"[Gemini TTS] Chunk {idx}/{total}: {min(50, len(chunk))} chars preview → '{_preview}' ...")

            # Rotate across available keys (skip cooldown). Prioritize provided api_key first if available
            available_keys = am.get_available_keys()
            if api_key and api_key in available_keys:
                # put in front
                available_keys = [api_key] + [k for k in available_keys if k != api_key]
            elif api_key and not am.is_key_on_cooldown(api_key):
                available_keys = [api_key] + available_keys
            if not available_keys:
                log("[Gemini TTS] ERROR: Tidak ada API key yang tersedia (semua cooldown).")
                return False

            response = None
            last_exc = None
            for ki, k in enumerate(available_keys, 1):
                try:
                    genai.configure(api_key=k, transport='rest')
                    log(f"[Gemini TTS]   menggunakan API key #{ki}/{len(available_keys)}...")
                    # Buat model baru agar binding client mengikuti key terbaru
                    model_k = genai.GenerativeModel("gemini-2.5-flash-preview-tts")
                    response = model_k.generate_content(
                        chunk,
                        generation_config=generation_config_base,
                        request_options={"timeout": 600}
                    )
                    # Success on this key
                    break
                except Exception as e:
                    last_exc = e
                    msg = str(e)
                    lower = msg.lower()
                    if ("toomanyrequests" in msg or " 429" in msg or "quota" in lower):
                        # Set 24h cooldown on this key and continue to next
                        am.set_key_cooldown(k, 24*3600)
                        log(f"[Gemini TTS]   key dibatasi (429/quota). Tandai cooldown 24 jam dan ganti key...")
                        continue
                    else:
                        log(f"[Gemini TTS]   key gagal: {e}")
                        continue
            if response is None:
                log(f"[Gemini TTS] ERROR: Semua API key gagal untuk chunk {idx}. Pesan terakhir: {last_exc}")
                return False

            if not response.candidates or not response.candidates[0].content.parts:
                log(f"[Gemini TTS] WARNING: Tidak ada data audio pada chunk {idx}")
                continue
            part = response.candidates[0].content.parts[0]
            if not hasattr(part, "inline_data") or not part.inline_data or not part.inline_data.data:
                log(f"[Gemini TTS] WARNING: inline_data kosong pada chunk {idx}")
                continue

            raw = part.inline_data.data
            audio_bytes = raw if isinstance(raw, (bytes, bytearray)) else base64.b64decode(raw)
            wav_path = tmp_dir / f"chunk_{idx:03d}.wav"
            # Tulis PCM 24kHz 16bit mono
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(audio_bytes)
            wav_paths.append(wav_path)
            log(f"[Gemini TTS] Chunk {idx}/{total} selesai: {wav_path.name}")

        if not wav_paths:
            log("FATAL: Tidak ada chunk audio yang berhasil dibuat.")
            return False

        # Gabungkan chunk WAV menjadi satu MP3 akhir untuk segmen ini
        concat_list = tmp_dir / "concat.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for p in wav_paths:
                f.write(f"file '{p.as_posix()}'\n")
        cmd = f"ffmpeg -y -f concat -safe 0 -i \"{concat_list}\" -c:a libmp3lame -q:a 3 \"{output_path}\""
        subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        log(f"[Gemini TTS] Penggabungan selesai → {output_path}")
        return True
    except Exception as e:
        log(f"FATAL: Terjadi error saat generasi TTS Gemini: {e}")
        log(traceback.format_exc())
        return False


def _split_text_for_tts_by_duration(text: str, wpm: int, max_sec: int = 180) -> list[str]:
    """Split text by sentence, targeting chunks up to ~max_sec based on words per minute.
    Falls back to char-based splitting if needed."""
    import re
    t = (text or "").strip()
    if not t:
        return [""]
    # Rough calculation: usable words per second (assume 90% speaking, 10% pause)
    words_per_sec = max(1.0, (wpm / 60.0) * 0.9)
    max_words = int(words_per_sec * max_sec)
    # Tokenize by sentences
    parts = re.split(r"([.!?]\s)", t)
    sentences = []
    for i in range(0, len(parts), 2):
        s = parts[i]
        sep = parts[i+1] if i+1 < len(parts) else ""
        sentences.append((s + sep).strip())
    chunks = []
    buf_words = 0
    buf = []
    for s in sentences:
        if not s:
            continue
        sw = len(s.split())
        if buf_words + sw <= max_words or not buf:
            buf.append(s); buf_words += sw
        else:
            chunks.append(" ".join(buf).strip()); buf = [s]; buf_words = sw
    if buf:
        chunks.append(" ".join(buf).strip())
    # Fallback if a single sentence is huge
    fixed = []
    for c in chunks:
        if len(c.split()) > max_words * 1.5:
            # split by chars roughly
            mc = max(300, int(max_words * 6))  # rough char cap
            for j in range(0, len(c), mc): fixed.append(c[j:j+mc])
        else:
            fixed.append(c)
    return fixed


def _generate_tts_via_rest(api_key: str, vo_script: str, language_code: str, voice_name: str, log):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro-preview-tts:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}

        body = {
            "contents": [{"role": "user", "parts": [{"text": vo_script}]}],
            "generationConfig": {
                "responseMimeType": "audio/mp3",
            }
        }

        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8"))
        cand = (data.get("candidates") or [None])[0]
        if not cand:
            log("REST TTS: candidates kosong.")
            return None
        parts = cand.get("content", {}).get("parts", [])
        if not parts:
            log("REST TTS: parts kosong.")
            return None
        inline = parts[0].get("inline_data") or parts[0].get("inlineData")
        if not inline or not inline.get("data"):
            log("REST TTS: inline_data tidak ditemukan.")
            return None
        b64 = inline["data"]
        try:
            return base64.b64decode(b64)
        except Exception:
            # Jika bukan base64, kembalikan bytes langsung dari string
            return bytes(b64, "utf-8")
    except urllib.error.HTTPError as he:
        log(f"REST TTS HTTPError: {he}")
        try:
            msg = he.read().decode("utf-8")
            log(msg)
        except Exception:
            pass
        return None
    except Exception as e:
        log(f"REST TTS error: {e}")
        return None

def _create_silent_audio_placeholder(output_path: str, duration: float = 1.0, log_func=print):
    """Membuat file MP3 hening sebagai placeholder jika terjadi error."""
    try:
        command = f'ffmpeg -y -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 -t {duration} -c:a libmp3lame -q:a 9 "{output_path}"'
        subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        log_func(f"Membuat placeholder MP3 hening di {output_path}")
    except Exception as e:
        log_func(f"FFmpeg gagal membuat MP3 hening: {e}")




