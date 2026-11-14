// OpenWrite Sovereign Client Controller
class OpenWriteApp {
  constructor() {
    this.ws = null;
    this.isRecording = false;
    this.activeMode = 'enhancement';
    this.liveTalkEnabled = false;
    this.rmsHistory = new Array(30).fill(0);
    this.currentRMS = 0;

    // Audio & MediaRecorder
    this.mediaStream = null;
    this.mediaRecorder = null;
    this.audioChunks = [];
    this.audioCtx = null;
    this.analyser = null;
    this.monitorAnimId = null;
    this.selectedDeviceId = localStorage.getItem('openwrite_mic_id') || '';
    this.availableDevices = [];

    // Web Speech API for Real-Time Streaming Live Dictation
    this.recognition = null;
    this.hasWebSpeech = false;
    this.isLiveRecognizing = false;
    this.liveStreamedText = '';

    // UI elements
    this.heroCanvas = document.getElementById('heroWaveform');
    this.heroCtx = this.heroCanvas ? this.heroCanvas.getContext('2d') : null;
    this.meterFill = document.getElementById('micMeterFill');
    this.micBadge = document.getElementById('micLiveBadge');
    this.dictationPad = document.getElementById('dictationPad');
    this.padStats = document.getElementById('padStats');
    this.micAlert = document.getElementById('micAlertBanner');
    this.liveBubble = document.getElementById('liveInterimBubble');
    this.liveInterimText = document.getElementById('liveInterimText');

    this.init();
  }

  async init() {
    this.bindEvents();
    this.initSpeechRecognition();
    this.connectWebSocket();
    this.startWaveformAnimation();
    this.initPad();
    await this.fetchStatus();
    await this.fetchHistory();
    await this.fetchVocabulary();
    await this.populateMicrophoneList();
  }

  // ----------------- Real-Time Streaming Live Dictation (Web Speech API) ----------------- //
  initSpeechRecognition() {
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRec) {
      console.warn('[OpenWrite] Web Speech API not supported in this browser, relying entirely on local Whisper.');
      this.hasWebSpeech = false;
      return;
    }

