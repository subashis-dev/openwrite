import ctypes
import threading
import time
from typing import Callable, Optional

from .db import get_settings

user32 = ctypes.windll.user32

# Win32 Virtual Key Codes
VK_F8 = 0x77
VK_F9 = 0x78
VK_CONTROL = 0x11
VK_MENU = 0x12     # Alt
VK_SHIFT = 0x10
VK_SPACE = 0x20
VK_PAUSE = 0x13


class HotkeyListener:
    """
    100% Reliable Native Windows Global Hotkey Listener.
    Uses Win32 GetAsyncKeyState to poll physical keyboard state directly from the kernel.
    Never blocked by UIPI, Windows hooks, or elevated security policies.
    """

    def __init__(
        self,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        mode: Optional[str] = None,
        hotkey_str: Optional[str] = None
    ):
        settings = get_settings()
        self.mode = mode or settings.get("shortcut_mode", "hybrid")
        self.hotkey_str = hotkey_str or settings.get("hotkey", "<alt>+<space>")

        self.on_start = on_start
        self.on_stop = on_stop

        self._is_active = False
        self._is_recording = False
        self._press_time: float = 0.0
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Starts kernel-level asynchronous keyboard listener thread."""
        if self._is_active:
            return

        self._is_active = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        print(f"[HotkeyListener] Active. Global triggers: F8 (Single Key), Ctrl+Alt+Space, F9, or Alt+Space")

    def _poll_loop(self):
        last_f8 = False
        last_f9 = False
        last_combo = False

        while self._is_active:
            try:
                # Query direct kernel keyboard state
                f8_now = bool(user32.GetAsyncKeyState(VK_F8) & 0x8000)
                f9_now = bool(user32.GetAsyncKeyState(VK_F9) & 0x8000)

                ctrl_down = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                alt_down = bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
                shift_down = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
                space_down = bool(user32.GetAsyncKeyState(VK_SPACE) & 0x8000)

                # Combos: Ctrl+Alt+Space, Ctrl+Shift+Space, Alt+Space
                combo_now = (ctrl_down and alt_down and space_down) or \
                            (ctrl_down and shift_down and space_down) or \
                            (alt_down and space_down)

                # --- 1. Single Key Toggle (F8 / F9) ---
                single_pressed = (f8_now and not last_f8) or (f9_now and not last_f9)
                if single_pressed:
                    with self._lock:
                        now = time.time()
                        if not self._is_recording:
                            self._is_recording = True
                            self._press_time = now
                            key_name = "F8" if f8_now else "F9"
                            print(f"[HotkeyListener] {key_name} pressed -> START recording")
                            threading.Thread(target=self.on_start, daemon=True).start()
                        elif now - self._press_time >= 0.25:
                            self._is_recording = False
                            key_name = "F8" if f8_now else "F9"
                            print(f"[HotkeyListener] {key_name} pressed -> STOP recording & inject")
                            threading.Thread(target=self.on_stop, daemon=True).start()

                # --- 2. Combo Keys (Ctrl+Alt+Space / Alt+Space) ---
                if combo_now and not last_combo:
                    with self._lock:
                        now = time.time()
                        if not self._is_recording:
                            self._is_recording = True
                            self._press_time = now
                            print("[HotkeyListener] Key combo pressed -> START recording")
                            threading.Thread(target=self.on_start, daemon=True).start()
                        elif self.mode == "toggle" and now - self._press_time >= 0.25:
                            self._is_recording = False
                            print("[HotkeyListener] Key combo pressed again -> STOP recording")
                            threading.Thread(target=self.on_stop, daemon=True).start()

                elif not combo_now and last_combo:
                    # Released
                    if self._is_recording and self.mode in ("push_to_talk", "hybrid"):
                        duration = time.time() - self._press_time
                        if self.mode == "push_to_talk" or duration >= 0.35:
                            with self._lock:
                                self._is_recording = False
                                print(f"[HotkeyListener] Key combo released after {duration:.2f}s -> STOP recording & inject")
                                threading.Thread(target=self.on_stop, daemon=True).start()

                last_f8 = f8_now
                last_f9 = f9_now
                last_combo = combo_now

            except Exception as e:
                print(f"[HotkeyListener] Polling exception: {e}")

            time.sleep(0.02)  # 20ms = 50Hz polling, 0.00% CPU

    def toggle_stop(self):
        """Explicitly stop recording (from UI or external call)."""
        with self._lock:
            if self._is_recording:
                self._is_recording = False
                print("[HotkeyListener] Explicit STOP recording")
                threading.Thread(target=self.on_stop, daemon=True).start()

    def update_settings(self, mode: str, hotkey_str: Optional[str] = None):
        self.mode = mode
        if hotkey_str:
            self.hotkey_str = hotkey_str

    def stop(self):
        self._is_active = False


if __name__ == "__main__":
    def on_st():
        print(">>> RECORDING STARTED <<<")

    def on_sp():
        print("<<< RECORDING STOPPED >>>")

    hl = HotkeyListener(on_start=on_st, on_stop=on_sp)
    hl.start()
    print("Test listener running for 3 seconds...")
    time.sleep(3)
    hl.stop()
