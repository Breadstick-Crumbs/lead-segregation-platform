# Classification and scoring

## Classification

One OpenAI call per lead, with structured output validated against a Pydantic
model:

```python
class LeadAnalysis(BaseModel):
    category: Literal["sales_opportunity", "demo_request", "partnership_inquiry",
                      "support_request", "general_inquiry", "spam"]
    category_confidence: float          # 0..1
    language: str                       # ISO 639-1, drives auto-reply language
    intent: int                         # 0..100 how clearly they want to buy/engage
    urgency: int                        # 0..100 timeline pressure ("this week", "ASAP")
    business_value: int                 # 0..100 deal size / seats / budget signals
    company_name: str | None
    summary: str                        # one-line summary for the dashboard
    rationale: str                      # short explanation shown to sales
```

The prompt is versioned (`prompt_version` stored with each score) so results
stay reproducible and can be evaluated against a labelled fixture set in tests.
Message content is treated as untrusted input: the prompt delimits it and the
output schema is enforced, so instructions embedded in an email cannot change
the system's behaviour.

## Scoring (0–100)

Hybrid: the LLM judges the semantic signals, deterministic rules handle the
facts we can compute ourselves. Weights live in `settings` and are editable by
admins.

| Factor (spec) | Source | Default weight |
|---------------|--------|----------------|
| Intent | LLM `intent` | 25 |
| Urgency | LLM `urgency` | 15 |
| Potential business value | LLM `business_value` | 20 |
| Relevant keywords | Rules: configurable keyword lists (e.g. "pricing", "quote", "demo", "RFP", "contract") with per-keyword points, capped at 100 | 15 |
| Company information | Rules: corporate domain vs free webmail, company name detected, signature/title present | 15 |
| Previous interactions | Rules: prior leads/messages from this contact, prior conversions, replies to our outreach | 10 |

`score = round(Σ factor_value × weight / Σ weights)`

Category adjustments (applied after the weighted sum):

- `spam` → score capped at 5 (never auto-replied, never notified).
- `support_request` → capped at a configurable max (default 40): it gets a
  reply but isn't routed as a sales priority.

The full breakdown is stored in `lead_scores.breakdown`, for example
`{"intent": {"value": 90, "weight": 25}, ...}`.

## Thresholds and actions (defaults, admin-configurable)

| Score | Priority | Action |
|-------|----------|--------|
| 0–20 | low | stored only, no auto-reply (spec) |
| 21–59 | medium | auto-reply via the source channel |
| 60–79 | high | auto-reply + notify the assigned owner (in-app + email) |
| 80–100 | critical | auto-reply + notify + **escalate** (sales manager list + Slack/Teams) |

SLA escalation: a high or critical lead with no human response within N
minutes (default 60) is escalated again by a scheduled sweep.

## Auto-reply

- Sent only when `score > 20`, category ≠ spam, and no auto-reply exists yet for
  this lead (enforced by a DB unique constraint, so it is idempotent).
- Email replies keep the thread (`In-Reply-To`/`References`) and use
  `Re: <original subject>`.
- WhatsApp replies are free-form text, which is allowed inside Meta's 24-hour
  customer-service window that the inbound message opens.
- Template per language (English default, plus others as configured).
