/**
 * A Server-Sent Events reader over fetch, because EventSource can't send an Authorization
 * header. Parses the text/event-stream format (https://html.spec.whatwg.org/#event-stream-
 * interpretation): `event:` and `data:` lines, a blank line ends an event, `:` lines are
 * comments (the server's keep-alive pings).
 */

export interface ServerEvent {
  event: string
  data: string
}

export class EventStreamParser {
  private buffer = ''
  private event = ''
  private data: string[] = []

  /** Feed a chunk of text; returns the events it completed. */
  push(chunk: string): ServerEvent[] {
    this.buffer += chunk
    const events: ServerEvent[] = []
    let newline: number
    while ((newline = this.buffer.search(/\r\n|\r|\n/)) !== -1) {
      const line = this.buffer.slice(0, newline)
      const width = this.buffer.startsWith('\r\n', newline) ? 2 : 1
      this.buffer = this.buffer.slice(newline + width)
      const done = this.line(line)
      if (done) events.push(done)
    }
    return events
  }

  private line(line: string): ServerEvent | null {
    if (line === '') {
      if (this.data.length === 0) {
        this.event = ''
        return null
      }
      const finished = { event: this.event || 'message', data: this.data.join('\n') }
      this.event = ''
      this.data = []
      return finished
    }
    if (line.startsWith(':')) return null
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') this.event = value
    else if (field === 'data') this.data.push(value)
    return null
  }
}

/** Read one SSE response until it ends or `signal` aborts, calling `onEvent` per event. */
export async function readEventStream(
  response: Response,
  onEvent: (event: ServerEvent) => void,
): Promise<void> {
  if (!response.body) return
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  const parser = new EventStreamParser()
  for (;;) {
    const { value, done } = await reader.read()
    if (done) return
    for (const event of parser.push(value)) onEvent(event)
  }
}
