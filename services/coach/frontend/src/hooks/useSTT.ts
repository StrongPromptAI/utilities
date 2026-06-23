/**
 * useSTT — Provider-agnostic speech-to-text hook.
 *
 * Supports two providers behind VITE_STT_PROVIDER:
 *   'deepgram' — wss://api.deepgram.com (default, requires VITE_DEEPGRAM_API_KEY)
 *   'sherpa'   — WebSocket to self-hosted sherpa-onnx (requires VITE_STT_URL)
 *
 * Pre-warms socket on mount, keepalive, auto-reconnect with exponential backoff.
 * Identical callback interface regardless of provider.
 */

import { useState, useRef, useCallback, useEffect } from 'react'

// ---- Types ----------------------------------------------------------------

export type STTProvider = 'deepgram' | 'sherpa'

export interface UseSTTOptions {
  onTranscript?: (text: string) => void
  onFinalTranscript?: (text: string) => void
  silenceTimeout?: number
}

export interface UseSTTReturn {
  isListening: boolean
  isConnecting: boolean
  transcript: string
  interimTranscript: string
  error: string | null
  startListening: () => Promise<void>
  stopListening: () => void
  toggleListening: () => void
  provider: STTProvider
}

// ---- Config ---------------------------------------------------------------

// Coach uses sherpa (shared-svcs STT) only — no Deepgram. Default provider is sherpa
// so no build-time env is required; override with VITE_STT_PROVIDER if ever needed.
const STT_PROVIDER: STTProvider =
  (import.meta.env.VITE_STT_PROVIDER as STTProvider) || 'sherpa'

// Deepgram config (unused in the coach; kept so the hook stays provider-agnostic)
const DG_API_KEY = import.meta.env.VITE_DEEPGRAM_API_KEY as string | undefined
const DG_URL = 'wss://api.deepgram.com/v1/listen?model=nova-3-medical&punctuate=true&interim_results=true&utterance_end_ms=3000&endpointing=true'

// sherpa-onnx config — points at shared-svcs Railway STT directly (cross-project, public
// URL: the coach lives in the kb project, STT in shared-svcs, so no railway.internal hop).
const _rawSttUrl =
  (import.meta.env.VITE_STT_URL as string | undefined) ||
  'wss://shared-svcs-stt.up.railway.app/transcribe'
