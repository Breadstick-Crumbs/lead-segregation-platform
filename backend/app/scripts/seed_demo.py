"""Demo leads for the dashboard: realistic GCC enquiries, run through the real pipeline.

    uv run python -m app.scripts.seed_demo

Run `app.scripts.seed` first (users and settings). Each lead goes through the real
ingestion, scoring, assignment and alerting code. Only the OpenAI calls are replaced: a
scripted classifier returns the analysis written below, and embeddings come from each lead's
topic, so "similar leads" group sensibly. Leads are then aged over the last two weeks and
some are moved on (contacted, converted, lost).

Stop the worker first (`docker compose stop worker`): it would race the script for the
leads' jobs. Jobs the seed leaves queued are removed, so a worker started afterwards doesn't
try to message the demo contacts.

Idempotent: if the demo contacts exist already, nothing is added (`--reset` removes them
first). Never run it in production.
"""

import asyncio
import hashlib
import math
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.runtime import use_selector_event_loop_on_windows
from app.db.session import SessionFactory, engine
from app.models import Contact, Lead, LeadEvent, Message
from app.models.enums import (
    Channel,
    DeliveryStatus,
    LeadCategory,
    LeadEventType,
    LeadStatus,
    MessageDirection,
)
from app.models.lead import EMBEDDING_DIMENSIONS
from app.schemas.analysis import LeadAnalysis, LeadContext
from app.schemas.app_settings import ScoringThresholds
from app.schemas.inbound import InboundMessage
from app.services.auto_reply import wants_auto_reply
from app.services.ingestion import IngestionService
from app.services.lead_pipeline import LeadPipeline
from app.services.notifications import NotificationService, alert_for_priority

logger = get_logger(__name__)

DEMO_MARKER = "demo-seed"  # in every demo email's Message-ID, to recognise them


@dataclass(frozen=True)
class DemoLead:
    channel: Channel
    name: str
    address: str  # email or +E.164
    subject: str | None
    body: str
    category: LeadCategory
    intent: int
    urgency: int
    value: int
    summary: str
    rationale: str
    topic: str  # leads sharing a topic embed close together
    days_ago: float
    company: str | None = None
    title: str | None = None
    language: str = "en"
    status: LeadStatus = LeadStatus.NEW
    replied: bool = False
    extra: tuple[str, ...] = field(default_factory=tuple)  # follow-up messages


