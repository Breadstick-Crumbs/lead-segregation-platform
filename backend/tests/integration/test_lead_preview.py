""" "Try a lead": the real pipeline's decisions on a typed message, with nothing stored or sent."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import LeadAI, get_lead_ai
from app.db.session import get_session
from app.integrations.openai_llm import AIError
from app.main import create_app
from app.models import Contact, Lead, LeadEvent, Message
from app.models.enums import LeadCategory
from tests.fakes.ai import FakeAnalyzer, FakeEmbedder, analysis
from tests.integration.conftest import CreateUser, bearer, login
from tests.integration.test_leads_api import _lead

pytestmark = pytest.mark.integration

URL = "/api/v1/leads/preview"
EMAIL_LEAD = {
    "channel": "email",
    "sender_name": "Omar Haddad",
    "sender_address": "omar@gulfbuild.ae",
    "subject": "Quotation for 40 seats",
    "text": "Hi, please send pricing for 40 seats. We want to start this month.",
}


@pytest.fixture
def app(db_session: AsyncSession) -> FastAPI:
    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session
    return app


def _use_ai(app: FastAPI, ai: LeadAI) -> None:
    app.dependency_overrides[get_lead_ai] = lambda: ai


@pytest.fixture
async def client(app: FastAPI, create_user: CreateUser) -> AsyncIterator[AsyncClient]:
    await create_user("rep@tawasol.test")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        c.headers.update(bearer(await login(c, "rep@tawasol.test")))
        yield c


async def _counts(session: AsyncSession) -> dict[str, Any]:
    counts: dict[str, Any] = {}
    for model in (Contact, Lead, Message, LeadEvent):
        counts[model.__tablename__] = await session.scalar(select(func.count()).select_from(model))
    counts["jobs"] = await session.scalar(text("SELECT count(*) FROM procrastinate_jobs"))
    return counts


async def test_a_message_is_classified_and_scored_without_storing_anything(
    app: FastAPI, client: AsyncClient, db_session: AsyncSession
) -> None:
    analyzer = FakeAnalyzer(analysis(intent=100, urgency=90, business_value=90))
    _use_ai(app, (analyzer, FakeEmbedder()))
    before = await _counts(db_session)

    response = await client.post(URL, json=EMAIL_LEAD)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["category"] == "sales_opportunity"
    assert body["priority"] in ("high", "critical")
    assert body["score"] == body["breakdown"]["weighted"]
    assert body["breakdown"]["keywords"]["matched"]  # "pricing" is a default keyword
    assert body["breakdown"]["company"]["signals"][0] == "corporate_domain"
    assert body["auto_reply"].startswith("Thank you for contacting us")  # the English template
    assert body["alert"] in ("high_priority_lead", "escalation")
    assert body["model"] == "fake-model"
    # The classifier saw the message as a lead's conversation.
    (context,) = analyzer.contexts
    assert context.company_domain == "gulfbuild.ae"
    assert context.messages[0].subject == "Quotation for 40 seats"
    assert await _counts(db_session) == before


async def test_spam_gets_no_reply_and_no_alert(app: FastAPI, client: AsyncClient) -> None:
    spam = analysis(category=LeadCategory.SPAM, intent=90, urgency=90, business_value=90)
    _use_ai(app, (FakeAnalyzer(spam), FakeEmbedder()))

    body = (await client.post(URL, json=EMAIL_LEAD)).json()

    assert body["score"] <= 5
    assert body["breakdown"]["cap"]["reason"] == "spam"
    assert body["auto_reply"] is None
    assert body["alert"] is None


async def test_similar_past_leads_are_found_with_pgvector(
    app: FastAPI, client: AsyncClient, db_session: AsyncSession
) -> None:
    past = await _lead(db_session, embedding_axis=0)
    await _lead(db_session, embedding_axis=7)  # unrelated
    _use_ai(app, (FakeAnalyzer(), FakeEmbedder(default=0)))

    body = (await client.post(URL, json=EMAIL_LEAD)).json()

    assert [s["id"] for s in body["similar"]] == [str(past)]
    assert body["similar"][0]["similarity"] == 1.0


async def test_without_openai_it_says_so(app: FastAPI, client: AsyncClient) -> None:
    _use_ai(app, (None, None))

    response = await client.post(URL, json=EMAIL_LEAD)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_not_configured"


async def test_an_ai_failure_is_reported_plainly(app: FastAPI, client: AsyncClient) -> None:
    failing = FakeAnalyzer(error=AIError("OpenAI returned HTTP 500", retryable=True))
    _use_ai(app, (failing, FakeEmbedder()))

    response = await client.post(URL, json=EMAIL_LEAD)

    assert response.status_code == 502
    assert response.json()["error"] == {
        "code": "ai_unavailable",
        "message": "The AI service couldn't process this: OpenAI returned HTTP 500",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"sender_address": "not-an-email"},
        {"channel": "whatsapp", "sender_address": "omar@gulfbuild.ae"},
        {"text": "   "},
        {"text": "x" * 4001},
    ],
)
async def test_bad_input_is_refused(
    app: FastAPI, client: AsyncClient, overrides: dict[str, str]
) -> None:
    _use_ai(app, (FakeAnalyzer(), FakeEmbedder()))

    response = await client.post(URL, json={**EMAIL_LEAD, **overrides})

    assert response.status_code == 422


async def test_a_whatsapp_message_has_no_subject_or_company_domain(
    app: FastAPI, client: AsyncClient
) -> None:
    analyzer = FakeAnalyzer()
    _use_ai(app, (analyzer, FakeEmbedder()))

    response = await client.post(
        URL,
        json={**EMAIL_LEAD, "channel": "whatsapp", "sender_address": "+971501234567"},
    )

    assert response.status_code == 200
    (context,) = analyzer.contexts
    assert (context.company_domain, context.messages[0].subject) == (None, None)


async def test_signing_in_is_required(app: FastAPI) -> None:
    _use_ai(app, (FakeAnalyzer(), FakeEmbedder()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.post(URL, json=EMAIL_LEAD)).status_code == 401
