import { useCallback, useEffect, useRef, useState } from 'react'
import { ChatInputBar } from './components/ChatInputBar'
import { MessageBubble, type CoachMessage } from './components/MessageBubble'
import { useSTT } from './hooks/useSTT'
import { streamChat, type ChatTurn } from './api'

const GREETING: CoachMessage = {
  id: 'coach-greeting',
  role: 'assistant',
  content:
    "I'm your Healing Journey sales coach — here to help you sign surgeons up for The Healing Journey. " +
    "Ask me how to introduce it, win or grow an account, or handle an objection. Or name a doctor or " +
    "practice you're about to meet, and I'll pull their public reviews and a natural way to open the conversation.",
}

// Conversation memory sent with each turn; the backend caps it too, this keeps requests small.
const MAX_HISTORY_TURNS = 10

let _idSeq = 0
const nextId = () => `m${++_idSeq}`

export function CoachChat() {
  const [messages, setMessages] = useState<CoachMessage[]>([GREETING])
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [email, setEmail] = useState<string | null>(null)
  const [accountOpen, setAccountOpen] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const endRef = useRef<HTMLDivElement>(null)

  const stt = useSTT({
    onFinalTranscript: (text) => {
      setInput((prev) => `${prev}${prev ? ' ' : ''}${text}`.trim())
    },
  })

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Who's signed in (for the account menu). 401 → not signed in (link expired / no cookie).
  useEffect(() => {
    fetch('/api/me', { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setEmail(d?.email ?? null))
      .catch(() => setEmail(null))
  }, [])

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || isLoading) return
    if (stt.isListening) stt.stopListening()
    setInput('')

    const userMsg: CoachMessage = { id: nextId(), role: 'user', content: text }
    const assistantId = nextId()
    const assistantMsg: CoachMessage = { id: assistantId, role: 'assistant', content: '', streaming: true }

    // History = prior turns (exclude the greeting and the just-added placeholder).
    const history: ChatTurn[] = messages
      .filter((m) => m.id !== GREETING.id)
      .slice(-MAX_HISTORY_TURNS * 2)
      .map((m) => ({ role: m.role, content: m.content }))

    setMessages((prev) => [...prev, userMsg, assistantMsg])
    setIsLoading(true)

    const patch = (fn: (m: CoachMessage) => CoachMessage) =>
      setMessages((prev) => prev.map((m) => (m.id === assistantId ? fn(m) : m)))

    try {
      for await (const ev of streamChat(text, history)) {
        if (ev.type === 'progress') {
          patch((m) => ({ ...m, status: ev.text }))
        } else if (ev.type === 'delta') {
          patch((m) => ({ ...m, content: m.content + ev.text, status: undefined }))
        } else if (ev.type === 'done') {
          patch((m) => ({ ...m, streaming: false, status: undefined }))
        }
      }
    } catch (e) {
      const msg = e instanceof Error && e.message === 'not-authorized'
        ? "Your session isn't authorized for the coach. Use your magic link to sign in, then reload."
        : "Something went wrong reaching the coach. Try again in a moment."
      patch((m) => ({ ...m, content: m.content || msg, streaming: false, status: undefined }))
    } finally {
      patch((m) => ({ ...m, streaming: false }))
      setIsLoading(false)
    }
  }, [input, isLoading, messages, stt])

  const onMicClick = useCallback(() => {
    if (stt.isListening) stt.stopListening()
    else void stt.startListening()
  }, [stt])

  const newChat = useCallback(() => {
    if (stt.isListening) stt.stopListening()
    setMessages([GREETING])
    setInput('')
    inputRef.current?.focus()
  }, [stt])

  return (
    <div className="min-h-dvh flex flex-col">
      <header
        className="sticky top-0 z-20 backdrop-blur bg-page/80 border-b border-accent-light"
        style={{ paddingTop: 'env(safe-area-inset-top)' }}
      >
        <div className="max-w-2xl mx-auto flex items-center justify-between gap-2.5 px-3 sm:px-4 py-2.5">
          <div className="flex items-center gap-2.5 min-w-0">
            <span className="inline-flex items-center justify-center w-8 h-8 shrink-0 rounded-full bg-primary/15 text-primary text-xs font-bold">
              Coach
            </span>
            <span className="text-sm font-semibold text-text truncate">The Healing Journey Sales Coach</span>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <button
              onClick={newChat}
              aria-label="New chat"
              title="New chat"
              className="inline-flex items-center justify-center w-10 h-10 rounded-full text-text-secondary hover:bg-surface-dim hover:text-text active:scale-95 transition-colors"
            >
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 20h9" />
                <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
              </svg>
            </button>

            <div className="relative">
              <button
                onClick={() => setAccountOpen((v) => !v)}
                aria-label="Account"
                title="Account"
                aria-expanded={accountOpen}
                className="inline-flex items-center justify-center w-10 h-10 rounded-full text-text-secondary hover:bg-surface-dim hover:text-text active:scale-95 transition-colors"
              >
                <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="8" r="4" />
                  <path d="M4 20c0-4 4-6 8-6s8 2 8 6" />
                </svg>
              </button>

              {accountOpen && (
                <>
                  {/* click-away backdrop */}
                  <div className="fixed inset-0 z-30" onClick={() => setAccountOpen(false)} />
                  <div className="absolute right-0 mt-2 w-64 z-40 rounded-xl border border-accent-light bg-surface shadow-lg overflow-hidden animate-fade-in">
                    <div className="px-4 py-3 border-b border-accent-light">
                      <div className="text-[11px] uppercase tracking-wide text-text-muted">Signed in as</div>
                      <div className="text-sm text-text truncate">{email ?? 'Not signed in'}</div>
                    </div>
                    <div className="px-4 py-3 text-xs text-text-secondary border-b border-accent-light">
                      Tip: name a doctor or practice and I'll pull their public reviews and an opener.
                    </div>
                    {email ? (
                      <a
                        href="/auth/logout"
                        className="block px-4 py-3 text-sm text-text hover:bg-surface-dim transition-colors"
                      >
                        Sign out
                      </a>
                    ) : (
                      <div className="px-4 py-3 text-xs text-text-muted">
                        Use your magic link to sign in.
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </header>

      <main className="flex-1 w-full">
        <div className="max-w-2xl mx-auto px-3 sm:px-4 pt-6 pb-44">
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
          <div ref={endRef} />
        </div>
      </main>

      <ChatInputBar
        inputRef={inputRef}
        value={input}
        onChange={setInput}
        onSend={send}
        isLoading={isLoading}
        isListening={stt.isListening}
        isConnecting={stt.isConnecting}
        interimTranscript={stt.interimTranscript}
        voiceError={stt.error}
        onMicClick={onMicClick}
        onStopListening={stt.stopListening}
      />
    </div>
  )
}
