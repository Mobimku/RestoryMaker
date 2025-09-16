# gui.py
# This file will contain the GUI for the RestoryMaker application using CustomTkinter.

import customtkinter as ctk
from tkinter import filedialog, messagebox
import os
import threading
import api_handler
import video_processor
import ffmpeg_utils
from pathlib import Path
import api_manager

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("RestoryMaker"); self.geometry("1200x800")

        self.api_manager = api_manager.APIManager()
        self.processing_thread = None
        self.stop_event = threading.Event()

        self.mp4_path = ctk.StringVar(); self.srt_path = ctk.StringVar(); self.output_folder = ctk.StringVar()
        self.bgm_path = ctk.StringVar(); self.selected_api_key = ctk.StringVar()

        # Variables for segment selection
        self.segment_vars = {name: ctk.BooleanVar(value=True) for name in ["Intro", "Rising", "Mid-conflict", "Climax", "Ending"]}
        self.process_all_segments = ctk.BooleanVar(value=True)

        ctk.set_appearance_mode("Dark"); ctk.set_default_color_theme("blue")
        self.tab_view = ctk.CTkTabview(self); self.tab_view.pack(padx=10, pady=10, fill="both", expand=True)
        self.editor_tab = self.tab_view.add("Editor"); self.api_tab = self.tab_view.add("API Management"); self.tutorial_tab = self.tab_view.add("Tutorial")

        self.setup_editor_tab(); self.setup_api_tab(); self.setup_tutorial_tab()

    def setup_editor_tab(self):
        self.editor_tab.grid_columnconfigure(0, weight=3); self.editor_tab.grid_columnconfigure(1, weight=2); self.editor_tab.grid_rowconfigure(1, weight=1)
        left_col = ctk.CTkFrame(self.editor_tab); left_col.grid(row=0, column=0, rowspan=3, padx=10, pady=10, sticky="nsew"); left_col.grid_rowconfigure(1, weight=1)
        right_col = ctk.CTkFrame(self.editor_tab); right_col.grid(row=0, column=1, rowspan=3, padx=10, pady=10, sticky="nsew")

        # ... (File IO, Log, Progress frames are the same)
        file_io_frame = ctk.CTkFrame(left_col); file_io_frame.pack(padx=10, pady=10, fill="x"); file_io_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(file_io_frame, text="MP4 File").grid(row=0, column=0, **self.grid_opts("w")); ctk.CTkEntry(file_io_frame, textvariable=self.mp4_path, state="disabled").grid(row=0, column=1, **self.grid_opts("ew")); ctk.CTkButton(file_io_frame, text="Browse...", command=self._select_mp4_file).grid(row=0, column=2, **self.grid_opts())
        ctk.CTkLabel(file_io_frame, text="SRT File").grid(row=1, column=0, **self.grid_opts("w")); ctk.CTkEntry(file_io_frame, textvariable=self.srt_path, state="disabled").grid(row=1, column=1, **self.grid_opts("ew")); ctk.CTkButton(file_io_frame, text="Browse...", command=self._select_srt_file).grid(row=1, column=2, **self.grid_opts())
        ctk.CTkLabel(file_io_frame, text="Output Folder").grid(row=2, column=0, **self.grid_opts("w")); ctk.CTkEntry(file_io_frame, textvariable=self.output_folder, state="disabled").grid(row=2, column=1, **self.grid_opts("ew")); ctk.CTkButton(file_io_frame, text="Browse...", command=self._select_output_folder).grid(row=2, column=2, **self.grid_opts())
        self.log_box = ctk.CTkTextbox(left_col, state="disabled", wrap="word"); self.log_box.pack(padx=10, pady=10, fill="both", expand=True)
        progress_frame = ctk.CTkFrame(left_col); progress_frame.pack(padx=10, pady=10, fill="x"); progress_frame.grid_columnconfigure(0, weight=1)
        self.progress_bar = ctk.CTkProgressBar(progress_frame); self.progress_bar.set(0); self.progress_bar.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        self.start_button = ctk.CTkButton(progress_frame, text="Start Processing", command=self._start_processing); self.start_button.grid(row=0, column=1, padx=10, pady=10)
        self.stop_button = ctk.CTkButton(progress_frame, text="Stop", command=self._stop_processing, state="disabled"); self.stop_button.grid(row=0, column=2, padx=10, pady=10)

        # --- RIGHT COLUMN WIDGETS ---
        canvas_frame = ctk.CTkFrame(right_col); canvas_frame.pack(padx=10, pady=10, fill="x")
        ctk.CTkLabel(canvas_frame, text="Canvas Settings", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10)
        self.bars_check = ctk.CTkCheckBox(canvas_frame, text="Add Black Bars"); self.bars_check.pack(anchor="w", padx=10, pady=5)
        self.bars_slider = ctk.CTkSlider(canvas_frame, from_=0, to=200); self.bars_slider.pack(fill="x", padx=10, pady=5); self.bars_slider.set(0)

        audio_frame = ctk.CTkFrame(right_col); audio_frame.pack(padx=10, pady=10, fill="x", pady_=(10,10))
        # ... (Audio frame is the same)
        ctk.CTkLabel(audio_frame, text="Audio Settings", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10)
        ctk.CTkLabel(audio_frame, text="Main VO Volume (%)").pack(anchor="w", padx=10, pady=(10, 0))
        self.main_vol_slider = ctk.CTkSlider(audio_frame, from_=0, to=300); self.main_vol_slider.pack(fill="x", padx=10, pady=5); self.main_vol_slider.set(100)
        ctk.CTkButton(audio_frame, text="Add Background Music...", command=self._select_bgm_file).pack(fill="x", padx=10, pady=10)
        self.bgm_label = ctk.CTkLabel(audio_frame, text="No BGM file selected.", text_color="gray", wraplength=250); self.bgm_label.pack(anchor="w", padx=10)
        ctk.CTkLabel(audio_frame, text="BGM Volume (%)").pack(anchor="w", padx=10, pady=(10, 0))
        self.bgm_vol_slider = ctk.CTkSlider(audio_frame, from_=0, to=100); self.bgm_vol_slider.pack(fill="x", padx=10, pady=5); self.bgm_vol_slider.set(10)

        # --- NEW: Segment Processing Frame ---
        segment_frame = ctk.CTkFrame(right_col); segment_frame.pack(padx=10, pady=10, fill="x")
        ctk.CTkLabel(segment_frame, text="Segment Processing", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10)
        ctk.CTkCheckBox(segment_frame, text="Process All Segments", variable=self.process_all_segments, command=self._toggle_all_segments).pack(anchor="w", padx=10, pady=5)

        self.segment_checkboxes = {}
        for name in self.segment_vars:
            cb = ctk.CTkCheckBox(segment_frame, text=name, variable=self.segment_vars[name])
            cb.pack(anchor="w", padx=30, pady=2)
            self.segment_checkboxes[name] = cb
        self._toggle_all_segments() # Set initial state

    def _toggle_all_segments(self):
        """Enables or disables individual segment checkboxes based on the 'All' checkbox."""
        if self.process_all_segments.get():
            for cb in self.segment_checkboxes.values():
                cb.configure(state="disabled")
                self.segment_vars[cb.cget("text")].set(True)
        else:
            for cb in self.segment_checkboxes.values():
                cb.configure(state="normal")

    # ... (rest of the methods are the same, will be updated in the next step) ...
    def grid_opts(self, sticky=""): return {"padx": 10, "pady": 5, "sticky": sticky}
    def _select_mp4_file(self): self.mp4_path.set(filedialog.askopenfilename(filetypes=[("MP4", "*.mp4")]))
    def _select_srt_file(self): self.srt_path.set(filedialog.askopenfilename(filetypes=[("SRT", "*.srt")]))
    def _select_output_folder(self): self.output_folder.set(filedialog.askdirectory())
    def _select_bgm_file(self):
        path = filedialog.askopenfilename(filetypes=[("Audio", "*.mp3 *.wav")]);
        if path: self.bgm_path.set(path); self.bgm_label.configure(text=os.path.basename(path), text_color="white")
        else: self.bgm_path.set(""); self.bgm_label.configure(text="No BGM file selected.", text_color="gray")
    def log_message(self, msg): self.after(0, self._log_message_thread_safe, msg)
    def _log_message_thread_safe(self, msg):
        self.log_box.configure(state="normal"); self.log_box.insert("end", str(msg) + "\n"); self.log_box.configure(state="disabled"); self.log_box.see("end")
    def _start_processing(self):
        api_key_to_use = self.api_manager.get_key(); mp4 = self.mp4_path.get()
        if not all([mp4, self.srt_path.get(), self.output_folder.get(), api_key_to_use]):
            self.log_message("ERROR: Please select MP4, SRT, Output Folder and add at least one API Key before starting."); return
        film_duration_sec = ffmpeg_utils.get_duration(mp4)
        if not film_duration_sec: self.log_message(f"ERROR: Could not read duration from video file: {mp4}"); return
        self.start_button.configure(state="disabled"); self.stop_button.configure(state="normal"); self.stop_event.clear()
        self.processing_thread = threading.Thread(target=self._processing_thread_target, args=(film_duration_sec, api_key_to_use)); self.processing_thread.start()
    def _stop_processing(self): self.log_message("STOP signal sent..."); self.stop_event.set()
    def _processing_thread_target(self, film_duration_sec, api_key):
        try:
            # Collect selected segments
            selected_segments = [name for name, var in self.segment_vars.items() if var.get()]
            if not selected_segments:
                raise Exception("No segments selected for processing.")

            user_settings = {
                "black_bars_size": self.bars_slider.get() if self.bars_check.get() else 0,
                "main_vo_volume": self.main_vol_slider.get() / 100.0,
                "bgm_path": self.bgm_path.get(),
                "bgm_volume": self.bgm_vol_slider.get() / 100.0,
                "output_path": str(Path(self.output_folder.get()) / f"{Path(self.mp4_path.get()).stem}_recap.mp4"),
                "selected_segments": selected_segments
            }
            storyboard = api_handler.get_storyboard_from_srt(self.srt_path.get(), api_key, int(film_duration_sec), self.output_folder.get(), "en", progress_callback=self.log_message)
            if not storyboard: raise Exception("Failed to get storyboard from API.")
            vo_audio_map = {}; temp_audio_dir = Path(self.output_folder.get()) / "temp_audio"; temp_audio_dir.mkdir(exist_ok=True)
            for i, segment in enumerate(storyboard.get('segments', [])):
                if self.stop_event.is_set(): raise InterruptedError("Processing stopped by user.")
                label = segment['label']; script = segment['vo_script']
                output_path = temp_audio_dir / f"vo_{label}.wav"
                if not api_handler.generate_vo_audio(script, api_key, str(output_path), progress_callback=self.log_message):
                    raise Exception(f"Failed to generate audio for segment {label}")
                vo_audio_map[label] = str(output_path)
            final_path = video_processor.process_video(storyboard, self.mp4_path.get(), vo_audio_map, user_settings, self.stop_event, self.log_message)
            if not final_path: raise Exception("Video processing failed.")
            self.log_message(f"SUCCESS: Processing complete. Final video at: {final_path}")
        except InterruptedError as e: self.log_message(f"STOPPED: {e}")
        except Exception as e: import traceback; self.log_message(f"FATAL ERROR: {e}"); self.log_message(traceback.format_exc())
        finally: self.after(0, lambda: (self.start_button.configure(state="normal"), self.stop_button.configure(state="disabled")))
    def setup_api_tab(self):
        self.api_tab.grid_columnconfigure(0, weight=1)
        # Add Key Frame
        add_frame = ctk.CTkFrame(self.api_tab); add_frame.pack(padx=10, pady=10, fill="x")
        ctk.CTkLabel(add_frame, text="New API Key:").pack(side="left", padx=10)
        self.new_api_key_entry = ctk.CTkEntry(add_frame, width=400, show="*"); self.new_api_key_entry.pack(side="left", fill="x", expand=True, padx=10)
        ctk.CTkButton(add_frame, text="Add Key", command=self._add_api_key).pack(side="left", padx=10)

        # Key List Frame
        self.key_list_frame = ctk.CTkScrollableFrame(self.api_tab, label_text="Saved API Keys")
        self.key_list_frame.pack(padx=10, pady=10, fill="both", expand=True)

        # Management Buttons Frame
        mgmt_frame = ctk.CTkFrame(self.api_tab); mgmt_frame.pack(padx=10, pady=10, fill="x")
        ctk.CTkButton(mgmt_frame, text="Delete Selected", command=self._delete_api_key, fg_color="red").pack(side="left", padx=10)
        ctk.CTkButton(mgmt_frame, text="Clear All", command=self._clear_api_keys, fg_color="gray").pack(side="right", padx=10)

        self._refresh_api_key_list()

    def _refresh_api_key_list(self):
        for widget in self.key_list_frame.winfo_children():
            widget.destroy()

        keys = self.api_manager.get_keys()
        if not keys:
            ctk.CTkLabel(self.key_list_frame, text="No API keys saved.").pack(pady=10)
            self.selected_api_key.set("")
            return

        for key in keys:
            masked_key = f"{key[:4]}...{key[-4:]}"
            ctk.CTkRadioButton(self.key_list_frame, text=masked_key, variable=self.selected_api_key, value=key).pack(anchor="w", padx=10, pady=2)

        if self.selected_api_key.get() not in keys:
            self.selected_api_key.set(keys[0])

    def _add_api_key(self):
        new_key = self.new_api_key_entry.get()
        if new_key:
            if self.api_manager.add_key(new_key):
                self.log_message("API Key added successfully.")
                self._refresh_api_key_list()
                self.new_api_key_entry.delete(0, 'end')
            else:
                messagebox.showwarning("Warning", "API Key already exists or is invalid.")
        else:
            messagebox.showwarning("Warning", "API Key field cannot be empty.")

    def _delete_api_key(self):
        key_to_delete = self.selected_api_key.get()
        if key_to_delete:
            if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete the key ending in ...{key_to_delete[-4:]}?"):
                self.api_manager.delete_key(key_to_delete)
                self.log_message("API Key deleted.")
                self._refresh_api_key_list()
        else:
            messagebox.showerror("Error", "No API Key selected for deletion.")

    def _clear_api_keys(self):
        if messagebox.askyesno("Confirm Clear All", "Are you sure you want to delete ALL saved API keys? This action cannot be undone."):
            self.api_manager.clear_all_keys()
            self.log_message("All API keys have been cleared.")
            self._refresh_api_key_list()
    def setup_tutorial_tab(self): ctk.CTkLabel(self.tutorial_tab, text="Tutorial content will go here.").pack(padx=20, pady=20)

if __name__ == "__main__":
    app = App()
    app.mainloop()
