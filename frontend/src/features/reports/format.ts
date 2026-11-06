export const REPORT_PERIODS = [7, 30, 90] as const
export type ReportPeriod = (typeof REPORT_PERIODS)[number]

/** 45 → "45 min", 200 → "3.3 h", 3000 → "2.1 days". */
export function duration(minutes: number | null | undefined): { value: string; unit: string } {
  if (minutes == null) return { value: '—', unit: '' }
  if (minutes < 60) return { value: String(Math.round(minutes)), unit: 'min' }
  if (minutes < 48 * 60) return { value: (minutes / 60).toFixed(1), unit: 'h' }
  return { value: (minutes / 1440).toFixed(1), unit: 'days' }
}
