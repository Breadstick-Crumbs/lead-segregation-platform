# Decisions and assumptions

These questions were open for Tawasol. On 2026-11-06 the decision was to
proceed on the assumptions below. Each one is configurable, so a different
answer from Tawasol later is a settings change, not a redesign.

| # | Topic | Decision |
|---|-------|----------|
| 1 | Languages | English and Arabic leads; auto-reply in the detected language. The dashboard UI is English and renders Arabic content with `dir="auto"`. |
| 2 | Thresholds | high ≥ 60, escalate ≥ 80; auto-reply when the score is above 20 (from the spec). Admin-configurable. |
| 3 | Escalation | An admin-configured escalation list plus a Slack/Teams webhook. 60-minute SLA for the first human response on high and critical leads. |
| 4 | Owner assignment | Round-robin among active sales reps, with manual reassignment. |
| 5 | Spam and support | Spam is never auto-replied or notified. Support requests get the auto-reply but are kept out of sales priority (score capped at 40). |
| 6 | Conversion | A rep changes the status manually to `converted`; no CRM sync. |
| 7 | Mail provider | Any IMAP/SMTP inbox. Per-mailbox auth: password (generic hosts, Gmail app passwords) or OAuth2 XOAUTH2 (Microsoft 365, Google Workspace). |
| 8 | WhatsApp number | Meta's **test number** from the WhatsApp Cloud API app dashboard. It can message up to 5 verified recipient numbers, which is enough for the demo. |
| 9 | Hosting and data | No residency constraints. Hosting on Cloudflare (see `docs/DEPLOYMENT.md`). |
| 10 | Deliverable | A public GitHub repository with CI, plus a deployed demo. |
