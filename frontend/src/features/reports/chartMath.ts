import { useLayoutEffect, useRef, useState } from 'react'

/** The rendered width of an element, kept current as it resizes. */
export function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) setWidth(Math.floor(entry.contentRect.width))
    })
    observer.observe(element)
    setWidth(Math.floor(element.getBoundingClientRect().width))
    return () => observer.disconnect()
  }, [])
  return [ref, width]
}

/** Tick values for an axis from 0 to a clean maximum (steps of 1, 2 or 5 × 10^n). Counts
 * are whole, so the step never goes below 1: no "half a lead" gridlines. */
export function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0, 1]
  const raw = max / count
  const power = 10 ** Math.floor(Math.log10(raw))
  const step = Math.max(1, [1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? power * 10)
  const ticks: number[] = []
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(Math.round(v * 1000) / 1000)
  const last = ticks.at(-1) ?? 0
  if (last < max) ticks.push(last + step)
  return ticks
}

export interface Segment<K extends string> {
  key: K
  from: number // cumulative value at the segment's bottom
  to: number // and at its top
  isTop: boolean // the data end, which gets the rounded corners
}

/** Stack one column's values bottom-up, skipping zeros, marking the topmost segment. */
export function stackSegments<K extends string>(
  values: Record<K, number>,
  keys: readonly K[],
): Segment<K>[] {
  const present = keys.filter((k) => values[k] > 0)
  let base = 0
  return present.map((key, index) => {
    const segment = { key, from: base, to: base + values[key], isTop: index === present.length - 1 }
    base = segment.to
    return segment
  })
}

/** A rectangle with only its top corners rounded (the data end); the baseline stays square. */
export function topRounded(x: number, y: number, w: number, h: number, r: number): string {
  const radius = Math.min(r, w / 2, h)
  return `M${x},${y + h} V${y + radius} Q${x},${y} ${x + radius},${y} H${x + w - radius} Q${x + w},${y} ${x + w},${y + radius} V${y + h} Z`
}
