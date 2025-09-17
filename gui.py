# gui.py
# This file will contain the GUI for the RestoryMaker application using CustomTkinter.

import customtkinter as ctk
from tkinter import filedialog, messagebox
import os
import threading
import json
import api_handler
import video_processor
import ffmpeg_utils
from pathlib import Path
import api_manager
import language_detector # New import

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("RestoryMaker"); self.geometry("1200x800")

        self.api_manager = api_manager.APIManager()
        self.processing_thread = None
        self.stop_event = threading.Event()

        self.mp4_path = ctk.StringVar(); self.srt_path = ctk.StringVar(); self.output_folder = ctk.StringVar(); self.storyboard_path = ctk.StringVar()
        self.bgm_path = ctk.StringVar(); self.selected_api_key = ctk.StringVar()

        self.segment_order = ["Intro", "Rising", "Mid-conflict", "Climax", "Ending"]
        self.segment_vars = {name: ctk.BooleanVar(value=True) for name in self.segment_order}
        # VO override (user-supplied audio per segment)
        self.vo_override_enabled = {name: ctk.BooleanVar(value=False) for name in self.segment_order}
        self.vo_override_files = {name: ctk.StringVar(value="") for name in self.segment_order}
        self.process_all_segments = ctk.BooleanVar(value=True)

        ctk.set_appearance_mode("Dark"); ctk.set_default_color_theme("blue")
        self.tab_view = ctk.CTkTabview(self); self.tab_view.pack(padx=10, pady=10, fill="both", expand=True)
        self.editor_tab = self.tab_view.add("Editor"); self.api_tab = self.tab_view.add("API Management"); self.tutorial_tab = self.tab_view.add("Tutorial")

        self.setup_editor_tab(); self.setup_api_tab(); self.setup_tutorial_tab()

    def setup_editor_tab(self):
        # ... (This method is unchanged)
        # Samakan lebar panel kiri/kanan
        self.editor_tab.grid_columnconfigure(0, weight=1); self.editor_tab.grid_columnconfigure(1, weight=1); self.editor_tab.grid_rowconfigure(1, weight=1)
        left_col = ctk.CTkScrollableFrame(self.editor_tab); left_col.grid(row=0, column=0, rowspan=3, padx=10, pady=10, sticky="nsew")
        right_col = ctk.CTkScrollableFrame(self.editor_tab); right_col.grid(row=0, column=1, rowspan=3, padx=10, pady=10, sticky="nsew")
        file_io_frame = ctk.CTkFrame(left_col); file_io_frame.pack(padx=10, pady=10, fill="x"); file_io_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(file_io_frame, text="MP4 File").grid(row=0, column=0, **self.grid_opts("w")); ctk.CTkEntry(file_io_frame, textvariable=self.mp4_path, state="disabled").grid(row=0, column=1, **self.grid_opts("ew")); ctk.CTkButton(file_io_frame, text="Browse...", command=self._select_mp4_file).grid(row=0, column=2, **self.grid_opts())
        ctk.CTkLabel(file_io_frame, text="SRT File").grid(row=1, column=0, **self.grid_opts("w")); ctk.CTkEntry(file_io_frame, textvariable=self.srt_path, state="disabled").grid(row=1, column=1, **self.grid_opts("ew")); ctk.CTkButton(file_io_frame, text="Browse...", command=self._select_srt_file).grid(row=1, column=2, **self.grid_opts())
        ctk.CTkLabel(file_io_frame, text="Output Folder").grid(row=2, column=0, **self.grid_opts("w")); ctk.CTkEntry(file_io_frame, textvariable=self.output_folder, state="disabled").grid(row=2, column=1, **self.grid_opts("ew")); ctk.CTkButton(file_io_frame, text="Browse...", command=self._select_output_folder).grid(row=2, column=2, **self.grid_opts())
        ctk.CTkLabel(file_io_frame, text="Storyboard JSON (optional)").grid(row=3, column=0, **self.grid_opts("w")); ctk.CTkEntry(file_io_frame, textvariable=self.storyboard_path, state="disabled").grid(row=3, column=1, **self.grid_opts("ew")); ctk.CTkButton(file_io_frame, text="Browse...", command=self._select_storyboard_file).grid(row=3, column=2, **self.grid_opts())
        # Perkecil log box agar panel kiri tidak terlalu panjang
        self.log_box = ctk.CTkTextbox(left_col, state="disabled", wrap="word", height=180); self.log_box.pack(padx=10, pady=10, fill="x")
        progress_frame = ctk.CTkFrame(left_col); progress_frame.pack(padx=10, pady=10, fill="x"); progress_frame.grid_columnconfigure(0, weight=1)
        self.progress_bar = ctk.CTkProgressBar(progress_frame); self.progress_bar.set(0); self.progress_bar.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        self.start_button = ctk.CTkButton(progress_frame, text="Start Processing", command=self._start_processing); self.start_button.grid(row=0, column=1, padx=10, pady=10)
        self.stop_button = ctk.CTkButton(progress_frame, text="Stop", command=self._stop_processing, state="disabled"); self.stop_button.grid(row=0, column=2, padx=10, pady=10)
        canvas_frame = ctk.CTkFrame(right_col); canvas_frame.pack(padx=10, pady=10, fill="x")
        ctk.CTkLabel(canvas_frame, text="Canvas Settings", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10)
        ctk.CTkLabel(canvas_frame, text="Letterbox (movie bars) diterapkan default saat render akhir.", wraplength=350, text_color="gray").pack(anchor="w", padx=10, pady=5)
        # Pindahkan Recap Duration ke panel kiri agar panel kanan tidak terlalu panjang
        duration_frame = ctk.CTkFrame(left_col); duration_frame.pack(padx=10, pady=10, fill="x")
        ctk.CTkLabel(duration_frame, text="Recap Duration (minutes)", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10)
        self.recap_minutes_var = ctk.StringVar(value="22")
        self.recap_minutes_menu = ctk.CTkOptionMenu(duration_frame, values=[str(x) for x in range(18, 26)], variable=self.recap_minutes_var)
        self.recap_minutes_menu.pack(fill="x", padx=10, pady=5)
        # Storyboard model selector
        model_frame = ctk.CTkFrame(left_col); model_frame.pack(padx=10, pady=10, fill="x")
        ctk.CTkLabel(model_frame, text="Storyboard Model", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10)
        self.storyboard_model_var = ctk.StringVar(value="gemini-2.5-flash")
        self.storyboard_model_menu = ctk.CTkOptionMenu(model_frame, values=["gemini-2.5-flash", "gemini-2.5-pro"], variable=self.storyboard_model_var)
        self.storyboard_model_menu.pack(fill="x", padx=10, pady=5)
        audio_frame = ctk.CTkFrame(right_col); audio_frame.pack(padx=10, pady=10, fill="x", pady_=(10,10))
        ctk.CTkLabel(audio_frame, text="Audio Settings", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10)
        ctk.CTkLabel(audio_frame, text="Main VO Volume (%)").pack(anchor="w", padx=10, pady=(10, 0))
        self.main_vol_slider = ctk.CTkSlider(audio_frame, from_=0, to=300); self.main_vol_slider.pack(fill="x", padx=10, pady=5); self.main_vol_slider.set(100)
        # Voice name untuk TTS
        ctk.CTkLabel(audio_frame, text="Voice Name (opsional)").pack(anchor="w", padx=10, pady=(10, 0))
        self.voice_name_entry = ctk.CTkEntry(audio_frame); self.voice_name_entry.pack(fill="x", padx=10, pady=5)
        # TTS Language override
        ctk.CTkLabel(audio_frame, text="TTS Language").pack(anchor="w", padx=10, pady=(10, 0))
        self.tts_lang_var = ctk.StringVar(value="auto")
        tts_lang_values = [
            "auto","ar","da","de","el","en","es","fi","fr","he","hi","it","ja","ko","ms","nl","no","pl","pt","ru","sv","sw","tr","zh","id"
        ]
        self.tts_lang_menu = ctk.CTkOptionMenu(audio_frame, values=tts_lang_values, variable=self.tts_lang_var)
        self.tts_lang_menu.pack(fill="x", padx=10, pady=5)
        # TTS Device (diabaikan untuk Gemini; sembunyikan opsi)
        self.tts_device_var = ctk.StringVar(value="cpu")
        # Voice Prompt Audio dihapus (tidak diperlukan)
        ctk.CTkButton(audio_frame, text="Add Background Music...", command=self._select_bgm_file).pack(fill="x", padx=10, pady=10)
        self.bgm_label = ctk.CTkLabel(audio_frame, text="No BGM file selected.", text_color="gray", wraplength=250); self.bgm_label.pack(anchor="w", padx=10)
        ctk.CTkLabel(audio_frame, text="BGM Volume (%)").pack(anchor="w", padx=10, pady=(10, 0))
        self.bgm_vol_slider = ctk.CTkSlider(audio_frame, from_=0, to=100); self.bgm_vol_slider.pack(fill="x", padx=10, pady=5); self.bgm_vol_slider.set(10); self.bgm_vol_slider.configure(state="disabled")
        ctk.CTkLabel(audio_frame, text="BGM Segment").pack(anchor="w", padx=10, pady=(10, 0))
        self.bgm_segment_var = ctk.StringVar(value="")
        self.bgm_segment_menu = ctk.CTkOptionMenu(audio_frame, values=[""], variable=self.bgm_segment_var)
        self.bgm_segment_menu.pack(fill="x", padx=10, pady=5); self.bgm_segment_menu.configure(state="disabled")
        segment_frame = ctk.CTkFrame(right_col); segment_frame.pack(padx=10, pady=10, fill="x")
        ctk.CTkLabel(segment_frame, text="Segment Processing", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10)
        ctk.CTkCheckBox(segment_frame, text="Process All Segments", variable=self.process_all_segments, command=self._toggle_all_segments).pack(anchor="w", padx=10, pady=5)
        self.segment_checkboxes = {}
        for name in self.segment_vars:
            cb = ctk.CTkCheckBox(segment_frame, text=name, variable=self.segment_vars[name]); cb.pack(anchor="w", padx=30, pady=2); self.segment_checkboxes[name] = cb
        self._toggle_all_segments()

        # VO override section (pindah ke panel kiri)
        vo_override_frame = ctk.CTkFrame(left_col)
        vo_override_frame.pack(padx=10, pady=10, fill="x")
        ctk.CTkLabel(vo_override_frame, text="VO Override (gunakan audio Anda per-segmen)", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10)
        ctk.CTkLabel(vo_override_frame, text="Catatan: Jika memakai VO override, wajib memilih Storyboard JSON terlebih dahulu.", text_color="orange").pack(anchor="w", padx=10, pady=(0,5))
        self.vo_override_browse = {}
        self.vo_override_labels = {}
        for name in self.segment_order:
            row = ctk.CTkFrame(vo_override_frame)
            row.pack(fill="x", padx=10, pady=4)
            c = ctk.CTkCheckBox(row, text=name, variable=self.vo_override_enabled[name], command=lambda n=name: self._toggle_vo_override(n))
            c.pack(side="left")
            btn = ctk.CTkButton(row, text="Browse VO...", command=lambda n=name: self._browse_vo_override(n))
            btn.pack(side="left", padx=6)
            lbl = ctk.CTkLabel(row, text="No file selected.", text_color="gray")
            lbl.pack(side="left", padx=6)
            self.vo_override_browse[name] = btn
            self.vo_override_labels[name] = lbl
            # Default disabled until checked
            btn.configure(state="disabled")

    def _toggle_all_segments(self):
        state = "disabled" if self.process_all_segments.get() else "normal"
        for cb in self.segment_checkboxes.values():
            cb.configure(state=state)
            if state == "disabled": self.segment_vars[cb.cget("text")].set(True)

    def grid_opts(self, sticky=""): return {"padx": 10, "pady": 5, "sticky": sticky}
    def _select_mp4_file(self): self.mp4_path.set(filedialog.askopenfilename(filetypes=[("MP4", "*.mp4")]))
    def _select_srt_file(self): self.srt_path.set(filedialog.askopenfilename(filetypes=[("SRT", "*.srt")]))
    def _select_output_folder(self): self.output_folder.set(filedialog.askdirectory())
    def _select_storyboard_file(self): self.storyboard_path.set(filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All", "*.*")]))
    def _toggle_vo_override(self, name: str):
        enabled = self.vo_override_enabled[name].get()
        self.vo_override_browse[name].configure(state="normal" if enabled else "disabled")
        if not enabled:
            self.vo_override_files[name].set("")
            self.vo_override_labels[name].configure(text="No file selected.", text_color="gray")
    def _browse_vo_override(self, name: str):
        path = filedialog.askopenfilename(filetypes=[("Audio", "*.mp3 *.wav *.m4a *.flac *.aac"), ("All", "*.*")])
        if path:
            self.vo_override_files[name].set(path)
            self.vo_override_labels[name].configure(text=os.path.basename(path), text_color="white")
        else:
            self.vo_override_files[name].set("")
            self.vo_override_labels[name].configure(text="No file selected.", text_color="gray")
    # Voice Prompt handler dihapus
    def _select_bgm_file(self):
        path = filedialog.askopenfilename(filetypes=[("Audio", "*.mp3 *.wav")]);
        if path:
            self.bgm_path.set(path); self.bgm_label.configure(text=os.path.basename(path), text_color="white")
            # Enable controls
            self.bgm_vol_slider.configure(state="normal")
            # Populate segment dropdown with currently selected segments
            current_segments = [name for name, var in self.segment_vars.items() if var.get()]
            if not current_segments:
                current_segments = list(self.segment_vars.keys())
            self.bgm_segment_menu.configure(values=current_segments)
            # Set default to first
            self.bgm_segment_var.set(current_segments[0] if current_segments else "")
            self.bgm_segment_menu.configure(state="normal")
        else:
            self.bgm_path.set(""); self.bgm_label.configure(text="No BGM file selected.", text_color="gray")
            self.bgm_vol_slider.configure(state="disabled")
            self.bgm_segment_var.set("")
            self.bgm_segment_menu.configure(values=[""], state="disabled")

    def log_message(self, msg): self.after(0, self._log_message_thread_safe, msg)
    def _log_message_thread_safe(self, msg):
        self.log_box.configure(state="normal"); self.log_box.insert("end", str(msg) + "\n"); self.log_box.configure(state="disabled"); self.log_box.see("end")

    def _start_processing(self):
        api_key_to_use = self.api_manager.get_key()
        mp4 = self.mp4_path.get()
        srt = self.srt_path.get()
        storyboard_json = self.storyboard_path.get()
        use_existing_storyboard = bool(storyboard_json and os.path.isfile(storyboard_json))

        if not mp4 or not self.output_folder.get():
            self.log_message("ERROR: Silakan pilih file MP4 dan Folder Output."); return
        # Jika ada VO override dipilih, wajib storyboard json
        any_override = any(self.vo_override_enabled[n].get() and self.vo_override_files[n].get() for n in self.segment_order)
        if any_override and not use_existing_storyboard:
            self.log_message("ERROR: Jika menggunakan VO Override, wajib memilih Storyboard JSON terlebih dahulu."); return
        if not use_existing_storyboard:
            if not srt:
                self.log_message("ERROR: Tidak ada SRT. Pilih SRT atau berikan Storyboard JSON."); return
            if not api_key_to_use:
                self.log_message("ERROR: Tambahkan API Key untuk generate storyboard dari Gemini, atau berikan Storyboard JSON."); return

        film_duration_sec = ffmpeg_utils.get_duration(mp4)
        if not film_duration_sec: self.log_message(f"ERROR: Tidak dapat membaca durasi dari file video: {mp4}"); return

        if not use_existing_storyboard:
            self.log_message(f"Mendeteksi bahasa dari {os.path.basename(srt)}...")
            detected_lang = language_detector.detect_language_from_srt(srt)
            self.log_message(f"Bahasa terdeteksi: '{detected_lang}'.")
        else:
            detected_lang = "en"

        self.start_button.configure(state="disabled"); self.stop_button.configure(state="normal"); self.stop_event.clear()
        # Pass selected recap duration (int)
        try:
            recap_minutes = int(self.recap_minutes_var.get())
        except Exception:
            recap_minutes = 22
        self.fast_storyboard_var = getattr(self, 'fast_storyboard_var', None)
        if self.fast_storyboard_var is None:
            self.fast_storyboard_var = ctk.BooleanVar(value=True)
        model_choice = getattr(self, 'storyboard_model_var', None).get() if hasattr(self, 'storyboard_model_var') else "gemini-2.5-flash"
        self.processing_thread = threading.Thread(target=self._processing_thread_target, args=(film_duration_sec, api_key_to_use, detected_lang, recap_minutes, model_choice)); self.processing_thread.start()

    def _stop_processing(self): self.log_message("STOP signal sent..."); self.stop_event.set()

    def _processing_thread_target(self, film_duration_sec, api_key, language_code, recap_minutes, model_choice):
        try:
            selected_segments = [name for name, var in self.segment_vars.items() if var.get()]
            if not selected_segments: raise Exception("No segments selected for processing.")

            # Validasi BGM segment: hanya izinkan dari segmen yang dipilih
            chosen_bgm_segment = self.bgm_segment_var.get()
            if chosen_bgm_segment and chosen_bgm_segment not in selected_segments:
                # Jika user memilih segmen yang tidak aktif, fallback ke segmen pertama aktif
                chosen_bgm_segment = selected_segments[0]

            user_settings = {
                "main_vo_volume": self.main_vol_slider.get() / 100.0,
                "bgm_path": self.bgm_path.get(),
                "bgm_volume": self.bgm_vol_slider.get() / 100.0,
                "bgm_segment": chosen_bgm_segment,
                "output_path": str(Path(self.output_folder.get()) / f"{Path(self.mp4_path.get()).stem}_recap.mp4"),
                "selected_segments": selected_segments,
                "process_all": self.process_all_segments.get()
            }

            storyboard_file = self.storyboard_path.get()
            if storyboard_file and os.path.isfile(storyboard_file):
                self.log_message(f"Memuat storyboard dari file: {os.path.basename(storyboard_file)} (melewati Gemini)")
                # Robust JSON loader: tangani code fences dan teks non-JSON murni
                raw = ""
                with open(storyboard_file, "r", encoding="utf-8", errors="ignore") as f:
                    raw = f.read()
                txt = raw.lstrip("\ufeff").strip()
                if not txt:
                    raise Exception("File storyboard kosong atau tidak dapat dibaca.")
                # Hilangkan code fence markdown jika ada
                for fence in ("```json", "```JSON", "```", "\u0060\u0060\u0060json"):
                    if txt.startswith(fence):
                        txt = txt[len(fence):].strip()
                if txt.endswith("```"):
                    txt = txt[:-3].strip()
                # Coba parse langsung (JSON) atau JSON5 jika tersedia
                try:
                    storyboard = json.loads(txt)
                except Exception:
                    try:
                        import json5  # type: ignore
                        storyboard = json5.loads(txt)
                    except Exception:
                        # Normalisasi: hapus komentar // ... atau /* ... */ dan trailing commas, lalu quote key tak ber-quote, dan ubah string single-quote -> double-quote jika aman
                        import re
                        cand_full = txt
                        # Hapus tag citation khusus dari model (di luar/di dalam string)
                        cand_full = re.sub(r"\[cite_start\]", "", cand_full)
                        cand_full = re.sub(r"\[cite_end\]", "", cand_full)
                        cand_full = re.sub(r"\[cite:[^\]]+\]", "", cand_full)
                        # Hapus komentar blok /* ... */
                        cand_full = re.sub(r"/\*.*?\*/", "", cand_full, flags=re.S)
                        # Hapus komentar single-line //...
                        cand_full = re.sub(r"(^|\s)//.*$", "", cand_full, flags=re.M)
                        # Hapus trailing comma sebelum } atau ]
                        cand_full = re.sub(r",\s*([}\]])", r"\1", cand_full)
                        # Quote key yang tak ber-quote: cari setelah { atau ,
                        cand_full = re.sub(r'([\{,]\s*)([A-Za-z_][\w\-]*)\s*:', r'\1"\2":', cand_full)
                        # Ubah string single-quoted menjadi double-quoted (hindari yang sudah dalam tanda kutip ganda)
                        cand_full = re.sub(r"'(.*?)'", lambda m: '"' + m.group(1).replace('"', '\\"') + '"', cand_full)
                        try:
                            storyboard = json.loads(cand_full)
                        except Exception:
                            # Coba ekstrak blok JSON pertama berdasarkan kurung kurawal dari teks yang sudah dinormalisasi
                            i = cand_full.find("{"); j = cand_full.rfind("}")
                        if i != -1 and j != -1 and j > i:
                            cand = cand_full[i:j+1]
                            try:
                                storyboard = json.loads(cand)
                            except Exception as e:
                                # Simpan debug
                                dbg = Path(self.output_folder.get()) / "storyboard_load_error.txt"
                                with open(dbg, "w", encoding="utf-8") as df:
                                    df.write(raw)
                                raise Exception(f"Gagal parsing JSON dari file storyboard. Detail: {e}. Salinan mentah disimpan ke {dbg}")
                        else:
                            dbg = Path(self.output_folder.get()) / "storyboard_load_error.txt"
                            with open(dbg, "w", encoding="utf-8") as df:
                                df.write(raw)
                            raise Exception(f"Format file storyboard tidak berisi JSON valid. Salinan mentah disimpan ke {dbg}")
            else:
                storyboard = api_handler.get_storyboard_from_srt(
                    self.srt_path.get(), api_key, int(film_duration_sec), self.output_folder.get(), language_code,
                    self.log_message, recap_minutes,
                    fast_mode=(model_choice == "gemini-2.5-flash"),
                    storyboard_model=model_choice
                )
                if not storyboard: raise Exception("Gagal mendapatkan storyboard dari API.")

            vo_audio_map = {}; temp_audio_dir = Path(self.output_folder.get()) / "temp_audio"; temp_audio_dir.mkdir(exist_ok=True)
            for segment in storyboard.get('segments', []):
                if self.stop_event.is_set(): raise InterruptedError("Proses dihentikan oleh pengguna.")
                label = segment['label']
                if label not in selected_segments: continue

                script = segment['vo_script']
                vo_lang = segment.get('vo_language', language_code)
                output_path = temp_audio_dir / f"vo_{label}.mp3"

                # VO override: jika user menyediakan file, gunakan dan lewati TTS
                if self.vo_override_enabled.get(label, ctk.BooleanVar(value=False)).get():
                    candidate = self.vo_override_files.get(label).get()
                    if candidate and os.path.isfile(candidate):
                        self.log_message(f"Menggunakan VO override untuk segmen '{label}': {os.path.basename(candidate)}")
                        vo_audio_map[label] = candidate
                        continue

                voice_name = self.voice_name_entry.get().strip()
                # Coba rotasi semua API key yang tersimpan jika terjadi quota/429
                keys = self.api_manager.get_keys() or [api_key]
                audio_success = False
                # Tentukan bahasa TTS final dari dropdown (override) atau auto
                selected_tts_lang = self.tts_lang_var.get().strip().lower() if self.tts_lang_var else "auto"
                final_tts_lang = vo_lang
                if selected_tts_lang and selected_tts_lang != "auto": final_tts_lang = selected_tts_lang
                # Baca konfigurasi chunk durasi (detik)
                try:
                    max_chunk_sec = int(self.tts_chunk_entry.get().strip()) if self.tts_chunk_entry.get().strip() else 180
                except Exception:
                    max_chunk_sec = 180

                for idx, k in enumerate(keys, start=1):
                    if self.stop_event.is_set(): raise InterruptedError("Proses dihentikan oleh pengguna.")
                    self.log_message(f"Mencoba TTS untuk segmen '{label}' dengan API key #{idx}/{len(keys)}...")
                    if api_handler.generate_vo_audio(
                        vo_script=script,
                        api_key=k,
                        output_path=str(output_path),
                        language_code=final_tts_lang,
                        voice_name=voice_name,
                        progress_callback=self.log_message,
                        tts_device=self.tts_device_var.get(),
                        voice_prompt_path="",
                        speech_rate_wpm=(segment.get('vo_meta', {}).get('speech_rate_wpm') if isinstance(segment.get('vo_meta'), dict) else None),
                        max_chunk_sec=max_chunk_sec,
                    ):
                        audio_success = True
                        break
                if not audio_success:
                    raise Exception(f"Gagal total saat menghasilkan audio untuk segmen '{label}'. Semua API key kehabisan kuota atau gagal.")

                vo_audio_map[label] = str(output_path)

            # Sisipkan peta VO ke user_settings agar processor bisa menghitung timing BGM
            user_settings["_vo_audio_map"] = vo_audio_map
            final_path = video_processor.process_video(storyboard, self.mp4_path.get(), vo_audio_map, user_settings, self.stop_event, self.log_message)
            if not final_path: raise Exception("Pemrosesan video gagal.")
            if isinstance(final_path, list):
                self.log_message("SUKSES: Proses selesai. Video per-segmen:")
                for p in final_path:
                    self.log_message(f"  - {p}")
            else:
                self.log_message(f"SUKSES: Proses selesai. Video akhir di: {final_path}")
        except InterruptedError as e: self.log_message(f"STOPPED: {e}")
        except Exception as e: import traceback; self.log_message(f"FATAL ERROR: {e}"); self.log_message(traceback.format_exc())
        finally: self.after(0, lambda: (self.start_button.configure(state="normal"), self.stop_button.configure(state="disabled")))

    def setup_api_tab(self):
        self.api_tab.grid_columnconfigure(0, weight=1)
        add_frame = ctk.CTkFrame(self.api_tab); add_frame.pack(padx=10, pady=10, fill="x")
        ctk.CTkLabel(add_frame, text="New API Key:").pack(side="left", padx=10)
        self.new_api_key_entry = ctk.CTkEntry(add_frame, width=400, show="*"); self.new_api_key_entry.pack(side="left", fill="x", expand=True, padx=10)
        ctk.CTkButton(add_frame, text="Add Key", command=self._add_api_key).pack(side="left", padx=10)
        self.key_list_frame = ctk.CTkScrollableFrame(self.api_tab, label_text="Saved API Keys")
        self.key_list_frame.pack(padx=10, pady=10, fill="both", expand=True)
        mgmt_frame = ctk.CTkFrame(self.api_tab); mgmt_frame.pack(padx=10, pady=10, fill="x")
        ctk.CTkButton(mgmt_frame, text="Delete Selected", command=self._delete_api_key, fg_color="red").pack(side="left", padx=10)
        ctk.CTkButton(mgmt_frame, text="Clear All", command=self._clear_api_keys, fg_color="gray").pack(side="right", padx=10)
        # Cooldown status section
        self.key_status_frame = ctk.CTkScrollableFrame(self.api_tab, label_text="Key Status (Cooldown)")
        self.key_status_frame.pack(padx=10, pady=10, fill="both", expand=True)
        btn_row = ctk.CTkFrame(self.api_tab); btn_row.pack(padx=10, pady=5, fill="x")
        ctk.CTkButton(btn_row, text="Refresh Status", command=self._refresh_key_status).pack(side="left")
        self._refresh_api_key_list(); self._refresh_key_status()

    def _refresh_api_key_list(self):
        for widget in self.key_list_frame.winfo_children(): widget.destroy()
        keys = self.api_manager.get_keys()
        if not keys: ctk.CTkLabel(self.key_list_frame, text="No API keys saved.").pack(pady=10); self.selected_api_key.set(""); return
        for key in keys:
            masked_key = f"{key[:4]}...{key[-4:]}"; ctk.CTkRadioButton(self.key_list_frame, text=masked_key, variable=self.selected_api_key, value=key).pack(anchor="w", padx=10, pady=2)
        if self.selected_api_key.get() not in keys: self.selected_api_key.set(keys[0])

    def _add_api_key(self):
        new_key = self.new_api_key_entry.get()
        if new_key:
            if self.api_manager.add_key(new_key): self.log_message("API Key added successfully."); self._refresh_api_key_list(); self._refresh_key_status(); self.new_api_key_entry.delete(0, 'end')
            else: messagebox.showwarning("Warning", "API Key already exists or is invalid.")
        else: messagebox.showwarning("Warning", "API Key field cannot be empty.")

    def _delete_api_key(self):
        key_to_delete = self.selected_api_key.get()
        if key_to_delete:
            if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete the key ending in ...{key_to_delete[-4:]}?"):
                self.api_manager.delete_key(key_to_delete); self.log_message("API Key deleted."); self._refresh_api_key_list(); self._refresh_key_status()
        else: messagebox.showerror("Error", "No API Key selected for deletion.")

    def _clear_api_keys(self):
        if messagebox.askyesno("Confirm Clear All", "Are you sure you want to delete ALL saved API keys? This action cannot be undone."):
            self.api_manager.clear_all_keys(); self.log_message("All API keys have been cleared."); self._refresh_api_key_list(); self._refresh_key_status()

    def _refresh_key_status(self):
        for widget in self.key_status_frame.winfo_children(): widget.destroy()
        try:
            import api_manager as _am
            am = _am.APIManager()
            status_list = am.get_status_list()
            if not status_list:
                ctk.CTkLabel(self.key_status_frame, text="No API keys.").pack(pady=10)
                return
            for key, rem in status_list:
                masked = f"{key[:4]}...{key[-4:]}"
                if rem <= 0:
                    text = f"{masked}: available"
                else:
                    hrs = rem // 3600; mins = (rem % 3600) // 60
                    text = f"{masked}: cooldown {hrs}h {mins}m remaining"
                ctk.CTkLabel(self.key_status_frame, text=text).pack(anchor="w", padx=10, pady=2)
        except Exception as e:
            ctk.CTkLabel(self.key_status_frame, text=f"Error reading status: {e}").pack(pady=10)

    def setup_tutorial_tab(self): ctk.CTkLabel(self.tutorial_tab, text="Tutorial content will go here.").pack(padx=20, pady=20)

if __name__ == "__main__":
    app = App()
    app.mainloop()
