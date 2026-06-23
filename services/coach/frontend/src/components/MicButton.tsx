import { ReactNode, useState, useEffect, useCallback } from 'react'

/**
 * Voice input state for the chat input button.
 *  'off' — Text typing. Mic is idle.
 *  'stt' — STT active: mic listening, transcript fills the input.
 */
export type VoiceMode = 'off' | 'stt'

interface MicButtonProps {
  active: boolean
  onClick: () => void
  isConnecting?: boolean
  size?: 'sm' | 'md'
}

const HINT_STORAGE_KEY = 'voice_hint'
const MAX_HINTS = 3
const RESET_AFTER_MS = 14 * 24 * 60 * 60 * 1000 // 14 days

interface HintState {
  shown: number
  lastSeen: number
  used: boolean
}

function loadHintState(): HintState {
  try {
    const raw = localStorage.getItem(HINT_STORAGE_KEY)
    if (raw) return JSON.parse(raw)
  } catch { /* ignore */ }
  return { shown: 0, lastSeen: Date.now(), used: false }
}

function saveHintState(state: HintState) {
  try {
    localStorage.setItem(HINT_STORAGE_KEY, JSON.stringify(state))
  } catch { /* ignore */ }
}

/** Standing microphone — STT mode. */
function MicIcon({ className = '' }: { className?: string }): ReactNode {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" y1="19" x2="12" y2="23" />
      <line x1="8" y1="23" x2="16" y2="23" />
    </svg>
  )
}

/**
 * Binary mic button. Default typing state is unlit; pressing the mic lights it
 * and lets the caller start STT.
 *
 * Discovery hint: a ring-blink animation pulses up to 3 times when voice
 * has never been used. Resets after 14 days of absence.
 */
export function MicButton({ active, onClick, isConnecting, size = 'md' }: MicButtonProps): ReactNode {
  const [showRing, setShowRing] = useState(false)

  useEffect(() => {
    const state = loadHintState()
    const now = Date.now()

    // Reset if user has been away 14+ days
    if (now - state.lastSeen >= RESET_AFTER_MS) {
      const reset: HintState = { shown: 0, lastSeen: now, used: false }
      saveHintState(reset)
      setShowRing(true)
      return
    }

    // Update last seen
    state.lastSeen = now
    saveHintState(state)

    // Show ring if under the hint cap and hasn't used voice yet
    if (!state.used && state.shown < MAX_HINTS) {
      setShowRing(true)
      state.shown += 1
      saveHintState(state)
    }
  }, [])

  const handleClick = useCallback(() => {
    // Mark voice as used — stop future hints
    setShowRing(false)
    const state = loadHintState()
    state.used = true
    saveHintState(state)
    onClick()
  }, [onClick])

  const iconSize = size === 'sm' ? 'w-4 h-4' : 'w-5 h-5'
  const buttonSize = size === 'sm' ? 'h-8 w-8' : 'h-10 w-10'
  const aria = isConnecting
    ? 'Connecting voice input'
    : active
      ? 'Voice input on'
      : 'Start voice input'

  return (
    <button
      onClick={handleClick}
      className={`
        relative ${buttonSize} rounded-full
        flex items-center justify-center
        active:scale-95
        transition-all duration-200
        ${active
          ? 'bg-primary text-white shadow-sm shadow-primary/30'
          : 'bg-surface-dim text-text-muted hover:bg-accent-light/60 hover:text-text'
        }
        ${isConnecting ? 'opacity-50' : ''}
      `}
      aria-label={aria}
      aria-pressed={active}
      title={aria}
      disabled={isConnecting}
      type="button"
    >
      <MicIcon className={iconSize} />

      {/* Ring blink: 2 pulses then stops */}
      {showRing && !active && (
        <span className="absolute inset-0 rounded-full border-2 border-primary/60 animate-ring-blink pointer-events-none" />
      )}
    </button>
  )
}

/** Whether the voice hint placeholder should show (for ChatInputBar) */
export function shouldShowVoiceHint(): boolean {
  const state = loadHintState()
  const now = Date.now()
  if (now - state.lastSeen >= RESET_AFTER_MS) return true
  return !state.used && state.shown <= MAX_HINTS
}
