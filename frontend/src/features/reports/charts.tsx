/**
 * Small hand-built SVG charts, following the dataviz rules: thin marks with 4px rounded
 * data ends anchored to the baseline, a 2px surface gap between stacked segments, hairline
 * recessive grids, a legend for two or more series, labels in text colours (never the
 * series colour), a hover tooltip on every mark and a table view of the same numbers.
 */
import { useState, type ReactNode } from 'react'

import { cn } from '../../lib/cn'
import { niceTicks, stackSegments, topRounded, useWidth } from './chartMath'

export interface StackSeries<K extends string> {
  key: K
  label: string
  color: string // a CSS colour, e.g. var(--color-series-email)
}

export function StackedColumns<
  K extends string,
  D extends { [P in K]: number } & { id: string; label: string },
>({
  data,
  series,
  height = 220,
  ariaLabel,
}: {
  data: D[]
  series: StackSeries<K>[]
  height?: number
  ariaLabel: string
}) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const margin = { top: 8, right: 8, bottom: 26, left: 32 }
  const innerW = Math.max(0, width - margin.left - margin.right)
  const innerH = height - margin.top - margin.bottom
  const totals = data.map((d) => series.reduce((sum, s) => sum + d[s.key], 0))
  const ticks = niceTicks(Math.max(...totals, 0))
  const top = ticks.at(-1) ?? 1
  const band = data.length ? innerW / data.length : 0
  const barW = Math.max(2, Math.min(24, band * 0.64))
  const y = (v: number) => margin.top + innerH - (v / top) * innerH
  const labelEvery = Math.max(1, Math.ceil(data.length / Math.max(1, Math.floor(innerW / 64))))

  return (
    <div ref={ref} className="relative">
      {width > 0 ? (
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={ariaLabel}
          onMouseLeave={() => setHover(null)}
        >
          {ticks.map((t) => (
            <g key={t}>
              <line
                x1={margin.left}
                x2={width - margin.right}
                y1={y(t)}
                y2={y(t)}
                stroke="var(--color-border)"
                strokeWidth={1}
              />
              <text
                x={margin.left - 8}
                y={y(t)}
                dy="0.32em"
                textAnchor="end"
                className="fill-muted font-mono text-[11px]"
              >
                {t}
              </text>
            </g>
          ))}
          {data.map((d, i) => {
            const x = margin.left + i * band + (band - barW) / 2
            const colour = new Map(series.map((s) => [s.key, s.color]))
            const faded = hover !== null && hover !== i
            return (
              <g key={d.id}>
                {stackSegments(
                  d,
                  series.map((s) => s.key),
                ).map((segment, index) => {
                  const yTop = y(segment.to)
                  // 2px surface gap; a non-zero segment never shrinks out of sight.
                  const h = Math.max(1.5, y(segment.from) - yTop - (index > 0 ? 2 : 0))
                  const fill = colour.get(segment.key)
                  return segment.isTop ? (
                    <path
                      key={segment.key}
                      d={topRounded(x, yTop, barW, h, 4)}
                      fill={fill}
                      opacity={faded ? 0.45 : 1}
                    />
                  ) : (
                    <rect
                      key={segment.key}
                      x={x}
                      y={yTop}
                      width={barW}
                      height={h}
                      fill={fill}
                      opacity={faded ? 0.45 : 1}
                    />
                  )
                })}
                {i % labelEvery === 0 ? (
                  <text
                    x={x + barW / 2}
                    y={height - 8}
                    textAnchor="middle"
                    className="fill-muted font-mono text-[11px]"
                  >
                    {d.label}
                  </text>
                ) : null}
                {/* Hit target: the whole band, wider than the mark. */}
                <rect
                  x={margin.left + i * band}
                  y={margin.top}
                  width={band}
                  height={innerH}
                  fill="transparent"
                  onPointerEnter={() => setHover(i)}
                  onPointerDown={() => setHover(i)}
                />
              </g>
            )
          })}
        </svg>
      ) : (
        <div style={{ height }} />
      )}
      {hover !== null && data[hover] !== undefined ? (
        <div
          role="tooltip"
          className="pointer-events-none absolute z-10 min-w-36 -translate-x-1/2 rounded-control border border-border bg-surface px-3 py-2 text-sm"
          style={{
            left: Math.min(Math.max(margin.left + hover * band + band / 2, 72), width - 72),
            top: 0,
          }}
        >
          <p className="font-semibold text-ink">{data[hover]?.label}</p>
          {series.map((s) => (
            <p key={s.key} className="flex items-center justify-between gap-4 text-body">
              <span className="flex items-center gap-1.5">
                <span className="size-2.5 rounded-full" style={{ background: s.color }} />
                {s.label}
              </span>
              <span className="font-mono tabular-nums text-ink">{data[hover]?.[s.key]}</span>
            </p>
          ))}
        </div>
      ) : null}
    </div>
  )
}

