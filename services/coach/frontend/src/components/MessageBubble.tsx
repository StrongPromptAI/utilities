import { ReactNode, useEffect, useState } from 'react'

// Lightweight markdown → HTML, ported verbatim from the THJ chat MessageBubble. Bold,
// italic, images, line breaks, and inline "1. 2. 3." → <ol>. The coach answers are plain
// conversational prose, so this is all the rendering the tailored shell needs.
export function renderMarkdown(text: string): string {
  if (!text) return ''
  const inlineStepPattern = /(?:^|(?<=\s))(\d+)\.\s/g
  const stepMatches = [...text.matchAll(inlineStepPattern)]
  if (stepMatches.length >= 3 && !text.includes('\n')) {
    const parts = text.split(/\s*\d+\.\s+/)
    const preamble = parts[0]
    const steps = parts.slice(1).filter(Boolean)
    if (steps.length >= 3) {
      const listItems = steps.map(s => `<li>${s.trim()}</li>`).join('')
      const ol = `<ol class="list-decimal list-inside space-y-1.5 mt-2 mb-1">${listItems}</ol>`
      return (preamble ? `${preamble}${ol}` : ol)
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    }
  }
  return text
    .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" class="rounded-lg max-w-full my-2" />')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/\n/g, '<br/>')
}

// Floating guide badge shown while the assistant is working (no bubble). Greyscale →
// color after 1.5s; an optional status line renders the slow-tool progress (meeting prep).
// Ported from the THJ ThinkingBadge; default guide name is the coach.
export function ThinkingBadge({ status, guideName = 'Coach' }: { status?: string; guideName?: string } = {}): ReactNode {
  const [phase, setPhase] = useState<'bw' | 'color'>('bw')
  useEffect(() => {
    const timer = setTimeout(() => setPhase('color'), 1500)
    return () => clearTimeout(timer)
  }, [])
  return (
    <span className="inline-flex items-center gap-2.5">
      <span
        className={`inline-flex items-center justify-center w-7 h-7 rounded-full text-[11px] font-bold transition-all duration-700 ease-in-out ${
          phase === 'bw' ? 'bg-gray-200 text-gray-500 animate-pulse' : 'bg-primary/20 text-primary animate-pulse'
        }`}
      >
        {guideName}
      </span>
      {status && <span className="italic text-text-muted text-sm leading-tight">{status}</span>}
    </span>
  )
}

export type CoachMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
  status?: string   // ephemeral progress line shown next to the badge before the first token
}

/** Chat bubble — user (right) and assistant (left), markdown-rendered. While an assistant
 *  turn is streaming with no text yet, show just the thinking badge + progress status. */
export function MessageBubble({ message, guideName = 'Coach' }: { message: CoachMessage; guideName?: string }): ReactNode {
  if (message.role === 'assistant' && message.streaming && !message.content) {
    return (
      <div className="flex justify-start mb-4 animate-fade-in pl-1">
        <ThinkingBadge status={message.status} guideName={guideName} />
      </div>
    )
  }
  const isUser = message.role === 'user'
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4 animate-fade-in`}>
      <div
        className={`max-w-[85%] md:max-w-[70%] px-5 py-3.5 rounded-2xl ${
          isUser
            ? 'bg-gradient-to-br from-primary to-primary-600 text-white'
            : 'bg-surface text-text border border-accent-light'
        }`}
      >
        <div
          className="prose prose-sm max-w-none leading-relaxed [overflow-wrap:anywhere]"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }}
        />
      </div>
    </div>
  )
}
