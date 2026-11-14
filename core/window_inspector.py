import ctypes
import os
import time
import subprocess
from typing import Optional
import pyperclip
import pyautogui

# Disable PyAutoGUI failsafe so mouse in screen corner never crashes dictation
pyautogui.FAILSAFE = False

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Virtual key codes for Windows SendInput / keybd_event
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002

APP_MODE_MAP = {
    "code.exe": "code",
    "cursor.exe": "code",
    "windsurf.exe": "code",
    "idea64.exe": "code",
    "pycharm64.exe": "code",
    "windowsterminal.exe": "code",
    "powershell.exe": "code",
    "cmd.exe": "code",
    "devenv.exe": "code",
    "sublime_text.exe": "code",
    "outlook.exe": "email",
    "thunderbird.exe": "email",
    "slack.exe": "dictation",
    "discord.exe": "dictation",
    "telegram.exe": "dictation",
    "whatsapp.exe": "dictation",
    "teams.exe": "dictation",
    "winword.exe": "enhancement",
    "notion.exe": "enhancement",
    "obsidian.exe": "enhancement",
    "onenote.exe": "enhancement",
}


class WindowInspector:
    @staticmethod
    def get_active_window_info() -> dict:
        """Returns details about the current active/foreground window."""
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {"title": "", "process": "", "recommended_mode": "enhancement"}

        length = user32.GetWindowTextLengthW(hwnd)
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        title = buff.value

        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        
        process_name = ""
        h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if h_process:
            try:
                name_buf = ctypes.create_unicode_buffer(1024)
                buf_size = ctypes.c_ulong(1024)
                if kernel32.QueryFullProcessImageNameW(h_process, 0, name_buf, ctypes.byref(buf_size)):
                    process_name = os.path.basename(name_buf.value).lower()
            finally:
                kernel32.CloseHandle(h_process)

        recommended_mode = "enhancement"
        if process_name in APP_MODE_MAP:
            recommended_mode = APP_MODE_MAP[process_name]
        elif "mail" in title.lower() or "gmail" in title.lower():
            recommended_mode = "email"
        elif "chat" in title.lower() or "slack" in title.lower() or "discord" in title.lower():
            recommended_mode = "dictation"

        return {
            "hwnd": hwnd,
            "title": title,
            "process": process_name,
            "recommended_mode": recommended_mode
        }

    @staticmethod
    def grab_selected_text() -> str:
        """Grabs currently selected text from the active window via Ctrl+C."""
        old_clipboard = ""
        try:
            old_clipboard = pyperclip.paste()
        except Exception:
            pass

        try:
            pyperclip.copy("")
            time.sleep(0.04)
            # Send Ctrl+C via Windows native keybd_event
            user32.keybd_event(VK_CONTROL, 0, 0, 0)
            user32.keybd_event(0x43, 0, 0, 0)  # 'C'
            time.sleep(0.05)
            user32.keybd_event(0x43, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.08)
            
            selected = pyperclip.paste().strip()
            return selected
        except Exception as e:
            print(f"[WindowInspector] Error grabbing text: {e}")
            return ""
        finally:
            if not pyperclip.paste().strip() and old_clipboard:
                pyperclip.copy(old_clipboard)

    @staticmethod
    def paste_text(text: str, target_hwnd: Optional[int] = None) -> bool:
        """
        Injects text into active window cursor position.
        Ensures modifier keys are released, activates the target window, and pastes cleanly.
        """
        if not text:
            return False

        try:
            # 1. Release any held modifier keys (Alt, Shift, Ctrl, Win) so Ctrl+V isn't intercepted
            VK_MENU = 0x12
            VK_SHIFT = 0x10
            VK_CONTROL = 0x11
            VK_LWIN = 0x5B
            KEYEVENTF_KEYUP = 0x0002

            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.04)

            # 2. Restore focus to target window if provided
            if target_hwnd and user32.IsWindow(target_hwnd):
                user32.SetForegroundWindow(target_hwnd)
                time.sleep(0.06)

            # 3. Copy text to clipboard and paste
            pyperclip.copy(text)
            time.sleep(0.06)

            # 4. Inject via PyAutoGUI (SendInput) and fallback to Win32 keybd_event
            try:
                pyautogui.hotkey('ctrl', 'v')
            except Exception:
                # Press Ctrl
                user32.keybd_event(VK_CONTROL, 0, 0, 0)
                # Press V
                user32.keybd_event(VK_V, 0, 0, 0)
                time.sleep(0.04)
                # Release V
                user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
                # Release Ctrl
                user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            return True
        except Exception as e:
            print(f"[WindowInspector] Native paste failed, trying fallback: {e}")
            try:
                pyautogui.hotkey("ctrl", "v")
                return True
            except Exception as e2:
                print(f"[WindowInspector] Paste error: {e2}")
                return False


if __name__ == "__main__":
    print("Testing WindowInspector...")
    print("Active window:", WindowInspector.get_active_window_info())
