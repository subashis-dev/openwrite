import ctypes
import math
import os
import threading
import time
import tkinter as tk
from typing import Callable, Optional

user32 = ctypes.windll.user32
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080


class FloatingHUD:
    """
    Sleek floating Dynamic Island HUD for Windows.
    Floats on top of all windows without stealing focus (WS_EX_NOACTIVATE).
    Shows live recording waveform, mode chips, and direct injection status.
    """

    def __init__(
        self,
        on_toggle_recording: Optional[Callable[[], None]] = None,
        on_mode_change: Optional[Callable[[str], None]] = None,
        initial_mode: str = "enhancement"
    ):
        self.on_toggle_recording = on_toggle_recording
        self.on_mode_change = on_mode_change
        self.active_mode = initial_mode
        self.is_recording = False
        self.current_app = ""
        self.current_rms = 0.0

        self.root: Optional[tk.Tk] = None
        self._thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self._drag_start_x = 0
        self._drag_start_y = 0

    def start(self):
        """Starts the Floating HUD in its own background UI thread."""
        self._thread = threading.Thread(target=self._run_ui, daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout=5.0)

    def _run_ui(self):
        self.root = tk.Tk()
        self.root.title("OpenWrite_Floating_HUD")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        # Center top position
        screen_w = self.root.winfo_screenwidth()
        w, h = 380, 52
        x = (screen_w - w) // 2
        y = 26
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.configure(bg="#0b0f19")

        # Container frame with border
        self.frame = tk.Frame(
            self.root,
            bg="#0f172a",
            highlightbackground="#334155",
            highlightthickness=1,
            padx=12,
            pady=6
        )
        self.frame.pack(fill=tk.BOTH, expand=True)

        # Left mic icon / dot
        self.mic_dot = tk.Label(
            self.frame,
            text="🎙️",
            bg="#0f172a",
            fg="#6366f1",
            font=("Segoe UI", 12, "bold")
        )
        self.mic_dot.pack(side=tk.LEFT, padx=(0, 8))

        # Center text & status
        self.text_frame = tk.Frame(self.frame, bg="#0f172a")
        self.text_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.title_label = tk.Label(
            self.text_frame,
            text="OpenWrite",
            bg="#0f172a",
            fg="#f8fafc",
            font=("Segoe UI", 10, "bold"),
            anchor="w"
        )
        self.title_label.pack(anchor="w")

        self.subtitle_label = tk.Label(
            self.text_frame,
            text="Press F8 or Ctrl+Alt+Space (or click)",
            bg="#0f172a",
            fg="#94a3b8",
            font=("Segoe UI", 8),
            anchor="w"
        )
        self.subtitle_label.pack(anchor="w")

        # Right mode chip button
        self.mode_btn = tk.Button(
            self.frame,
            text=self.active_mode.capitalize(),
            bg="#1e293b",
            fg="#a5b4fc",
            activebackground="#334155",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=2,
            cursor="hand2",
            command=self._cycle_mode
        )
        self.mode_btn.pack(side=tk.RIGHT, padx=(4, 0))

        # Action / Mic button
        self.act_btn = tk.Button(
            self.frame,
            text="●",
            bg="#312e81",
            fg="#818cf8",
            activebackground="#4338ca",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 11, "bold"),
            width=2,
            cursor="hand2",
            command=self._on_click_record
        )
        self.act_btn.pack(side=tk.RIGHT, padx=(4, 4))

        # Dragging behavior
        for w_elem in (self.root, self.frame, self.text_frame, self.title_label, self.subtitle_label):
            w_elem.bind("<Button-1>", self._start_drag)
            w_elem.bind("<B1-Motion>", self._on_drag)

        # Apply Win32 WS_EX_NOACTIVATE style so clicks never steal focus from user's app
        self.root.update_idletasks()
        try:
            hwnd = user32.FindWindowW(None, "OpenWrite_Floating_HUD")
            if hwnd:
                cur_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(
                    hwnd,
                    GWL_EXSTYLE,
                    cur_style | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
                )
        except Exception as e:
            print(f"[FloatingHUD] Win32 style error: {e}")

        self._ready_event.set()
        self.root.mainloop()

    def _start_drag(self, event):
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _on_drag(self, event):
        x = self.root.winfo_x() + (event.x - self._drag_start_x)
        y = self.root.winfo_y() + (event.y - self._drag_start_y)
        self.root.geometry(f"+{x}+{y}")

    def _on_click_record(self):
        if self.on_toggle_recording:
            threading.Thread(target=self.on_toggle_recording, daemon=True).start()

    def _cycle_mode(self):
        modes = ["enhancement", "dictation", "code", "email", "rewrite", "assistant"]
        idx = (modes.index(self.active_mode) + 1) % len(modes) if self.active_mode in modes else 0
        new_mode = modes[idx]
        self.set_mode(new_mode)
        if self.on_mode_change:
            threading.Thread(target=self.on_mode_change, args=(new_mode,), daemon=True).start()

    def set_mode(self, mode: str):
        self.active_mode = mode
        if self.root and self.mode_btn:
            self.root.after(0, lambda: self.mode_btn.configure(text=mode.capitalize()))

    def set_recording(self, recording: bool, app_name: str = ""):
        self.is_recording = recording
        self.current_app = app_name
        if not self.root:
            return

        def _update():
            if recording:
                self.frame.configure(bg="#1e1b4b", highlightbackground="#6366f1")
                self.text_frame.configure(bg="#1e1b4b")
                self.mic_dot.configure(text="🔴", bg="#1e1b4b", fg="#ef4444")
                self.title_label.configure(text="Listening...", bg="#1e1b4b", fg="#fca5a5")
                app_info = f" • In: {app_name}" if app_name else ""
                self.subtitle_label.configure(text=f"Speak naturally{app_info}", bg="#1e1b4b", fg="#cbd5e1")
                self.act_btn.configure(bg="#dc2626", fg="#ffffff", text="■")
            else:
                self.frame.configure(bg="#0f172a", highlightbackground="#334155")
                self.text_frame.configure(bg="#0f172a")
                self.mic_dot.configure(text="🎙️", bg="#0f172a", fg="#6366f1")
                self.title_label.configure(text="OpenWrite Ready", bg="#0f172a", fg="#f8fafc")
                self.subtitle_label.configure(text="Press F8 or Ctrl+Alt+Space (or click)", bg="#0f172a", fg="#94a3b8")
                self.act_btn.configure(bg="#312e81", fg="#818cf8", text="●")

        self.root.after(0, _update)

    def show_status(self, title: str, subtitle: str, auto_reset_sec: float = 2.5):
        if not self.root:
            return

        def _update():
            self.title_label.configure(text=title)
            self.subtitle_label.configure(text=subtitle)

        self.root.after(0, _update)

        if auto_reset_sec > 0:
            def _reset():
                time.sleep(auto_reset_sec)
                if not self.is_recording:
                    self.set_recording(False, self.current_app)
            threading.Thread(target=_reset, daemon=True).start()


if __name__ == "__main__":
    print("Testing FloatingHUD...")
    hud = FloatingHUD()
    hud.start()
    time.sleep(1)
    hud.set_recording(True, "notepad.exe")
    time.sleep(2)
    hud.show_status("✨ Injected into Notepad!", "Transcribed in 850ms")
    time.sleep(3)
    print("FloatingHUD test completed successfully.")
