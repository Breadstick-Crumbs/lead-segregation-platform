# Reporting

`GET /api/v1/reports?days=30` returns the reporting page: leads opened in the last 7, 30
or 90 days (any 1–365 via the API). Any signed-in user can see it.

A period of N days is N whole local days ending today: it starts at midnight N − 1 days
ago in the reporting time zone, so a 7-day report has 7 days and only today is partial.

## What each figure means

| Figure | Definition |
|--------|------------|
| **Leads** | Every lead opened in the period, spam included (spam is shown separately). |
| **Leads by source** | New leads per day, stacked email over WhatsApp. Days run in the reporting time zone (`REPORTING_TIMEZONE`, default `Asia/Dubai`; `?timezone=` overrides it), and days with no leads are included. |
| **Categories** | The AI's classification, including "Not classified" for leads whose scoring is pending or failed. |
| **Conversion funnel** | Non-spam leads by the furthest stage each one reached: contacted, qualified, converted. A lead that reached qualified and was then lost still counts as qualified. The rate is converted ÷ non-spam leads. |
| **Time to first response** | From the lead arriving to the first thing a person did: replying, or moving it to contacted, qualified or converted. Marking a lead spam or lost isn't a response (it does stop the SLA clock, which is a different question). A reply that failed to send doesn't count. Shown as the median, the 90th percentile and by source. |
| **Within SLA** | Of the high and critical leads whose outcome is known, how many got a first response within `sla_minutes`. A lead still inside its window isn't counted yet. One that waited past it counts as a miss. One closed as lost without a response is left out, because that stops the SLA clock, as it does for alerts. |
| **High-value leads** | Non-spam leads scoring at or above the "high" threshold: the count and its share of genuine (non-spam) leads, conversions (leads that reached converted, as in the funnel) and the top 10. |
| **Auto-replies sent** | Acknowledgements that left (sent, delivered or read), not queued or failed ones. |

All figures come from the same history the app writes as it works: status-change events
with the person who made them, and outbound messages. No separate counters can drift.

## Charts

The charts are plain SVG, built to the dataviz rules:

- one validated colour pair: email in berry, WhatsApp in blue, re-stepped for dark mode;
- thin marks with rounded data ends and a 2 px surface gap between stacked segments;
- hairline grids;
- a legend for the two-series chart;
- a tooltip on hover;
- a "View as table" for each chart.

Magnitudes use one hue.
