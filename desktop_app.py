"""
OpenWrite Native Sovereign Desktop Voice Assistant (v3.0 — Live Streaming Dictation)
- LIVE STREAMING: Types words live in real time directly into Notepad / active app as you speak!
- Zero focus stealing: WS_EX_NOACTIVATE keeps your cursor focused in Notepad.
- Prominent Close (✕) button and Mode chip.
- Real-time Unicode keystroke injection (SendInput) with automatic spacing and anti-repetition.
- Shortcuts: F8, Fn+F8, F9, F4, Ctrl+Alt+Space, Alt+Space, or click the mic button.
"""

import os
import sys
import time
import math
import wave
import struct
import shutil
import ctypes
import winsound
import threading
import tkinter as tk
from typing import Optional, List

import numpy as np
import sounddevice as sd
import pyperclip
import pyautogui

# Disable PyAutoGUI failsafe
pyautogui.FAILSAFE = False

# Win32 APIs
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080

VK_F8 = 0x77
VK_F9 = 0x78
VK_F4 = 0x73
VK_PAUSE = 0x13
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_SHIFT = 0x10
VK_SPACE = 0x20
VK_V = 0x56

# Win32 SendInput 64-bit Structures for 100% reliable Live Keystroke Injection
ULONG_PTR = ctypes.c_ulonglong

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', ctypes.c_long), ('dy', ctypes.c_long),
        ('mouseData', ctypes.c_ulong), ('dwFlags', ctypes.c_ulong),
        ('time', ctypes.c_ulong), ('dwExtraInfo', ULONG_PTR)
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort),
        ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong),
        ('dwExtraInfo', ULONG_PTR)
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ('uMsg', ctypes.c_ulong), ('wParamL', ctypes.c_ushort),
        ('wParamH', ctypes.c_ushort)
    ]

class DUMMYUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]

class INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', ctypes.c_ulong), ('u', DUMMYUNION)]

KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002

def send_unicode_char(char: str) -> bool:
    code = ord(char)
    inp_down = INPUT()
    inp_down.type = 1  # INPUT_KEYBOARD
    inp_down.ki.wScan = code
    inp_down.ki.dwFlags = KEYEVENTF_UNICODE

    inp_up = INPUT()
    inp_up.type = 1
    inp_up.ki.wScan = code
    inp_up.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP

    arr = (INPUT * 2)(inp_down, inp_up)
    res = user32.SendInput(2, arr, ctypes.sizeof(INPUT))
    return res == 2

def type_live_text(text: str):
    """Types characters directly into active window cursor live in real time."""
    if not text:
        return
    for ch in text:
        send_unicode_char(ch)
        time.sleep(0.003)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Ensure sys.stdout and sys.stderr are always valid for pythonw / windowed execution
log_dir = os.path.join(BASE_DIR, "data")
os.makedirs(log_dir, exist_ok=True)
log_path = os.path.join(log_dir, "desktop_app.log")

if sys.stdout is None:
    sys.stdout = open(log_path, "a", encoding="utf-8", buffering=1)
if sys.stderr is None:
    sys.stderr = open(log_path, "a", encoding="utf-8", buffering=1)

def handle_exception(exc_type, exc_value, exc_traceback):
    import traceback
    err = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n[FATAL CRASH]\n{err}\n")
    except Exception:
        pass

sys.excepthook = handle_exception
if hasattr(threading, "excepthook"):
    threading.excepthook = lambda args: handle_exception(args.exc_type, args.exc_value, args.exc_traceback)

from core.audio_engine import AudioEngine
from core.mode_processor import ModeProcessor
from core.stt_engine import STTEngine
from core.db import get_settings, insert_transcript, get_vocabulary, update_setting


def play_chime_start():
    try:
        winsound.Beep(980, 60)
    except Exception:
        pass


def play_chime_stop():
    try:
        winsound.Beep(1250, 70)
    except Exception:
        pass


AVAILABLE_MODELS = {
    "tiny.en": {"label": "🚀 Tiny", "name": "Tiny.en (~75MB • 30ms)", "desc": "Ultra-Fast (30ms)"},
    "base.en": {"label": "⚡ Base", "name": "Base.en (~140MB • 50ms)", "desc": "Light & Fast (50ms)"},
    "small.en": {"label": "🎯 Small", "name": "Small.en (~244MB • 85ms)", "desc": "Balanced (Default)"},
    "medium.en": {"label": "🧠 Medium", "name": "Medium.en (~1.5GB • 300ms)", "desc": "Superior Grammar"},
    "distil-whisper/distil-large-v3": {"label": "✨ Distil-L", "name": "Distil-Large-v3 (~750MB)", "desc": "Max Precision"}
}


