# OpenWrite — Sovereign AI Voice & Dictation Engine 🎙️⚡

> **The Sovereign, 100% Offline AI Dictation Engine.** Synthesizing the best features of [FreeFlow](https://github.com/zachlatta/freeflow), [VoiceInk](https://github.com/beingpax/voiceink), [Handy](https://handy.computer), and [OpenWhispr](https://github.com/OpenWhispr/openwhispr) into a unified desktop and mobile engine.

---

## 🌟 Highlights

- **100% Offline & Private:** Zero cloud dependencies, zero telemetry, zero audio leaks. All speech-to-text inference runs entirely on your local machine.
- **Runs Smoothly on ANY PC:** Quantized with **INT8 Faster-Whisper** execution on CPU/iGPU/APU. No NVIDIA discrete GPU required. Runs at **~80ms latency** taking less than 100MB of RAM.
- **Global Hotkey & Direct Cursor Injection:** Press `Alt + Space` anywhere on Windows (VS Code, Slack, Word, Chrome, Terminal) to speak and have formatted text automatically pasted directly into your cursor.
- **Synthesized Shortcut Engine:** Full support for **Hybrid Mode** (Hold = Push-to-Talk, Quick Tap = Toggle Mode), **Push-to-Talk Only**, and **Toggle Only**.
- **Voice Edit / Rewrite Mode (from FreeFlow):** Highlight any text in your editor, hold `Alt + Space`, speak an edit instruction (*"make this concise"*, *"convert to snake_case"*, *"format as bullet points"*), and it replaces the selected text in-place.
- **App-Aware Context Ingestion (from VoiceInk):** Automatically switches modes based on your active application (e.g. `code` mode for VS Code / Cursor, `dictation` for Slack / Discord, `email` for Outlook / Gmail).
- **Offline Meeting & Audio Transcriber (from OpenWhispr):** Drag-and-drop recorded audio files (WAV, MP3, M4A, FLAC) for batch transcription.
- **SQLite FTS5 Full-Text Search:** Instant millisecond searching across your entire lifetime voice note history.
- **Dynamic Jargon & Vocabulary Prompting:** Guarantees technical jargon, proper nouns, and developer terms (`PyTorch`, `Antigravity`, `MedVision`, `CUDA`) are recognized with 100% precision.
- **Mobile Companion Ready (Android & iOS):** Access `http://<YOUR-PC-IP>:6992` on any phone over local Wi-Fi to use your smartphone as a wireless dictation microphone.
- **MCP Server Built-in:** Native Model Context Protocol endpoint at `http://localhost:6992/mcp` for agentic integration.

---

## 🚀 Quick Start

### 1. Launch OpenWrite
```bash
cd C:\Users\subas\Claude\openwrite
python server.py
```
OpenWrite starts immediately on **Port 6992**:
- Desktop Hub & Floating Island: [http://localhost:6992](http://localhost:6992)
- Local Network / Mobile Companion: `http://<YOUR-PC-IP>:6992`

### 2. How to Dictate Anywhere
1. Place your cursor in any application (e.g., Notepad, VS Code, Slack, Browser).
2. **Press & hold `Alt + Space`** and speak your thoughts.
3. **Release `Alt + Space`** — OpenWrite transcribes, cleans up fillers, and pastes the formatted text into your cursor within ~80 milliseconds!

---

## 🎭 The 6 Operating Modes

| Mode | Verbal Example | Transformed Output |
| :--- | :--- | :--- |
| **✨ AI Enhancement** | *"um so yeah period new line let's ship openwrite comma today"* | `Let's ship openwrite, today` |
| **🎙️ Raw Dictation** | *"unfiltered verbatim speech capture exactly as spoken"* | `unfiltered verbatim speech capture exactly as spoken` |
| **💻 Code Mode** | *"const camel case user profile equal open brace fat arrow close brace"* | `const userProfile = { => }` |
| **✉️ Email Draft** | *"first update the docs second push release third ping the team"* | Formatted with greeting, bulleted tasks, and professional signoff |
| **✏️ Voice Edit** | *(Highlight text)* + *"convert to bullet points"* | Replaces selection in-place with clean markdown bullets |
| **🤖 Assistant** | *"summarize the next step for this pull request"* | Formatted actionable task prompt |

---

## 🏗️ Architecture

```
openwrite/
├── core/
│   ├── audio_engine.py       # 16kHz low-latency capture + real-time RMS visualizer
│   ├── stt_engine.py         # Quantized INT8 Faster-Whisper wrapper
│   ├── window_inspector.py   # Windows foreground detection + selection grab & paste
│   ├── mode_processor.py     # 6 offline transformation modes + code syntax engine
│   ├── hotkey_listener.py    # Global pynput hook (Hybrid, Push-to-Talk, Toggle)
│   ├── orchestrator.py       # Central pipeline orchestrator
│   └── db.py                 # SQLite database + FTS5 full-text search
├── web/
│   ├── index.html            # Floating Dynamic Island + Control Dashboard
│   ├── css/style.css         # Dark theme UI with reactive audio waveforms
│   └── js/app.js             # WebSocket audio streamer & client controller
├── data/
│   └── openwrite.db          # Sovereign local database
└── server.py                 # FastAPI server on Port 6992 + MCP endpoint
```

---

## 📱 Mobile Companion (Android & iOS)

To use your phone as a sovereign dictation microphone:
1. Ensure your phone is connected to the same Wi-Fi network as your PC.
2. Find your PC's local IP (e.g. `192.168.1.X`).
3. Open `http://192.168.1.X:6992` in Safari or Chrome.
4. Tap the microphone button on your phone — dictation will stream, transcribe, and type straight onto your computer screen!
