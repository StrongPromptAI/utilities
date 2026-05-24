# Shared Services Voice Demo

Small local harness for exercising shared-svcs STT and TTS transport without product code.

## Run

Start the shared services in separate terminals when you want live STT/TTS:

```bash
cd ~/repos/utilities/services/stt && uv run uvicorn app:app --port 8101
cd ~/repos/utilities/services/tts && uv run uvicorn app:app --port 8102
```

Start the harness on the assigned test frontend port:

```bash
cd ~/repos/utilities && uv run uvicorn voice_demo.server:app --host 127.0.0.1 --port 9101
```

Open http://127.0.0.1:9101.

## Scope

- Mirrors the iTheraputix tri-state input control: Type, Listen, Talk.
- `Listen` streams mic audio to shared-svcs STT.
- `Talk` enables TTS playback using shared-svcs TTS.
- Playback is half-duplex: starting TTS stops and closes mic/STT; mic controls stay disabled until audio ends.
- No LLM or product routing is included.