LEADS: tuple[DemoLead, ...] = (
    DemoLead(
        Channel.EMAIL,
        "Omar Haddad",
        "omar.haddad@gulfbuild.ae",
        "Quotation for 40 user licences",
        "Hello,\n\nWe are evaluating your platform for our sales team of 40 people across Dubai "
        "and Abu Dhabi. Could you send a quotation for annual licences including onboarding? "
        "We need to decide before the end of the month.\n\nBest regards,\nOmar Haddad\n"
        "Procurement Manager, Gulf Build LLC",
        LeadCategory.SALES_OPPORTUNITY,
        88,
        70,
        75,
        "Quote for 40 annual licences with onboarding, decision this month",
        "Explicit quotation request for a 40-seat team with a month-end deadline.",
        "licences",
        0.1,
        "Gulf Build LLC",
        "Procurement Manager",
    ),
    DemoLead(
        Channel.EMAIL,
        "Tenders Office",
        "tenders@emiratesrail.ae",
        "RFP 2026-114: CRM and lead management",
        "Emirates Rail invites your company to respond to RFP 2026-114 for a lead management "
        "system covering 6 regional offices. Submissions close next Thursday. Tender documents "
        "are attached. Urgent: please confirm participation.",
        LeadCategory.SALES_OPPORTUNITY,
        92,
        90,
        95,
        "RFP for lead management across 6 regional offices, closes next Thursday",
        "Formal tender with a near deadline and enterprise scope.",
        "tender",
        0.3,
        "Emirates Rail",
    ),
    DemoLead(
        Channel.WHATSAPP,
        "فاطمة المنصوري",
        "+971501234567",
        None,
        "مرحبا، نحتاج عرض سعر لنظامكم لفريق مبيعات من 15 شخص. "
        "هل يمكن إرسال الأسعار اليوم؟ الأمر عاجل.",
        LeadCategory.SALES_OPPORTUNITY,
        80,
        85,
        55,
        "Needs pricing today for a 15-person sales team",
        "Urgent pricing request in Arabic for a small team.",
        "pricing",
        0.05,
        language="ar",
    ),
    DemoLead(
        Channel.EMAIL,
        "Lina Saeed",
        "lina.saeed@brightclinics.com",
        "Demo request",
        "Hi, I run operations at Bright Clinics (12 branches). Could we book a 30-minute demo "
        "next week to see how lead routing works?\n\nLina Saeed\nHead of Operations",
        LeadCategory.DEMO_REQUEST,
        75,
        55,
        70,
        "Demo next week for a 12-branch clinic group",
        "Concrete demo request with a clear operational need.",
        "demo",
        1.2,
        "Bright Clinics",
        "Head of Operations",
        status=LeadStatus.CONTACTED,
        replied=True,
    ),
    DemoLead(
        Channel.EMAIL,
        "خالد الهاشمي",
        "k.alhashimi@alnoor-group.com",
        "طلب عرض تجريبي",
        "السلام عليكم، نرغب في حجز عرض تجريبي للمنصة لفريقنا في دبي وأبوظبي خلال هذا الأسبوع. "
        "مع التحية، خالد الهاشمي، مدير المبيعات، مجموعة النور",
        LeadCategory.DEMO_REQUEST,
        78,
        70,
        72,
        "Demo this week for the Dubai and Abu Dhabi sales teams",
        "Arabic demo request from a sales director with a timeline.",
        "demo",
        0.6,
        "Al Noor Group",
        "Sales Director",
        language="ar",
    ),
    DemoLead(
        Channel.WHATSAPP,
        "Raj Menon",
        "+971559876543",
        None,
        "hey can i get a free trial or a quick walkthrough of the dashboard?",
        LeadCategory.DEMO_REQUEST,
        50,
        25,
        30,
        "Asks for a free trial or a quick walkthrough",
        "Interested but vague, no company or timeline.",
        "demo",
        2.5,
    ),
    DemoLead(
        Channel.EMAIL,
        "Ahmed Al Qahtani",
        "partners@techdistri.sa",
        "Reseller partnership in KSA",
        "We are a software distributor with 200 SMB customers in Saudi Arabia. We would like to "
        "discuss a reseller partnership for your product, including margins and co-marketing.",
        LeadCategory.PARTNERSHIP_INQUIRY,
        65,
        30,
        70,
        "KSA distributor with 200 SMB customers proposes reselling",
        "Credible channel partner with a sizeable customer base.",
        "partner",
        3.0,
        "TechDistri Arabia",
    ),
    DemoLead(
        Channel.EMAIL,
        "Maya Chen",
        "bd@sendflow.io",
        "Integration partnership",
        "Hi team, SendFlow is an SMS gateway used across the GCC. We'd love to explore a native "
        "integration and a joint webinar. Who should I talk to?",
        LeadCategory.PARTNERSHIP_INQUIRY,
        45,
        20,
        40,
        "SMS gateway proposes an integration and joint webinar",
        "Partnership interest without commercial commitment.",
        "partner",
        5.0,
        "SendFlow",
    ),
    DemoLead(
        Channel.EMAIL,
        "Sami Darwish",
        "it@gulfbuild.ae",
        "Cannot log in since this morning",
        "Our account users are getting an error when they sign in since this morning. Please "
        "help urgently, ticket #4471.",
        LeadCategory.SUPPORT_REQUEST,
        20,
        90,
        30,
        "Existing customer can't sign in since this morning (ticket #4471)",
        "Support issue from a customer account; urgent but not a sales lead.",
        "support",
        0.4,
        "Gulf Build LLC",
    ),
    DemoLead(
        Channel.WHATSAPP,
        "Noura Al Suwaidi",
        "+971502223333",
        None,
        "عندي مشكلة في الفاتورة الأخيرة، تم خصم المبلغ مرتين من حسابنا.",
        LeadCategory.SUPPORT_REQUEST,
        10,
        60,
        20,
        "Billing problem: last invoice charged twice",
        "Billing complaint in Arabic from an existing customer.",
        "support",
        1.8,
        language="ar",
    ),
    DemoLead(
        Channel.EMAIL,
        "Sara Mahmoud",
        "sara.m.cs@gmail.com",
        "Internship",
        "Hello, I'm a final-year computer science student. Are you offering any internships this "
        "summer?",
        LeadCategory.GENERAL_INQUIRY,
        10,
        15,
        5,
        "Student asking about summer internships",
        "General enquiry with no commercial value.",
        "general",
        4.0,
    ),
    DemoLead(
        Channel.WHATSAPP,
        "Ali Rahman",
        "+971504445555",
        None,
        "What are your office hours and where is your office located?",
        LeadCategory.GENERAL_INQUIRY,
        15,
        10,
        10,
        "Asks for office hours and location",
        "Simple information request.",
        "general",
        6.0,
    ),
    DemoLead(
        Channel.EMAIL,
        "Rank Boost",
        "growth@rank-boost-pro.biz",
        "Get 1st page on Google GUARANTEED",
        "Dear website owner, we noticed your site is not ranking. Our SEO experts guarantee page "
        "1 in 30 days. Special price $99 only, reply YES to start!!!",
        LeadCategory.SPAM,
        0,
        10,
        0,
        "Unsolicited SEO offer",
        "Mass marketing with no relation to our product.",
        "spam",
        2.0,
    ),
    DemoLead(
        Channel.EMAIL,
        "Hessa Al Mazrouei",
        "hessa@desertrose.ae",
        "Pricing for enterprise plan",
        "Good morning, Desert Rose Trading would like pricing for your enterprise plan for about "
        "120 users, including SSO with Microsoft. Budget is approved for this quarter.\n\n"
        "Hessa Al Mazrouei\nIT Director",
        LeadCategory.SALES_OPPORTUNITY,
        90,
        60,
        90,
        "Enterprise pricing for ~120 users with Microsoft SSO, budget approved",
        "Budgeted enterprise purchase with a clear requirement.",
        "licences",
        9.0,
        "Desert Rose Trading",
        "IT Director",
        status=LeadStatus.CONVERTED,
        replied=True,
    ),
    DemoLead(
        Channel.EMAIL,
        "Yousef Karim",
        "yousef@karimlogistics.com",
        "Quote for 25 seats",
        "Hi, could you share a quote for 25 seats? We're comparing three vendors this month.",
        LeadCategory.SALES_OPPORTUNITY,
        72,
        50,
        55,
        "Quote for 25 seats while comparing three vendors",
        "Active evaluation with a moderate deal size.",
        "licences",
        11.0,
        "Karim Logistics",
        status=LeadStatus.LOST,
        replied=True,
    ),
    DemoLead(
        Channel.WHATSAPP,
        "Mariam Al Kaabi",
        "+971506667777",
        None,
        "Hi, we saw your demo at GITEX. Can someone call me today about pricing for our "
        "retail chain? 18 stores.",
        LeadCategory.SALES_OPPORTUNITY,
        85,
        80,
        70,
        "Met at GITEX; wants a call today about pricing for 18 stores",
        "Warm event lead with urgency and a multi-site deal.",
        "pricing",
        0.8,
        "Kaabi Retail",
    ),
    DemoLead(
        Channel.EMAIL,
        "Faisal Al Harbi",
        "faisal@harbi-holding.sa",
        "Proposal request",
        "Please send a proposal for 60 users across our three companies. We would like to start "
        "in January.\n\nFaisal Al Harbi\nCOO, Al Harbi Holding",
        LeadCategory.SALES_OPPORTUNITY,
        82,
        40,
        80,
        "Proposal for 60 users across three group companies, January start",
        "Group-level proposal request from a COO.",
        "licences",
        13.0,
        "Al Harbi Holding",
        "COO",
        status=LeadStatus.QUALIFIED,
        replied=True,
    ),
)