export interface BarRow {
  key: string
  label: string
  value: number
  note?: string // shown after the value, e.g. "42%"
  display?: string // replaces the number, e.g. "—" when there's no value
}

/** Horizontal bars in one hue (magnitude, sorted by the caller), value at the bar's tip. */
export function BarList({
  rows,
  max,
  ariaLabel,
}: {
  rows: BarRow[]
  max?: number
  ariaLabel: string
}) {
  const top = max ?? Math.max(...rows.map((r) => r.value), 1)
  const [hover, setHover] = useState<string | null>(null)
  return (
    <ul aria-label={ariaLabel} className="flex flex-col gap-3">
      {rows.map((row) => (
        <li
          key={row.key}
          onMouseEnter={() => setHover(row.key)}
          onMouseLeave={() => setHover(null)}
          className="relative"
        >
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="text-body">{row.label}</span>
            <span className="font-mono text-ink tabular-nums">
              {row.display ?? row.value}
              {row.note ? <span className="ml-1.5 text-muted">{row.note}</span> : null}
            </span>
          </div>
          <div className="mt-1.5 h-2.5 overflow-hidden rounded-r-[4px] bg-plate">
            <div
              className={cn(
                'h-full rounded-r-[4px] bg-accent transition-opacity duration-150',
                hover && hover !== row.key && 'opacity-45',
              )}
              style={{ width: `${top ? (row.value / top) * 100 : 0}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  )
}

export function Legend({
  items,
}: {
  items: { label: string; color: string; value?: ReactNode }[]
}) {
  return (
    <ul className="flex flex-wrap gap-4 text-sm" aria-label="Legend">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-2 text-body">
          <span className="size-2.5 rounded-full" style={{ background: item.color }} aria-hidden />
          {item.label}
          {item.value != null ? (
            <span className="font-mono text-ink tabular-nums">{item.value}</span>
          ) : null}
        </li>
      ))}
    </ul>
  )
}

/** The chart's numbers as a table, for screen readers and anyone who prefers it. */
export function DataTable({
  caption,
  columns,
  rows,
}: {
  caption: string
  columns: string[]
  rows: (string | number)[][]
}) {
  return (
    <details className="mt-4 text-sm">
      <summary className="inline-flex min-h-11 cursor-pointer items-center font-semibold text-accent">
        View as table
      </summary>
      <div className="mt-2 max-h-72 overflow-auto rounded-control border border-border">
        <table className="w-full text-left">
          <caption className="sr-only">{caption}</caption>
          <thead className="sticky top-0 bg-surface">
            <tr>
              {columns.map((c) => (
                <th
                  key={c}
                  scope="col"
                  className="border-b border-border px-3 py-2 font-mono text-[11px] font-normal tracking-[0.1em] text-muted uppercase"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-border last:border-0">
                {row.map((cell, j) => (
                  <td key={j} className={cn('px-3 py-1.5', j > 0 && 'font-mono tabular-nums')}>
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}
