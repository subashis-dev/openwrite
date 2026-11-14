import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 6992
URL = f"http://localhost:{PORT}"


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def wait_for_server(timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{URL}/api/status", timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def launch_standalone_window():
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for exe in candidates:
        if os.path.exists(exe):
            subprocess.Popen(
                [exe, f"--app={URL}", "--window-size=1100,760"],
                cwd=BASE_DIR,
            )
            return
    webbrowser.open(URL)


def main():
    if not is_port_open(HOST, PORT):
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [sys.executable, os.path.join(BASE_DIR, "server.py")],
            cwd=BASE_DIR,
            creationflags=creation_flags,
        )
        wait_for_server(25.0)

    launch_standalone_window()


if __name__ == "__main__":
    main()
