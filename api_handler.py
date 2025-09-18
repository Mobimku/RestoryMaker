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
from pathlib import Path
import xml.etree.ElementTree as ET

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
2) Abaikan bumper atau introduction jika ada. Temukan momen penting (establishing context, inciting incident, turning points, confrontation, climax, resolution).
3) Untuk setiap babak, pilih rentang timestamp SRT yang paling representatif (boleh discontinuous). Jumlah rentang tidak dibatasi angka tetap; cukup untuk menyusun BEATS klip 3-4 detik hingga menutup durasi VO segmen.
4) Tulis recap ringkas per babak yang konsisten dengan target words_target per segmen (lihat PARAMETER KONKRIT); hindari patokan jumlah kalimat tetap.

PENULISAN VO (WAJIB):
- Kalimat pertama HARUS menjadi **HOOK punchy** sesuai konteks segmen (12–18 kata).
- Gunakan teknik storrytelling Hook, Foreshadow, Story, Payoff tiap segment nya.
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
- Pecah visual menjadi klip 3-4 detik secara BERURUTAN (bukan potongan kontinu panjang).
- Urutan klip mengikuti urutan narasi/VO (ascending `at_ms`) dan menjaga progresi waktu sumber (gunakan `block_index` dan posisi di timeblock secara menaik) agar visual sinkron dengan VO.
- Efek visual akan ditambahkan otomatis saat proses editing. JANGAN keluarkan daftar efek dalam output.
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

# Prompt sederhana untuk mode cepat (Flash)
STORYBOARD_PROMPT_SIMPLE = """
Anda adalah AI untuk membuat storyboard film recap.
Input: subtitle SRT film berdurasi {durasi_film} menit.
Output: JSON storyboard.

TUGAS UTAMA:
1) Bagi film jadi 5 segmen: Intro, Rising, Mid-conflict, Climax, Ending
2) Pilih timeblock penting dari SRT untuk setiap segmen (start-end + alasan singkat)
3) Tulis VO (voice over) bahasa {lang} untuk setiap segmen sesuai durasi target

TARGET DURASI (detik): Intro={intro_sec}, Rising={rising_sec}, Mid-conflict={mid_sec}, Climax={climax_sec}, Ending={ending_sec}

FORMAT JSON OUTPUT:
{{
  "film_meta": {{"title": "", "duration_sec": {durasi_total_detik}}},
  "segments": [
    {{
      "label": "Intro",
      "vo_language": "{lang}",
      "target_vo_duration_sec": {intro_sec},
      "vo_script": "HOOK menarik di awal... (narasi ulang, ringkas, padat)",
      "vo_meta": {{"speech_rate_wpm": 160, "fit": "OK"}},
      "source_timeblocks": [
        {{"start": "00:01:30,000", "end": "00:03:15,000", "reason": "opening scene"}}
      ]
    }}
  ]
}}

ATURAN NARASI (SANGAT PENTING):
- Mulai dengan hook menarik (12-18 kata)
- Gunakan bahasa {lang}
- Jangan copy dialog langsung, buat narasi ulang
- Pertahankan SEMUA informasi penting (jangan buang detail inti)
- Jangan menambahkan keterangan ekstra atau karakter baru
- Sesuaikan panjang VO dengan target durasi (150-175 WPM)

Keluarkan HANYA JSON tanpa penjelasan lain.
"""

def _ensure_storyboard_minimal_fields(sb: dict, film_duration: int, language: str, secs_map: dict | None = None) -> dict:
    sb = sb or {}
    fm = sb.get("film_meta") or {}
    fm.setdefault("title", "")
    fm.setdefault("duration_sec", int(film_duration))
    sb["film_meta"] = fm
    segs = sb.get("segments") or []
    if not isinstance(segs, list):
        segs = []
    for seg in segs:
        seg.setdefault("vo_language", language)
        # Tidak menambahkan effects_pool apapun; efek dihandle di tahap editing
        vr = seg.get("vo_meta") or {}
        vr.setdefault("speech_rate_wpm", 160)
        vr.setdefault("fit", "OK")
        seg["vo_meta"] = vr
        seg.setdefault("source_timeblocks", [{"start": "00:00:00,000", "end": "00:00:10,000", "reason": "auto"}])
        if "target_vo_duration_sec" not in seg:
            # default ringan 180s
            seg["target_vo_duration_sec"] = 180
    if not segs:
        labels = ["Intro","Rising","Mid-conflict","Climax","Ending"]
        if secs_map:
            durs = [int(secs_map.get(lab, 180)) for lab in labels]
        else:
            durs = [180,480,360,360,240]
        for lab, du in zip(labels, durs):
            segs.append({
                "label": lab,
                "vo_language": language,
                "target_vo_duration_sec": du,
                "vo_script": f"Narasi {lab}.",
                "vo_meta": {"speech_rate_wpm": 160, "fit": "OK"},
                "source_timeblocks": [{"start": "00:00:00,000", "end": "00:00:10,000", "reason": lab}],
            })
    sb["segments"] = segs
    return sb

