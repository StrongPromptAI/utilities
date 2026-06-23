import { type ReactNode, type RefObject, useEffect, useState } from 'react'
import { MicButton, shouldShowVoiceHint } from './MicButton'

// Tailored from the THJ ChatInputBar — same visual (mic + auto-grow textarea + send,
// listening indicator, live interim preview, voice-error toast), minus the patient-app
// props (reviewer queue, check-in steps, panels) the coach has no use for.

function getInputMaxHeight(): number {
  if (typeof window === 'undefined') return 240
  return Math.max(160, Math.min(320, Math.floor(window.innerHeight * 0.42)))
}

function resizeTextarea(el: HTMLTextAreaElement): void {
  const maxHeight = getInputMaxHeight()
  el.style.height = 'auto'
  if (el.scrollHeight === 0) return
  el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`
  el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden'
}

interface Props {
  inputRef: RefObject<HTMLTextAreaElement>
  value: string
  onChange: (v: string) => void
  onSend: () => void
  isLoading: boolean
  // Voice
  isListening: boolean
  isConnecting: boolean
  interimTranscript: string
  voiceError: string | null
  onMicClick: () => void
  onStopListening: () => void
}

export function ChatInputBar({
  inputRef, value, onChange, onSend, isLoading,
  isListening, isConnecting, interimTranscript, voiceError, onMicClick, onStopListening,
}: Props): ReactNode {
  const [heldInterim, setHeldInterim] = useState('')

  useEffect(() => {
    if (!isListening) { setHeldInterim(''); return }
    const next = interimTranscript.trim()
    if (next) setHeldInterim(next)
  }, [interimTranscript, isListening])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'Enter' || e.shiftKey) return
    e.preventDefault()
    if (isListening) onStopListening()
    else onSend()
  }

  const handleSendClick = () => {
    if (isListening) onStopListening()
    else onSend()
  }

  const placeholder = isListening
    ? 'Listening... press Enter to send'
    : shouldShowVoiceHint()
      ? '← tap mic to dictate'
      : 'Ask your sales coach anything…'

  // While listening, show the live interim appended to the committed input (unless it's
  // already the tail) — the THJ double-render guard.
  const visibleValue =
    isListening && heldInterim && !value.endsWith(heldInterim)
      ? `${value} ${heldInterim}`.trim()
      : value

  useEffect(() => {
    const el = inputRef.current
    if (el) resizeTextarea(el)
  }, [inputRef, visibleValue])

  const sendDisabled = (!visibleValue.trim() && !isListening) || isLoading

  return (
    <>
      {voiceError && (
        <div className="fixed top-6 left-1/2 -translate-x-1/2 bg-red-500/90 text-white px-4 py-2 rounded-lg text-sm animate-fade-in z-10">
          {voiceError}
        </div>
      )}

      {isListening && (
        <div className="fixed bottom-32 left-1/2 -translate-x-1/2 bg-surface/95 backdrop-blur text-text-secondary px-4 py-2 rounded-full text-sm flex items-center gap-2 animate-fade-in border border-accent-light z-10">
          <span className="w-2 h-2 bg-primary rounded-full animate-pulse" />
          Listening... speak now
        </div>
      )}

      <div
        className="fixed bottom-0 left-0 right-0 pt-2 sm:pt-6"
        style={{
          background: `linear-gradient(to top, rgb(var(--page)) 60%, transparent)`,
          paddingBottom: 'calc(env(safe-area-inset-bottom) + 12px)',
        }}
      >
        <div className="max-w-2xl mx-auto px-3 sm:px-4">
          <div className={`relative flex items-end gap-2 bg-surface backdrop-blur rounded-2xl border p-2 shadow-lg transition-colors ${isListening ? 'border-primary/50' : 'border-accent-light'}`}>
            <MicButton active={isListening} onClick={onMicClick} isConnecting={isConnecting} />
            <textarea
              ref={inputRef}
              rows={1}
              value={visibleValue}
              onChange={(e) => { if (!isListening) onChange(e.target.value); resizeTextarea(e.target) }}
              onKeyDown={handleKeyDown}
              placeholder={placeholder}
              enterKeyHint="send"
              autoCapitalize="sentences"
              // text-base (16px) — anything smaller triggers iOS auto-zoom on focus.
              className={`flex-1 bg-transparent px-4 py-3 text-base text-text placeholder-text-muted focus:outline-none resize-none ${isListening && heldInterim ? 'text-text-secondary' : ''}`}
              style={{ maxHeight: `${getInputMaxHeight()}px` }}
            />
            <button
              onClick={handleSendClick}
              disabled={sendDisabled}
              className={`p-3 rounded-xl transition-all duration-200 ${
                !sendDisabled
                  ? 'bg-gradient-to-br from-primary to-primary-600 text-white hover:shadow-lg hover:shadow-primary/30'
                  : 'bg-surface-dim text-text-muted'
              }`}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19V5m-7 7l7-7 7 7" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