const SHERPA_URL = _rawSttUrl.startsWith('ws')
  ? _rawSttUrl
  : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}${_rawSttUrl}`

const KEEPALIVE_MS = 25_000  // Cellular NATs drop at 30s on some carriers; 25s sits inside both that and Railway's 60s edge proxy.
const RECONNECT_BASE_MS = 1000
const RECONNECT_MAX_MS = 30_000

// shared-svcs WS close codes
const STT_CLOSE_AUTH_REJECTED = 4401  // bad/expired/wrong-aud token — fetch fresh and reopen
const STT_CLOSE_TOKEN_EXPIRED = 1001  // server-initiated clean close at token expiry — fetch fresh and reopen

/**
 * Mint a 5-min STT token from the coach backend. Same-origin, so the coach_session
 * cookie rides automatically (credentials: 'include') — no Bearer/localStorage. The
 * backend gates it on the same allowlist as /api/chat, then mints an aud="stt" JWT.
 */
async function fetchSttToken(): Promise<string> {
  const resp = await fetch('/api/stt-token', { credentials: 'include' })
  if (!resp.ok) throw new Error(`stt-token: ${resp.status}`)
  const data = await resp.json()
  return data.token as string
}

// ---- Provider message parsers ---------------------------------------------

interface ParsedSTTMessage {
  text: string
  isFinal: boolean
}

function parseDeepgramMessage(data: string): ParsedSTTMessage | null {
  const msg = JSON.parse(data)
  const text = msg.channel?.alternatives?.[0]?.transcript
  if (!text) return null
  return { text, isFinal: msg.is_final }
}

function parseSherpaMessage(data: string): ParsedSTTMessage | null {
  const msg = JSON.parse(data)
  if (!msg.text) return null
  return { text: msg.text, isFinal: msg.is_final }
}

// ---- Audio capture helpers ------------------------------------------------

function createPCMProcessor(
  stream: MediaStream,
  onChunk: (data: ArrayBuffer) => void,
): { context: AudioContext; stop: () => void } {
  const context = new AudioContext({ sampleRate: 16000 })
  const source = context.createMediaStreamSource(stream)
  const processor = context.createScriptProcessor(1024, 1, 1)

  processor.onaudioprocess = (e) => {
    const float32 = e.inputBuffer.getChannelData(0)
    const int16 = new Int16Array(float32.length)
    for (let i = 0; i < float32.length; i++) {
      const sample = float32[i] ?? 0
      int16[i] = Math.max(-32768, Math.min(32767, Math.round(sample * 32767)))
    }
    onChunk(int16.buffer)
  }

  source.connect(processor)
  processor.connect(context.destination)

  return {
    context,
    stop: () => {
      processor.disconnect()
      source.disconnect()
      context.close()
    },
  }
}

// ---- Hook -----------------------------------------------------------------

export function useSTT({
  onTranscript,
  onFinalTranscript,
  silenceTimeout = 3000,
}: UseSTTOptions): UseSTTReturn {
  const [isListening, setIsListening] = useState(false)
  const [isConnecting, setIsConnecting] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [interimTranscript, setInterimTranscript] = useState('')
  const [error, setError] = useState<string | null>(null)

  const socketRef = useRef<WebSocket | null>(null)
  const socketReadyRef = useRef(false)
  const streamRef = useRef<MediaStream | null>(null)
  const audioRef = useRef<{ stop: () => void } | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const keepaliveTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectDelayRef = useRef(RECONNECT_BASE_MS)
  const finalTranscriptRef = useRef('')
  const isActiveRef = useRef(false)
  const isMountedRef = useRef(true)
  const callbacksRef = useRef({ onTranscript, onFinalTranscript })

  useEffect(() => {
    callbacksRef.current = { onTranscript, onFinalTranscript }
  }, [onTranscript, onFinalTranscript])

  const isSherpa = STT_PROVIDER === 'sherpa'
  const parseMessage = isSherpa ? parseSherpaMessage : parseDeepgramMessage

  // ---- Socket lifecycle ---------------------------------------------------

  const clearKeepalive = useCallback(() => {
    if (keepaliveTimerRef.current) {
      clearInterval(keepaliveTimerRef.current)
      keepaliveTimerRef.current = null
    }
  }, [])

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
  }, [])

  const openSocket = useCallback(async () => {
    if (!isMountedRef.current) return
    if (socketRef.current && socketRef.current.readyState <= WebSocket.OPEN) return

    // Provider-specific validation
    if (!isSherpa && !DG_API_KEY) return

    const tag = isSherpa ? 'sherpa' : 'Deepgram'
    console.log(`[STT] Pre-warming ${tag} socket...`)
    socketReadyRef.current = false

    // Sherpa needs a 5-min STT token minted by the backend; sent as the first
    // text frame after onopen. Fetched here, before WS construction, so a 401
    // from /api/stt-token surfaces as a clear error rather than a flaky 4401
    // close after an opaque WS handshake.
    let sttToken: string | null = null
    if (isSherpa) {
      try {
        sttToken = await fetchSttToken()
      } catch (e) {
        console.error('[STT] Failed to mint token:', e)
        if (isActiveRef.current) setError('Could not authenticate STT')
        // Schedule a backoff retry — token may be unavailable transiently
        // (e.g., session token still being persisted on first page load).
        if (isMountedRef.current) {
          const delay = reconnectDelayRef.current
          clearReconnectTimer()
          reconnectTimerRef.current = setTimeout(() => {
            reconnectDelayRef.current = Math.min(delay * 2, RECONNECT_MAX_MS)
            openSocket()
          }, delay)
        }
        return
      }
    }

    const socket = isSherpa
      ? new WebSocket(SHERPA_URL)
      : new WebSocket(DG_URL, ['token', DG_API_KEY!])

    socketRef.current = socket

    socket.onopen = () => {
      if (!isMountedRef.current) { socket.close(); return }

      // Sherpa: first text frame is the JWT. shared-svcs validates within 5s
      // or closes with 4401. Done before marking the socket ready so we don't
      // race audio bytes against an unauthenticated socket.
      if (isSherpa && sttToken) {
        socket.send(sttToken)
      }

      console.log(`[STT] ${tag} socket warm`)
      socketReadyRef.current = true
      reconnectDelayRef.current = RECONNECT_BASE_MS

      clearKeepalive()
      // Both providers need keepalive — Deepgram closes idle at 60s, Railway's
      // edge proxy closes at ~60s, cellular NATs drop at 30s on some carriers.
      keepaliveTimerRef.current = setInterval(() => {
        if (socket.readyState !== WebSocket.OPEN) return
        if (isSherpa) {
          socket.send('ping')  // shared-svcs discards 'ping' text frames silently
        } else {
          socket.send(JSON.stringify({ type: 'KeepAlive' }))
        }
      }, KEEPALIVE_MS)
    }

    socket.onerror = () => {
      if (!isMountedRef.current) return
      console.error(`[STT] ${tag} socket error`)
      socketReadyRef.current = false
      if (isActiveRef.current) {
        setError('Connection error')
        stopRecording()
      }
    }

    socket.onclose = (e: CloseEvent) => {
      if (!isMountedRef.current) return
      // Server-initiated clean closes that should reopen with no backoff:
      //   4401 = auth rejected (fetch fresh token, reconnect)
      //   1001 = token expired (server-initiated clean close at 5min)
      //   1006 immediately AFTER our EOS send = sherpa's "finalize and disconnect"
      //         (server breaks the receive loop without a close frame; browser → 1006)
      const eosClose = (socket as WebSocket & { __pttEosSent?: boolean }).__pttEosSent === true
      const cleanReopen = isSherpa && (
        e.code === STT_CLOSE_AUTH_REJECTED ||
        e.code === STT_CLOSE_TOKEN_EXPIRED ||
        (e.code === 1006 && eosClose)
      )
      console.log(`[STT] ${tag} socket closed code=${e.code}${cleanReopen ? ' (clean reopen)' : ''}`)
      socketReadyRef.current = false
      clearKeepalive()
      socketRef.current = null

      if (isActiveRef.current) stopRecording()

      // Auto-reconnect: clean reopens skip the backoff wait; other closes back off.
      if (isMountedRef.current) {
        const delay = cleanReopen ? 0 : reconnectDelayRef.current
        if (!cleanReopen) console.log(`[STT] Reconnecting in ${delay}ms...`)
        clearReconnectTimer()
        reconnectTimerRef.current = setTimeout(() => {
          if (!cleanReopen) reconnectDelayRef.current = Math.min(delay * 2, RECONNECT_MAX_MS)
          openSocket()
        }, delay)
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clearKeepalive, clearReconnectTimer, isSherpa])

  // ---- Recording lifecycle ------------------------------------------------

  const stopRecording = useCallback(() => {
    if (!isActiveRef.current) return
    console.log('[STT] Stopping recording...')
    isActiveRef.current = false

    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current)
      silenceTimerRef.current = null
    }

    // sherpa: send EOS to finalize the utterance. Server's contract is to
    // append silence padding, emit the final transcript, then break the
    // receive loop — which closes the WS with no close frame (browser sees
    // code 1006). Tag the socket so onclose treats that as expected and
    // reopens immediately instead of running the exponential backoff.
    if (isSherpa && socketRef.current?.readyState === WebSocket.OPEN) {
      ;(socketRef.current as WebSocket & { __pttEosSent?: boolean }).__pttEosSent = true
      socketRef.current.send('EOS')
    }

    // Stop MediaRecorder (Deepgram path)
    if (mediaRecorderRef.current) {
      try {
        if (mediaRecorderRef.current.state !== 'inactive') mediaRecorderRef.current.stop()
      } catch { /* ignore */ }
      mediaRecorderRef.current = null
    }

    // Stop PCM processor (sherpa path)
    if (audioRef.current) {
      audioRef.current.stop()
      audioRef.current = null
    }

    // Release mic
    if (streamRef.current) {
      try { streamRef.current.getTracks().forEach(t => t.stop()) } catch { /* ignore */ }
      streamRef.current = null
    }

    const finalText = finalTranscriptRef.current
    setIsListening(false)
    setIsConnecting(false)

    if (finalText) {
      callbacksRef.current.onFinalTranscript?.(finalText)
      finalTranscriptRef.current = ''
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSherpa])

  const startListening = useCallback(async () => {
    if (isActiveRef.current) return

    if (!isSherpa && !DG_API_KEY) {
      setError('VITE_DEEPGRAM_API_KEY not set')
      return
    }

    console.log('[STT] Starting...')
    isActiveRef.current = true
    setError(null)
    setTranscript('')
    setInterimTranscript('')
    finalTranscriptRef.current = ''

    // Wait for socket if not ready
    if (!socketReadyRef.current) {
      setIsConnecting(true)
      openSocket()
      const ready = await new Promise<boolean>(resolve => {
        let elapsed = 0
        const check = setInterval(() => {
          elapsed += 50
          if (socketReadyRef.current) { clearInterval(check); resolve(true) }
          if (elapsed >= 3000 || !isActiveRef.current) { clearInterval(check); resolve(false) }
        }, 50)
      })
      if (!ready || !isActiveRef.current) {
        setError('Could not connect')
        isActiveRef.current = false
        setIsConnecting(false)
        return
      }
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (!isActiveRef.current) { stream.getTracks().forEach(t => t.stop()); return }

      streamRef.current = stream
      const socket = socketRef.current
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        setError('Socket not ready')
        stream.getTracks().forEach(t => t.stop())
        streamRef.current = null
        isActiveRef.current = false
        setIsConnecting(false)
        return
      }

      // Wire message handler
      socket.onmessage = (event: MessageEvent) => {
        if (!isActiveRef.current) return
        const parsed = parseMessage(event.data)
        if (!parsed) return

        resetSilenceTimer()
        if (parsed.isFinal) {
          finalTranscriptRef.current = (finalTranscriptRef.current + ' ' + parsed.text).trim()
          setTranscript(finalTranscriptRef.current)
          setInterimTranscript('')
          callbacksRef.current.onTranscript?.(finalTranscriptRef.current)
        } else {
          setInterimTranscript(parsed.text)
        }
      }

      // Start audio capture — provider-specific
      if (isSherpa) {
        // sherpa: send raw PCM int16 at 16kHz
        const pcm = createPCMProcessor(stream, (chunk) => {
          if (socket.readyState === WebSocket.OPEN) socket.send(chunk)
        })
        audioRef.current = pcm
      } else {
        // Deepgram: send webm/opus via MediaRecorder
        const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' })
        mediaRecorderRef.current = recorder
        recorder.ondataavailable = (e: BlobEvent) => {
          if (e.data.size > 0 && socket.readyState === WebSocket.OPEN) socket.send(e.data)
        }
        recorder.start(250)
      }

      setIsConnecting(false)
      setIsListening(true)
      console.log(`[STT] Recording on ${isSherpa ? 'sherpa' : 'Deepgram'} socket`)
      resetSilenceTimer()

    } catch (err: any) {
      console.error('[STT] Error:', err)
      setError(err.message || 'Microphone access denied')
      isActiveRef.current = false
      setIsConnecting(false)
    }

    function resetSilenceTimer() {
      if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current)
      silenceTimerRef.current = setTimeout(() => {
        console.log('[STT] Silence timeout')
        stopRecording()
      }, silenceTimeout)
    }
  }, [silenceTimeout, stopRecording, openSocket, isSherpa, parseMessage])

  const stopListening = stopRecording

  const toggleListening = useCallback(() => {
    if (isActiveRef.current) stopRecording()
    else startListening()
  }, [startListening, stopRecording])

  // Pre-warm on mount, cleanup on unmount
  useEffect(() => {
    isMountedRef.current = true
    openSocket()
    return () => {
      isMountedRef.current = false
      isActiveRef.current = false
      if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current)
      clearKeepalive()
      clearReconnectTimer()
      if (mediaRecorderRef.current) {
        try { if (mediaRecorderRef.current.state !== 'inactive') mediaRecorderRef.current.stop() } catch { /* ignore */ }
      }
      if (audioRef.current) audioRef.current.stop()
      if (socketRef.current) { try { socketRef.current.close() } catch { /* ignore */ } }
      if (streamRef.current) { try { streamRef.current.getTracks().forEach(t => t.stop()) } catch { /* ignore */ } }
    }
  }, [openSocket, clearKeepalive, clearReconnectTimer])

  return {
    isListening,
    isConnecting,
    transcript,
    interimTranscript,
    error,
    startListening,
    stopListening,
    toggleListening,
    provider: STT_PROVIDER,
  }
}
