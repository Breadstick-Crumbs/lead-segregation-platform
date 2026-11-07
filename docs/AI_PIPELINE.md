# AI pipeline

`process_lead` (worker job) turns a stored conversation into a category, a score, a
priority, an owner and an embedding. The rules for scoring are in `docs/SCORING.md`; this
page covers how a run works and what it guarantees.

## One run

```
1. read      snapshot: lead, contact, inbound messages (count = watermark)   → end transaction
2. call      OpenAI Responses API, structured output → LeadAnalysis
             OpenAI embeddings (1536 dims)
3. write     lock contact, then lead (same order as ingestion)
             still pending and the same inbound count?   no → SUPERSEDED, discard
             pgvector neighbours → history factor, duplicate flag
             hybrid score → lead_scores row (breakdown, model, prompt_version)
             lead: category, score, priority, language, summary, embedding → processed
             contact: company name if unknown
             round-robin owner if unassigned (not spam)
             events: classified, scored, assigned
```

No transaction stays open while OpenAI is called. The client has a 60 s timeout and one
SDK retry, so a run is bounded well under the sweep's 10-minute grace period.

## Guarantees

| Concern | How |
|---------|-----|
| Follow-up during a run | Ingestion reopens the lead with an explicit `UPDATE`, which takes the row lock: it waits for a run that is saving and then sets `pending` again. A run that sees a newer message discards its result; the follow-up's own job sees the whole conversation. |
| Duplicate work | One waiting job per lead (`queueing_lock`). The sweep skips leads with a job queued or running. |
| Retries | Rate limits, timeouts, 408/409/429 and 5xx retry with backoff (5 runs). A retry steps aside if a newer job for the lead is already queued, so it never clashes with that job's queueing lock. |
| Giving up | On the last attempt, or a permanent error (no API key, 4xx), the lead becomes `failed` with `processing_error` and a `processing_failed` event. Errors in the write step are handled the same way. A failure never hides a follow-up that arrived meanwhile. |
| Stranded leads | Every 5 minutes, `sweep_pending_leads` queues open `pending` leads with no job, not updated for 10 minutes, and with no voice note still being transcribed. This also backfills leads ingested before the pipeline existed. First, `process_lead` jobs whose worker stopped sending heartbeats for 5 minutes (it crashed mid-run) are marked failed, so their leads aren't skipped forever. |
| Deadlocks | The write step locks the contact before the lead, like ingestion. |
| Concurrent assignment | Serialised by an advisory lock, so two leads processed together go to different reps. |
| Prompt injection | Lead text is only ever inside `<message>` tags that it can't close; sender name, address and subject are collapsed to one line, so they can't add fact lines. The output must match the schema, and the prompt tells the model to treat instructions in messages as a spam signal. The labelled set includes an injection case. |
| Reproducibility | `PROMPT_VERSION` and the model are stored on every `lead_scores` row. |

## Evaluating the classifier

`backend/tests/fixtures/leads/cases.json` holds 53 labelled leads: every category,
English and Arabic, email and WhatsApp, and two prompt-injection attempts. With
`OPENAI_API_KEY` set, run in `backend/`:

```bash
uv run python -m tests.live.evaluation   # writes docs/CLASSIFIER_EVAL.md
uv run pytest -m live_llm -s             # the same run as a pass/fail check
```

The report has accuracy overall and per language, per-category precision and recall, a
confusion matrix, language detection, scores against their expected bands, spam and
injection results, the time per lead, and every miss with the model's reasoning. The
latest results are in [CLASSIFIER_EVAL.md](CLASSIFIER_EVAL.md): 98% category accuracy
(52/53). The pytest check fails below 85% accuracy or 85% of scores in band, or if an
injection works. Run both after any prompt or model change and bump `PROMPT_VERSION`.

## Trying a message (dry run)

**Try a lead** in the dashboard (`POST /api/v1/leads/preview`) runs a typed message
through the same code: the classifier, embedding, similar-lead search, scoring,
auto-reply template and alert rules. It stores, sends and assigns nothing. It is the one
place OpenAI is called inside a request, because a person is waiting for the answer. It
is limited to signed-in users and 10 runs a minute per address, and the OpenAI calls
finish before any database work starts (`app/services/lead_preview.py`).

## Configuration

| Setting | Where | Default |
|---------|-------|---------|
| `OPENAI_MODEL` | `.env` | `gpt-6-luna` |
| `OPENAI_EMBEDDING_MODEL` | `.env` | `text-embedding-3-small` (must support `dimensions`) |
| Weights, thresholds, keywords | `app_settings` (admin) | `docs/SCORING.md` |
| Similarity and duplicate thresholds | `app_settings` key `ai.similarity` | 0.75 / top 10 / 0.95 / 7 days |
