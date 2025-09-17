# language_detector.py
# Modul untuk mendeteksi bahasa dari file subtitle (SRT).

from langdetect import detect, LangDetectException
import re

def _clean_srt_text(content):
    """Membersihkan teks SRT dari timestamp, nomor, dan tag HTML untuk akurasi deteksi."""
    content = re.sub(r'^\d+\s*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}', '', content)
    content = re.sub(r'<[^>]+>', '', content)
    cleaned_text = '\n'.join(line for line in content.splitlines() if line.strip())
    return cleaned_text

def detect_language_from_srt(srt_path: str, default_lang='en') -> str:
    """Mendeteksi kode bahasa (misal: 'en', 'id') dari sampel file SRT."""
    try:
        with open(srt_path, 'r', encoding='utf-8', errors='ignore') as f:
            sample_content = "".join([next(f) for _ in range(100) if f])

        text_to_detect = _clean_srt_text(sample_content)
        if not text_to_detect.strip():
            print("Peringatan: Teks tidak ditemukan di sampel SRT untuk deteksi bahasa.")
            return default_lang

        lang_code = detect(text_to_detect)
        print(f"Bahasa terdeteksi: {lang_code}")
        return lang_code

    except Exception:
        print("Peringatan: Tidak dapat mendeteksi bahasa. Menggunakan default.")
        return default_lang
