"""Task names, so services can enqueue work without importing the task modules (which import
the services: that would be a cycle)."""

PROCESS_WHATSAPP_WEBHOOK = "process_whatsapp_webhook"
TRANSCRIBE_MESSAGE = "transcribe_message"
SWEEP_PENDING_LEADS = "sweep_pending_leads"
PROCESS_LEAD = "process_lead"
POLL_MAILBOX = "poll_mailbox"
POLL_ALL_MAILBOXES = "poll_all_mailboxes"
PRUNE_HISTORY = "prune_history"
