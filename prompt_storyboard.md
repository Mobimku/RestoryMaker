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
        {"start": "HH:MM:SS.mmm", "end": "HH:MM:SS.mmm", "reason": "…"},
        {"start": "…", "end": "…", "reason": "…"}
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
        {"at_ms": 0, "action": "logo/titlecard optional"},
        {"at_ms": 8000, "action": "establishing wide → crop-pan"},
        {"at_ms": 18000, "action": "insert pip+blur for commentary"}
      ]
    },
    { "label": "Rising", … },
    { "label": "Mid-conflict", … },
    { "label": "Climax", … },
    { "label": "Ending", … }
  ]
}
