import { useEffect, useState } from 'react'

/** Whether the page has scrolled past `threshold` pixels; drives the navbar's solid state. */
export function useScrolled(threshold = 10): boolean {
  const [scrolled, setScrolled] = useState(() => window.scrollY > threshold)

  useEffect(() => {
    const update = () => setScrolled(window.scrollY > threshold)
    update()
    window.addEventListener('scroll', update, { passive: true })
    return () => window.removeEventListener('scroll', update)
  }, [threshold])

  return scrolled
}