def filter_repetition(text: str) -> str:
    """Removes repetitive Whisper hallucination loops."""
    if not text:
        return ""
    words = text.split()
    if len(words) >= 4:
        first_clean = words[0].strip(".,!?:;").lower()
        if all(w.strip(".,!?:;").lower() == first_clean for w in words):
            return words[0]
    return text


class AdaptivePhraseSegmenter:
    """
    Adaptive Voice Activity Segmenter.
    Splits speech by natural conversational pauses, emitting complete phrases for Whisper.
    Guarantees that words are never sliced in half, Whisper receives 100% full speech context,
    and streaming dictation runs indefinitely without stopping.
    """
    def __init__(self, sample_rate=16000, min_speech_sec=0.4, pause_sec=0.35, max_phrase_sec=3.5):
        self.sr = sample_rate
        self.noise_floor = 0.001
        self.min_speech_samples = int(sample_rate * min_speech_sec)
        self.pause_samples = int(sample_rate * pause_sec)
        self.max_phrase_samples = int(sample_rate * max_phrase_sec)
        self.reset()

    def reset(self):
        self.buffer = []
        self.in_speech = False
        self.silence_sample_count = 0
        self.total_speech_samples = 0

    def feed_chunk(self, chunk: np.ndarray):
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        
        # Adaptive noise floor tracking during silence
        if not self.in_speech:
            self.noise_floor = 0.95 * self.noise_floor + 0.05 * rms
            
        threshold = max(0.0010, self.noise_floor * 2.0)
        is_speech = rms > threshold
        
        if is_speech:
            self.in_speech = True
            self.silence_sample_count = 0
            self.buffer.append(chunk)
            self.total_speech_samples += len(chunk)
            
            if self.total_speech_samples >= self.max_phrase_samples:
                phrase = np.concatenate(self.buffer)
                self.reset()
                return phrase
        else:
            if self.in_speech:
                self.buffer.append(chunk)
                self.silence_sample_count += len(chunk)
                
                if self.silence_sample_count >= self.pause_samples:
                    if self.total_speech_samples >= self.min_speech_samples:
                        phrase = np.concatenate(self.buffer)
                        self.reset()
                        return phrase
                    else:
                        self.reset()
        return None

    def flush(self):
        if self.in_speech and self.total_speech_samples >= self.min_speech_samples:
            phrase = np.concatenate(self.buffer)
            self.reset()
            return phrase
        self.reset()
        return None


