/**
 * A select in the app's own style. The browser's native menu can't be themed (it opens in the
 * OS look, blue highlight and all), so this is the WAI-ARIA "select-only combobox": a button
 * that opens a listbox. Options are written as plain `<option>` children, like a native select.
 *
 * Keyboard: Enter, Space, Alt+Down or the arrows open it; the arrows, Home and End move;
 * typing jumps to a matching option; Enter or Space picks; Escape closes; Tab picks the
 * highlighted option and moves on.
 */
import {
  Children,
  isValidElement,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
} from 'react'

import { cn } from '../lib/cn'
import { Icon } from './Icon'

interface Option {
  value: string
  label: string
  disabled: boolean
}

type OptionElement = ReactElement<{
  value?: string | number
  children?: ReactNode
  disabled?: boolean
}>

function text(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(text).join('')
  return ''
}

function readOptions(children: ReactNode): Option[] {
  return Children.toArray(children)
    .filter((child): child is OptionElement => isValidElement(child) && child.type === 'option')
    .map((child) => ({
      value: String(child.props.value ?? ''),
      label: text(child.props.children),
      disabled: Boolean(child.props.disabled),
    }))
}

const MENU_MAX_HEIGHT = 320 // px; matches max-h-80
const MENU_MAX_WIDTH = 352 // px; matches max-w 22rem