def get_storyboard_from_srt_fast(
    srt_path: str,
    api_key: str,
    film_duration: int,
    output_folder: str,
    language: str = "en",
    progress_callback=None,
    recap_minutes: int | None = None,
    timeout_s: int = 180,
):
    def log(msg):
        if progress_callback: progress_callback(msg)

    # Siapkan rotasi key
    try:
        import api_manager as _am
        am = _am.APIManager()
        available_keys = am.get_available_keys()
        if api_key and api_key in available_keys:
            available_keys = [api_key] + [k for k in available_keys if k != api_key]
        elif api_key and not am.is_key_on_cooldown(api_key):
            available_keys = [api_key] + available_keys
        if not available_keys:
            available_keys = [api_key] if api_key else []
    except Exception:
        am = None
        available_keys = [api_key] if api_key else []

    # Hitung porsi durasi per segmen berdasarkan recap_minutes (default 10 menit)
    total_target_sec = int((recap_minutes or 10) * 60)
    dist = {"Intro": 0.11, "Rising": 0.30, "Mid-conflict": 0.22, "Climax": 0.22, "Ending": 0.15}
    secs_map = {k: max(30, int(round(v * total_target_sec))) for k, v in dist.items()}

    sys_prompt = STORYBOARD_PROMPT_SIMPLE \
        .replace("{durasi_film}", str(int(film_duration//60))) \
        .replace("{durasi_total_detik}", str(int(film_duration))) \
        .replace("{lang}", language) \
        .replace("{intro_sec}", str(secs_map["Intro"])) \
        .replace("{rising_sec}", str(secs_map["Rising"])) \
        .replace("{mid_sec}", str(secs_map["Mid-conflict"])) \
        .replace("{climax_sec}", str(secs_map["Climax"])) \
        .replace("{ending_sec}", str(secs_map["Ending"]))

    last_exc = None
    uploaded_per_key = {}
    for ki, k in enumerate(available_keys, 1):
        try:
            genai.configure(api_key=k)
            log(f"[FAST] Upload SRT untuk key#{ki}/{len(available_keys)}...")
            uf = genai.upload_file(path=srt_path)
            uploaded_per_key[k] = uf
            model = genai.GenerativeModel(
                model_name="gemini-2.5-flash",
                generation_config={
                    "temperature": 0.5,
                    "top_p": 0.8,
                    "response_mime_type": "application/json",
                },
                safety_settings=[{"category": c, "threshold": "BLOCK_NONE"} for c in [
                    "HARM_CATEGORY_HARASSMENT","HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT","HARM_CATEGORY_DANGEROUS_CONTENT"]]
            )
            t0 = time.time()
            resp = model.generate_content([sys_prompt, "\n\n---\n\n## SRT FILE INPUT:\n", uf], request_options={"timeout": timeout_s})
            log(f"[FAST] Storyboard via key#{ki} selesai dalam {time.time()-t0:.1f}s")
            if not resp.candidates or resp.candidates[0].finish_reason.name != "STOP":
                raise RuntimeError("FAST: candidate kosong atau tidak STOP")
            txt = (resp.text or "").strip()
            txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                data = json.loads(txt)
            except Exception:
                m = re.search(r"\{[\s\S]*\}$", txt)
                if not m:
                    raise
                data = json.loads(m.group(0))
            data = _ensure_storyboard_minimal_fields(data, film_duration, language, secs_map)
            out = Path(output_folder) / "storyboard_output.json"
            with open(out, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log(f"[FAST] Menyimpan storyboard JSON ke {out}")
            return data
        except Exception as e:
            last_exc = e
            msg = str(e); low = msg.lower()
            if ("429" in msg) or ("toomanyrequests" in low) or ("quota" in low):
                if am:
                    try:
                        am.set_key_cooldown(k, 24*3600)
                        log(f"[FAST] key#{ki} quota/429. Cooldown & coba key lain...")
                    except Exception:
                        pass
                time.sleep(1.2)
                continue
            elif ("deadline" in low) or ("504" in msg):
                log(f"[FAST] key#{ki} timeout/504. Coba key lain...")
                time.sleep(0.8)
                continue
            else:
                log(f"[FAST] key#{ki} gagal: {e}")
                time.sleep(0.6)
                continue
        finally:
            try:
                uf = uploaded_per_key.get(k)
                if uf:
                    genai.configure(api_key=k)
                    genai.delete_file(name=getattr(uf, 'name', None))
            except Exception:
                pass

    # Fallback: excerpt teks tanpa upload
    try:
        excerpt = ""
        with open(srt_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        if len(text) > 6000:
            one = 2000
            excerpt = text[:one] + "\n\n...\n\n" + text[len(text)//2:len(text)//2+one] + "\n\n...\n\n" + text[-one:]
        else:
            excerpt = text
        for ki, k in enumerate(available_keys, 1):
            try:
                genai.configure(api_key=k)
                model = genai.GenerativeModel(
                    model_name="gemini-2.5-flash",
                    generation_config={"temperature": 0.5, "top_p": 0.8, "response_mime_type": "application/json"},
                    safety_settings=[{"category": c, "threshold": "BLOCK_NONE"} for c in [
                        "HARM_CATEGORY_HARASSMENT","HARM_CATEGORY_HATE_SPEECH",
                        "HARM_CATEGORY_SEXUALLY_EXPLICIT","HARM_CATEGORY_DANGEROUS_CONTENT"]]
                )
                resp = model.generate_content([sys_prompt, "\n\n## SRT EXCERPT:\n", excerpt], request_options={"timeout": timeout_s})
                if not resp.candidates or resp.candidates[0].finish_reason.name != "STOP":
                    raise RuntimeError("FAST(excerpt): gagal")
                txt = (resp.text or "").strip()
                txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                data = json.loads(txt)
                data = _ensure_storyboard_minimal_fields(data, film_duration, language, secs_map)
                out = Path(output_folder) / "storyboard_output.json"
                with open(out, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                log(f"[FAST] (excerpt) Menyimpan storyboard JSON ke {out}")
                return data
            except Exception as e:
                log(f"[FAST] excerpt via key#{ki} gagal: {e}")
                continue
    except Exception as e:
        log(f"[FAST] fallback excerpt error: {e}")
    return None

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

    uploaded_file = None  # deprecated single-file usage
    uploaded_files: dict[str, object] = {}
    try:
        genai.configure(api_key=api_key)
        # Jangan upload di sini. Kita akan upload per-key saat memanggil model agar file dapat diakses oleh key tsb.
        log("Menyiapkan unggah SRT per-key untuk akses file yang konsisten...")

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
        # Siapkan rotasi key untuk menghindari TooManyRequests/Quota
        try:
            import api_manager as _am
            am = _am.APIManager()
            available_keys = am.get_available_keys()
            if api_key and api_key in available_keys:
                available_keys = [api_key] + [k for k in available_keys if k != api_key]
            elif api_key and not am.is_key_on_cooldown(api_key):
                available_keys = [api_key] + available_keys
            if not available_keys:
                available_keys = [api_key] if api_key else []
        except Exception:
            am = None
            available_keys = [api_key] if api_key else []
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
                "source_timeblocks, edit_rules (cut_length_sec 3-4, tanpa daftar efek), dan beats (satu beat per klip 3-4 detik).\n"
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

        # Aktifkan PLANNER: bangun story per segmen untuk memastikan kepatuhan words_target (±10%)
        # Wrapper pemanggilan model dengan timeout adaptif + logging durasi
        def call_model(prompt, lbl: str = "", timeout_s: int | None = None):
            # Timeout lebih singkat untuk flash, lebih longgar untuk pro
            if timeout_s is None:
                timeout_s = 120 if "flash" in (model_name or "") else 300
            last_exc = None
            for ki, k in enumerate(available_keys, 1):
                try:
                    genai.configure(api_key=k)
                    model_k = genai.GenerativeModel(
                        model_name=model_name,
                        generation_config=generation_config,
                        safety_settings=safety_settings
                    )
                    t0 = time.time()
                    resp = model_k.generate_content(prompt, request_options={'timeout': timeout_s})
                    dt = time.time() - t0
                    try:
                        if lbl:
                            log(f"[API] {lbl} via key#{ki}/{len(available_keys)} selesai dalam {dt:.1f}s")
                    except Exception:
                        pass
                    return resp
                except Exception as e:
                    last_exc = e
                    msg = str(e)
                    low = msg.lower()
                    if ('429' in msg) or ('toomanyrequests' in low) or ('quota' in low):
                        if am:
                            try:
                                am.set_key_cooldown(k, 24*3600)
                                log(f"[API] key dibatasi (429/quota). Tandai cooldown dan coba key berikutnya...")
                            except Exception:
                                pass
                        time.sleep(1.5)
                        continue
                    else:
                        try:
                            log(f"[API] {lbl} gagal pada key#{ki}: {e}")
                        except Exception:
                            pass
                        time.sleep(1.0)
                        continue
            raise last_exc or RuntimeError("All API keys failed")

        # Selalu gunakan file upload untuk planner utama (tetap fallback ke excerpt jika gagal)
        use_upload = True

        # Planner untuk timeblocks minimal
        plan_prompt = (
            system_prompt + "\n\n# Planner Timeblocks (JSON saja)\n"
            "Instruksi: Keluarkan JSON dengan array 'segments' berisi 5 item (Intro, Rising, Mid-conflict, Climax, Ending).\n"
            "Setiap item wajib berisi: label, source_timeblocks (daftar objek {start,end,reason}).\n"
            "Jangan keluarkan VO, beats, atau bidang lain. JSON minimal saja.\n"
        )
        # Planner dengan retry + fallback excerpt jika timeout
        def _build_srt_excerpt_all(path: str, max_chars: int = 8000) -> str:
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
                # Ringkas: ambil setiap N karakter secara berkala untuk meratakan sampling
                if len(text) <= max_chars:
                    return text
                step = max(1, len(text) // max_chars)
                sampled = text[::step][:max_chars]
                return sampled
            except Exception:
                return ''

        tries_plan = 0; plan_resp = None
        while tries_plan < 3:
            tries_plan += 1
            log(f"Meminta planner timeblocks minimal... (try {tries_plan}/3)")
            plan_resp = None
            # Coba dengan upload file per-key agar tidak ada 403 (permission)
            for ki, k in enumerate(available_keys, 1):
                try:
                    genai.configure(api_key=k)
                    model_k = genai.GenerativeModel(
                        model_name=model_name,
                        generation_config=generation_config,
                        safety_settings=safety_settings
                    )
                    uf = uploaded_files.get(k)
                    if not uf:
                        log(f"Mengunggah SRT untuk key #{ki}/{len(available_keys)}...")
                        uf = genai.upload_file(path=srt_path)
                        uploaded_files[k] = uf
                        log(f"Upload sukses (key#{ki}): {uf.name}")
                    plan_resp = model_k.generate_content([plan_prompt, "\n\n---\n\n## SRT FILE INPUT:\n", uf], request_options={'timeout': 120 if 'flash' in model_name else 300})
                    if plan_resp.candidates and plan_resp.candidates[0].finish_reason.name == "STOP":
                        log(f"Planner(upload) via key#{ki}/{len(available_keys)} OK")
                        break
                    else:
                        log(f"Planner tidak STOP via key#{ki}. Coba key lain...")
                        continue
                except Exception as e:
                    msg = str(e); low = msg.lower()
                    if ('429' in msg) or ('toomanyrequests' in low) or ('quota' in low):
                        try:
                            am.set_key_cooldown(k, 24*3600)  # type: ignore[name-defined]
                            log(f"Planner: key#{ki} 429/quota. Cooldown & coba key berikutnya...")
                        except Exception:
                            pass
                        time.sleep(1.2)
                        continue
                    else:
                        log(f"Planner error via key#{ki}: {e}")
                        time.sleep(0.8)
                        continue

            if plan_resp and plan_resp.candidates and plan_resp.candidates[0].finish_reason.name == "STOP":
                break

            # Fallback: gunakan excerpt teks SRT (tanpa upload file)
            excerpt_all = _build_srt_excerpt_all(srt_path, max_chars=8000)
            if excerpt_all:
                try:
                    log("Planner fallback dengan excerpt teks SRT...")
                    plan_resp = call_model(plan_prompt + "\n\n## SRT EXCERPT (RINGKAS):\n" + excerpt_all, lbl=f"Planner(excerpt) try-{tries_plan}")
                    if plan_resp.candidates and plan_resp.candidates[0].finish_reason.name == "STOP":
                        break
                except Exception as e2:
                    log(f"Planner fallback error: {e2}")
        if not plan_resp or not plan_resp.candidates or plan_resp.candidates[0].finish_reason.name != "STOP":
            log("ERROR: Planner gagal setelah retry. Menggunakan rencana minimal lokal dari SRT...")
            plan_obj = _naive_plan_from_srt(srt_path)
            if not plan_obj.get('segments'):
                log("Gagal membuat rencana lokal minimal.")
                return None
        else:
            plan_txt = plan_resp.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                plan_obj = json.loads(plan_txt)
            except Exception as e:
                log(f"ERROR parse JSON planner: {e}"); log(plan_txt[:500])
                log("Coba rencana minimal lokal dari SRT...")
                plan_obj = _naive_plan_from_srt(srt_path)
                if not plan_obj.get('segments'):
                    return None

        storyboard = {
            "film_meta": {"title": "", "duration_sec": int(film_duration)},
            "recap": {"intro": "", "rising": "", "mid_conflict": "", "climax": "", "ending": ""},
            "segments": []
        }

        seg_map = {seg.get('label', ''): seg for seg in (plan_obj.get('segments') or [])}

        # Per segmen: generate JSON lengkap + validasi words_actual ±10%, retry max 2x
        def build_segment_prompt(label: str, vo_sec: int, wpm: int, words: int, ranges_sample: str) -> str:
            return (
                "# Segmen Storyboard (JSON saja)\n"
                f"Label: {label}\n"
                f"Bahasa VO: {language}\n"
                "Instruksi: Hanya keluarkan JSON untuk SATU segmen di bawah ini, tanpa catatan tambahan.\n"
                "Wajib isi: label, vo_language, target_vo_duration_sec, vo_script, vo_meta (speech_rate_wpm, fill_ratio=0.90, words_target, words_actual, sentences, commas, predicted_duration_sec, delta_sec, fit),\n"
                "source_timeblocks, edit_rules (cut_length_sec 3-4, tanpa daftar efek), dan beats (satu beat per klip 3-4 detik).\n"
                "Kepatuhan durasi & kata WAJIB: gunakan angka eksplisit di bawah ini.\n\n"
                "PARAMETER SEGMENT (WAJIB DIGUNAKAN):\n"
                f"- target_vo_duration_sec={vo_sec}, speech_rate_wpm={wpm}, words_target={words}\n\n"
                "SRT RINGKAS (relevan untuk segmen ini):\n" + ranges_sample + "\n\n"
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
                "    \"transition_every_sec\": 25,\n"
                "    \"transition_type\": \"crossfade\",\n"
                "    \"transition_duration_sec\": 0.5\n"
                "  },\n"
                "  \"beats\": [ {\"at_ms\": 0, \"block_index\": 0, \"src_at_ms\": 0, \"src_length_ms\": 3500, \"note\": \"...\"} ]\n"
                "}\n"
            )

        def describe_ranges(ranges: list) -> str:
            lines = []
            for r in (ranges or [])[:8]:
                lines.append(f"- {r.get('start','')} --> {r.get('end','')}: {r.get('reason','')}")
            return "\n".join(lines)

        for label in order:
            ranges = (seg_map.get(label) or {}).get('source_timeblocks') or []
            ranges_sample = describe_ranges(ranges)
            tries = 0
            while tries < 3:
                tries += 1
                prompt = build_segment_prompt(label, secs_map[label], wpm_map[label], words_map[label], ranges_sample)
                log(f"Generate segmen: {label} (try {tries}/3, target {secs_map[label]}s, ~{words_map[label]} kata)")
                resp = call_model(prompt, lbl=f"Segmen {label} try-{tries}")
                if not resp.candidates or resp.candidates[0].finish_reason.name != "STOP":
                    log(f"ERROR: gagal segmen {label} pada try {tries}")
                    continue
                txt = resp.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                try:
                    seg_obj = json.loads(txt)
                except Exception as e:
                    log(f"ERROR parse segmen {label}: {e}")
                    continue
                # Validasi words_actual vs words_target (±10%)
                vo = seg_obj.get('vo_script', '')
                words_actual = len((vo or '').split())
                target = words_map[label]
                if target and abs(words_actual - target) / target > 0.10 and tries < 3:
                    log(f"WARNING: segmen {label} words_actual={words_actual} target={target} (dev>10%). retry...")
                    continue
                storyboard['segments'].append(seg_obj)
                break
            else:
                log(f"FATAL: segmen {label} gagal memenuhi kriteria.")
                return None

        json_path = pathlib.Path(output_folder) / "storyboard_output.json"
        with open(json_path, "w", encoding="utf-8") as jf:
            json.dump(storyboard, jf, ensure_ascii=False, indent=2)
        log(f"Menyimpan storyboard JSON ke {json_path}")
        return storyboard
    except Exception as e:
        log(f"Terjadi error saat memanggil Gemini API: {e}")
        log(traceback.format_exc())
        return None
    finally:
        # Hapus semua file terunggah per-key
        if uploaded_files:
            for k, uf in list(uploaded_files.items()):
                try:
                    genai.configure(api_key=k)
                    log(f"Menghapus file yang diunggah (key): {getattr(uf, 'name', '?')}")
                    genai.delete_file(name=getattr(uf, 'name', None))
                except Exception:
                    pass

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

        # Gunakan model TTS pratinjau yang mendukung audio output
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
            # Struktur voice_config untuk prebuilt voice (SDK pratinjau TTS)
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
                        time.sleep(1.5)
                        continue
                    else:
                        log(f"[Gemini TTS]   key gagal: {e}")
                        time.sleep(1.0)
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
            mime = getattr(part.inline_data, "mime_type", None)
            audio_bytes = raw if isinstance(raw, (bytes, bytearray)) else base64.b64decode(raw)
            # Simpan sesuai mime: jika mp3 -> .mp3, selain itu -> .wav (PCM/WAV container)
            if mime and ("mp3" in mime or "mpeg" in mime):
                out_path = tmp_dir / f"chunk_{idx:03d}.mp3"
                with open(out_path, "wb") as outf:
                    outf.write(audio_bytes)
                wav_paths.append(out_path)
            else:
                out_path = tmp_dir / f"chunk_{idx:03d}.wav"
                with wave.open(str(out_path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(audio_bytes)
                wav_paths.append(out_path)
            log(f"[Gemini TTS] Chunk {idx}/{total} selesai: {out_path.name} ({mime or 'pcm'})")

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


def _json3_to_word_srt(json_path: str, srt_out: str, log=print) -> bool:
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        events = data.get('events') or []
        idx = 1
        out = []
        def fmt(ms: int) -> str:
            ms = max(0, int(ms))
            h = ms // 3600000; ms %= 3600000
            m = ms // 60000; ms %= 60000
            s = ms // 1000; ms %= 1000
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
        for ev in events:
            t0 = int(ev.get('tStartMs') or 0)
            dur = int(ev.get('dDurationMs') or 0)
            segs = ev.get('segs') or []
            words = []
            for sg in segs:
                w = (sg.get('utf8') or '').strip()
                if not w:
                    continue
                # Skip forced newline markers
                if w in {'\n', '\r', '\r\n'}:
                    continue
                words.append((w, int(sg.get('tOffsetMs') or 0)))
            if not words:
                continue
            # If segs include tOffsetMs, use t0 + offset; else distribute evenly
            has_offsets = any(off for _, off in words)
            if has_offsets:
                for i, (w, off) in enumerate(words):
                    st = t0 + off
                    # next offset or end
                    if i + 1 < len(words):
                        et = t0 + words[i+1][1]
                    else:
                        et = t0 + dur if dur > 0 else st + 200
                    if et <= st:
                        et = st + 150
                    out.extend([str(idx), f"{fmt(st)} --> {fmt(et)}", w, ""]) ; idx += 1
            else:
                # Evenly split duration among tokens; fallback 200ms each if dur unknown
                per = int(dur / max(1, len(words))) if dur > 0 else 200
                cur = t0
                for (w, _) in words:
                    st = cur
                    et = st + per
                    out.extend([str(idx), f"{fmt(st)} --> {fmt(et)}", w, ""]) ; idx += 1
                    cur = et
        if not out:
            return False
        with open(srt_out, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write('\r\n'.join(out))
        return True
    except Exception as e:
        try:
            log(f"JSON3->SRT error: {e}")
        except Exception:
            pass
        return False


def _srv3_to_word_srt(xml_path: str, srt_out: str, log=print) -> bool:
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        # YouTube srv3 structure: <timedtext><body><p t="start" d="dur"><s t="offset">word</s>...</p>...
        idx = 1
        out = []
        def fmt(ms: int) -> str:
            ms = max(0, int(ms))
            h = ms // 3600000; ms %= 3600000
            m = ms // 60000; ms %= 60000
            s = ms // 1000; ms %= 1000
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
        for p in root.findall('.//p'):
            t0 = int(p.attrib.get('t', '0'))
            dur = int(p.attrib.get('d', '0'))
            s_nodes = p.findall('s')
            if s_nodes:
                # word-level offsets present
                for i, s in enumerate(s_nodes):
                    w = (s.text or '').strip()
                    if not w:
                        continue
                    off = int(s.attrib.get('t', '0'))
                    st = t0 + off
                    if i + 1 < len(s_nodes):
                        nxt = int(s_nodes[i+1].attrib.get('t', '0'))
                        et = t0 + nxt
                    else:
                        et = t0 + dur if dur > 0 else st + 200
                    if et <= st:
                        et = st + 150
                    out.extend([str(idx), f"{fmt(st)} --> {fmt(et)}", w, ""]) ; idx += 1
            else:
                # No <s>, split whole paragraph into words evenly
                text = ''.join(p.itertext()).strip()
                tokens = [t for t in text.split() if t]
                if not tokens:
                    continue
                per = int(dur / max(1, len(tokens))) if dur > 0 else 200
                cur = t0
                for w in tokens:
                    st = cur; et = st + per
                    out.extend([str(idx), f"{fmt(st)} --> {fmt(et)}", w, ""]) ; idx += 1
                    cur = et
        if not out:
            return False
        with open(srt_out, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write('\r\n'.join(out))
        return True
    except Exception as e:
        try:
            log(f"SRV3->SRT error: {e}")
        except Exception:
            pass
        return False


def _json3_to_srt(json_path: str, srt_out: str, log=print) -> bool:
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        events = data.get('events') or []
        idx = 1
        out = []
        def fmt(ms: int) -> str:
            ms = max(0, int(ms))
            h = ms // 3600000; ms %= 3600000
            m = ms // 60000; ms %= 60000
            s = ms // 1000; ms %= 1000
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
        for ev in events:
            t0 = int(ev.get('tStartMs') or 0)
            dur = int(ev.get('dDurationMs') or 0)
            segs = ev.get('segs') or []
            words = []
            for sg in segs:
                w = (sg.get('utf8') or '').replace('\n', ' ').strip()
                if w:
                    words.append(w)
            if not words:
                continue
            text = ' '.join(words).strip()
            if not text:
                continue
            if dur <= 0:
                dur = max(1000, 200 * len(words))
            st = t0
            et = t0 + dur
            if et <= st:
                et = st + 500
            out.extend([str(idx), f"{fmt(st)} --> {fmt(et)}", text, ""]) ; idx += 1
        if not out:
            return False
        with open(srt_out, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write('\r\n'.join(out))
        return True
    except Exception as e:
        try:
            log(f"JSON3->SRT(simple) error: {e}")
        except Exception:
            pass
        return False


def _srv3_to_srt(xml_path: str, srt_out: str, log=print) -> bool:
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        idx = 1
        out = []
        def fmt(ms: int) -> str:
            ms = max(0, int(ms))
            h = ms // 3600000; ms %= 3600000
            m = ms // 60000; ms %= 60000
            s = ms // 1000; ms %= 1000
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
        for p in root.findall('.//p'):
            t0 = int(p.attrib.get('t', '0'))
            dur = int(p.attrib.get('d', '0'))
            # Concatenate inner text
            text = ''.join(p.itertext()).replace('\n', ' ').strip()
            if not text:
                continue
            if dur <= 0:
                tokens = [t for t in text.split() if t]
                dur = max(1000, 200 * len(tokens))
            st = t0; et = t0 + dur
            if et <= st:
                et = st + 500
            out.extend([str(idx), f"{fmt(st)} --> {fmt(et)}", text, ""]) ; idx += 1
        if not out:
            return False
        with open(srt_out, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write('\r\n'.join(out))
        return True
    except Exception as e:
        try:
            log(f"SRV3->SRT(simple) error: {e}")
        except Exception:
            pass
        return False


def _vtt_to_srt(vtt_path: str, srt_out: str, log=print) -> bool:
    try:
        cmd = f"ffmpeg -y -i \"{vtt_path}\" \"{srt_out}\""
        subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        return True
    except Exception as e:
        try:
            log(f"VTT->SRT error: {e}")
        except Exception:
            pass
        return False


# ============ NEW: YouTube transcription to word-level SRT via Gemini ============
def transcribe_youtube_to_srt(
    youtube_url: str,
    api_key: str,
    output_folder: str,
    language: str = "auto",
    progress_callback=None,
    model_name: str = "gemini-2.5-flash",
) -> tuple[str | None, dict]:
    """Download audio from YouTube, transcribe with Gemini to word-level, write .srt.
    Returns (srt_path, info_dict). info_dict may contain duration, title, lang.
    """
    def log(msg):
        if progress_callback: progress_callback(msg)

    info = {}
    srt_path = None
    try:
        log(f"Transkripsi dari YouTube link (tanpa unduh awal): {youtube_url}")

        genai.configure(api_key=api_key)
        safety_settings = [{"category": c, "threshold": "BLOCK_NONE"} for c in [
            "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"
        ]]
        # Siapkan rotasi key agar tidak menembak satu key terus-menerus
        try:
            import api_manager as _am
            am = _am.APIManager()
            available_keys = am.get_available_keys()
            if api_key and api_key in available_keys:
                available_keys = [api_key] + [k for k in available_keys if k != api_key]
            elif api_key and not am.is_key_on_cooldown(api_key):
                available_keys = [api_key] + available_keys
            if not available_keys:
                available_keys = [api_key] if api_key else []
        except Exception:
            am = None
            available_keys = [api_key] if api_key else []

        # Prompt SRT langsung agar menghindari parsing JSON yang rawan noise
        prompt = (
            "Buatkan transkrip dari video YouTube ini, dan beri timestamp word level untuk setiap kata.\n\n"
            "Keluarkan HANYA file SRT valid (tanpa code fence, tanpa penjelasan).\n"
            "Format SRT setiap cue:\n"
            "<index>\n"
            "HH:MM:SS,mmm --> HH:MM:SS,mmm\n"
            "<teks satu kata (boleh termasuk tanda baca)>\n\n"
            "Aturan:\n"
            "- Gunakan link YouTube yang saya berikan sebagai sumber audio.\n"
            "- Setiap kata menjadi satu cue terpisah.\n"
            "- Gunakan milidetik (mmm). Pastikan end >= start.\n"
            "- Jangan keluarkan teks selain isi SRT.\n"
        )

        # Bentuk payload seperti dokumentasi: parts = [file_data(file_uri=...), text(...)]
        content_payload = None
        try:
            from google.generativeai import types as genai_types  # type: ignore
            content_payload = genai_types.Content(parts=[
                genai_types.Part(file_data=genai_types.FileData(file_uri=youtube_url)),
                genai_types.Part(text=prompt),
            ])
        except Exception:
            # Fallback dict-based content
            content_payload = {
                "parts": [
                    {"file_data": {"file_uri": youtube_url}},
                    {"text": prompt},
                ]
            }

        t0 = time.time()
        resp = None
        last_exc = None
        for ki, k in enumerate(available_keys, 1):
            try:
                # Inisialisasi client per-key
                try:
                    from google.generativeai import Client as _GenClient  # type: ignore
                    client = _GenClient(api_key=k)
                except Exception:
                    client = None
                genai.configure(api_key=k)
                t0 = time.time()
                if client is not None:
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=content_payload,
                        generation_config={"temperature": 0.2, "response_mime_type": "text/plain"},
                        safety_settings=safety_settings,
                        request_options={"timeout": 300}
                    )
                else:
                    model = genai.GenerativeModel(model_name=model_name,
                        generation_config={"temperature": 0.2, "response_mime_type": "text/plain"},
                        safety_settings=safety_settings)
                    resp = model.generate_content(content_payload, request_options={"timeout": 300})
                log(f"[API] Transkripsi selesai via key#{ki}/{len(available_keys)} dalam {time.time()-t0:.1f}s")
                break
            except Exception as e:
                last_exc = e
                msg = str(e)
                low = msg.lower()
                if ('429' in msg) or ('toomanyrequests' in low) or ('quota' in low):
                    if am:
                        try:
                            am.set_key_cooldown(k, 24*3600)
                            log(f"[API] Transkrip: key#{ki} dibatasi (429/quota). Cooldown & coba key berikutnya...")
                        except Exception:
                            pass
                    time.sleep(1.5)
                    continue
                else:
                    log(f"[API] Transkrip gagal via key#{ki}: {e}")
                    time.sleep(1.0)
                    continue
        if resp is None:
            log(f"[API] Transkripsi gagal pada semua key: {last_exc}")
            return None, info
        txt = (resp.text or "").strip()
        # Bersihkan code fence jika ada
        if txt.startswith("```"):
            if txt.lower().startswith("```srt"):
                txt = txt[6:]
            else:
                txt = txt[3:]
            if txt.endswith("```"):
                txt = txt[:-3]
            txt = txt.strip()

        # Normalisasi SRT: pastikan penomoran berurutan, times valid, CRLF
        import re as _re
        def _parse_time(s: str) -> int | None:
            m = _re.match(r"^(\d\d):(\d\d):(\d\d),(\d\d\d)$", s.strip())
            if not m:
                return None
            h, mi, se, ms = map(int, m.groups())
            return ((h*60+mi)*60+se)*1000 + ms
        def _fmt_time_ms(ms: int) -> str:
            ms = max(0, int(ms))
            h = ms // 3600000; ms %= 3600000
            m = ms // 60000; ms %= 60000
            s = ms // 1000; ms %= 1000
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
        lines = [l.rstrip("\r") for l in txt.splitlines()]
        cues = []
        i = 0
        time_re = _re.compile(r"^(\d\d:\d\d:\d\d,\d\d\d)\s+-->\s+(\d\d:\d\d:\d\d,\d\d\d)$")
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1; continue
            # optional index line
            idx_line = None
            if line.isdigit():
                idx_line = int(line)
                i += 1
                if i >= len(lines): break
                line = lines[i].strip()
            m = time_re.match(line)
            if not m:
                i += 1
                continue
            start_s, end_s = m.group(1), m.group(2)
            i += 1
            # collect text lines until blank
            text_lines = []
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].strip())
                i += 1
            # skip blank separator
            while i < len(lines) and not lines[i].strip():
                i += 1
            text = " ".join(text_lines).strip()
            if not text:
                continue
            st = _parse_time(start_s)
            et = _parse_time(end_s)
            if st is None or et is None:
                continue
            if et <= st:
                et = st + 200
            cues.append((st, et, text))

        if not cues:
            log("Transkrip SRT kosong atau tidak dikenali.")
            return None, info

        # Bangun ulang SRT dengan penomoran berurutan
        cues.sort(key=lambda x: (x[0], x[1]))
        out_lines = []
        for n, (st, et, text) in enumerate(cues, start=1):
            out_lines.append(str(n))
            out_lines.append(f"{_fmt_time_ms(st)} --> {_fmt_time_ms(et)}")
            out_lines.append(text)
            out_lines.append("")

        srt_path = str(Path(output_folder) / "youtube_transcript_wordlevel.srt")
        with open(srt_path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write("\r\n".join(out_lines))
        log(f"SRT word-level tersimpan: {srt_path}")

        return srt_path, info
    except Exception as e:
        log(f"Transkripsi YouTube gagal: {e}")
        log(traceback.format_exc())
        return None, info
    finally:
        pass