def _topic_vector(topic: str, jitter: str) -> list[float]:
    """A unit vector near the topic's direction, nudged per lead, so neighbours make sense."""

    def bucket(seed: str) -> int:
        return int(hashlib.sha256(seed.encode()).hexdigest(), 16) % EMBEDDING_DIMENSIONS

    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[bucket(topic)] = 1.0
    vector[bucket(jitter)] += 0.35
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector]


class ScriptedAnalyzer:
    model = "demo-scripted"

    def __init__(self, lead: DemoLead) -> None:
        self._lead = lead

    async def analyze(self, context: LeadContext) -> LeadAnalysis:
        d = self._lead
        return LeadAnalysis(
            category=d.category,
            category_confidence=0.9,
            language=d.language,
            intent=d.intent,
            urgency=d.urgency,
            business_value=d.value,
            company_name=d.company,
            job_title=d.title,
            summary=d.summary,
            rationale=d.rationale,
        )


class TopicEmbedder:
    def __init__(self, lead: DemoLead) -> None:
        self._lead = lead

    async def embed(self, text: str) -> list[float]:
        return _topic_vector(self._lead.topic, self._lead.address)


async def _seed_one(session: AsyncSession, demo: DemoLead) -> None:
    received = datetime.now(UTC) - timedelta(days=demo.days_ago)
    is_email = demo.channel == Channel.EMAIL
    result = await IngestionService(session).ingest(
        InboundMessage(
            channel=demo.channel,
            external_id=(
                f"<{uuid.uuid4()}.{DEMO_MARKER}@demo>"
                if is_email
                else f"wamid.{DEMO_MARKER}.{uuid.uuid4()}"
            ),
            sender_address=demo.address,
            sender_name=demo.name,
            subject=demo.subject,
            body_text=demo.body,
            occurred_at=received,
        )
    )
    if result.lead_id is None:
        return
    lead_id = result.lead_id
    await LeadPipeline(session, ScriptedAnalyzer(demo), TopicEmbedder(demo)).process(
        lead_id, final_attempt=True
    )

    # The acknowledgement the worker would have sent, shown in the thread.
    lead = await session.get(Lead, lead_id)
    if lead is not None and wants_auto_reply(lead.score, lead.category, ScoringThresholds()):
        session.add(
            Message(
                lead_id=lead_id,
                channel=demo.channel,
                direction=MessageDirection.OUTBOUND,
                external_id=f"<ack.{uuid.uuid4()}.{DEMO_MARKER}@demo>"
                if is_email
                else f"wamid.ack.{uuid.uuid4()}",
                thread_ref=None,
                sender_address="sales@tawasol.test" if is_email else "whatsapp",
                subject=f"Re: {demo.subject}" if demo.subject else None,
                body_text=(
                    "شكرًا لتواصلك معنا. لقد استلمنا استفسارك وسيتواصل معك أحد أعضاء فريقنا قريبًا."
                    if demo.language == "ar"
                    else "Thank you for contacting us. We have received your inquiry and a member "
                    "of our team will get in touch with you shortly."
                ),
                is_auto_reply=True,
                delivery_status=DeliveryStatus.DELIVERED if not is_email else DeliveryStatus.SENT,
                occurred_at=received + timedelta(minutes=1),
            )
        )
        session.add(
            LeadEvent(lead_id=lead_id, type=LeadEventType.AUTO_REPLIED, payload={"status": "sent"})
        )

    # Alerts, as the worker would raise them (in-app only: no deliveries are queued here).
    kind = alert_for_priority(lead.priority) if lead is not None else None
    await session.commit()
    if kind is not None:
        await NotificationService(
            session,
            get_settings().model_copy(
                update={"slack_webhook_url": None, "teams_webhook_url": None}
            ),
        ).raise_alert(lead_id, kind)

    # Age the lead and move it along its story.
    values: dict[str, object] = {"created_at": received, "updated_at": received}
    if demo.status != LeadStatus.NEW:
        values.update(status=demo.status, first_response_at=received + timedelta(minutes=35))
    if demo.status == LeadStatus.CONVERTED:
        values["converted_at"] = received + timedelta(days=4)
    await session.execute(update(Lead).where(Lead.id == lead_id).values(**values))
    await session.execute(
        update(LeadEvent)
        .where(LeadEvent.lead_id == lead_id)
        .values(created_at=received + timedelta(seconds=5))
    )
    await session.commit()


