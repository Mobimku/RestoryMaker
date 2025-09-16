# ⚙️ Flow Editing Video Recap (Berbasis JSON + VO)

## Input
- `film.mp4` → video sumber
- `storyboard.json` → hasil dari prompt
- `vo_{SEG}.wav` → VO per segmen (hasil Gemini TTS)

## Alur Editing Per Segmen
1. **Potong Timeblocks dari JSON**
   ```bash
   ffmpeg -ss {start} -to {end} -i film.mp4 -c copy tb_000.mp4
   ```
   → gabungkan jadi `seg_raw.mp4`

2. **Pecah seg_raw jadi klip 3–6 detik**
   ```bash
   ffmpeg -i seg_raw.mp4 -c copy -map 0 -f segment -segment_time 5 clip_%03d.mp4
   ```

3. **Pilih urutan acak**
   - Shuffle klip (`random.shuffle`)
   - Simpan ke `order_raw.txt`

4. **Apply Efek Template**
   - Boost color  
     ```bash
     -vf "eq=contrast=1.1:saturation=1.25"
     ```
   - Random zoom/pan (scale 1.3–1.6)  
     ```bash
     -vf "crop=iw*0.7:ih*0.7:x=iw*0.15:y=ih*0.15,scale=1280:720"
     ```

5. **Rakit video sesuai VO**
   - Concat → `seg_joined.mp4`
   - Cek durasi video vs VO
   - Jika terlalu panjang → trim klip terakhir
   - Jika terlalu pendek → tambah klip dari pool
   - Retime tipis (≤2%)  
     ```bash
     ffmpeg -i seg_joined.mp4 -filter:v "setpts=(DUR_VO/DUR_VIDEO)*PTS" seg_fit.mp4
     ```

6. **Replace audio dengan VO**
   ```bash
   ffmpeg -i seg_fit.mp4 -i vo_Intro.wav      -map 0:v -map 1:a -c:v libx264 -crf 22 -preset medium      -c:a aac -b:a 192k seg_intro.mp4
   ```

## Output Per Segmen
- `seg_intro.mp4`, `seg_rising.mp4`, dst.

## Final Concat
Gabungkan semua segmen + crossfade antar segmen:
```bash
ffmpeg -i seg_intro.mp4 -i seg_rising.mp4 -filter_complex "[0:v][1:v]xfade=transition=fade:duration=1:offset=179[v];  [0:a][1:a]acrossfade=d=1[a]" -map "[v]" -map "[a]" FINAL.mp4
```

---

## Logic Editing (Ringkasan)
- **Timeblocks** dari JSON jadi bahan utama, bukan deteksi visual otomatis.
- **Shuffle klip** + efek transformasi = membuat hasil *transformative* dan aman dari Content ID.
- **VO sebagai patokan durasi** → sinkronisasi penuh antara narasi dan visual.
- **Crossfade hanya antar segmen**, bukan antar klip, agar durasi VO tidak bergeser.
- **Audio film asli dibuang**, diganti VO (dan optional BGM).

Hasil akhirnya: recap panjang 18–25 menit dengan gaya *transformative editing*.
