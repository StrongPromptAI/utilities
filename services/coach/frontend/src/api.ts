// Coach chat SSE client. Posts to the same-origin /api/chat (coach_session cookie rides)
// and yields typed events: 'delta' (answer text), 'progress' (slow-tool phase updates,
// e.g. meeting-prep scrape→craft), and end. Mirrors the coach backend's SSE shape
// (app.py: data: {"delta"|"progress": ...} ... data: [DONE]).

export type ChatTurn = { role: 'user' | 'assistant'; content: string }
export type ChatEvent =
  | { type: 'delta'; text: string }
  | { type: 'progress'; text: string }
  | { type: 'done' }

export async function* streamChat(
  message: string,
  history: ChatTurn[],
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const resp = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ message, history }),
    signal,
  })
  if (resp.status === 401 || resp.status === 403) {
    throw new Error('not-authorized')
  }
  if (!resp.ok || !resp.body) {
    throw new Error(`chat: ${resp.status}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // SSE frames are separated by blank lines; each carries one `data:` line.
    let nl: number
    while ((nl = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, nl).trimEnd()
      buffer = buffer.slice(nl + 1)
      if (!line.startsWith('data:')) continue
      const data = line.slice(5).trim()
      if (!data) continue
      if (data === '[DONE]') { yield { type: 'done' }; return }
      try {
        const ev = JSON.parse(data)
        if (typeof ev.delta === 'string') yield { type: 'delta', text: ev.delta }
        else if (typeof ev.progress === 'string') yield { type: 'progress', text: ev.progress }
      } catch { /* ignore malformed frame */ }
    }
  }
  yield { type: 'done' }
}