WORKER_ALIVE_SECONDS = 60


async def _worker_running(session: AsyncSession) -> bool:
    alive = await session.scalar(
        text(
            "SELECT count(*) FROM procrastinate_workers"
            " WHERE last_heartbeat > now() - make_interval(secs => :secs)"
        ),
        {"secs": WORKER_ALIVE_SECONDS},
    )
    return bool(alive)


def _demo_contacts() -> list[str]:
    return [d.address for d in LEADS]


async def _reset(session: AsyncSession) -> None:
    contacts = select(Contact.id).where(
        func.coalesce(Contact.email, Contact.phone_e164).in_(_demo_contacts())
    )
    await session.execute(delete(Lead).where(Lead.contact_id.in_(contacts)))
    await session.execute(
        delete(Contact).where(
            func.coalesce(Contact.email, Contact.phone_e164).in_(_demo_contacts())
        )
    )
    await session.commit()


async def _drop_demo_jobs(session: AsyncSession) -> None:
    """Queued work for demo leads (sends, alerts): never let a worker act on it."""
    await session.execute(
        text(
            "DELETE FROM procrastinate_jobs WHERE status = 'todo' AND args->>'lead_id' IN"
            " (SELECT l.id::text FROM leads l JOIN contacts c ON c.id = l.contact_id"
            "  WHERE coalesce(c.email, c.phone_e164) = ANY(:addresses))"
        ),
        {"addresses": _demo_contacts()},
    )
    await session.commit()


async def seed_demo(*, reset: bool) -> int:
    async with SessionFactory() as session:
        if await _worker_running(session):
            raise SystemExit("Stop the worker first (docker compose stop worker), then re-run.")
        if reset:
            await _reset(session)
        existing = await session.scalar(
            select(Contact.id).where(Contact.email == LEADS[0].address).limit(1)
        )
        if existing is not None:
            logger.info("demo_already_seeded")
            return 0
        for demo in LEADS:
            await _seed_one(session, demo)
        await _drop_demo_jobs(session)
    await engine.dispose()
    return len(LEADS)


def main() -> None:
    settings = get_settings()
    if settings.is_production:
        raise SystemExit("Refusing to seed demo data in production")
    configure_logging(settings.log_level, json_logs=False)
    use_selector_event_loop_on_windows()
    count = asyncio.run(seed_demo(reset="--reset" in sys.argv))
    print(f"Seeded {count} demo leads")


if __name__ == "__main__":
    main()