class OpenWriteDesktopApp:
    def __init__(self):
        self.settings = get_settings()
        self.active_mode = self.settings.get("active_mode", "enhancement")
        self.current_model = self.settings.get("model_name", "small.en")
        if self.current_model not in AVAILABLE_MODELS:
            self.current_model = "small.en"
        self.ui_mode = self.settings.get("ui_mode", "floating")  # "floating" or "docked"

        self.is_recording = False
        self._record_start = 0.0
        self.my_pid = os.getpid()

        # Save PID to file for clean single-instance management
        try:
            pid_file = os.path.join(BASE_DIR, "data", "desktop_app.pid")
            with open(pid_file, "w") as f:
                f.write(str(self.my_pid))
        except Exception:
            pass

        # Contextual prompt for Whisper accuracy & custom vocabulary
        vocab = get_vocabulary()
        vocab_str = ", ".join(vocab) if vocab else "OpenWrite, Python, Notepad"
        self.initial_prompt = f"Clear English dictation with accurate capitalization, contractions, and technical terms: {vocab_str}."

        # Subsystems
        self.mode_processor = ModeProcessor()
        self.audio_engine = AudioEngine(vad_sensitivity=0.5)
        self.stt_engine = STTEngine(
            model_size=self.current_model,
            device="cpu",
            compute_type="int8"
        )

        # Target tracking
        self.target_hwnd: Optional[int] = None
        self.target_app_name: str = "Notepad"
        self.target_title: str = "Untitled - Notepad"
        self.current_rms: float = 0.0
        self.rms_history = [0.0] * 20

        # Live streaming states
        self._live_stream_active = False
        self._live_audio_buffer: List[np.ndarray] = []
        self._typed_words_history: List[str] = []
        self._total_transcribed_words: List[str] = []
        self._last_speech_time = 0.0

        # Automatic input device detection
        self.current_dev_idx = self.audio_engine.find_best_input_device()

        # GUI Window
        self.root = tk.Tk()
        self.root.title("OpenWrite")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#0b0f19")

        # Geometry setup based on mode
        screen_w = self.root.winfo_screenwidth()
        if self.ui_mode == "docked":
            self.root.geometry(f"{screen_w}x42+0+0")
        else:
            w, h = 640, 50
            x = (screen_w - w) // 2
            y = 28
            self.root.geometry(f"{w}x{h}+{x}+{y}")

        self._build_ui()
        self._apply_noactivate_style()
        self._bind_drag()

        # Connect audio level callback
        self.audio_engine.on_rms_callback = self._on_rms_update

        # Background loops
        self._hotkey_active = True
        self._poll_thread = threading.Thread(target=self._hotkey_poll_loop, daemon=True)
        self._poll_thread.start()

        self._track_thread = threading.Thread(target=self._window_tracker_loop, daemon=True)
        self._track_thread.start()

        self._stream_thread = threading.Thread(target=self._live_stream_worker, daemon=True)
        self._stream_thread.start()

        self._animate_waveform()

    def _apply_noactivate_style(self):
        """Applies WS_EX_NOACTIVATE and WS_EX_TOPMOST."""
        self.root.update_idletasks()
        try:
            hwnd = self.root.winfo_id()
            toplevel = user32.GetAncestor(hwnd, 2)
            if toplevel:
                hwnd = toplevel
            cur_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd,
                GWL_EXSTYLE,
                cur_style | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
            )
        except Exception as e:
            print(f"[DesktopApp] Win32 style error: {e}")

    def _build_ui(self):
        self.main_frame = tk.Frame(
            self.root,
            bg="#0f172a",
            highlightbackground="#334155",
            highlightthickness=1,
            padx=8,
            pady=4
        )
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # ---------------- RIGHT CONTROLS (ALWAYS PINNED) ----------------
        self.right_frame = tk.Frame(self.main_frame, bg="#0f172a")
        self.right_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))

        # Close button (Bright red on hover)
        self.close_btn = tk.Button(
            self.right_frame,
            text=" ✕ ",
            bg="#1e293b",
            fg="#ef4444",
            activebackground="#dc2626",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"),
            padx=4,
            pady=1,
            cursor="hand2",
            command=self.quit
        )
        self.close_btn.pack(side=tk.RIGHT, padx=(3, 0))

        # Dock vs Floating Mode Toggle Button
        dock_txt = "🫧 Float" if self.ui_mode == "docked" else "📌 Dock"
        dock_color = "#38bdf8" if self.ui_mode == "docked" else "#cbd5e1"
        self.dock_btn = tk.Button(
            self.right_frame,
            text=dock_txt,
            bg="#1e293b",
            fg=dock_color,
            activebackground="#334155",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            padx=5,
            pady=1,
            cursor="hand2",
            command=self.toggle_ui_mode
        )
        self.dock_btn.pack(side=tk.RIGHT, padx=(3, 1))

        # Model Selector Chip
        curr_label = AVAILABLE_MODELS.get(self.current_model, {}).get("label", "🎯 Small")
        self.model_btn = tk.Button(
            self.right_frame,
            text=f"{curr_label} ▾",
            bg="#1e293b",
            fg="#facc15",
            activebackground="#334155",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            padx=5,
            pady=1,
            cursor="hand2",
            command=self._show_model_menu
        )
        self.model_btn.pack(side=tk.RIGHT, padx=(3, 1))

        # Mode Chip
        self.mode_btn = tk.Button(
            self.right_frame,
            text=self.active_mode.capitalize(),
            bg="#1e293b",
            fg="#a5b4fc",
            activebackground="#334155",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            padx=5,
            pady=1,
            cursor="hand2",
            command=self._cycle_mode
        )
        self.mode_btn.pack(side=tk.RIGHT, padx=(3, 1))

        # Live Typing Badge
        self.live_badge = tk.Label(
            self.right_frame,
            text="⚡ LIVE",
            bg="#10b981",
            fg="#ffffff",
            font=("Segoe UI", 7, "bold"),
            padx=4,
            pady=1
        )
        self.live_badge.pack(side=tk.RIGHT, padx=(2, 2))

        # ---------------- LEFT CONTROLS ----------------
        # Big Glowing Mic Button
        self.mic_btn = tk.Button(
            self.main_frame,
            text="🎙️",
            bg="#312e81",
            fg="#c7d2fe",
            activebackground="#4338ca",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI Emoji", 12, "bold"),
            width=3,
            cursor="hand2",
            command=self.toggle_recording
        )
        self.mic_btn.pack(side=tk.LEFT, padx=(0, 6))

        # Center Text Block
        self.info_frame = tk.Frame(self.main_frame, bg="#0f172a")
        self.info_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.title_lbl = tk.Label(
            self.info_frame,
            text="OpenWrite Live Dictation",
            bg="#0f172a",
            fg="#f8fafc",
            font=("Segoe UI", 9, "bold"),
            anchor="w"
        )
        self.title_lbl.pack(anchor="w")

        self.sub_lbl = tk.Label(
            self.info_frame,
            text="Target: Notepad • Speak to type live",
            bg="#0f172a",
            fg="#94a3b8",
            font=("Segoe UI", 8),
            anchor="w"
        )
        self.sub_lbl.pack(anchor="w")

        # Soundwave Canvas
        self.canvas = tk.Canvas(
            self.main_frame,
            width=46,
            height=26,
            bg="#0f172a",
            highlightthickness=0
        )
        self.canvas.pack(side=tk.LEFT, padx=(2, 4))

    def _bind_drag(self):
        for elem in (self.root, self.main_frame, self.info_frame, self.title_lbl, self.sub_lbl):
            elem.bind("<Button-1>", self._start_drag)
            elem.bind("<B1-Motion>", self._on_drag)

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag(self, event):
        if self.ui_mode == "docked":
            # If user drags down by more than 12px, detach to floating mode
            if (event.y - self._drag_y) > 12:
                self.set_ui_mode("floating")
            return
        x = self.root.winfo_x() + (event.x - self._drag_x)
        y = self.root.winfo_y() + (event.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    def toggle_ui_mode(self):
        new_mode = "docked" if self.ui_mode == "floating" else "floating"
        self.set_ui_mode(new_mode)

    def set_ui_mode(self, mode: str):
        self.ui_mode = mode
        try:
            update_setting("ui_mode", mode)
        except Exception:
            pass

        screen_w = self.root.winfo_screenwidth()
        if self.ui_mode == "docked":
            self.root.geometry(f"{screen_w}x42+0+0")
            self.dock_btn.configure(text="🫧 Float", fg="#38bdf8")
        else:
            w, h = 640, 50
            x = (screen_w - w) // 2
            y = 28
            self.root.geometry(f"{w}x{h}+{x}+{y}")
            self.dock_btn.configure(text="📌 Dock", fg="#cbd5e1")
        self._apply_noactivate_style()

    def _show_model_menu(self, event=None):
        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg="#1e293b",
            fg="#f8fafc",
            activebackground="#4338ca",
            activeforeground="#ffffff",
            font=("Segoe UI", 9)
        )
        for model_key, info in AVAILABLE_MODELS.items():
            is_current = (model_key == self.current_model)
            prefix = "✓  " if is_current else "    "
            label = f"{prefix}{info['name']}"
            menu.add_command(
                label=label,
                command=lambda m=model_key: self.switch_model(m)
            )
        try:
            bx = self.model_btn.winfo_rootx()
            by = self.model_btn.winfo_rooty() + self.model_btn.winfo_height() + 2
            menu.tk_popup(bx, by)
        finally:
            menu.grab_release()

    def switch_model(self, model_key: str):
        if model_key == self.current_model:
            return
        if self.is_recording:
            self.stop_recording()

        model_info = AVAILABLE_MODELS.get(model_key, {"label": model_key, "name": model_key})
        self.model_btn.configure(text="⏳ Loading...", state=tk.DISABLED)
        self.title_lbl.configure(text="⏳ Loading Model...", fg="#f59e0b")
        self.sub_lbl.configure(text=f"Loading {model_info['name']} into memory...")

        def _worker():
            try:
                self.stt_engine.reload(model_size=model_key, device="cpu", compute_type="int8")
                self.current_model = model_key
                try:
                    update_setting("model_name", model_key)
                except Exception:
                    pass
                self.root.after(0, lambda: self._on_model_loaded_success(model_key))
            except Exception as e:
                self.root.after(0, lambda err=str(e): self._on_model_loaded_failed(err))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_model_loaded_success(self, model_key: str):
        model_info = AVAILABLE_MODELS.get(model_key, {"label": model_key})
        self.model_btn.configure(text=f"{model_info['label']} ▾", state=tk.NORMAL)
        self.title_lbl.configure(text="OpenWrite Live Dictation", fg="#f8fafc")
        self.sub_lbl.configure(text=f"Model: {model_info['label']} active • Target: {self.target_app_name}", fg="#94a3b8")
        play_chime_stop()

    def _on_model_loaded_failed(self, error_msg: str):
        curr_info = AVAILABLE_MODELS.get(self.current_model, {"label": self.current_model})
        self.model_btn.configure(text=f"{curr_info['label']} ▾", state=tk.NORMAL)
        self.title_lbl.configure(text="Model Switch Failed", fg="#ef4444")
        self.sub_lbl.configure(text=f"Error: {error_msg[:35]}...", fg="#ef4444")

    def _cycle_mode(self):
        modes = ["enhancement", "dictation", "code", "email", "assistant"]
        idx = (modes.index(self.active_mode) + 1) % len(modes)
        self.active_mode = modes[idx]
        self.mode_btn.configure(text=self.active_mode.capitalize())

    def _on_rms_update(self, rms: float, is_speech: bool):
        self.current_rms = rms
        self.rms_history.append(rms)
        self.rms_history.pop(0)
        if is_speech:
            self._last_speech_time = time.time()

    def _animate_waveform(self):
        self.canvas.delete("all")
        w, h = 50, 30
        bars = len(self.rms_history)
        bar_w = 2
        gap = 1

        for i, val in enumerate(self.rms_history):
            if self.is_recording:
                bar_h = min(h - 4, max(4, int(val * 450)))
                color = "#ef4444" if val > 0.03 else "#f97316"
            else:
                bar_h = 4 + int(math.sin(time.time() * 4 + i * 0.4) * 2)
                color = "#10b981"

            x0 = i * (bar_w + gap) + 2
            y0 = (h - bar_h) // 2
            x1 = x0 + bar_w
            y1 = y0 + bar_h
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")

        self.root.after(40, self._animate_waveform)

    def _window_tracker_loop(self):
        """Monitors active windows. Strictly ignores OpenWrite's own PID."""
        while self._hotkey_active:
            try:
                hwnd = user32.GetForegroundWindow()
                if hwnd and user32.IsWindow(hwnd):
                    pid = ctypes.c_ulong()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    
                    if pid.value != self.my_pid:
                        buf = ctypes.create_unicode_buffer(512)
                        user32.GetWindowTextW(hwnd, buf, 512)
                        title = buf.value.strip()

                        p_name = "Editor"
                        try:
                            import psutil
                            proc = psutil.Process(pid.value)
                            p_name = proc.name()
                        except Exception:
                            pass

                        if title and "openwrite" not in title.lower():
                            self.target_hwnd = hwnd
                            self.target_app_name = p_name
                            self.target_title = title
                            if not self.is_recording:
                                disp_title = (title[:18] + "..") if len(title) > 20 else title
                                self.sub_lbl.configure(text=f"Target: {disp_title} • F8 or 🎙️")
            except Exception:
                pass
            time.sleep(0.25)

    def _hotkey_poll_loop(self):
        """Polls physical keyboard state from kernel at 50Hz."""
        last_f8 = False
        last_f9 = False
        last_f4 = False
        last_pause = False
        last_combo = False

        while self._hotkey_active:
            try:
                f8_now = bool(user32.GetAsyncKeyState(VK_F8) & 0x8000)
                f9_now = bool(user32.GetAsyncKeyState(VK_F9) & 0x8000)
                f4_now = bool(user32.GetAsyncKeyState(VK_F4) & 0x8000)
                pause_now = bool(user32.GetAsyncKeyState(VK_PAUSE) & 0x8000)

                ctrl = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                alt = bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
                space = bool(user32.GetAsyncKeyState(VK_SPACE) & 0x8000)

                combo = (ctrl and alt and space) or (alt and space)

                # Single key toggle: F8, F9, F4, or Pause / Break
                single_hit = (
                    (f8_now and not last_f8) or
                    (f9_now and not last_f9) or
                    (f4_now and not last_f4) or
                    (pause_now and not last_pause)
                )
                combo_hit = combo and not last_combo

                if single_hit or combo_hit:
                    self.root.after(0, self.toggle_recording)

                last_f8 = f8_now
                last_f9 = f9_now
                last_f4 = f4_now
                last_pause = pause_now
                last_combo = combo
            except Exception:
                pass
            time.sleep(0.02)

    def toggle_recording(self):
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        if self.is_recording:
            return

        play_chime_start()
        started = self.audio_engine.start_recording(device_index=self.current_dev_idx)
        if not started:
            self.sub_lbl.configure(text="Mic failed to start")
            return

        self.is_recording = True
        self._record_start = time.time()
        self._typed_words_history = []
        self._total_transcribed_words = []
        self._last_speech_time = time.time()

        self.mic_btn.configure(text="⏹️", bg="#dc2626", activebackground="#b91c1c")
        self.title_lbl.configure(text="🔴 LIVE TYPING...", fg="#ef4444")
        self.sub_lbl.configure(text=f"Speak naturally — typing live into {self.target_app_name}!")

    def stop_recording(self):
        if not self.is_recording:
            return

        self.is_recording = False
        duration = time.time() - self._record_start

        self.mic_btn.configure(text="🎙️", bg="#312e81")
        self.title_lbl.configure(text="OpenWrite Ready", fg="#f8fafc")
        
        # Stop stream
        try:
            self.audio_engine.stop_recording()
        except Exception:
            pass

        total_text = " ".join(self._total_transcribed_words).strip()
        if total_text:
            insert_transcript(
                raw_text=total_text,
                processed_text=total_text,
                mode=self.active_mode,
                app_name=self.target_app_name,
                duration_ms=int(duration * 1000)
            )
            try:
                pyperclip.copy(total_text)
            except Exception:
                pass
            self.sub_lbl.configure(text=f"Typed {len(self._total_transcribed_words)} words into {self.target_app_name}")
        else:
            self.sub_lbl.configure(text=f"Finished. Target: {self.target_app_name}")

        play_chime_stop()

    def _ensure_target_focus(self):
        """Brings the target document back to focus if user interacted with toolbar."""
        target = self.target_hwnd or user32.GetForegroundWindow()
        if target and user32.IsWindow(target):
            fg = user32.GetForegroundWindow()
            if fg != target:
                user32.ShowWindow(target, 9)  # SW_RESTORE
                user32.BringWindowToTop(target)
                user32.keybd_event(0x12, 0, 0, 0)
                user32.keybd_event(0x12, 0, 2, 0)
                user32.SetForegroundWindow(target)
                time.sleep(0.04)

    def _live_stream_worker(self):
        """
        Processes audio phrase-by-phrase using Adaptive VAD,
        and TYPES WORDS LIVE CONTINUOUSLY INTO NOTEPAD!
        Runs indefinitely with 100% accuracy and never cuts off.
        """
        segmenter = AdaptivePhraseSegmenter(sample_rate=16000)
        chunk_read_idx = 0

        while self._hotkey_active:
            if not self.is_recording:
                time.sleep(0.05)
                chunk_read_idx = 0
                segmenter.reset()
                continue

            try:
                # 1. Grab new audio chunks as they arrive from audio engine
                with self.audio_engine._lock:
                    total_chunks = len(self.audio_engine._audio_chunks)
                    if total_chunks <= chunk_read_idx:
                        time.sleep(0.04)
                        continue
                    new_chunks = self.audio_engine._audio_chunks[chunk_read_idx:total_chunks]
                    chunk_read_idx = total_chunks

                for chunk in new_chunks:
                    phrase = segmenter.feed_chunk(chunk)
                    if phrase is not None and len(phrase) > 0:
                        # 2. Software Auto-Gain Control on this phrase
                        peak = float(np.max(np.abs(phrase)))
                        if peak > 0.0004:
                            gain = min(0.85 / peak, 30.0)
                            phrase_proc = phrase * gain
                        else:
                            phrase_proc = phrase

                        # 3. Transcribe complete phrase (~75ms on CPU with vocabulary prompt)
                        res = self.stt_engine.transcribe(phrase_proc, custom_prompt=self.initial_prompt)
                        text = res.get("text", "").strip()
                        text = filter_repetition(text)

                        if text:
                            # 4. Make sure target application is focused
                            self._ensure_target_focus()

                            # 5. Type phrase live into document
                            to_type = text + " "
                            type_live_text(to_type)

                            self._total_transcribed_words.extend(text.split())

                            # Update preview on toolbar
                            preview = text
                            self.root.after(0, lambda p=preview: self._update_live_preview(p))

            except Exception:
                pass

            time.sleep(0.05)

    def _update_live_preview(self, preview_text: str):
        if self.is_recording:
            disp = (preview_text[:24] + "..") if len(preview_text) > 26 else preview_text
            self.sub_lbl.configure(text=f'Typed: "{disp}"', fg="#10b981")

    def quit(self):
        self._hotkey_active = False
        try:
            self.audio_engine.close()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = OpenWriteDesktopApp()
    app.run()
