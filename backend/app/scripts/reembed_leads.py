"""Re-embed every lead's conversation with the configured OpenAI embedding model.

    uv run python -m app.scripts.reembed_leads

The demo seed gives its leads topic vectors instead of real embeddings (it runs without
OpenAI). A real lead's embedding can't be compared with those, so similar-lead search and
"Try a lead" find nothing until the demo leads are re-embedded. Also useful after changing
OPENAI_EMBEDDING_MODEL. Idempotent; costs one embedding call per lead.
"""

import asyncio

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.runtime import use_selector_event_loop_on_windows
from app.db.session import SessionFactory, engine
from app.integrations.openai_llm import OpenAIEmbedder, embedding_text
from app.models import Contact, Lead
from app.models.enums import MessageDirection
from app.repositories.leads import MessageRepository
from app.schemas.analysis import LeadContext
from app.services.lead_pipeline import conversation_message

logger = get_logger(__name__)


async def reembed() -> int:
    settings = get_settings()
    if settings.openai_api_key is None:
        raise SystemExit("OPENAI_API_KEY is not set")
    embedder = OpenAIEmbedder(settings)
    done = 0
    async with SessionFactory() as session:
        rows = (await session.execute(select(Lead, Contact).join(Contact))).all()
        messages = MessageRepository(session)
        for lead, contact in rows:
            inbound = [
                m
                for m in await messages.for_lead(lead.id)
                if m.direction == MessageDirection.INBOUND
            ]
            if not inbound:
                continue
            context = LeadContext(
                channel=lead.source,
                sender_name=contact.name,
                sender_address=inbound[-1].sender_address,
                company_domain=contact.company_domain,
                messages=tuple(conversation_message(m) for m in inbound),
            )
            lead.embedding = await embedder.embed(embedding_text(context))
            await session.commit()
            done += 1
            logger.info("lead_reembedded", lead_id=str(lead.id))
    await engine.dispose()
    return done


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.is_production)
    use_selector_event_loop_on_windows()
    print(f"Re-embedded {asyncio.run(reembed())} leads")


if __name__ == "__main__":
    main()
