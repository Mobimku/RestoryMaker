# gui.py
# This file will contain the GUI for the RestoryMaker application using CustomTkinter.

import customtkinter as ctk
from tkinter import filedialog
import os
import threading
import api_handler
import video_processor
import ffmpeg_utils # Import to get duration
from pathlib import Path

class App(ctk.CTk):
    # ... (init and setup_editor_tab are the same) ...
    def __init__(self):
        super().__init__()
        self.title("RestoryMaker"); self.geometry("1200x800")
        self.api_key = None; self.processing_thread = None; self.stop_event = threading.Event()
        self.mp4_path = ctk.StringVar(); self.srt_path = ctk.StringVar(); self.output_folder = ctk.StringVar()
        self.watermark_path = ctk.StringVar(); self.bgm_path = ctk.StringVar()
        ctk.set_appearance_mode("Dark"); ctk.set_default_color_theme("blue")
        self.tab_view = ctk.CTkTabview(self); self.tab_view.pack(padx=10, pady=10, fill="both", expand=True)
        self.editor_tab = self.tab_view.add("Editor"); self.api_tab = self.tab_view.add("API Management"); self.tutorial_tab = self.tab_view.add("Tutorial")
        self.setup_editor_tab(); self.setup_api_tab(); self.setup_tutorial_tab()

    def setup_editor_tab(self):
        self.editor_tab.grid_columnconfigure(0, weight=2); self.editor_tab.grid_columnconfigure(1, weight=1); self.editor_tab.grid_rowconfigure(1, weight=1)
        left_col = ctk.CTkFrame(self.editor_tab); left_col.grid(row=0, column=0, rowspan=3, padx=10, pady=10, sticky="nsew"); left_col.grid_rowconfigure(1, weight=1)
        right_col = ctk.CTkFrame(self.editor_tab); right_col.grid(row=0, column=1, rowspan=3, padx=10, pady=10, sticky="nsew")
        file_io_frame = ctk.CTkFrame(left_col); file_io_frame.pack(padx=10, pady=10, fill="x"); file_io_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(file_io_frame, text="MP4 File").grid(row=0, column=0, **self.grid_opts("w")); ctk.CTkEntry(file_io_frame, textvariable=self.mp4_path, state="disabled").grid(row=0, column=1, **self.grid_opts("ew")); ctk.CTkButton(file_io_frame, text="Browse...", command=self._select_mp4_file).grid(row=0, column=2, **self.grid_opts())
        ctk.CTkLabel(file_io_frame, text="SRT File").grid(row=1, column=0, **self.grid_opts("w")); ctk.CTkEntry(file_io_frame, textvariable=self.srt_path, state="disabled").grid(row=1, column=1, **self.grid_opts("ew")); ctk.CTkButton(file_io_frame, text="Browse...", command=self._select_srt_file).grid(row=1, column=2, **self.grid_opts())
        ctk.CTkLabel(file_io_frame, text="Output Folder").grid(row=2, column=0, **self.grid_opts("w")); ctk.CTkEntry(file_io_frame, textvariable=self.output_folder, state="disabled").grid(row=2, column=1, **self.grid_opts("ew")); ctk.CTkButton(file_io_frame, text="Browse...", command=self._select_output_folder).grid(row=2, column=2, **self.grid_opts())
        self.log_box = ctk.CTkTextbox(left_col, state="disabled", wrap="word"); self.log_box.pack(padx=10, pady=10, fill="both", expand=True)
        progress_frame = ctk.CTkFrame(left_col); progress_frame.pack(padx=10, pady=10, fill="x"); progress_frame.grid_columnconfigure(0, weight=1)
        self.progress_bar = ctk.CTkProgressBar(progress_frame); self.progress_bar.set(0); self.progress_bar.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        self.start_button = ctk.CTkButton(progress_frame, text="Start Processing", command=self._start_processing); self.start_button.grid(row=0, column=1, padx=10, pady=10)
        self.stop_button = ctk.CTkButton(progress_frame, text="Stop", command=self._stop_processing, state="disabled"); self.stop_button.grid(row=0, column=2, padx=10, pady=10)
        canvas_frame = ctk.CTkFrame(right_col); canvas_frame.pack(padx=10, pady=10, fill="x")
        ctk.CTkLabel(canvas_frame, text="Canvas Settings", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10)
        self.bars_check = ctk.CTkCheckBox(canvas_frame, text="Add Black Bars"); self.bars_check.pack(anchor="w", padx=10, pady=5)
        self.bars_slider = ctk.CTkSlider(canvas_frame, from_=0, to=200); self.bars_slider.pack(fill="x", padx=10, pady=5)
        self.wm_check = ctk.CTkCheckBox(canvas_frame, text="Add Watermark"); self.wm_check.pack(anchor="w", padx=10, pady=(10,0))
        ctk.CTkButton(canvas_frame, text="Browse Watermark Image...", command=self._select_watermark_file).pack(fill="x", padx=10, pady=5)
        self.wm_pos_menu = ctk.CTkOptionMenu(canvas_frame, values=["top_right", "bottom_right", "top_left", "bottom_left"]); self.wm_pos_menu.pack(fill="x", padx=10, pady=5)
        audio_frame = ctk.CTkFrame(right_col); audio_frame.pack(padx=10, pady=10, fill="x", pady_=(20,10))
        ctk.CTkLabel(audio_frame, text="Audio Settings", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10)
        ctk.CTkButton(audio_frame, text="Add Background Music...", command=self._select_bgm_file).pack(fill="x", padx=10, pady=10)
        self.bgm_vol_slider = ctk.CTkSlider(audio_frame, from_=0, to=100); self.bgm_vol_slider.pack(fill="x", padx=10, pady=5); self.bgm_vol_slider.set(20)

    def grid_opts(self, sticky=""): return {"padx":10, "pady":5, "sticky":sticky}
    def _select_mp4_file(self): self.mp4_path.set(filedialog.askopenfilename(filetypes=[("MP4", "*.mp4")]))
    def _select_srt_file(self): self.srt_path.set(filedialog.askopenfilename(filetypes=[("SRT", "*.srt")]))
    def _select_output_folder(self): self.output_folder.set(filedialog.askdirectory())
    def _select_watermark_file(self): self.watermark_path.set(filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg")]))
    def _select_bgm_file(self): self.bgm_path.set(filedialog.askopenfilename(filetypes=[("Audio", "*.mp3 *.wav")]))

    def log_message(self, msg): self.after(0, self._log_message_thread_safe, msg)
    def _log_message_thread_safe(self, msg):
        self.log_box.configure(state="normal"); self.log_box.insert("end", str(msg) + "\n"); self.log_box.configure(state="disabled"); self.log_box.see("end")

    def _start_processing(self):
        mp4 = self.mp4_path.get()
        if not all([mp4, self.srt_path.get(), self.output_folder.get(), self.api_key]):
            self.log_message("ERROR: Please select MP4, SRT, Output Folder and set API Key before starting.")
            return

        film_duration_sec = ffmpeg_utils.get_duration(mp4)
        if not film_duration_sec:
            self.log_message(f"ERROR: Could not read duration from video file: {mp4}")
            return

        self.start_button.configure(state="disabled"); self.stop_button.configure(state="normal"); self.stop_event.clear()
        self.processing_thread = threading.Thread(target=self._processing_thread_target, args=(film_duration_sec,))
        self.processing_thread.start()

    def _stop_processing(self):
        self.log_message("STOP signal sent. Finishing current task and cleaning up...")
        self.stop_event.set()

    def _processing_thread_target(self, film_duration_sec):
        try:
            user_settings = {
                "black_bars_size": self.bars_slider.get() if self.bars_check.get() else 0,
                "watermark_path": self.watermark_path.get() if self.wm_check.get() else None,
                "watermark_pos": self.wm_pos_menu.get(),
                "bgm_path": self.bgm_path.get(),
                "bgm_volume": self.bgm_vol_slider.get() / 100.0,
                "output_path": str(Path(self.output_folder.get()) / f"{Path(self.mp4_path.get()).stem}_recap.mp4")
            }
            srt_content = Path(self.srt_path.get()).read_text(encoding='utf-8')

            if self.stop_event.is_set(): return
            self.log_message("Phase 1: Generating storyboard from Gemini API...")
            storyboard = api_handler.get_storyboard_from_srt(srt_content, self.api_key, int(film_duration_sec), "en")
            if not storyboard: raise Exception("Failed to get storyboard from API.")

            if self.stop_event.is_set(): return
            self.log_message("Phase 2: Generating voice-over audio...")
            vo_audio_map = {}; temp_audio_dir = Path(self.output_folder.get()) / "temp_audio"; temp_audio_dir.mkdir(exist_ok=True)
            for segment in storyboard.get('segments', []):
                if self.stop_event.is_set(): return
                label = segment['label']; script = segment['vo_script']
                output_path = temp_audio_dir / f"vo_{label}.wav"
                if not api_handler.generate_vo_audio(script, self.api_key, str(output_path)):
                    raise Exception(f"Failed to generate audio for segment {label}")
                vo_audio_map[label] = str(output_path)

            if self.stop_event.is_set(): return
            self.log_message("Phase 3: Starting video processing pipeline...")
            final_path = video_processor.process_video(storyboard, self.mp4_path.get(), vo_audio_map, user_settings, self.stop_event, self.log_message)
            if not final_path: raise Exception("Video processing failed.")

            self.log_message(f"SUCCESS: Processing complete. Final video at: {final_path}")
        except Exception as e:
            self.log_message(f"FATAL ERROR: {e}")
        finally:
            self.after(0, lambda: (self.start_button.configure(state="normal"), self.stop_button.configure(state="disabled")))

    def setup_api_tab(self):
        api_frame = ctk.CTkFrame(self.api_tab); api_frame.pack(padx=20, pady=20, fill="x")
        ctk.CTkLabel(api_frame, text="Google API Key").pack(side="left", padx=10)
        self.api_key_entry = ctk.CTkEntry(api_frame, width=400, show="*"); self.api_key_entry.pack(side="left", fill="x", expand=True, padx=10)
        ctk.CTkButton(api_frame, text="Save Key", command=self.save_api_key).pack(side="left", padx=10)
        self.api_status_label = ctk.CTkLabel(self.api_tab, text="API Key not set."); self.api_status_label.pack(pady=10)
    def save_api_key(self):
        self.api_key = self.api_key_entry.get()
        if self.api_key: self.api_status_label.configure(text="API Key has been set.", text_color="green")
        else: self.api_status_label.configure(text="API Key field is empty.", text_color="yellow")
    def setup_tutorial_tab(self):
        ctk.CTkLabel(self.tutorial_tab, text="Tutorial content will go here.").pack(padx=20, pady=20)

if __name__ == "__main__":
    app = App()
    app.mainloop()
