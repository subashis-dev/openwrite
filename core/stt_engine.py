import os
import time
import threading
from typing import Dict, List, Optional, Union
import numpy as np
import av
from faster_whisper import WhisperModel

from .db import get_settings

HALLUCINATION_PHRASES = {
    "thanks for watching",
    "thanks for watching!",
    "thank you for watching",
    "thank you for watching.",
    "please subscribe",
    "like and subscribe",
    "subtitles by",
    "watching!",
    "cheers.",
    "bye.",
    "you",
    "i hope you enjoyed",
    "i hope you enjoyed.",
    "i hope you enjoyed!",
    "letting down",
}


def load_and_normalize_audio(audio_input: Union[str, np.ndarray]) -> np.ndarray:
    """
    Decodes audio from file, WebM blob, or array, resamples to 16,000 Hz float32 mono,
    and applies gentle peak normalization.
    """
    if isinstance(audio_input, np.ndarray):
        audio = audio_input.astype(np.float32)
    else:
        try:
            container = av.open(audio_input)
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
            frames = []
            for frame in container.decode(audio=0):
                for r in resampler.resample(frame):
                    frames.append(r.to_ndarray())
            if not frames:
                return np.array([], dtype=np.float32)
            audio = np.concatenate(frames, axis=1)[0].astype(np.float32)
        except Exception as e:
            print(f"[STTEngine] PyAV decode fallback: {e}")
            if isinstance(audio_input, str):
                import soundfile as sf
                try:
                    data, sr = sf.read(audio_input, dtype="float32")
                    if data.ndim > 1:
                        data = np.mean(data, axis=1)
                    if sr != 16000:
                        target_len = int(len(data) * (16000 / sr))
                        audio = np.interp(np.linspace(0, len(data) - 1, target_len), np.arange(len(data)), data).astype(np.float32)
                    else:
                        audio = data
                except Exception as e2:
                    print(f"[STTEngine] Soundfile fallback failed: {e2}")
                    return np.array([], dtype=np.float32)
            else:
                return np.array([], dtype=np.float32)

    if len(audio) == 0:
        return audio

    rms = float(np.sqrt(np.mean(audio ** 2)))
    peak = float(np.max(np.abs(audio)))
    
    # Only skip if truly zero signal (dead mic / completely muted)
    if peak < 0.001 and rms < 0.0001:
        print(f"[STTEngine] Silent/zero-signal audio detected (rms={rms:.6f}, peak={peak:.6f}). Skipping Whisper.")
        return np.array([], dtype=np.float32)

    # Clean Peak Normalization to ensure Whisper receives optimal dynamic range
    if peak > 0.0005 and peak < 0.85:
        gain = min(0.85 / peak, 30.0)
        audio = audio * gain

    return audio


class STTEngine:
    def __init__(self, model_size: Optional[str] = None, device: Optional[str] = None, compute_type: Optional[str] = None):
        settings = get_settings()
        self.model_size = model_size or settings.get("model_name", "small.en")
        self.device = device or settings.get("device", "cpu")
        self.compute_type = compute_type or settings.get("compute_type", "int8")
        
        self.model: Optional[WhisperModel] = None
        self._lock = threading.Lock()
        self._load_model()

    def _load_model(self):
        """Loads or reloads the Faster-Whisper model."""
        with self._lock:
            print(f"[STTEngine] Loading model '{self.model_size}' on {self.device} ({self.compute_type})...")
            start = time.time()
            try:
                self.model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                    cpu_threads=4,
                    num_workers=1
                )
            except Exception as e:
                print(f"[STTEngine] Failed loading '{self.model_size}', falling back to 'base.en': {e}")
                self.model_size = "base.en"
                self.model = WhisperModel("base.en", device="cpu", compute_type="int8")

            elapsed = time.time() - start
            print(f"[STTEngine] Model '{self.model_size}' loaded in {elapsed:.2f}s")

    def reload(self, model_size: str, device: str = "cpu", compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._load_model()

    def transcribe(
        self,
        audio_input: Union[np.ndarray, str],
        language: str = "en",
        custom_prompt: Optional[str] = None,
        beam_size: int = 5
    ) -> Dict:
        if self.model is None:
            self._load_model()

        start_time = time.time()
        processed_audio = load_and_normalize_audio(audio_input)

        if len(processed_audio) == 0:
            return {
                "text": "",
                "segments": [],
                "language": language,
                "duration_ms": 0,
                "word_count": 0
            }

        with self._lock:
            # Robust transcription parameters: never drop real human voice
            segments_gen, info = self.model.transcribe(
                processed_audio,
                language=language if language != "auto" else "en",
                initial_prompt=custom_prompt if custom_prompt else None,
                beam_size=beam_size,
                best_of=beam_size,
                temperature=0.0,
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters=dict(
                    threshold=0.35,
                    min_silence_duration_ms=500,
                    speech_pad_ms=250
                ),
                no_speech_threshold=0.7,
                compression_ratio_threshold=2.4,
                log_prob_threshold=-1.0
            )

            segments = []
            full_text_parts = []
            for s in segments_gen:
                text_clean = s.text.strip()
                if not text_clean:
                    continue

                # Filter out obvious empty YouTube subtitle hallucinations on dead air
                if text_clean.lower() in HALLUCINATION_PHRASES:
                    continue

                full_text_parts.append(text_clean)
                segments.append({
                    "start": round(s.start, 2),
                    "end": round(s.end, 2),
                    "text": text_clean,
                    "confidence": round(float(np.exp(s.avg_logprob)) if s.avg_logprob else 0.8, 2)
                })

        raw_text = " ".join(full_text_parts).strip()
        elapsed_ms = int((time.time() - start_time) * 1000)

        return {
            "text": raw_text,
            "segments": segments,
            "language": info.language if info else language,
            "duration_ms": elapsed_ms,
            "word_count": len(raw_text.split()) if raw_text else 0
        }


if __name__ == "__main__":
    stt = STTEngine()
    print("STTEngine verified.")