export function Select({
  id,
  value,
  onValueChange,
  children,
  disabled,
  invalid,
  describedBy,
  labelledBy,
  className,
}: {
  id?: string
  value: string
  onValueChange: (value: string) => void
  children: ReactNode
  disabled?: boolean
  invalid?: boolean
  describedBy?: string
  /** The id of the visible label; the trigger is named "<label> <current value>". */
  labelledBy?: string
  className?: string
}) {
  const ownId = useId()
  const triggerId = id ?? ownId
  const listId = `${triggerId}-listbox`
  const options = useMemo(() => readOptions(children), [children])
  const selectedIndex = options.findIndex((o) => o.value === value)
  const selected = options[selectedIndex]

  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [above, setAbove] = useState(false)
  const [alignEnd, setAlignEnd] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const typed = useRef({ text: '', at: 0 })

  const enabled = (index: number) => options[index]?.disabled === false

  function step(from: number, delta: 1 | -1): number {
    for (let i = from + delta; i >= 0 && i < options.length; i += delta) {
      if (enabled(i)) return i
    }
    return from
  }

  function openMenu(highlight = selectedIndex) {
    if (disabled) return
    const rect = triggerRef.current?.getBoundingClientRect()
    if (rect) {
      const below = window.innerHeight - rect.bottom
      setAbove(below < Math.min(MENU_MAX_HEIGHT, options.length * 40 + 16) && rect.top > below)
      // Near the right edge, grow leftwards so a wide menu stays on screen.
      setAlignEnd(rect.left + MENU_MAX_WIDTH > window.innerWidth)
    }
    setActive(enabled(highlight) ? highlight : step(-1, 1))
    setOpen(true)
  }

  function close(focusTrigger = true) {
    setOpen(false)
    if (focusTrigger) triggerRef.current?.focus()
  }

  /** Report a pick, if it is a real, enabled option and a change. */
  function pick(index: number) {
    const option = options[index]
    if (option && !option.disabled && option.value !== value) onValueChange(option.value)
  }

  function choose(index: number) {
    if (!enabled(index)) return
    pick(index)
    close()
  }

  // Close on a click or focus anywhere outside.
  useEffect(() => {
    if (!open) return
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) close(false)
    }
    window.addEventListener('pointerdown', onPointer)
    return () => window.removeEventListener('pointerdown', onPointer)
  }, [open])

  // Keep the highlighted option in view.
  useLayoutEffect(() => {
    if (!open || active < 0) return
    listRef.current
      ?.querySelector<HTMLElement>(`[data-index="${active}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [open, active])

  function typeahead(key: string) {
    const now = Date.now()
    typed.current = {
      text: now - typed.current.at > 600 ? key : typed.current.text + key,
      at: now,
    }
    const query = typed.current.text.toLowerCase()
    const start = open ? active : selectedIndex
    const order = [...options.keys()].map((i) => (start + 1 + i) % options.length)
    const match = order.find((i) => enabled(i) && options[i]?.label.toLowerCase().startsWith(query))
    if (match === undefined) return
    if (open) setActive(match)
    else pick(match)
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const { key } = event
    if (!open) {
      if (['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(key)) {
        event.preventDefault()
        openMenu(key === 'ArrowUp' && selectedIndex < 0 ? options.length - 1 : selectedIndex)
      } else if (key === 'Home' || key === 'End') {
        event.preventDefault()
        openMenu(key === 'Home' ? step(-1, 1) : step(options.length, -1))
      } else if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        typeahead(key)
      }
      return
    }
    switch (key) {
      case 'ArrowDown':
        event.preventDefault()
        setActive((a) => step(a, 1))
        break
      case 'ArrowUp':
        event.preventDefault()
        if (event.altKey) choose(active)
        else setActive((a) => step(a, -1))
        break
      case 'Home':
        event.preventDefault()
        setActive(step(-1, 1))
        break
      case 'End':
        event.preventDefault()
        setActive(step(options.length, -1))
        break
      case 'PageDown':
        event.preventDefault()
        setActive((a) => Math.min(options.length - 1, a + 10))
        break
      case 'PageUp':
        event.preventDefault()
        setActive((a) => Math.max(0, a - 10))
        break
      case 'Enter':
      case ' ':
        event.preventDefault()
        choose(active)
        break
      case 'Escape':
        event.preventDefault()
        close()
        break
      case 'Tab':
        pick(active)
        close(false)
        break
      default:
        if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) typeahead(key)
    }
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        id={triggerId}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open && active >= 0 ? `${triggerId}-opt-${active}` : undefined}
        aria-labelledby={labelledBy ? `${labelledBy} ${triggerId}` : undefined}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        disabled={disabled}
        onClick={() => (open ? close() : openMenu())}
        onKeyDown={onKeyDown}
        className={cn(
          'flex min-h-11 w-full items-center justify-between gap-2 rounded-control border border-border bg-surface pr-3 pl-3 text-left text-[16px] text-ink',
          'transition-colors duration-150 hover:border-muted focus-visible:border-accent disabled:cursor-not-allowed disabled:opacity-60',
          open && 'border-accent',
          invalid && 'border-priority-critical',
          className,
        )}
      >
        <span className="truncate">{selected?.label ?? ' '}</span>
        <Icon
          name="chevronDown"
          size={18}
          className={cn(
            'shrink-0 text-muted transition-transform duration-200 motion-reduce:transition-none',
            open && 'rotate-180',
          )}
        />
      </button>
      {open ? (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          aria-labelledby={labelledBy}
          tabIndex={-1}
          className={cn(
            // At least as wide as the trigger, wider when an option needs it, never off-screen.
            'absolute z-50 max-h-80 w-max max-w-[min(22rem,calc(100vw-2rem))] min-w-full overflow-auto rounded-control border border-border bg-surface p-1.5',
            'shadow-lg shadow-ink/5 dark:shadow-black/30',
            above ? 'bottom-full mb-1.5' : 'top-full mt-1.5',
            alignEnd ? 'right-0' : 'left-0',
          )}
        >
          {options.map((option, index) => {
            const isSelected = index === selectedIndex
            return (
              <li
                key={`${option.value}-${index}`}
                id={`${triggerId}-opt-${index}`}
                data-index={index}
                role="option"
                aria-selected={isSelected}
                aria-disabled={option.disabled || undefined}
                // Keep focus on the trigger, so the keyboard keeps working after a click.
                onPointerDown={(event) => event.preventDefault()}
                onPointerMove={() => !option.disabled && active !== index && setActive(index)}
                onClick={() => choose(index)}
                className={cn(
                  'flex min-h-10 cursor-pointer items-center justify-between gap-3 rounded-[8px] px-3 text-[15px] transition-colors duration-100',
                  index === active ? 'bg-plate text-ink' : 'text-body',
                  isSelected && 'font-semibold text-accent',
                  option.disabled && 'cursor-not-allowed opacity-50',
                )}
              >
                <span className="truncate">{option.label}</span>
                {isSelected ? <Icon name="check" size={16} className="shrink-0" /> : null}
              </li>
            )
          })}
        </ul>
      ) : null}
    </div>
  )
}
