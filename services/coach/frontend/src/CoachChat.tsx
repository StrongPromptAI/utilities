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

// Peterson plays — 5 per book. Each tap closes the dialog and asks the coach this question
// (Healing-Journey-framed so the answer is immediately usable).
type Play = { name: string; blurb: string; q: string }
const PETERSON: { book: string; tag: string; plays: Play[] }[] = [
  {
    book: 'Conversations That Win the Complex Sale',
    tag: 'win a new account',
    plays: [
      { name: 'Why Change', blurb: 'break the surgeon off the status quo', q: "Coach me on the 'Why Change' play to get a surgeon off the status quo and onto The Healing Journey." },
      { name: 'Unconsidered Needs', blurb: 'surface a problem they overlook', q: "Help me surface an 'unconsidered need' a surgeon doesn't realize they have about post-op recovery, to open the door for The Healing Journey." },
      { name: 'Value Wedge', blurb: 'anchor on what only we do', q: "How do I build a 'value wedge' for The Healing Journey — the thing only we do that a surgeon should care about?" },
      { name: 'Contrast Story', blurb: 'before → after, dramatized', q: "Help me tell a 'before and after' contrast story for a surgeon adopting The Healing Journey." },
      { name: 'Provocative Grabber', blurb: 'open with an insight, not features', q: "Give me a provocative 'grabber' to open a first conversation with a surgeon about The Healing Journey." },
    ],
  },
  {
    book: 'The Expansion Sale',
    tag: 'grow an account you have',
    plays: [
      { name: 'Why Evolve', blurb: 'reinforce, then add one new need', q: "How do I use 'Why Evolve' to expand a surgeon who already buys from us into The Healing Journey?" },
      { name: 'Why Stay', blurb: 'reinforce delivered value', q: "Coach me on the 'Why Stay' conversation to reinforce the value we've delivered so a surgeon keeps and deepens The Healing Journey." },
      { name: 'Why Pay More', blurb: 'justify a price increase', q: "How do I run a 'Why Pay More' conversation to justify pricing for The Healing Journey without losing the account?" },
      { name: 'Why Change (save it)', blurb: 're-provoke an at-risk account', q: "An existing surgeon account is wavering on The Healing Journey — coach me on the 'Why Change' save play." },
      { name: "Reinforce, don't provoke", blurb: 'status quo now works for you', q: "Remind me how to coach an EXISTING account differently from a new one — reinforce instead of provoke — for The Healing Journey." },
    ],
  },
]

function formatExpiry(exp: number | null): string | null {
  if (!exp) return null
  try {
    return new Date(exp * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  } catch { return null }
}

export function CoachChat() {
  const [messages, setMessages] = useState<CoachMessage[]>([GREETING])
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [email, setEmail] = useState<string | null>(null)
  const [exp, setExp] = useState<number | null>(null)
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
      .then((d) => { setEmail(d?.email ?? null); setExp(d?.exp ?? null) })
      .catch(() => { setEmail(null); setExp(null) })
  }, [])

  const sendMessage = useCallback(async (raw: string) => {
    const text = raw.trim()
    if (!text || isLoading) return
    if (stt.isListening) stt.stopListening()

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
  }, [isLoading, messages, stt])

  const send = useCallback(() => {
    const text = input
    setInput('')
    void sendMessage(text)
  }, [input, sendMessage])

  // A topic tap: close the dialog and send its question straight into the chat.
  const askTopic = useCallback((question: string) => {
    setAccountOpen(false)
    void sendMessage(question)
  }, [sendMessage])

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

            <button
              onClick={() => setAccountOpen(true)}
              aria-label="Account"
              title="Account"
              className="inline-flex items-center justify-center w-10 h-10 rounded-full text-text-secondary hover:bg-surface-dim hover:text-text active:scale-95 transition-colors"
            >
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="8" r="4" />
                <path d="M4 20c0-4 4-6 8-6s8 2 8 6" />
              </svg>
            </button>
          </div>
        </div>
      </header>

      {/* Account dialog — bottom sheet on mobile, centered card on desktop */}
      {accountOpen && (
        <div className="fixed inset-0 z-40 flex items-end sm:items-center justify-center" role="dialog" aria-modal="true" aria-label="Account">
          <div className="absolute inset-0 bg-black/40 animate-fade-in" onClick={() => setAccountOpen(false)} />
          <div
            className="relative w-full sm:max-w-md max-h-[88vh] overflow-y-auto bg-surface rounded-t-2xl sm:rounded-2xl border border-accent-light shadow-xl animate-slide-up"
            style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
          >
            <div className="sticky top-0 z-10 bg-surface flex items-center justify-between px-4 py-3 border-b border-accent-light">
              <span className="text-sm font-semibold text-text">Account</span>
              <button
                onClick={() => setAccountOpen(false)}
                aria-label="Close"
                className="inline-flex items-center justify-center w-9 h-9 rounded-full text-text-secondary hover:bg-surface-dim hover:text-text active:scale-95 transition-colors"
              >
                <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="px-4 py-3 border-b border-accent-light">
              <div className="text-[11px] uppercase tracking-wide text-text-muted">Signed in as</div>
              <div className="text-sm text-text break-all">{email ?? 'Not signed in'}</div>
              {formatExpiry(exp) && (
                <div className="text-xs text-text-muted mt-1">Access valid until {formatExpiry(exp)}</div>
              )}
            </div>

            <div className="px-4 py-3 text-xs text-text-secondary border-b border-accent-light">
              Tip: name a doctor or practice and I'll pull their public reviews and an opener.
            </div>

            <div className="px-4 py-3">
              <div className="text-[11px] uppercase tracking-wide text-text-muted mb-2">Coach me on a Peterson play</div>
              {PETERSON.map((g) => (
                <div key={g.book} className="mb-3 last:mb-0">
                  <div className="text-xs font-semibold text-text">{g.book}</div>
                  <div className="text-[11px] text-text-muted mb-1.5">{g.tag}</div>
                  <div className="flex flex-col gap-1.5">
                    {g.plays.map((p) => (
                      <button
                        key={p.name}
                        onClick={() => askTopic(p.q)}
                        disabled={isLoading}
                        className="text-left rounded-lg border border-accent-light px-3 py-2 hover:bg-surface-dim active:scale-[0.99] transition disabled:opacity-50"
                      >
                        <span className="text-sm text-text font-medium">{p.name}</span>
                        <span className="block text-[11px] text-text-muted">{p.blurb}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            {email ? (
              <a href="/auth/logout" className="block px-4 py-3.5 text-sm text-text border-t border-accent-light hover:bg-surface-dim transition-colors">
                Sign out
              </a>
            ) : (
              <div className="px-4 py-3.5 text-xs text-text-muted border-t border-accent-light">
                Use your magic link to sign in.
              </div>
            )}
          </div>
        </div>
      )}

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
