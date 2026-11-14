import threading
import time
import numpy as np
import sounddevice as sd
from typing import Callable, List, Dict, Optional, Tuple

TARGET_SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = np.float32


class AudioEngine:
    def __init__(self, sample_rate: int = TARGET_SAMPLE_RATE, vad_sensitivity: float = 0.5):
        self.target_rate = sample_rate
        self.vad_sensitivity = vad_sensitivity
        
        self._stream: Optional[sd.InputStream] = None
        self._is_recording = False
        self._lock = threading.Lock()
        
        self._audio_chunks: List[np.ndarray] = []
        self._current_rms: float = 0.0
        self._is_speech_active: bool = False
        self._input_rate: int = TARGET_SAMPLE_RATE
        
        self._base_silence_threshold = 0.005 + (1.0 - vad_sensitivity) * 0.015
        self.on_rms_callback: Optional[Callable[[float, bool], None]] = None

    @staticmethod
    def list_input_devices() -> List[Dict]:
        """List all available microphone devices."""
        devices = sd.query_devices()
        input_devs = []
        for i, dev in enumerate(devices):
            if dev.get("max_input_channels", 0) > 0:
                input_devs.append({
                    "id": i,
                    "name": dev["name"],
                    "channels": dev["max_input_channels"],
                    "default_rate": dev.get("default_samplerate", 44100),
                    "default": (i == sd.default.device[0])
                })
        return input_devs

    def _audio_callback(self, indata, frames, time_info, status):
        """Streaming callback executed by sounddevice."""
        data = indata[:, 0].copy()
        
        # Calculate RMS
        rms = float(np.sqrt(np.mean(data ** 2)))
        self._current_rms = rms
        
        is_speech = rms > self._base_silence_threshold
        self._is_speech_active = is_speech
        
        if self._is_recording:
            with self._lock:
                self._audio_chunks.append(data)
                
        if self.on_rms_callback:
            try:
                self.on_rms_callback(rms, is_speech)
            except Exception:
                pass

    @staticmethod
    def find_best_input_device() -> int:
        """Automatically detects the active working microphone (system default or laptop array)."""
        try:
            from .db import get_settings
            saved_dev = get_settings().get("input_device_index")
            if saved_dev is not None and str(saved_dev).isdigit():
                return int(saved_dev)
        except Exception:
            pass

        try:
            devs = sd.query_devices()
            def_id = sd.default.device[0]

            # 1. First priority: Windows System Default input device
            if def_id is not None and def_id >= 0:
                d = devs[def_id]
                if d.get("max_input_channels", 0) > 0:
                    return def_id

            # 2. Second priority: Laptop Built-in Microphone Array
            for idx, d in enumerate(devs):
                if d.get("max_input_channels", 0) > 0 and "array" in d.get("name", "").lower():
                    return idx

            # 3. Third priority: Any physical microphone
            for idx, d in enumerate(devs):
                if d.get("max_input_channels", 0) > 0 and "mapper" not in d.get("name", "").lower():
                    return idx

            return 0
        except Exception:
            return 0

    def start_recording(self, device_index: Optional[int] = None) -> bool:
        """Start capturing microphone audio using the device's native sample rate."""
        with self._lock:
            if self._is_recording:
                return True
            self._audio_chunks = []
            self._is_recording = True

        try:
            dev_id = device_index if device_index is not None else self.find_best_input_device()

            # Record directly at 16,000 Hz (Whisper's native rate)
            # Windows audio driver automatically performs high-quality resampling
            try:
                rate = TARGET_SAMPLE_RATE
                block_size = int(rate * 0.05)  # 50ms chunk (800 samples)
                self._stream = sd.InputStream(
                    samplerate=rate,
                    channels=CHANNELS,
                    dtype=DTYPE,
                    blocksize=block_size,
                    device=dev_id,
                    callback=self._audio_callback
                )
                self._input_rate = rate
            except Exception:
                # Fallback to device's default rate if 16kHz is refused
                dev_info = sd.query_devices(dev_id)
                rate = int(dev_info.get("default_samplerate", 44100))
                block_size = int(rate * 0.05)
                self._stream = sd.InputStream(
                    samplerate=rate,
                    channels=CHANNELS,
                    dtype=DTYPE,
                    blocksize=block_size,
                    device=dev_id,
                    callback=self._audio_callback
                )
                self._input_rate = rate

            self._stream.start()
            print(f"[AudioEngine] Stream active on device {dev_id} at {rate}Hz (target: 16000Hz)")
            return True
        except Exception as e:
            self._is_recording = False
            print(f"[AudioEngine] Failed to start stream: {e}")
            return False

    def get_recent_16k_audio(self, max_seconds: float = 3.5) -> np.ndarray:
        """Returns the most recent audio buffer strictly resampled to 16,000 Hz."""
        with self._lock:
            if not self._audio_chunks:
                return np.array([], dtype=DTYPE)
            # Estimate chunk count for max_seconds
            samples_needed = int(self._input_rate * max_seconds)
            chunks_to_take = []
            accumulated = 0
            for c in reversed(self._audio_chunks):
                chunks_to_take.append(c)
                accumulated += len(c)
                if accumulated >= samples_needed:
                    break
            chunks_to_take.reverse()
            raw_audio = np.concatenate(chunks_to_take, axis=0)

        # Resample to exactly 16,000 Hz if recorded at different rate
        if self._input_rate != self.target_rate and len(raw_audio) > 0:
            target_len = int(len(raw_audio) * (self.target_rate / self._input_rate))
            indices = np.linspace(0, len(raw_audio) - 1, target_len)
            return np.interp(indices, np.arange(len(raw_audio)), raw_audio).astype(DTYPE)
        return raw_audio

    def stop_recording(self) -> np.ndarray:
        """Stop recording, resample to 16kHz, and return trimmed audio."""
        with self._lock:
            self._is_recording = False
            if not self._audio_chunks:
                return np.array([], dtype=DTYPE)
            raw_audio = np.concatenate(self._audio_chunks, axis=0)
            self._audio_chunks = []

        # Resample to 16,000 Hz if recorded at 44.1kHz or 48kHz
        if self._input_rate != self.target_rate and len(raw_audio) > 0:
            target_len = int(len(raw_audio) * (self.target_rate / self._input_rate))
            indices = np.linspace(0, len(raw_audio) - 1, target_len)
            resampled = np.interp(indices, np.arange(len(raw_audio)), raw_audio).astype(DTYPE)
        else:
            resampled = raw_audio

        # Trim leading and trailing silence
        return self._trim_silence(resampled)

    def _trim_silence(self, audio: np.ndarray, frame_ms: int = 30) -> np.ndarray:
        """Trims silence padding around speech."""
        if len(audio) == 0:
            return audio
            
        frame_len = int(self.target_rate * (frame_ms / 1000.0))
        num_frames = len(audio) // frame_len
        if num_frames == 0:
            return audio

        frames = audio[:num_frames * frame_len].reshape((num_frames, frame_len))
        energies = np.sqrt(np.mean(frames ** 2, axis=1))
        
        threshold = max(self._base_silence_threshold * 0.5, 0.003)
        speech_indices = np.where(energies > threshold)[0]

        if len(speech_indices) == 0:
            return audio

        start_frame = max(0, speech_indices[0] - 2)
        end_frame = min(num_frames, speech_indices[-1] + 3)

        start_sample = start_frame * frame_len
        end_sample = min(len(audio), end_frame * frame_len)

        return audio[start_sample:end_sample]

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def current_rms(self) -> float:
        return self._current_rms

    def close(self):
        self._is_recording = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None


if __name__ == "__main__":
    engine = AudioEngine()
    print("Testing AudioEngine start/stop...")
    engine.start_recording()
    sd.sleep(500)
    audio = engine.stop_recording()
    print("Recorded audio shape:", audio.shape, "Rate: 16000Hz")
    engine.close()