    try {
      this.hasWebSpeech = true;
      this.recognition = new SpeechRec();
      this.recognition.continuous = true;
      this.recognition.interimResults = true;
      this.recognition.lang = 'en-US';

      this.recognition.onstart = () => {
        console.log('[OpenWrite] Live Speech Recognition engine started');
        this.isLiveRecognizing = true;
        if (this.liveBubble) {
          this.liveBubble.style.display = 'flex';
        }
        if (this.liveInterimText) {
          this.liveInterimText.textContent = 'Listening live... Speak now';
        }
      };

      this.recognition.onresult = (event) => {
        let interim = '';
        let final = '';

        for (let i = event.resultIndex; i < event.results.length; ++i) {
          const res = event.results[i];
          if (res.isFinal) {
            final += res[0].transcript;
          } else {
            interim += res[0].transcript;
          }
        }

        const heroText = document.getElementById('heroStatusText');
        const displayText = (final || interim || '').trim();

        if (displayText) {
          if (this.liveBubble) this.liveBubble.style.display = 'flex';
          if (this.liveInterimText) this.liveInterimText.textContent = `🗣️ "${displayText}"`;
          if (heroText) heroText.textContent = `Live: "${displayText}"`;

          // Hide silent alert since sound is confirmed
          if (this.micAlert) this.micAlert.style.display = 'none';
        }

        if (final && final.trim()) {
          this.liveStreamedText += (this.liveStreamedText ? ' ' : '') + final.trim();
          this.appendLiveToPad(final.trim());
        }
      };

      this.recognition.onerror = (e) => {
        console.warn('[OpenWrite] Speech recognition event:', e.error);
        if (e.error === 'no-speech' && this.isRecording) {
          // Normal silence, keep listening
        }
      };

      this.recognition.onend = () => {
        this.isLiveRecognizing = false;
        // Keep restarting while user has recording active
        if (this.isRecording) {
          try { this.recognition.start(); } catch (err) {}
        } else {
          if (this.liveBubble) this.liveBubble.style.display = 'none';
        }
      };
    } catch (err) {
      console.warn('[OpenWrite] Error initializing Web Speech API:', err);
      this.hasWebSpeech = false;
    }
  }

  appendLiveToPad(text) {
    if (!this.dictationPad || !text) return;
    const current = this.dictationPad.value.trim();
    this.dictationPad.value = current ? `${current} ${text}` : text;
    this.updatePadStats();
    localStorage.setItem('openwrite_pad_content', this.dictationPad.value);
    this.dictationPad.scrollTop = this.dictationPad.scrollHeight;
  }

  initPad() {
    const saved = localStorage.getItem('openwrite_pad_content');
    if (saved && this.dictationPad && !this.dictationPad.value) {
      this.dictationPad.value = saved;
    }
    this.updatePadStats();
    if (this.dictationPad) {
      this.dictationPad.addEventListener('input', () => {
        this.updatePadStats();
        localStorage.setItem('openwrite_pad_content', this.dictationPad.value);
      });
    }

    const autoPaste = document.getElementById('autoPasteCheckbox');
    if (autoPaste) {
      const savedAuto = localStorage.getItem('openwrite_autopaste');
      if (savedAuto !== null) autoPaste.checked = savedAuto === 'true';
      autoPaste.addEventListener('change', (e) => {
        localStorage.setItem('openwrite_autopaste', e.target.checked);
        fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ auto_paste: e.target.checked })
        }).catch(() => {});
      });
    }
  }

  updatePadStats() {
    if (!this.dictationPad || !this.padStats) return;
    const text = this.dictationPad.value.trim();
    const words = text ? text.split(/\s+/).length : 0;
    const chars = text.length;
    this.padStats.textContent = `${words} words • ${chars} chars`;
  }

  // ----------------- Live Microphone Monitoring & Selection ----------------- //
  async populateMicrophoneList() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const audioInputs = devices.filter(d => d.kind === 'audioinput');
      this.availableDevices = audioInputs;
      const select = document.getElementById('micDeviceSelect');
      if (!select) return;

      select.innerHTML = '';
      if (audioInputs.length === 0) {
        select.innerHTML = '<option value="default">Default Microphone</option>';
        return;
      }

      // Find the best working microphone (prefer built-in Microphone Array if Yeti is muted)
      let preferredId = this.selectedDeviceId;
      if (!preferredId || preferredId === 'default') {
        const arrayDev = audioInputs.find(d => {
          const l = (d.label || '').toLowerCase();
          return l.includes('array') || l.includes('intel') || l.includes('realtek');
        });
        if (arrayDev && arrayDev.deviceId) {
          preferredId = arrayDev.deviceId;
          this.selectedDeviceId = preferredId;
          localStorage.setItem('openwrite_mic_id', preferredId);
        }
      }

      audioInputs.forEach((dev, idx) => {
        const opt = document.createElement('option');
        opt.value = dev.deviceId || 'default';
        const label = dev.label || `Microphone ${idx + 1}`;
        opt.textContent = label;
        if (opt.value === this.selectedDeviceId) {
          opt.selected = true;
        }
        select.appendChild(opt);
      });

      select.onchange = async (e) => {
        this.selectedDeviceId = e.target.value;
        localStorage.setItem('openwrite_mic_id', this.selectedDeviceId);
        if (this.micAlert) this.micAlert.style.display = 'none';
        await this.startLiveMicMonitoring();
      };
    } catch (err) {
      console.warn('[OpenWrite] Device enumeration error:', err);
    }
  }

  async startLiveMicMonitoring() {
    try {
      if (this.mediaStream) {
        this.mediaStream.getTracks().forEach(t => t.stop());
        this.mediaStream = null;
      }
      if (this.audioCtx && this.audioCtx.state !== 'closed') {
        await this.audioCtx.close().catch(() => {});
        this.audioCtx = null;
      }

      const constraints = {
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      };
      if (this.selectedDeviceId && this.selectedDeviceId !== 'default') {
        constraints.audio.deviceId = { exact: this.selectedDeviceId };
      }

      this.mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
      // Re-populate device list with granted labels
      await this.populateMicrophoneList();

      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      this.audioCtx = new AudioContextClass();
      if (this.audioCtx.state === 'suspended') {
        await this.audioCtx.resume().catch(() => {});
      }

      const source = this.audioCtx.createMediaStreamSource(this.mediaStream);
      this.analyser = this.audioCtx.createAnalyser();
      this.analyser.fftSize = 256;
      source.connect(this.analyser);

      const dataArray = new Uint8Array(this.analyser.frequencyBinCount);
      let silentFrames = 0;

      const updateMeter = () => {
        if (!this.analyser) return;
        this.analyser.getByteFrequencyData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
          sum += dataArray[i];
        }
        const avg = sum / dataArray.length;
        const norm = Math.min(1.0, avg / 128.0);
        this.currentRMS = norm;
        this.rmsHistory.push(norm);
        this.rmsHistory.shift();

        if (this.meterFill) {
          this.meterFill.style.width = `${Math.min(100, norm * 200)}%`;
        }

        if (this.micBadge) {
          if (norm > 0.03) {
            this.micBadge.textContent = `🟢 Mic: Live (${Math.round(norm * 100)}%)`;
            this.micBadge.style.color = '#10b981';
            this.micBadge.style.background = 'rgba(16, 185, 129, 0.15)';
            silentFrames = 0;
            if (this.micAlert) this.micAlert.style.display = 'none';
          } else {
            this.micBadge.textContent = 'Mic: Listening';
            this.micBadge.style.color = 'var(--text-muted)';
            this.micBadge.style.background = 'rgba(255,255,255,0.06)';
            if (this.isRecording) {
              silentFrames++;
              // If recording for > 2s with zero sound, alert user
              if (silentFrames > 120 && this.micAlert) {
                this.micAlert.style.display = 'flex';
              }
            }
          }
        }

        this.monitorAnimId = requestAnimationFrame(updateMeter);
      };

      if (this.monitorAnimId) cancelAnimationFrame(this.monitorAnimId);
      this.monitorAnimId = requestAnimationFrame(updateMeter);

    } catch (err) {
      console.warn('[OpenWrite] Mic monitor init error:', err);
      if (this.micBadge) {
        this.micBadge.textContent = '⚠️ Mic: Click to Allow';
        this.micBadge.style.color = '#f59e0b';
      }
    }
  }

  // ----------------- WebSocket Connection ----------------- //
  connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      console.log('[OpenWrite WS] Connected');
      this.updateHudStatus('OpenWrite Ready', 'Alt + Space or Click Mic');
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        this.handleWsMessage(msg);
      } catch (e) {
        console.error('[OpenWrite WS] Parse error:', e);
      }
    };

    this.ws.onclose = () => {
      this.updateHudStatus('Reconnecting...', 'Connecting to port 6992');
      setTimeout(() => this.connectWebSocket(), 2000);
    };

    this.ws.onerror = (err) => {
      console.error('[OpenWrite WS] Error:', err);
    };
  }

  handleWsMessage(msg) {
    if (msg.type === 'state') {
      const d = msg.data;
      if (d.event === 'recording_started') {
        this.setRecordingState(true, d);
      } else if (d.event === 'transcribing') {
        this.updateHudStatus('Transcribing...', 'Whisper running');
      } else if (d.event === 'idle' || d.event === 'recording_cancelled') {
        this.setRecordingState(false);
      }
    } else if (msg.type === 'transcription') {
      this.handleNewTranscription(msg.data);
    }
  }

  // ----------------- Dual-Engine Capture: Real-Time Live Dictation + Local Whisper ----------------- //
  async startBrowserRecording() {
    try {
      // Resume audio context on user click
      if (this.audioCtx && this.audioCtx.state === 'suspended') {
        await this.audioCtx.resume();
      }

      if (!this.mediaStream || !this.mediaStream.active) {
        await this.startLiveMicMonitoring();
      }

      if (!this.mediaStream) {
        alert('Could not access microphone. Please grant mic permissions.');
        return;
      }

      // Pick best supported MIME type
      let mimeType = '';
      const candidates = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/ogg;codecs=opus',
        'audio/mp4'
      ];
      for (const c of candidates) {
        if (MediaRecorder.isTypeSupported(c)) {
          mimeType = c;
          break;
        }
      }

      this.audioChunks = [];
      this.liveStreamedText = '';
      const options = mimeType ? { mimeType } : {};
      this.mediaRecorder = new MediaRecorder(this.mediaStream, options);

      this.mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          this.audioChunks.push(e.data);
        }
      };

      this.mediaRecorder.start(100);
      this.setRecordingState(true, { app: 'Microphone Active' });

      // Start Real-Time Streaming Live Dictation
      if (this.recognition && this.hasWebSpeech && !this.isLiveRecognizing) {
        try {
          this.recognition.start();
        } catch (e) {
          console.warn('[OpenWrite] Recognition start error:', e);
        }
      }

    } catch (err) {
      console.error('[OpenWrite] Recording start error:', err);
      alert('Microphone recording error: ' + err.message);
      this.setRecordingState(false);
    }
  }

  async stopBrowserRecording() {
    this.setRecordingState(false);

    // Stop real-time speech recognizer
    if (this.recognition && this.isLiveRecognizing) {
      try { this.recognition.stop(); } catch (e) {}
    }
    if (this.liveBubble) this.liveBubble.style.display = 'none';

    if (!this.mediaRecorder || this.mediaRecorder.state === 'inactive') {
      return;
    }

    this.updateHudStatus('Refining & Polishing...', 'Local AI mode processing');
    const heroText = document.getElementById('heroStatusText');
    if (heroText) heroText.textContent = 'Polishing transcription...';

    await new Promise((resolve) => {
      this.mediaRecorder.onstop = resolve;
      this.mediaRecorder.stop();
    });

    const recordedBlob = new Blob(this.audioChunks, {
      type: this.mediaRecorder.mimeType || 'audio/webm'
    });

    if (this.liveTalkEnabled) {
      await this.uploadTalkBlob(recordedBlob);
    } else {
      await this.uploadBrowserBlob(recordedBlob);
    }
  }

  async uploadBrowserBlob(blob) {
    const formData = new FormData();
    const ext = blob.type.includes('ogg') ? '.ogg' : (blob.type.includes('mp4') ? '.mp4' : '.webm');
    formData.append('file', blob, `speech${ext}`);
    formData.append('mode', this.activeMode);

    try {
      const res = await fetch('/api/dictate/browser', {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      if (data.status === 'success' && data.result) {
        this.handleNewTranscription(data.result);
      } else {
        // If server detected silence, check if Web Speech captured text
        if (this.liveStreamedText.trim()) {
          this.handleNewTranscription({
            raw_text: this.liveStreamedText,
            processed_text: this.liveStreamedText,
            mode: this.activeMode,
            word_count: this.liveStreamedText.split(/\s+/).length,
            duration_ms: 1200
          });
        } else {
          this.updateHudStatus('No Speech Detected', 'Speak clearly into mic');
          const heroText = document.getElementById('heroStatusText');
          if (heroText) heroText.textContent = 'No speech detected. Check mic selection or speak louder.';
          if (this.micAlert) this.micAlert.style.display = 'flex';
        }
      }
    } catch (err) {
      console.error('[OpenWrite] Upload error:', err);
      if (this.liveStreamedText.trim()) {
        this.handleNewTranscription({
          raw_text: this.liveStreamedText,
          processed_text: this.liveStreamedText,
          mode: this.activeMode,
          word_count: this.liveStreamedText.split(/\s+/).length,
          duration_ms: 1000
        });
      } else {
        this.updateHudStatus('Error', 'Transcription failed');
      }
    }
  }

  async uploadTalkBlob(blob) {
    const formData = new FormData();
    const ext = blob.type.includes('ogg') ? '.ogg' : (blob.type.includes('mp4') ? '.mp4' : '.webm');
    formData.append('file', blob, `talk${ext}`);
    formData.append('voice', 'af_heart');

    try {
      this.updateHudStatus('Thinking...', 'Generating AI reply & Kokoro voice');
      const res = await fetch('/api/talk', {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      if (data.status === 'success' && data.result) {
        this.handleNewTranscription(data.result);
        if (data.audio_base64) {
          this.playBase64Audio(data.audio_base64);
        }
      } else {
        this.updateHudStatus('No Speech Detected', 'Speak clearly into mic');
        const heroText = document.getElementById('heroStatusText');
        if (heroText) heroText.textContent = 'No speech detected. Speak clearly into mic.';
        if (this.micAlert) this.micAlert.style.display = 'flex';
      }
    } catch (err) {
      console.error('[OpenWrite] Live Talk failed:', err);
      this.updateHudStatus('Error', 'Live Talk failed');
    }
  }

  // ----------------- Mic Quick Test (3-second Record & Playback) ----------------- //
  async testMicrophone() {
    const btn = document.getElementById('testMicBtn');
    if (this.audioCtx && this.audioCtx.state === 'suspended') {
      await this.audioCtx.resume();
    }
    if (!this.mediaStream) await this.startLiveMicMonitoring();
    if (!this.mediaStream) {
      alert('Microphone not accessible.');
      return;
    }

    try {
      const chunks = [];
      const rec = new MediaRecorder(this.mediaStream);
      rec.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };
      rec.start();

      let sec = 3;
      if (btn) btn.textContent = `🔴 Recording (${sec}s)...`;
      const interval = setInterval(() => {
        sec--;
        if (sec > 0 && btn) {
          btn.textContent = `🔴 Recording (${sec}s)...`;
        } else {
          clearInterval(interval);
        }
      }, 1000);

      await new Promise(r => setTimeout(r, 3100));
      rec.stop();
      await new Promise(r => { rec.onstop = r; });

      if (btn) btn.textContent = '🔊 Playing Back...';
      const testBlob = new Blob(chunks, { type: rec.mimeType || 'audio/webm' });
      const testUrl = URL.createObjectURL(testBlob);
      const audio = new Audio(testUrl);
      audio.onended = () => {
        URL.revokeObjectURL(testUrl);
        if (btn) btn.textContent = '🔊 Test Mic (3s)';
      };
      audio.play().catch(e => {
        console.warn('Playback error:', e);
        if (btn) btn.textContent = '🔊 Test Mic (3s)';
      });
    } catch (e) {
      console.error('Test mic error:', e);
      if (btn) btn.textContent = '🔊 Test Mic (3s)';
    }
  }

  // ----------------- Audio & TTS Playback ----------------- //
  async speakText(text) {
    if (!text) return;
    try {
      const res = await fetch('/api/tts/speak', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, voice: 'af_heart', speed: 1.05 })
      });
      if (!res.ok) return;
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      audio.play().catch(e => console.warn('[OpenWrite] Audio playback blocked:', e));
    } catch (err) {
      console.error('[OpenWrite] TTS failed:', err);
    }
  }

  playBase64Audio(b64) {
    try {
      const byteChars = atob(b64);
      const byteNums = new Array(byteChars.length);
      for (let i = 0; i < byteChars.length; i++) {
        byteNums[i] = byteChars.charCodeAt(i);
      }
      const blob = new Blob([new Uint8Array(byteNums)], { type: 'audio/wav' });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      audio.play().catch(e => console.warn('[OpenWrite] Audio play error:', e));
    } catch (err) {
      console.error('[OpenWrite] Playback error:', err);
    }
  }

  // ----------------- Waveform Animation ----------------- //
  startWaveformAnimation() {
    const render = () => {
      this.renderHeroWaveform();
      requestAnimationFrame(render);
    };
    requestAnimationFrame(render);
  }

  renderHeroWaveform() {
    if (!this.heroCtx || !this.heroCanvas) return;
    const ctx = this.heroCtx;
    const w = this.heroCanvas.width;
    const h = this.heroCanvas.height;

    ctx.clearRect(0, 0, w, h);

    const bars = 50;
    const barWidth = 6;
    const gap = 6;
    const startX = (w - (bars * (barWidth + gap))) / 2;

    for (let i = 0; i < bars; i++) {
      let val = this.rmsHistory[Math.floor(i * (this.rmsHistory.length / bars))] || 0;
      let barH = Math.min(h - 10, Math.max(6, val * 650));
      if (!this.isRecording) {
        barH = 6 + Math.sin(Date.now() / 400 + i * 0.2) * 3;
      }

      const x = startX + i * (barWidth + gap);
      const y = (h - barH) / 2;

      const grad = ctx.createLinearGradient(0, y, 0, y + barH);
      if (this.isRecording) {
        grad.addColorStop(0, '#ef4444');
        grad.addColorStop(1, '#f97316');
      } else {
        grad.addColorStop(0, '#6366f1');
        grad.addColorStop(1, '#a855f7');
      }

      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.roundRect(x, y, barWidth, barH, 3);
      ctx.fill();
    }
  }

  // ----------------- State & UI Updates ----------------- //
  setRecordingState(isRec, data = {}) {
    this.isRecording = isRec;
    const hudPill = document.getElementById('hudPill');
    const mainBtn = document.getElementById('mainRecordBtn');
    const hudPtt = document.getElementById('hudPttBtn');
    const heroText = document.getElementById('heroStatusText');

    if (isRec) {
      hudPill?.classList.add('recording');
      mainBtn?.classList.add('recording');
      hudPtt?.classList.add('recording');
      
      const app = data.app ? ` • ${data.app}` : '';
      this.updateHudStatus('Listening Live...', `Recording voice${app}`);
      if (heroText) heroText.textContent = `Listening... Speak naturally (Tap to finish)`;
      if (mainBtn) mainBtn.querySelector('span').textContent = 'Stop Dictating';
    } else {
      hudPill?.classList.remove('recording');
      mainBtn?.classList.remove('recording');
      hudPtt?.classList.remove('recording');
      
      this.updateHudStatus('OpenWrite Ready', 'F8 or Ctrl+Alt+Space to write anywhere');
      if (heroText) heroText.textContent = 'Press F8, Ctrl+Alt+Space, or Tap Mic';
      if (mainBtn) mainBtn.querySelector('span').textContent = 'Start Dictating';
    }
  }

  updateHudStatus(title, sub) {
    const tEl = document.getElementById('hudStatus');
    const sEl = document.getElementById('hudAppDetail');
    if (tEl) tEl.textContent = title;
    if (sEl) sEl.textContent = sub;
  }

  handleNewTranscription(data) {
    const text = data.processed_text || data.raw_text;
    if (!text) return;

    const textEl = document.getElementById('lastOutputText');
    const modeBadge = document.getElementById('lastOutputMode');
    const statsEl = document.getElementById('lastOutputStats');

    if (textEl) textEl.textContent = text;
    if (modeBadge) modeBadge.textContent = data.mode.toUpperCase();
    if (statsEl) statsEl.textContent = `${data.word_count} words • ${data.duration_ms}ms`;

    // Ensure text is immediately copied to Windows clipboard so it's always ready to paste anywhere
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(() => {});
    }

    // Append to live Dictation Pad (if not already appended via live speech)
    if (this.dictationPad) {
      const prev = this.dictationPad.value.trim();
      if (!prev.endsWith(text.trim())) {
        this.dictationPad.value = prev ? `${prev}\n\n${text}` : text;
        this.updatePadStats();
        localStorage.setItem('openwrite_pad_content', this.dictationPad.value);
        this.dictationPad.scrollTop = this.dictationPad.scrollHeight;
      }
    }

    this.updateHudStatus('Transcribed & Injected', `${data.word_count} words in ${data.duration_ms}ms`);
    this.fetchHistory();
  }

  // ----------------- API Calls ----------------- //
  async fetchStatus() {
    try {
      const res = await fetch('/api/status');
      const data = await res.json();
      this.activeMode = data.active_mode || 'enhancement';
      this.updateModeUI(this.activeMode);

      const appBadge = document.getElementById('activeAppBadge');
      if (appBadge && data.active_app) {
        appBadge.textContent = `Focused App: ${data.active_app}`;
      }

      const shortcutSelect = document.getElementById('settingShortcutMode');
      if (shortcutSelect) shortcutSelect.value = data.shortcut_mode;

      const hotkeyInput = document.getElementById('settingHotkey');
      if (hotkeyInput) hotkeyInput.value = data.hotkey;

      const modelSelect = document.getElementById('settingModelName');
      if (modelSelect) modelSelect.value = data.model_name;

      const autoPaste = document.getElementById('settingAutoPaste');
      if (autoPaste) autoPaste.checked = data.auto_paste;

      const autoPasteCheck = document.getElementById('autoPasteCheckbox');
      if (autoPasteCheck) autoPasteCheck.checked = data.auto_paste;

      const appAware = document.getElementById('settingAppAware');
      if (appAware) appAware.checked = data.app_aware;

    } catch (e) {
      console.error('Failed to fetch status:', e);
    }
  }

  async fetchHistory(query = '') {
    try {
      const url = query ? `/api/history?query=${encodeURIComponent(query)}` : '/api/history';
      const res = await fetch(url);
      const list = await res.json();
      this.renderHistory(list);
    } catch (e) {
      console.error('Failed to fetch history:', e);
    }
  }

  renderHistory(items) {
    const container = document.getElementById('historyList');
    if (!container) return;

    if (!items || items.length === 0) {
      container.innerHTML = `<div style="text-align:center; padding: 40px; color: var(--text-muted)">No voice notes yet. Speak into your mic to start dictating!</div>`;
      return;
    }

    container.innerHTML = items.map(item => `
      <div class="history-card" data-id="${item.id}">
        <div class="history-top">
          <span class="badge">${(item.mode || 'enhancement').toUpperCase()}</span>
          <span class="time">${new Date(item.created_at * 1000).toLocaleTimeString()}</span>
        </div>
        <div class="history-text">${item.processed_text || item.raw_text}</div>
        <div class="history-footer">
          <span>${item.app_name ? 'App: ' + item.app_name + ' • ' : ''}${item.word_count} words • ${item.duration_ms}ms</span>
          <button class="action-btn" onclick="navigator.clipboard.writeText(\`${(item.processed_text || item.raw_text).replace(/`/g, '\\`')}\`)">Copy</button>
        </div>
      </div>
    `).join('');
  }

  async fetchVocabulary() {
    try {
      const res = await fetch('/api/vocabulary');
      const terms = await res.json();
      this.renderVocabulary(terms);
    } catch (e) {
      console.error('Failed to fetch vocabulary:', e);
    }
  }

  renderVocabulary(terms) {
    const list = document.getElementById('vocabList');
    if (!list) return;

    if (!terms || terms.length === 0) {
      list.innerHTML = `<div style="color: var(--text-muted); font-size: 13px;">No custom jargon added yet. Add domain terms (APIs, names, code words) above.</div>`;
      return;
    }

    list.innerHTML = terms.map(t => `
      <div class="vocab-tag">
        <span>${t.term}</span>
        <button class="remove-btn" onclick="window.app.deleteVocab('${t.term}')">×</button>
      </div>
    `).join('');
  }

  async addVocab(term) {
    if (!term.trim()) return;
    try {
      await fetch('/api/vocabulary', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({term: term.trim()})
      });
      const inp = document.getElementById('vocabInput');
      if (inp) inp.value = '';
      this.fetchVocabulary();
    } catch (e) {
      console.error('Failed to add vocab:', e);
    }
  }

  async deleteVocab(term) {
    try {
      await fetch(`/api/vocabulary/${encodeURIComponent(term)}`, {method: 'DELETE'});
      this.fetchVocabulary();
    } catch (e) {
      console.error('Failed to delete vocab:', e);
    }
  }

  updateModeUI(mode) {
    document.querySelectorAll('.mode-chip').forEach(btn => {
      if (btn.dataset.mode === mode) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });
    const lastMode = document.getElementById('lastOutputMode');
    if (lastMode) lastMode.textContent = mode.toUpperCase();
  }

  async setMode(mode) {
    this.updateModeUI(mode);
    try {
      await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({active_mode: mode})
      });
    } catch (e) {
      console.error('Failed to set mode:', e);
    }
  }

  // ----------------- Event Listeners ----------------- //
  bindEvents() {
    window.app = this;

    document.querySelectorAll('.nav-item').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
        
        btn.classList.add('active');
        const tabId = `tab-${btn.dataset.tab}`;
        document.getElementById(tabId)?.classList.add('active');
      });
    });

    document.querySelectorAll('.mode-chip').forEach(btn => {
      btn.addEventListener('click', () => {
        this.setMode(btn.dataset.mode);
      });
    });

    const toggleRecord = async () => {
      if (this.audioCtx && this.audioCtx.state === 'suspended') {
        await this.audioCtx.resume().catch(() => {});
      }
      if (this.isRecording) {
        this.stopBrowserRecording();
      } else {
        this.startBrowserRecording();
      }
    };

    document.getElementById('mainRecordBtn')?.addEventListener('click', toggleRecord);
    document.getElementById('hudPttBtn')?.addEventListener('click', toggleRecord);

    document.getElementById('liveTalkToggleBtn')?.addEventListener('click', (e) => {
      this.liveTalkEnabled = !this.liveTalkEnabled;
      const btn = e.currentTarget;
      btn.querySelector('span').textContent = this.liveTalkEnabled
        ? '🎙️ Live Talk (AI + Kokoro): ON'
        : '🎙️ Live Talk (AI + Kokoro): OFF';
      btn.style.background = this.liveTalkEnabled ? 'rgba(99, 102, 241, 0.25)' : '';
      btn.style.borderColor = this.liveTalkEnabled ? '#6366f1' : '';
    });

    document.getElementById('testMicBtn')?.addEventListener('click', () => {
      this.testMicrophone();
    });

    // Switch mic alert button
    document.getElementById('switchMicBtn')?.addEventListener('click', async () => {
      const select = document.getElementById('micDeviceSelect');
      const arrayDev = this.availableDevices.find(d => {
        const l = (d.label || '').toLowerCase();
        return l.includes('array') || l.includes('intel') || l.includes('realtek');
      });
      if (arrayDev && select) {
        this.selectedDeviceId = arrayDev.deviceId;
        select.value = this.selectedDeviceId;
        localStorage.setItem('openwrite_mic_id', this.selectedDeviceId);
        if (this.micAlert) this.micAlert.style.display = 'none';
        await this.startLiveMicMonitoring();
        alert(`Switched to: ${arrayDev.label}! Speak to test the volume meter.`);
      } else {
        alert('Could not find laptop microphone array. Check Windows audio settings.');
      }
    });

    // Pad buttons
    document.getElementById('copyPadBtn')?.addEventListener('click', () => {
      const text = this.dictationPad?.value;
      if (text) {
        navigator.clipboard.writeText(text);
        const btn = document.getElementById('copyPadBtn');
        const original = btn.innerHTML;
        btn.innerHTML = '<span>✅ Copied!</span>';
        setTimeout(() => { btn.innerHTML = original; }, 1500);
      }
    });

    document.getElementById('clearPadBtn')?.addEventListener('click', () => {
      if (this.dictationPad) {
        this.dictationPad.value = '';
        this.updatePadStats();
        localStorage.removeItem('openwrite_pad_content');
      }
    });

    document.getElementById('speakPadBtn')?.addEventListener('click', () => {
      const text = this.dictationPad?.value?.trim();
      if (text) this.speakText(text);
    });

    document.getElementById('downloadPadBtn')?.addEventListener('click', () => {
      const text = this.dictationPad?.value;
      if (!text) return;
      const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `openwrite_notes_${new Date().toISOString().slice(0, 10)}.md`;
      a.click();
      URL.revokeObjectURL(url);
    });

    // Last output buttons
    document.getElementById('speakLastBtn')?.addEventListener('click', () => {
      const text = document.getElementById('lastOutputText')?.textContent?.trim();
      if (text) this.speakText(text);
    });

    document.getElementById('copyLastBtn')?.addEventListener('click', () => {
      const text = document.getElementById('lastOutputText')?.textContent;
      if (text) navigator.clipboard.writeText(text);
    });

    document.getElementById('pasteLastBtn')?.addEventListener('click', async () => {
      const text = document.getElementById('lastOutputText')?.textContent;
      if (text) {
        await fetch('/api/settings', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({last_paste: text})
        });
      }
    });

    let searchTimeout;
    document.getElementById('historySearchInput')?.addEventListener('input', (e) => {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(() => {
        this.fetchHistory(e.target.value);
      }, 250);
    });

    document.getElementById('addVocabBtn')?.addEventListener('click', () => {
      const val = document.getElementById('vocabInput')?.value;
      if (val) this.addVocab(val);
    });
    document.getElementById('vocabInput')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const val = e.target.value;
        if (val) this.addVocab(val);
      }
    });

    const fileInput = document.getElementById('audioFileInput');
    fileInput?.addEventListener('change', (e) => {
      if (e.target.files.length) {
        this.uploadAudioFile(e.target.files[0]);
      }
    });

    // Settings save button
    document.getElementById('saveSettingsBtn')?.addEventListener('click', async () => {
      const payload = {
        model_name: document.getElementById('settingModelName')?.value,
        shortcut_mode: document.getElementById('settingShortcutMode')?.value,
        hotkey: document.getElementById('settingHotkey')?.value,
        auto_paste: document.getElementById('settingAutoPaste')?.checked,
        app_aware: document.getElementById('settingAppAware')?.checked,
      };
      try {
        await fetch('/api/settings', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        alert('Settings saved successfully!');
      } catch (err) {
        alert('Failed to save settings: ' + err.message);
      }
    });
  }

  async uploadAudioFile(file) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('mode', this.activeMode);

    const resultsDiv = document.getElementById('meetingResults');
    const textDiv = document.getElementById('meetingText');
    if (resultsDiv) resultsDiv.style.display = 'block';
    if (textDiv) textDiv.textContent = 'Transcribing meeting audio offline...';

    try {
      const res = await fetch('/api/upload', {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      if (data.status === 'success') {
        const text = data.result.processed_text || data.result.raw_text;
        if (textDiv) textDiv.textContent = text;
        if (this.dictationPad) {
          const prev = this.dictationPad.value.trim();
          this.dictationPad.value = prev ? `${prev}\n\n[Imported Audio]:\n${text}` : `[Imported Audio]:\n${text}`;
          this.updatePadStats();
        }
      } else {
        if (textDiv) textDiv.textContent = 'Failed to transcribe audio file.';
      }
    } catch (e) {
      if (textDiv) textDiv.textContent = 'Error: ' + e.message;
    }
  }
}

document.addEventListener('DOMContentLoaded', () => {
  new OpenWriteApp();
});
