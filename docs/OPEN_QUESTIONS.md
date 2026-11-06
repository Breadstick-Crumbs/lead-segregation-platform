# Open questions for Tawasol

Assumptions are marked; the build proceeds on them unless answered otherwise.

1. **Languages.** Will leads arrive in Arabic as well as English? Should the
   auto-reply match the lead's language?
   *Assumption: yes, English and Arabic, reply in the detected language.*
2. **Thresholds.** Which scores count as "high priority" and which trigger
   escalation? *Assumption: high ≥ 60, escalate ≥ 80 (configurable).*
3. **Escalation target.** Who gets escalations (a sales manager, a channel), and
   is there an SLA for first human response?
   *Assumption: admin-configured list plus Slack/Teams, 60-minute SLA.*
4. **Owner assignment.** How are leads assigned: round-robin, by category, by
   region, or manually? *Assumption: round-robin among active reps, with manual
   reassignment.*
5. **Spam and support.** Should spam ever get an auto-reply? Should support
   requests be routed to a support team instead of sales?
   *Assumption: spam never; support is replied to but kept out of sales priority.*
6. **Conversion definition.** What counts as "converted": status set manually
   by a rep, or a sync from a CRM? *Assumption: manual status change.*
7. **Mail provider.** Which provider hosts the company mailbox (Microsoft 365,
   Google Workspace, other)? Microsoft 365 needs OAuth2 for IMAP/SMTP; basic
   auth is retired. *Assumption: support password auth and OAuth2 (XOAUTH2).*
8. **WhatsApp number.** Is there an existing WhatsApp Business account/number,
   or should the demo use Meta's test number?
9. **Hosting/data.** Any hosting or data-residency requirements (for example,
   UAE/KSA)? Retention period for message content?
10. **Deliverable.** Is a deployed URL expected, or a repository with Docker
    Compose plus a demo video?
