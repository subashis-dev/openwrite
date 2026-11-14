import io
import os
import subprocess
import tempfile
import threading
import urllib.request
from typing import Optional

import soundfile as sf

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(BASE_DIR, "data", "models")
KOKORO_MODEL_PATH = os.path.join(MODELS_DIR, "kokoro-v1.0.onnx")
KOKORO_VOICES_PATH = os.path.join(MODELS_DIR, "voices-v1.0.bin")

KOKORO_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"


class TTSEngine:
    """
    Lightweight local Text-to-Speech engine powered by Kokoro ONNX (CPU/iGPU friendly),
    with automatic Windows SAPI fallback if Kokoro model files are unavailable.
    """

    def __init__(self, default_voice: str = "af_heart", default_speed: float = 1.05):
        self.default_voice = default_voice
        self.default_speed = default_speed
        self._kokoro = None
        self._lock = threading.Lock()
        self._backend = "uninitialized"

    def ensure_models(self, auto_download: bool = False) -> bool:
        """Checks for Kokoro ONNX model files; optionally downloads them."""
        os.makedirs(MODELS_DIR, exist_ok=True)
        if os.path.exists(KOKORO_MODEL_PATH) and os.path.exists(KOKORO_VOICES_PATH):
            return True
        if not auto_download:
            return False

        try:
            if not os.path.exists(KOKORO_MODEL_PATH):
                print(f"[TTSEngine] Downloading kokoro-v1.0.onnx to {KOKORO_MODEL_PATH}...")
                urllib.request.urlretrieve(KOKORO_MODEL_URL, KOKORO_MODEL_PATH)
            if not os.path.exists(KOKORO_VOICES_PATH):
                print(f"[TTSEngine] Downloading voices-v1.0.bin to {KOKORO_VOICES_PATH}...")
                urllib.request.urlretrieve(KOKORO_VOICES_URL, KOKORO_VOICES_PATH)
            return os.path.exists(KOKORO_MODEL_PATH) and os.path.exists(KOKORO_VOICES_PATH)
        except Exception as e:
            print(f"[TTSEngine] Failed downloading Kokoro models: {e}")
            return False

    def _load_kokoro(self) -> bool:
        with self._lock:
            if self._kokoro is not None:
                return True
            if not self.ensure_models(auto_download=True):
                return False
            try:
                import onnxruntime as ort
                from kokoro_onnx import Kokoro
                sess = ort.InferenceSession(KOKORO_MODEL_PATH, providers=["CPUExecutionProvider"])
                self._kokoro = Kokoro.from_session(sess, KOKORO_VOICES_PATH)
                self._backend = "kokoro-onnx (CPU)"
                print("[TTSEngine] Kokoro ONNX loaded successfully on pure CPU.")
                return True
            except Exception as e:
                print(f"[TTSEngine] Kokoro load error: {e}")
                self._kokoro = None
                return False

    def synthesize_wav(self, text: str, voice: Optional[str] = None, speed: Optional[float] = None) -> bytes:
        """Synthesizes speech to WAV bytes."""
        clean_text = (text or "").strip()
        if not clean_text:
            return b""

        voice = voice or self.default_voice
        speed = speed or self.default_speed

        if self._load_kokoro() and self._kokoro is not None:
            try:
                samples, sample_rate = self._kokoro.create(
                    clean_text,
                    voice=voice,
                    speed=speed,
                    lang="en-us",
                )
                buf = io.BytesIO()
                sf.write(buf, samples, sample_rate, format="WAV")
                return buf.getvalue()
            except Exception as e:
                print(f"[TTSEngine] Kokoro synthesis error, falling back to SAPI: {e}")

        # Windows SAPI fallback (offline, zero extra files)
        return self._synthesize_sapi_wav(clean_text)

    @staticmethod
    def _synthesize_sapi_wav(text: str) -> bytes:
        self_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = self_tmp.name
        self_tmp.close()
        try:
            safe_text = text.replace("'", "''")
            ps_script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.SetOutputToWaveFile('{tmp_path}'); "
                f"$s.Speak('{safe_text}'); "
                "$s.Dispose()"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                timeout=20,
                check=True,
            )
            with open(tmp_path, "rb") as f:
                return f.read()
        except Exception as e:
            print(f"[TTSEngine] SAPI fallback error: {e}")
            return b""
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
