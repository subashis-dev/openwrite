import os
import json
import base64
import asyncio
import shutil
import tempfile
import time
from typing import Dict, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from core.db import (
    init_db,
    get_settings,
    update_setting,
    search_transcripts,
    get_vocabulary,
    add_vocabulary,
    insert_transcript,
    get_db_connection
)
from core.orchestrator import get_orchestrator, OpenWriteOrchestrator
from core.tts_engine import TTSEngine

init_db()
tts_engine = TTSEngine()

app = FastAPI(title="OpenWrite Sovereign Voice Engine", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
UPLOAD_DIR = os.path.join(BASE_DIR, "data", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()
server_loop: Optional[asyncio.AbstractEventLoop] = None

def broadcast_threadsafe(message: dict):
    global server_loop
    if server_loop and server_loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(manager.broadcast(message), server_loop)
        except Exception:
            pass

@app.on_event("startup")
async def on_startup():
    global server_loop
    server_loop = asyncio.get_running_loop()
    print("[Server] Captured running asyncio event loop for WebSockets.")

# Wire orchestrator events
def on_state_change(data: dict):
    broadcast_threadsafe({"type": "state", "data": data})

def on_rms_update(rms: float, is_speech: bool):
    broadcast_threadsafe({
        "type": "audio_level",
        "rms": round(rms, 4),
        "is_speech": is_speech
    })

def on_transcription_complete(result: dict):
    broadcast_threadsafe({"type": "transcription", "data": result})

orchestrator = get_orchestrator()
orchestrator.on_state_change = on_state_change
orchestrator.on_rms_update = on_rms_update
orchestrator.on_transcription_complete = on_transcription_complete


# ----------------- REST Endpoints ----------------- #

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>OpenWrite is running on port 6992</h1>")


@app.get("/api/status")
async def get_status():
    settings = get_settings()
    return {
        "status": "online",
        "is_recording": orchestrator.is_recording,
        "active_mode": settings.get("active_mode", "enhancement"),
        "shortcut_mode": settings.get("shortcut_mode", "hybrid"),
        "hotkey": settings.get("hotkey", "<alt>+<space>"),
        "model_name": settings.get("model_name", "tiny.en"),
        "device": settings.get("device", "cpu"),
        "compute_type": settings.get("compute_type", "int8"),
        "auto_paste": settings.get("auto_paste", "true") == "true",
        "app_aware": settings.get("app_aware", "true") == "true",
        "active_app": orchestrator.current_window_info.get("process", ""),
        "active_title": orchestrator.current_window_info.get("title", ""),
    }


@app.get("/api/settings")
async def fetch_settings():
    return get_settings()


@app.post("/api/settings")
async def update_settings(payload: Dict[str, str]):
    orchestrator.update_settings(payload)
    return {"status": "success", "settings": get_settings()}


@app.get("/api/devices")
async def get_audio_devices():
    return orchestrator.audio_engine.list_input_devices()


@app.get("/api/history")
async def get_history(query: str = "", mode: Optional[str] = None, limit: int = 50):
    return search_transcripts(query=query, mode=mode, limit=limit)


@app.delete("/api/history/{record_id}")
async def delete_history(record_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM transcripts WHERE id = ?", (record_id,))
    cur.execute("DELETE FROM transcripts_fts WHERE rowid = ?", (record_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted", "id": record_id}


@app.get("/api/vocabulary")
async def fetch_vocabulary():
    return get_vocabulary()


@app.post("/api/vocabulary")
async def add_term(payload: Dict[str, str]):
    term = payload.get("term", "").strip()
    category = payload.get("category", "custom")
    if not term:
        raise HTTPException(status_code=400, detail="Term cannot be empty")
    success = add_vocabulary(term, category)
    return {"status": "success" if success else "duplicate", "term": term}


@app.delete("/api/vocabulary/{term}")
async def delete_term(term: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM vocabulary WHERE term = ?", (term.strip(),))
    conn.commit()
    conn.close()
    return {"status": "deleted", "term": term}


@app.post("/api/dictate/start")
async def start_dictation():
    """Manual trigger from desktop / hotkey."""
    orchestrator.start_recording()
    return {"status": "recording_started"}


@app.post("/api/dictate/stop")
async def stop_dictation():
    """Manual stop from desktop / hotkey."""
    result = orchestrator.stop_recording()
    return {"status": "recording_stopped", "result": result}


@app.post("/api/dictate/browser")
async def dictate_from_browser(file: UploadFile = File(...), mode: Optional[str] = Form(None)):
    """
    Directly receives browser-captured audio (WebRTC / getUserMedia) from PC or mobile.
    Transcribes offline, applies mode transformations, saves to DB, and auto-pastes!
    """
    file_ext = os.path.splitext(file.filename)[1] or ".webm"
    temp_path = os.path.join(UPLOAD_DIR, f"browser_mic_{int(time.time()*1000)}{file_ext}")

    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        # Save a debug copy to inspect audio quality
        debug_path = os.path.join(BASE_DIR, "data", f"last_recording{file_ext}")
        shutil.copy2(temp_path, debug_path)

        active_mode = mode or orchestrator.settings.get("active_mode", "enhancement")
        
        # 1. Transcribe offline with Whisper
        stt_result = orchestrator.stt_engine.transcribe(temp_path)
        raw_text = stt_result.get("text", "").strip()

        if not raw_text:
            return {"status": "no_speech", "result": None}

        # 2. Process through ModeProcessor
        processed_text = orchestrator.mode_processor.process(
            raw_text=raw_text,
            mode=active_mode,
            selected_text=orchestrator.selected_text_cache,
            app_name="BrowserCompanion"
        )

        # 3. Save to database
        record_id = insert_transcript(
            raw_text=raw_text,
            processed_text=processed_text,
            mode=active_mode,
            app_name="BrowserCompanion",
            duration_ms=stt_result.get("duration_ms", 0)
        )

        result = {
            "id": record_id,
            "raw_text": raw_text,
            "processed_text": processed_text,
            "mode": active_mode,
            "app_name": "BrowserCompanion",
            "duration_ms": stt_result.get("duration_ms", 0),
            "word_count": len(processed_text.split()),
            "created_at": time.time()
        }

        # 4. Auto-paste into active cursor if enabled
        if orchestrator.settings.get("auto_paste", "true").lower() == "true":
            orchestrator.window_inspector.paste_text(processed_text)
            result["pasted"] = True

        # 5. Broadcast to connected WebSockets
        broadcast_threadsafe({"type": "transcription", "data": result})
        broadcast_threadsafe({"type": "state", "data": {"event": "idle", "last_result": result}})

        return {"status": "success", "result": result}
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


@app.post("/api/tts/speak")
async def tts_speak(payload: Dict = Body(...)):
    """Synthesize text to WAV audio using Kokoro ONNX (with Windows SAPI fallback)."""
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    voice = payload.get("voice", "af_heart")
    speed = float(payload.get("speed", 1.05))
    wav_bytes = await asyncio.to_thread(tts_engine.synthesize_wav, text, voice, speed)
    if not wav_bytes:
        raise HTTPException(status_code=500, detail="TTS synthesis failed")
    return Response(content=wav_bytes, media_type="audio/wav")


@app.post("/api/talk")
async def live_talk(file: UploadFile = File(...), voice: Optional[str] = Form("af_heart")):
    """
    Live Voice Talk loop:
    1. Transcribe user speech (Whisper)
    2. Generate conversational AI response (LM Studio / fallback)
    3. Synthesize spoken reply (Kokoro TTS)
    """
    file_ext = os.path.splitext(file.filename)[1] or ".webm"
    temp_path = os.path.join(UPLOAD_DIR, f"talk_mic_{int(time.time()*1000)}{file_ext}")
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        stt_result = await asyncio.to_thread(orchestrator.stt_engine.transcribe, temp_path)
        raw_text = stt_result.get("text", "").strip()
        if not raw_text:
            return {"status": "no_speech", "user_text": "", "reply_text": "", "audio_base64": ""}

        reply_text = await asyncio.to_thread(
            orchestrator.mode_processor.process,
            raw_text,
            "assistant",
            "",
            "LiveTalk",
        )

        wav_bytes = await asyncio.to_thread(tts_engine.synthesize_wav, reply_text, voice or "af_heart", 1.05)
        audio_b64 = base64.b64encode(wav_bytes).decode("ascii") if wav_bytes else ""

        record_id = insert_transcript(
            raw_text=raw_text,
            processed_text=reply_text,
            mode="assistant",
            app_name="LiveTalk",
            duration_ms=stt_result.get("duration_ms", 0),
        )

        result = {
            "id": record_id,
            "raw_text": raw_text,
            "processed_text": reply_text,
            "mode": "assistant",
            "app_name": "LiveTalk",
            "duration_ms": stt_result.get("duration_ms", 0),
            "word_count": len(reply_text.split()),
            "created_at": time.time(),
        }
        broadcast_threadsafe({"type": "transcription", "data": result})
        broadcast_threadsafe({"type": "state", "data": {"event": "idle", "last_result": result}})

        return {
            "status": "success",
            "user_text": raw_text,
            "reply_text": reply_text,
            "audio_base64": audio_b64,
            "result": result,
        }
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


@app.post("/api/upload")
async def upload_meeting_audio(file: UploadFile = File(...), mode: Optional[str] = Form("enhancement")):
    """Upload meeting audio / voice memo for offline transcription."""
    file_ext = os.path.splitext(file.filename)[1]
    temp_path = os.path.join(UPLOAD_DIR, f"import_{int(time.time()*1000)}{file_ext}")
    
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        result = orchestrator.transcribe_file(temp_path, mode=mode)
        return {"status": "success", "result": result}
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


@app.post("/api/tts/speak")
async def tts_speak(payload: Dict = Body(...)):
    """Synthesize text to WAV audio using Kokoro ONNX (with Windows SAPI fallback)."""
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    voice = payload.get("voice", "af_heart")
    speed = float(payload.get("speed", 1.05))
    wav_bytes = await asyncio.to_thread(tts_engine.synthesize_wav, text, voice, speed)
    if not wav_bytes:
        raise HTTPException(status_code=500, detail="TTS synthesis failed")
    return Response(content=wav_bytes, media_type="audio/wav")


@app.post("/api/talk")
async def live_talk(file: UploadFile = File(...), voice: Optional[str] = Form("af_heart")):
    """
    Live Voice Talk loop:
    1. Transcribe user speech (Whisper)
    2. Generate conversational AI response (LM Studio / fallback)
    3. Synthesize spoken reply (Kokoro TTS)
    """
    file_ext = os.path.splitext(file.filename)[1] or ".webm"
    temp_path = os.path.join(UPLOAD_DIR, f"talk_mic_{int(time.time()*1000)}{file_ext}")
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        stt_result = await asyncio.to_thread(orchestrator.stt_engine.transcribe, temp_path)
        raw_text = stt_result.get("text", "").strip()
        if not raw_text:
            return {"status": "no_speech", "user_text": "", "reply_text": "", "audio_base64": ""}

        reply_text = await asyncio.to_thread(
            orchestrator.mode_processor.process,
            raw_text,
            "assistant",
            "",
            "LiveTalk",
        )

        wav_bytes = await asyncio.to_thread(tts_engine.synthesize_wav, reply_text, voice or "af_heart", 1.05)
        audio_b64 = base64.b64encode(wav_bytes).decode("ascii") if wav_bytes else ""

        record_id = insert_transcript(
            raw_text=raw_text,
            processed_text=reply_text,
            mode="assistant",
            app_name="LiveTalk",
            duration_ms=stt_result.get("duration_ms", 0),
        )

        result = {
            "id": record_id,
            "raw_text": raw_text,
            "processed_text": reply_text,
            "mode": "assistant",
            "app_name": "LiveTalk",
            "duration_ms": stt_result.get("duration_ms", 0),
            "word_count": len(reply_text.split()),
            "created_at": time.time(),
        }
        broadcast_threadsafe({"type": "transcription", "data": result})
        broadcast_threadsafe({"type": "state", "data": {"event": "idle", "last_result": result}})

        return {
            "status": "success",
            "user_text": raw_text,
            "reply_text": reply_text,
            "audio_base64": audio_b64,
            "result": result,
        }
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


@app.get("/mcp")
async def mcp_manifest():
    return {
        "name": "openwrite-mcp",
        "description": "Sovereign offline voice dictation, transcription, and audio processing MCP server",
        "version": "1.0.0",
        "tools": [
            {
                "name": "dictate_text",
                "description": "Trigger voice dictation or get last dictated note",
                "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["start", "stop", "last"]}}}
            },
            {
                "name": "search_voice_notes",
                "description": "Search local voice notes using full-text search",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
            },
            {
                "name": "transcribe_audio_file",
                "description": "Transcribe an audio file offline using INT8 Whisper",
                "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}
            }
        ]
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    await websocket.send_json({
        "type": "init",
        "data": {
            "status": "connected",
            "is_recording": orchestrator.is_recording,
            "settings": get_settings()
        }
    })
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            action = msg.get("action")
            
            if action == "start":
                orchestrator.start_recording()
            elif action == "stop":
                orchestrator.stop_recording()
            elif action == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


if __name__ == "__main__":
    print("=" * 60)
    print(" OpenWrite Sovereign Voice Engine")
    print(" Running on Port: 6992")
    print(" Accessible locally at: http://localhost:6992")
    print("=" * 60)
    uvicorn.run("server:app", host="0.0.0.0", port=6992, reload=False, workers=1)
