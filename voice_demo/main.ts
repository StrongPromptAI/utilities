const STT_WS_URL = "ws://127.0.0.1:8101/transcribe";

const transcriptEl = document.querySelector("#transcript");
const chatForm = document.querySelector("#chatForm");
const chatInput = document.querySelector("#chatInput");
const ttsText = document.querySelector("#ttsText");
const voiceModeButton = document.querySelector("#voiceModeButton");
const voiceIcon = document.querySelector("#voiceIcon");
const voiceLabel = document.querySelector("#voiceLabel");
const sendButton = document.querySelector("#sendButton");
const speakButton = document.querySelector("#speakButton");
const stopAudioButton = document.querySelector("#stopAudioButton");
const statusLine = document.querySelector("#statusLine");
const sttHealth = document.querySelector("#sttHealth");
const ttsHealth = document.querySelector("#ttsHealth");

const voiceModes = ["off", "stt", "talk"];
let voiceMode = "off";
let ws = null;
let micStream = null;
let audioContext = null;
let audioSource = null;
let audioProcessor = null;
let keepaliveTimer = null;
let activeAudio = null;
let isListening = false;
let isConnecting = false;
let isTtsPlaying = false;
let interimText = "";
let finalText = "";

const icons = {
  off: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="8" y1="12" x2="16" y2="12"/><line x1="8" y1="16" x2="13" y2="16"/></svg>`,
  stt: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>`,
  talk: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>`,
};

function requireElement(value, name) {
  if (!value) throw new Error(`Missing ${name}`);
  return value;
}

const ui = {
  transcript: requireElement(transcriptEl, "#transcript"),
  chatForm: requireElement(chatForm, "#chatForm"),
  chatInput: requireElement(chatInput, "#chatInput"),
  ttsText: requireElement(ttsText, "#ttsText"),
  voiceModeButton: requireElement(voiceModeButton, "#voiceModeButton"),
  voiceIcon: requireElement(voiceIcon, "#voiceIcon"),
  voiceLabel: requireElement(voiceLabel, "#voiceLabel"),
  sendButton: requireElement(sendButton, "#sendButton"),
  speakButton: requireElement(speakButton, "#speakButton"),
  stopAudioButton: requireElement(stopAudioButton, "#stopAudioButton"),
  statusLine: requireElement(statusLine, "#statusLine"),
  sttHealth: requireElement(sttHealth, "#sttHealth"),
  ttsHealth: requireElement(ttsHealth, "#ttsHealth"),
};

function setStatus(message) {
  ui.statusLine.textContent = message;
}

function appendBubble(role, label, text) {
  const article = document.createElement("article");
  article.className = `bubble ${role}`;
  const labelEl = document.createElement("span");
  labelEl.className = "bubble-label";
  labelEl.textContent = label;
  const p = document.createElement("p");
  p.textContent = text;
  article.append(labelEl, p);
  ui.transcript.append(article);
  ui.transcript.scrollTop = ui.transcript.scrollHeight;
}

function updateModeButton() {
  const meta = {
    off: { label: "Type", aria: "Text only - tap to enable voice input" },
    stt: { label: isListening ? "Listening" : "Listen", aria: "STT active - tap for Talk mode" },
    talk: { label: "Talk", aria: "Half-duplex STT and TTS mode - tap to return to Type" },
  }[voiceMode];

  ui.voiceIcon.innerHTML = icons[voiceMode];
  ui.voiceLabel.textContent = meta.label;
  ui.voiceModeButton.setAttribute("aria-label", meta.aria);
  ui.voiceModeButton.title = meta.aria;
  ui.voiceModeButton.classList.toggle("active", voiceMode !== "off");
  ui.voiceModeButton.classList.toggle("blocked", isTtsPlaying);
  ui.voiceModeButton.disabled = isConnecting || isTtsPlaying;
  ui.chatInput.disabled = isTtsPlaying;
  ui.sendButton.disabled = isTtsPlaying;
  ui.speakButton.disabled = isTtsPlaying;
  ui.stopAudioButton.disabled = !isTtsPlaying;
}

function nextVoiceMode() {
  const index = voiceModes.indexOf(voiceMode);
  return voiceModes[(index + 1) % voiceModes.length];
}

function clearKeepalive() {
  if (keepaliveTimer !== null) {
    clearInterval(keepaliveTimer);
    keepaliveTimer = null;
  }
}

function closeSocket() {
  clearKeepalive();
  if (ws) {
    const socket = ws;
    ws = null;
    socket.onclose = null;
    socket.onerror = null;
    socket.onmessage = null;
    try {
      socket.close();
    } catch {
      // no-op
    }
  }
}

function stopMicTracks() {
  if (audioProcessor) {
    try {
      audioProcessor.disconnect();
    } catch {
      // no-op
    }
    audioProcessor = null;
  }
  if (audioSource) {
    try {
      audioSource.disconnect();
    } catch {
      // no-op
    }
    audioSource = null;
  }
  if (audioContext) {
    void audioContext.close().catch(() => {});
    audioContext = null;
  }
  if (micStream) {
    micStream.getTracks().forEach((track) => track.stop());
    micStream = null;
  }
}

function stopListening(options = {}) {
  const close = options.close === true;
  if (ws?.readyState === WebSocket.OPEN) {
    try {
      ws.send("EOS");
    } catch {
      // no-op
    }
  }
  stopMicTracks();
  isListening = false;
  isConnecting = false;
  if (finalText.trim()) {
    const text = finalText.trim();
    ui.chatInput.value = text;
    appendBubble("system", "STT final", text);
    ui.ttsText.value = `I heard: ${text}`;
  }
  finalText = "";
  interimText = "";
  if (close) closeSocket();
  updateModeButton();
}

function parseSttMessage(data) {
  try {
    const parsed = JSON.parse(data);
    return parsed.text ? parsed : null;
  } catch {
    return null;
  }
}

async function openSttSocket() {
  closeSocket();
  setStatus("Connecting to STT...");
  const socket = new WebSocket(STT_WS_URL);
  ws = socket;

  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("STT connection timed out")), 5000);
    socket.onopen = () => {
      clearTimeout(timeout);
      socket.send("local-dev-token");
      keepaliveTimer = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) socket.send("ping");
      }, 25000);
      resolve(undefined);
    };
    socket.onerror = () => {
      clearTimeout(timeout);
      reject(new Error("STT socket error"));
    };
  });

  socket.onmessage = (event) => {
    const parsed = parseSttMessage(String(event.data));
    if (!parsed) return;
    if (parsed.is_final) {
      finalText = `${finalText} ${parsed.text}`.trim();
      interimText = "";
      ui.chatInput.value = finalText;
    } else {
      interimText = parsed.text;
      ui.chatInput.value = `${finalText} ${interimText}`.trim();
    }
  };

  socket.onclose = (event) => {
    clearKeepalive();
    if (isListening) {
      stopMicTracks();
      isListening = false;
      setStatus(`STT closed (${event.code}).`);
      updateModeButton();
    }
  };
}

function createPcmProcessor(stream, socket) {
  const context = new AudioContext({ sampleRate: 16000 });
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(1024, 1, 1);

  processor.onaudioprocess = (event) => {
    if (socket.readyState !== WebSocket.OPEN || isTtsPlaying) return;
    const float32 = event.inputBuffer.getChannelData(0);
    const int16 = new Int16Array(float32.length);
    for (let index = 0; index < float32.length; index += 1) {
      const sample = float32[index] || 0;
      int16[index] = Math.max(-32768, Math.min(32767, Math.round(sample * 32767)));
    }
    socket.send(int16.buffer);
  };

  source.connect(processor);
  processor.connect(context.destination);
  audioContext = context;
  audioSource = source;
  audioProcessor = processor;
}

async function startListening() {
  if (isTtsPlaying || isListening) return;
  isConnecting = true;
  finalText = "";
  interimText = "";
  updateModeButton();

  try {
    await openSttSocket();
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (!ws || ws.readyState !== WebSocket.OPEN) throw new Error("STT socket not ready");
    micStream = stream;
    createPcmProcessor(stream, ws);
    isListening = true;
    setStatus("Listening. Send, stop, or wait for a final transcript.");
  } catch (error) {
    closeSocket();
    stopMicTracks();
    const message = error instanceof Error ? error.message : String(error);
    setStatus(message);
    appendBubble("system", "STT error", message);
  } finally {
    isConnecting = false;
    updateModeButton();
  }
}

async function handleVoiceModeClick() {
  if (isTtsPlaying) return;
  const target = nextVoiceMode();
  if (target === "off") {
    stopListening({ close: true });
    voiceMode = "off";
    setStatus("Type mode.");
  } else if (target === "stt") {
    voiceMode = "stt";
    await startListening();
  } else {
    voiceMode = "talk";
    if (!isListening) await startListening();
    setStatus("Talk mode. TTS playback will close the mic until audio ends.");
  }
  updateModeButton();
}

function sendCurrentText() {
  const text = ui.chatInput.value.trim();
  if (!text) return;
  if (isListening) stopListening({ close: true });
  appendBubble("user", "You", text);
  ui.ttsText.value = `I heard: ${text}`;
  ui.chatInput.value = "";
  setStatus(voiceMode === "talk" ? "Message captured. Use Play TTS to test half-duplex playback." : "Message captured.");
}

async function playTts() {
  const text = ui.ttsText.value.trim();
  if (!text) return;
  stopListening({ close: true });
  isTtsPlaying = true;
  updateModeButton();
  setStatus("Playing TTS. Mic is closed while audio plays.");
  appendBubble("assistant", "TTS", text);

  try {
    const response = await fetch("/tts/speech", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input: text,
        voice: "af_heart",
        response_format: "wav",
        speed: 1.0,
        language: "en-us",
      }),
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`TTS HTTP ${response.status}: ${detail.slice(0, 180)}`);
    }

    const audioBlob = await response.blob();
    const audioUrl = URL.createObjectURL(audioBlob);
    const audio = new Audio(audioUrl);
    activeAudio = audio;

    await new Promise((resolve, reject) => {
      audio.onended = () => resolve(undefined);
      audio.onerror = () => reject(new Error("Audio playback failed"));
      void audio.play().catch(reject);
    });
    URL.revokeObjectURL(audioUrl);
    setStatus("TTS complete. Mic can be used again.");
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    appendBubble("system", "TTS error", message);
    setStatus(message);
  } finally {
    activeAudio = null;
    isTtsPlaying = false;
    updateModeButton();
  }
}

function stopAudio() {
  if (!activeAudio) return;
  activeAudio.pause();
  activeAudio.currentTime = 0;
  activeAudio.dispatchEvent(new Event("ended"));
}

async function checkHealth(label, url, pill) {
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    pill.textContent = `${label} ready`;
    pill.classList.add("ok");
    pill.classList.remove("error");
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    pill.textContent = `${label} unavailable`;
    pill.title = message;
    pill.classList.add("error");
    pill.classList.remove("ok");
  }
}

ui.voiceModeButton.addEventListener("click", () => {
  void handleVoiceModeClick();
});

ui.chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  sendCurrentText();
});

ui.chatInput.addEventListener("input", () => {
  ui.chatInput.style.height = "auto";
  ui.chatInput.style.height = `${Math.min(ui.chatInput.scrollHeight, 132)}px`;
});

ui.speakButton.addEventListener("click", () => {
  void playTts();
});

ui.stopAudioButton.addEventListener("click", stopAudio);

window.addEventListener("beforeunload", () => {
  stopListening({ close: true });
  if (activeAudio) activeAudio.pause();
});

updateModeButton();
void checkHealth("STT", "/stt/health", ui.sttHealth);
void checkHealth("TTS", "/tts/health", ui.ttsHealth);
