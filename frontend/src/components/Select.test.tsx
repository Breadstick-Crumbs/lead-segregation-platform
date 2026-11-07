import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'

import { Select } from './Select'

function StatusPicker({ onPick = () => {} }: { onPick?: (value: string) => void }) {
  const [value, setValue] = useState('')
  return (
    <>
      <span id="status-label">Status</span>
      <Select
        labelledBy="status-label"
        value={value}
        onValueChange={(next) => {
          setValue(next)
          onPick(next)
        }}
      >
        <option value="">Any status</option>
        <option value="new">New</option>
        <option value="contacted">Contacted</option>
        <option value="lost" disabled>
          Lost
        </option>
        <option value="spam">Spam</option>
      </Select>
    </>
  )
}

const trigger = () => screen.getByRole('combobox', { name: /Status/ })

test('opens a themed list instead of the native menu, and picks with the mouse', () => {
  const onPick = vi.fn()
  render(<StatusPicker onPick={onPick} />)
  expect(trigger()).toHaveTextContent('Any status')
  expect(screen.queryByRole('listbox')).toBeNull()

  fireEvent.click(trigger())
  expect(trigger()).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
    'Any status',
    'New',
    'Contacted',
    'Lost',
    'Spam',
  ])
  expect(screen.getByRole('option', { name: 'Any status' })).toHaveAttribute(
    'aria-selected',
    'true',
  )

  fireEvent.click(screen.getByRole('option', { name: 'Contacted' }))
  expect(onPick).toHaveBeenCalledWith('contacted')
  expect(trigger()).toHaveTextContent('Contacted')
  expect(screen.queryByRole('listbox')).toBeNull()
})

test('works from the keyboard and skips disabled options', () => {
  const onPick = vi.fn()
  render(<StatusPicker onPick={onPick} />)

  fireEvent.keyDown(trigger(), { key: 'ArrowDown' })
  expect(screen.getByRole('listbox')).toBeInTheDocument()
  fireEvent.keyDown(trigger(), { key: 'ArrowDown' }) // New
  fireEvent.keyDown(trigger(), { key: 'ArrowDown' }) // Contacted
  fireEvent.keyDown(trigger(), { key: 'ArrowDown' }) // Lost is disabled: Spam
  expect(trigger().getAttribute('aria-activedescendant')).toMatch(/opt-4$/)
  fireEvent.keyDown(trigger(), { key: 'Enter' })
  expect(onPick).toHaveBeenLastCalledWith('spam')

  fireEvent.keyDown(trigger(), { key: 'Home' })
  fireEvent.keyDown(trigger(), { key: 'Escape' })
  expect(screen.queryByRole('listbox')).toBeNull()
  expect(onPick).toHaveBeenCalledTimes(1)
})

test('typing jumps to a matching option', () => {
  const onPick = vi.fn()
  render(<StatusPicker onPick={onPick} />)

  fireEvent.keyDown(trigger(), { key: 'c' })
  expect(onPick).toHaveBeenLastCalledWith('contacted')
})

test('a click outside closes it without changing anything', () => {
  const onPick = vi.fn()
  render(<StatusPicker onPick={onPick} />)
  fireEvent.click(trigger())

  fireEvent.pointerDown(document.body)
  expect(screen.queryByRole('listbox')).toBeNull()
  expect(onPick).not.toHaveBeenCalled()
})
