import asyncio
import time
import threading
from typing import Callable, Dict, List, Optional, Any
import numpy as np

from .audio_engine import AudioEngine
from .stt_engine import STTEngine
from .window_inspector import WindowInspector
from .mode_processor import ModeProcessor
from .hotkey_listener import HotkeyListener
from .db import (
    get_settings,
    update_setting,
    insert_transcript,
    search_transcripts,
    get_vocabulary,
    add_vocabulary
)


class OpenWriteOrchestrator:
    def __init__(self):
        self.settings = get_settings()
        
        # Core subsystems
        self.audio_engine = AudioEngine(vad_sensitivity=float(self.settings.get("vad_sensitivity", "0.5")))
        self.stt_engine = STTEngine(
            model_size=self.settings.get("model_name", "tiny.en"),
            device=self.settings.get("device", "cpu"),
            compute_type=self.settings.get("compute_type", "int8")
        )
        self.mode_processor = ModeProcessor()
        self.window_inspector = WindowInspector()

        # State
        self.is_recording = False
        self.current_window_info: Dict[str, str] = {}
        self.target_hwnd: Optional[int] = None
        self.selected_text_cache: str = ""
        self._record_start_time: float = 0.0

        # Event hooks for WebSocket / UI
        self.on_state_change: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_rms_update: Optional[Callable[[float, bool], None]] = None
        self.on_transcription_complete: Optional[Callable[[Dict[str, Any]], None]] = None

        # Wire audio engine RMS callback
        self.audio_engine.on_rms_callback = self._handle_rms_update

        # Native Windows Floating HUD (Dynamic Island)
        try:
            from .floating_hud import FloatingHUD
            self.hud = FloatingHUD(
                on_toggle_recording=self.toggle_recording,
                on_mode_change=self.set_active_mode,
                initial_mode=self.settings.get("active_mode", "enhancement")
            )
            self.hud.start()
        except Exception as e:
            print(f"[Orchestrator] Floating HUD init warning: {e}")
            self.hud = None

        # Hotkey listener
        self.hotkey_listener = HotkeyListener(
            on_start=self.start_recording,
            on_stop=self.stop_recording,
            mode=self.settings.get("shortcut_mode", "hybrid"),
            hotkey_str=self.settings.get("hotkey", "<alt>+<space>")
        )
        self.hotkey_listener.start()

    def toggle_recording(self):
        """Toggles recording from HUD click or external triggers."""
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def set_active_mode(self, mode: str):
        self.settings["active_mode"] = mode
        update_setting("active_mode", mode)

    def _handle_rms_update(self, rms: float, is_speech: bool):
        if self.on_rms_update and self.is_recording:
            try:
                self.on_rms_update(rms, is_speech)
            except Exception:
                pass

    def start_recording(self):
        """Called on hotkey press or web UI start."""
        if self.is_recording:
            return

        # 1. Inspect active window before switching focus
        self.current_window_info = self.window_inspector.get_active_window_info()
        self.target_hwnd = self.current_window_info.get("hwnd")
        app_process = self.current_window_info.get("process", "Editor")

        if self.hud:
            self.hud.set_recording(True, app_process)
        
        # 2. Grab highlighted text (for Voice Edit / Rewrite Mode)
        # Only grab if user might be editing or rewritng
        active_mode = self.settings.get("active_mode", "enhancement")
        if active_mode == "rewrite":
            self.selected_text_cache = self.window_inspector.grab_selected_text()
        else:
            self.selected_text_cache = ""

        # 3. Start audio stream
        started = self.audio_engine.start_recording()
        if not started:
            print("[Orchestrator] Audio capture failed to start.")
            return

        self.is_recording = True
        self._record_start_time = time.time()

        # Notify subscribers (Dynamic Island / Web UI)
        if self.on_state_change:
            self.on_state_change({
                "event": "recording_started",
                "app": self.current_window_info.get("process", ""),
                "title": self.current_window_info.get("title", ""),
                "recommended_mode": self.current_window_info.get("recommended_mode", "enhancement"),
                "has_selection": bool(self.selected_text_cache)
            })
        print(f"[Orchestrator] Recording started for app: {self.current_window_info.get('process')}")

    def stop_recording(self) -> Optional[Dict[str, Any]]:
        """Called on hotkey release or web UI stop."""
        if not self.is_recording:
            return None

        self.is_recording = False
        duration_sec = time.time() - self._record_start_time

        # 1. Stop audio capture & retrieve trimmed audio
        audio = self.audio_engine.stop_recording()

        # If recording was too short (< 0.25s) or silent, ignore
        if len(audio) < int(16000 * 0.25):
            print("[Orchestrator] Recording too short or empty, ignoring.")
            if self.hud:
                self.hud.set_recording(False, self.current_window_info.get("process", ""))
            if self.on_state_change:
                self.on_state_change({"event": "recording_cancelled", "reason": "too_short"})
            return None

        app_name = self.current_window_info.get("process", "Editor")
        if self.hud:
            self.hud.show_status("⚡ Transcribing...", f"Processing speech for {app_name}")

        if self.on_state_change:
            self.on_state_change({"event": "transcribing"})

        # 2. Transcribe with Whisper (INT8 CPU/iGPU)
        transcription = self.stt_engine.transcribe(audio)
        raw_text = transcription.get("text", "").strip()

        if not raw_text:
            print("[Orchestrator] No speech detected.")
            if self.hud:
                self.hud.show_status("No Speech Detected", "Speak clearly into mic")
            if self.on_state_change:
                self.on_state_change({"event": "recording_cancelled", "reason": "no_speech"})
            return None

        # 3. Determine active mode (app-aware auto-switching if enabled)
        app_aware = self.settings.get("app_aware", "true").lower() == "true"
        chosen_mode = self.settings.get("active_mode", "enhancement")
        
        if app_aware and self.current_window_info.get("recommended_mode"):
            # If active window is IDE/editor or chat, auto-adapt mode
            rec_mode = self.current_window_info["recommended_mode"]
            if chosen_mode not in ("rewrite", "assistant"):
                chosen_mode = rec_mode

        # 4. Process text through ModeProcessor
        processed_text = self.mode_processor.process(
            raw_text=raw_text,
            mode=chosen_mode,
            selected_text=self.selected_text_cache,
            app_name=self.current_window_info.get("process", "")
        )

        # 5. Persist to SQLite & FTS5 search
        app_name = self.current_window_info.get("process", "")
        record_id = insert_transcript(
            raw_text=raw_text,
            processed_text=processed_text,
            mode=chosen_mode,
            app_name=app_name,
            duration_ms=transcription.get("duration_ms", int(duration_sec * 1000))
        )

        result = {
            "id": record_id,
            "raw_text": raw_text,
            "processed_text": processed_text,
            "mode": chosen_mode,
            "app_name": app_name,
            "duration_ms": transcription.get("duration_ms", 0),
            "word_count": len(processed_text.split()),
            "created_at": time.time()
        }

        # 6. Auto-paste into active cursor position if enabled
        auto_paste = self.settings.get("auto_paste", "true").lower() == "true"
        if auto_paste and processed_text:
            time.sleep(0.04)  # Let modifier keys settle
            pasted = self.window_inspector.paste_text(processed_text, target_hwnd=self.target_hwnd)
            result["pasted"] = pasted
            if self.hud:
                self.hud.show_status(f"✨ Injected into {app_name}!", f"{len(processed_text.split())} words typed")
        elif self.hud:
            self.hud.show_status("✨ Ready", "Text transcribed")

        # 7. Notify UI & WebSockets
        if self.on_transcription_complete:
            self.on_transcription_complete(result)

        if self.on_state_change:
            self.on_state_change({"event": "idle", "last_result": result})

        print(f"[Orchestrator] Transcribed ({chosen_mode}): {processed_text}")
        return result

    def transcribe_file(self, file_path: str, mode: Optional[str] = None) -> Dict[str, Any]:
        """Transcribe uploaded audio file (OpenWhispr meeting feature)."""
        mode = mode or self.settings.get("active_mode", "enhancement")
        stt_result = self.stt_engine.transcribe(file_path)
        raw_text = stt_result.get("text", "")
        processed = self.mode_processor.process(raw_text, mode=mode)
        
        record_id = insert_transcript(
            raw_text=raw_text,
            processed_text=processed,
            mode=mode,
            app_name="AudioFileImport",
            duration_ms=stt_result.get("duration_ms", 0)
        )
        return {
            "id": record_id,
            "raw_text": raw_text,
            "processed_text": processed,
            "mode": mode,
            "segments": stt_result.get("segments", []),
            "duration_ms": stt_result.get("duration_ms", 0),
            "word_count": len(processed.split())
        }

    def update_settings(self, new_settings: Dict[str, str]):
        """Updates runtime settings & reconfigures components."""
        for k, v in new_settings.items():
            update_setting(k, str(v))
            self.settings[k] = str(v)

        if "shortcut_mode" in new_settings:
            self.hotkey_listener.update_settings(mode=new_settings["shortcut_mode"])

        if "model_name" in new_settings:
            self.stt_engine.reload(
                model_size=new_settings["model_name"],
                device=self.settings.get("device", "cpu"),
                compute_type=self.settings.get("compute_type", "int8")
            )

    def shutdown(self):
        """Clean resource release."""
        self.hotkey_listener.stop()
        self.audio_engine.close()


# Global singleton orchestrator
_orchestrator_instance: Optional[OpenWriteOrchestrator] = None

def get_orchestrator() -> OpenWriteOrchestrator:
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = OpenWriteOrchestrator()
    return _orchestrator_instance
