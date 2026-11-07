# Classifier evaluation

How well the lead classifier does on the labelled set (`backend/tests/fixtures/leads/cases.json`). It runs the real OpenAI model with the production prompt; scores use the default settings and no contact history. Regenerate with `uv run python -m tests.live.evaluation` (in `backend/`).

- **Run:** 2026-11-07 10:50 UTC · model `gpt-6-luna` · prompt `2026-11-06.1`
- **Set:** 53 leads: 13 ar, 40 en; 37 email, 16 whatsapp

## Headline

| Measure | Result |
|---------|--------|
| Category accuracy | **98%** (52/53) |
| Accuracy, `ar` | 100% (13/13) |
| Accuracy, `en` | 98% (39/40) |
| Language detected correctly | 100% (53/53) |
| Score in the expected band | 100% (53/53) |
| Spam caught | 100% (10/10) |
| Prompt injections resisted | 2/2 |
| Median time per lead | 3.2 s |

## Per category

| Category | Precision | Recall | F1 | Leads |
|----------|-----------|--------|----|-------|
| Sales opportunity | 100% | 100% | 1.00 | 10 |
| Demo request | 100% | 100% | 1.00 | 9 |
| Partnership | 100% | 100% | 1.00 | 8 |
| Support | 89% | 100% | 0.94 | 8 |
| General | 100% | 88% | 0.93 | 8 |
| Spam | 100% | 100% | 1.00 | 10 |

## Confusion matrix

Rows are the labelled category, columns what the model chose.

| Labelled ↓ / chosen → | Sales opportunity | Demo request | Partnership | Support | General | Spam |
|---|---|---|---|---|---|---|
| **Sales opportunity** | 10 | · | · | · | · | · |
| **Demo request** | · | 9 | · | · | · | · |
| **Partnership** | · | · | 8 | · | · | · |
| **Support** | · | · | · | 8 | · | · |
| **General** | · | · | · | 1 | 7 | · |
| **Spam** | · | · | · | · | · | 10 |

## Misses

| Lead | Labelled | Chosen | Score (band) | Model's reasoning |
|---|---|---|---|---|
| `general-newsletter-en` | General | Support | 4 (0 to 40) | This is an administrative request from someone asking to unsubscribe, so it fits a support request rather than a sales inquiry. There is no buying intent, timeline pressure, or commercial value indicated. |

## Reading this

- The set is small and hand-labelled, so treat one miss as a prompt to read the case, not a trend. Some messages are honestly ambiguous (a partner who also wants pricing).
- Category accuracy is what routing depends on. The score band check is looser: it catches a hot lead scored cold or spam scored warm, not a few points either way.
- Every new labelled case should be added to `cases.json` and this report regenerated whenever the prompt (`PROMPT_VERSION`) or model changes.
