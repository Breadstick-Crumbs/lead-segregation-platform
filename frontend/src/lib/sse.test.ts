import { EventStreamParser } from './sse'

test('parses named events and joins multi-line data', () => {
  const parser = new EventStreamParser()

  const events = parser.push(
    'event: ready\ndata: {}\n\nevent: notification\ndata: line one\ndata: line two\n\n',
  )

  expect(events).toEqual([
    { event: 'ready', data: '{}' },
    { event: 'notification', data: 'line one\nline two' },
  ])
})

test('keeps partial events across chunks and handles CRLF', () => {
  const parser = new EventStreamParser()

  expect(parser.push('event: notifi')).toEqual([])
  expect(parser.push('cation\r\ndata: {"id":"n1"}\r')).toEqual([])
  expect(parser.push('\n\r\n')).toEqual([{ event: 'notification', data: '{"id":"n1"}' }])
})

test('ignores comments (keep-alive pings) and blank events', () => {
  const parser = new EventStreamParser()

  expect(parser.push(': ping - 2026-11-06\n\n\n')).toEqual([])
  expect(parser.push('data: plain\n\n')).toEqual([{ event: 'message', data: 'plain' }])
})
