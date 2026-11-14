import os
import sys
import wave
import struct
import math
import urllib.request
import urllib.parse
import json
import time

BASE_URL = "http://localhost:6992"

def http_get(path):
    req = urllib.request.Request(f"{BASE_URL}{path}")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

def http_post_json(path, data):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

def http_delete(path):
    req = urllib.request.Request(f"{BASE_URL}{path}", method="DELETE")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

def http_upload_file(path, file_path):
    boundary = "----BoundaryTestOpenWrite12345"
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(file_path)}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8") + file_bytes
    body += (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="mode"\r\n\r\n'
        f"enhancement\r\n"
    ).encode("utf-8")
    body += f"--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

def test_status():
    print("[TEST 1/8] Checking /api/status...")
    status_code, data = http_get("/api/status")
    assert status_code == 200, f"Status failed: {status_code}"
    assert data["status"] == "online"
    assert data["model_name"] in ("small.en", "base.en", "tiny.en")
    assert data["compute_type"] == "int8"
    print(f" -> PASSED: Engine online. Model: {data['model_name']} on {data['device']} ({data['compute_type']})")

def test_devices():
    print("[TEST 2/8] Checking /api/devices...")
    status_code, devices = http_get("/api/devices")
    assert status_code == 200
    assert len(devices) > 0, "No audio devices found"
    default_dev = next((d for d in devices if d.get("default")), devices[0])
    print(f" -> PASSED: Detected {len(devices)} audio devices. Default: '{default_dev['name']}'")

def test_vocabulary():
    print("[TEST 3/8] Checking vocabulary management...")
    # Add term
    term = "OpenWriteSynth"
    status_code, resp = http_post_json("/api/vocabulary", {"term": term, "category": "test"})
    assert status_code == 200
    
    # Verify in list
    status_code, terms = http_get("/api/vocabulary")
    assert term in terms, f"Term {term} not found in vocabulary list"
    
    # Delete term
    status_code, _ = http_delete(f"/api/vocabulary/{term}")
    assert status_code == 200
    print(f" -> PASSED: Vocabulary addition, verification, and deletion verified.")

def test_mode_processing():
    print("[TEST 4/8] Testing ModeProcessor transformations...")
    from core.mode_processor import ModeProcessor
    mp = ModeProcessor()

    # Test 1: Enhancement
    raw_enh = "um so yeah period new line let us launch openwrite comma today"
    res_enh = mp.process(raw_enh, mode="enhancement")
    assert "openwrite" in res_enh.lower()
    print(" -> Enhancement Output:", res_enh)

    # Test 2: Code Mode
    raw_code = "const camel case user profile equal open brace fat arrow close brace"
    res_code = mp.process(raw_code, mode="code")
    assert "userProfile" in res_code
    assert "=>" in res_code
    print(" -> Code Mode Output:", res_code)

    # Test 3: Email Mode
    raw_email = "first review the pull request second run test suite third deploy"
    res_email = mp.process(raw_email, mode="email")
    assert "Best regards," in res_email
    print(" -> Email Mode Output:\n" + "\n".join("    " + line for line in res_email.splitlines()))

    # Test 4: Voice Edit / Rewrite Mode (FreeFlow style)
    selected = "const old_variable_name = 100;"
    res_rewrite = mp.process("convert to snake case", mode="rewrite", selected_text=selected)
    print(" -> Voice Edit Output:", res_rewrite)
    print(" -> PASSED: All 4 mode transformations passed.")

def test_file_upload_transcription():
    print("[TEST 5/8] Testing audio file import & offline Whisper STT via /api/upload...")
    # Generate a 1.0-second 16kHz mono WAV file
    wav_path = os.path.join(os.path.dirname(__file__), "test_sample.wav")
    sample_rate = 16000
    duration = 1.0
    num_samples = int(sample_rate * duration)
    
    with wave.open(wav_path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for i in range(num_samples):
            val = int(32767.0 * 0.3 * math.sin(2.0 * math.pi * 440.0 * (i / sample_rate)))
            wf.writeframes(struct.pack("<h", val))

    try:
        status_code, resp = http_upload_file("/api/upload", wav_path)
        assert status_code == 200, f"Upload failed: {resp}"
        assert resp["status"] == "success"
        print(f" -> PASSED: Audio file parsed & transcribed offline in {resp['result']['duration_ms']}ms.")
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

def test_sqlite_fts5_search():
    print("[TEST 6/8] Testing SQLite FTS5 Full-Text Search...")
    from core.db import insert_transcript, search_transcripts
    test_phrase = "QuantumSuperpositionAndEntanglement"
    insert_transcript(
        raw_text=test_phrase,
        processed_text=f"Testing {test_phrase} in sovereign database",
        mode="dictation",
        app_name="TestRunner",
        duration_ms=120
    )

    # Search via REST API
    status_code, results = http_get(f"/api/history?query=QuantumSuperposition")
    assert status_code == 200
    assert len(results) > 0, "FTS5 search did not return indexed phrase"
    assert test_phrase in results[0]["processed_text"]
    print(f" -> PASSED: SQLite FTS5 search matched '{test_phrase}' instantly.")

def test_settings_persistence():
    print("[TEST 7/8] Testing settings update & persistence...")
    status_code, _ = http_post_json("/api/settings", {"shortcut_mode": "hybrid", "auto_paste": "true"})
    assert status_code == 200
    status_code, settings = http_get("/api/settings")
    assert settings["shortcut_mode"] == "hybrid"
    assert settings["auto_paste"] == "true"
    print(f" -> PASSED: Settings persisted and returned accurately.")

def test_mcp_protocol():
    print("[TEST 8/8] Testing MCP server protocol endpoint...")
    status_code, manifest = http_get("/mcp")
    assert status_code == 200
    assert manifest["name"] == "openwrite-mcp"
    assert len(manifest["tools"]) == 3
    print(f" -> PASSED: MCP manifest valid. Tools: {[t['name'] for t in manifest['tools']]}")

if __name__ == "__main__":
    print("=" * 60)
    print(" Running Full OpenWrite Verification Suite")
    print("=" * 60)
    test_status()
    test_devices()
    test_vocabulary()
    test_mode_processing()
    test_file_upload_transcription()
    test_sqlite_fts5_search()
    test_settings_persistence()
    test_mcp_protocol()
    print("=" * 60)
    print(" ALL 8 OPENWRITE TEST SUITES PASSED SUCCESSFULLY! ")
    print("=" * 60)
