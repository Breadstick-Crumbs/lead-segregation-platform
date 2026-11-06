# Design direction

**Reference:** the style of [smartmum.ai](https://smartmum.ai/), especially its
"Doctor Portal" dashboard mockup. We take its visual vocabulary, not its brand:
different accent, our own content. The measured notes and screenshots are in
the taste profile (`~/.susanoo/taste/sites/smartmum/`).

**Feeling:** calm and in control. A rep opens the dashboard and immediately
knows which three leads need them now and why.

## Signature moves

1. **Two-tone headings.** The page title's first line is ink, the second is the
   accent: "Leads / **that need you**", "Reports / **this month**". Use this on
   page headers only, not on cards.
2. **The big number is the hero.** Score and KPIs are set large and tight with
   a small unit beside them, like SmartMum's "142 bpm": **82** /100,
   **14** new today, **3m** avg first response.
3. **Mono eyebrows.** Small tracked uppercase mono labels above titles and
   stats (`LEAD SCORE`, `WHATSAPP · 2 MIN AGO`) against the rounded sans.
4. **Tinted plates, no shadows.** Depth comes from a soft tinted backing plate
   and 1px borders. The lead-detail score card sits on a backing plate rotated
   about 2°, the one decorative flourish in the app.
5. **Floating card nav with a scroll-spy pill.** The top nav is a white card
   inset 16px with a 1px border and a 16px radius. The active item is an accent
   pill with a 5–8% tint and accent-coloured text.

## Tokens

| Token | Light | Dark | Use |
|-------|-------|------|-----|
| `--color-bg` | `#FAFAFA` | `#0B0F17` | page |
| `--color-surface` | `#FFFFFF` | `#121826` | cards |
| `--color-plate` | `#F4F1F2` | `#1A2131` | backing plates, stat tiles |
| `--color-border` | `#ECECEF` | `#252D3D` | 1px card and nav borders |
| `--color-ink` | `#0F172A` | `#F1F5F9` | headings, numbers |
| `--color-body` | `#475569` | `#CBD5E1` | body text |
| `--color-muted` | `#64748B` | `#94A3B8` | eyebrows, meta |
| `--color-accent` | `#0E7C6B` | `#2DD4BF` | primary CTA, second heading line, active nav, chart series |
| `--color-accent-tint` | accent at 6% | accent at 12% | active pill, chips |

Fixed status colours (each paired with text, never colour alone):

| Status | Colour |
|--------|--------|
| critical | `#B42318` |
| high | `#C2410C` |
| medium | `#A16207` |
| low | `#64748B` |
| success / connected | `#15803D` |

- **Accent:** deep teal for now. Swap `--color-accent` for Tawasol's brand
  colour if one is supplied; nothing else changes.
- **Type:** Urbanist (Google Fonts) for everything, and JetBrains Mono for
  eyebrows, IDs and tabular numbers.
  - Scale: 12 / 14 / 16 / 18 / 24 / 32 / 48. Numbers are 48–64 px, weight 700,
    tracking −0.02em.
  - Body is 16 px with line-height 1.6.
- **Radius:** 12px for buttons, inputs and chips (pills are fully round), 16px
  for cards and the nav, 24px for backing plates.
- **Spacing:** a 4px base; cards have 24px padding; 24px gaps between sections.
- **Motion:** 0.15 s ease-out for UI and 0.3 s for the nav and pill. New-lead
  toasts slide in once. A "Live" dot pulses slowly. Nothing else animates.
  Respect `prefers-reduced-motion`.

## Screens

- **Leads (home).** Two-tone header, then a stats row (new today, high
  priority, unanswered past SLA, conversion rate), then a "Needs you" strip of
  high and critical leads assigned to the current user, then the full table.
  The table filters by source, category, score range, status and owner, and
  has search and sort.
  - Source shows as a small icon chip (mail or WhatsApp).
  - Score is a mono number with a priority bar.
  - Category is a tinted chip.
- **Lead detail.**
  - Left: the conversation thread (inbound and outbound bubbles; auto-replies
    labelled "Auto-reply · sent 12:03").
  - Right: the score card on the tilted plate, then the factor breakdown bars,
    the AI rationale in quotes, and the owner/status controls.
  - Below: the event timeline.
- **Reports.**
  - Volume by source over time, category distribution, a conversion funnel,
    time to first response, and high-value leads.
  - Charts use the accent plus neutrals, follow the `dataviz` skill, and show
    big-number summaries above each chart.
- **Notifications.** A bell with a count in the nav; a panel listing each
  item's lead, score and reason ("Escalated: no response in 60 min").
- **Settings (admin).** Thresholds, scoring weights with a live preview, keyword
  lists, auto-reply templates per language, mailboxes, WhatsApp status, users.

## Rules

- Every list has designed empty, loading (skeletons the shape of the content)
  and error states.
- Lead message content may be Arabic. Render it with `dir="auto"` and a
  font stack that covers Arabic (Urbanist, then IBM Plex Sans Arabic). The UI
  chrome is English.
- Tables become stacked cards below 768px. The nav collapses to a full-screen
  sheet with big links.
- Dark mode via a nav toggle, and the accent survives in both themes.
- Use real-looking seed data: GCC company names, realistic subjects and
  amounts. No lorem ipsum.
- Focus rings are visible (2px accent outline, 2px offset). Tap targets are at
  least 44px.
