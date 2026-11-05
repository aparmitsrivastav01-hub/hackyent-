import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.database import Base
from app.models import EmailDelivery, EmailDeliveryItem, Opportunity, OpportunityMatch, ResearchRun
from app.schemas import ExtractedOpportunity, ExtractionResult
from app.services import email


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session, factory
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def configure_email(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "resend_api_key", "unit-test-secret")
    monkeypatch.setattr(settings, "email_from", "OpportunityOS <alerts@example.com>")
    monkeypatch.setattr(settings, "email_to", "student@example.com")


def add_opportunity(db, title="AI Build Weekend", kind="hackathon"):
    item = Opportunity(
        title=title, normalized_title=title.lower(), opportunity_type=kind,
        organization="Open Lab", normalized_organization="open lab",
        source_url=f"https://example.org/{title.lower().replace(' ', '-')}",
        application_url="https://example.org/apply", summary="Build useful AI tools.",
        eligibility="Students", location="Online", status="open", raw_data={},
        deadline=datetime(2026, 12, 1, tzinfo=timezone.utc),
        prize_pool="$2,000" if kind == "hackathon" else None,
        stipend="$1,000/month" if kind == "internship" else None,
    )
    item.match = OpportunityMatch(overall_score=87, skill_score=80, eligibility_score=90, location_score=90, experience_score=80, explanation="Good fit")
    db.add(item)
    db.commit()
    return item


def test_email_configuration_reports_missing_and_validates_addresses(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "resend_api_key", "")
    monkeypatch.setattr(settings, "email_from", "not an address")
    monkeypatch.setattr(settings, "email_to", "")
    _, missing = email.email_configuration()
    assert "RESEND_API_KEY" in missing
    assert any(value.startswith("EMAIL_FROM") for value in missing)
    assert any(value.startswith("EMAIL_TO") for value in missing)
    monkeypatch.setattr(settings, "resend_api_key", "test")
    monkeypatch.setattr(settings, "email_from", "OpportunityOS <alerts@example.com>")
    monkeypatch.setattr(settings, "email_to", "student@example.com")
    _, missing = email.email_configuration()
    assert missing == []


def test_template_has_separate_sections_details_links_and_empty_fallback(db_session):
    db, _ = db_session
    add_opportunity(db, "AI Build Weekend", "hackathon")
    add_opportunity(db, "Python Intern", "internship")
    entries = list(db.scalars(select(Opportunity).order_by(Opportunity.id)).all())
    html_body, text_body = email.render_digest(entries)
    assert "Hackathons" in html_body and "Internships" in html_body
    assert "AI Build Weekend" in html_body and "Python Intern" in text_body
    assert "Open Lab" in text_body and "Students" in text_body
    assert "87%" in text_body and "$2,000" in text_body and "$1,000/month" in text_body
    assert "https://example.org/apply" in text_body and "Register" in html_body and "Apply" in html_body
    empty_html, empty_text = email.render_digest([])
    assert "No new opportunities" in empty_html
    assert "No new opportunities" in empty_text


def test_sent_opportunities_are_not_included_again_and_delivery_persists(db_session, monkeypatch):
    configure_email(monkeypatch)
    db, _ = db_session
    add_opportunity(db)
    sent_payloads = []

    async def fake_post(payload, api_key, client=None):
        sent_payloads.append(payload)
        return {"id": "re_mock_123"}

    monkeypatch.setattr(email, "_post_resend", fake_post)
    async def run():
        first = await email.send_digest(db)
        second = await email.send_digest(db)
        return first, second

    first, second = asyncio.run(run())
    assert first.status == "sent" and first.provider_message_id == "re_mock_123"
    assert first.sent_at is not None and first.opportunity_count == 1
    assert second is None
    assert len(sent_payloads) == 1
    assert "AI Build Weekend" in sent_payloads[0]["html"]
    assert db.scalar(func_count_delivery_items()) == 1


def test_meaningfully_updated_opportunity_can_be_included_in_a_new_digest(db_session, monkeypatch):
    configure_email(monkeypatch)
    db, _ = db_session
    opportunity = add_opportunity(db)
    async def fake_post(payload, api_key, client=None):
        return {"id": "re_versioned"}
    monkeypatch.setattr(email, "_post_resend", fake_post)
    asyncio.run(email.send_digest(db))
    opportunity.deadline = datetime(2026, 12, 15, tzinfo=timezone.utc)
    opportunity.notification_version += 1
    db.commit()
    pending = email._pending_opportunities(db)
    assert [item.id for item in pending] == [opportunity.id]


def func_count_delivery_items():
    from sqlalchemy import func
    return select(func.count(EmailDeliveryItem.id))


def test_empty_manual_digest_and_test_email_are_recorded(db_session, monkeypatch):
    configure_email(monkeypatch)
    db, _ = db_session
    payloads = []

    async def fake_post(payload, api_key, client=None):
        payloads.append(payload)
        return {"id": "re_empty"}

    monkeypatch.setattr(email, "_post_resend", fake_post)
    async def run():
        empty = await email.send_digest(db, allow_empty=True)
        test = await email.send_digest(db, is_test=True)
        return empty, test

    empty, test = asyncio.run(run())
    assert empty.status == "sent" and empty.opportunity_count == 0
    assert "No new opportunities" in payloads[0]["text"]
    assert test.status == "sent" and test.is_test
    assert db.query(EmailDelivery).count() == 2


def test_provider_failure_is_persisted_and_failure_does_not_mark_opportunity_sent(db_session, monkeypatch):
    configure_email(monkeypatch)
    db, _ = db_session
    add_opportunity(db)
    attempts = []

    async def failing_post(payload, api_key, client=None):
        attempts.append(payload)
        raise email.EmailDeliveryError("Email provider rejected the message (HTTP 400).")

    monkeypatch.setattr(email, "_post_resend", failing_post)
    failed = asyncio.run(email.send_digest(db))
    assert failed.status == "failed"
    assert failed.sent_at is None
    assert "HTTP 400" in failed.error
    assert len(email._pending_opportunities(db)) == 1
    assert db.scalar(select(EmailDelivery).where(EmailDelivery.id == failed.id)).status == "failed"


def test_resend_retries_rate_limits_then_succeeds():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429 if len(calls) < 2 else 200, json={"id": "re_ok"}, request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await email._post_resend({"subject": "test"}, "hidden-key", client)

    assert asyncio.run(run()) == {"id": "re_ok"}
    assert len(calls) == 2
    assert "hidden-key" not in repr(calls[0].url)


def test_discovery_saves_opportunity_then_sends_digest(db_session, monkeypatch):
    configure_email(monkeypatch)
    _, factory = db_session
    from app import discovery

    class FakeTinyFish:
        def __init__(self, key): pass
        async def search(self, query, domains=None):
            return {"results": [{"url": "https://devfolio.co/hackathons/verified-build", "title": "Verified Build", "snippet": "AI hackathon"}]}
        async def fetch(self, urls):
            return {"results": [{"url": url, "title": "Verified Build", "text": "Verified Build hackathon. Apply at https://devfolio.co/apply. Deadline Dec 1, 2026.", "links": ["https://devfolio.co/apply"]} for url in urls], "errors": []}

    class FakeGemini:
        def __init__(self, key, model): pass
        async def extract(self, page, kind):
            item = ExtractedOpportunity(title="Verified Build", opportunity_type=kind, organization="Open Lab", summary="Build an AI project", source_url=page["url"], application_url="https://devfolio.co/apply", eligibility="Students", status="open", evidence=[])
            return ExtractionResult(opportunities=[item])

    deliveries = []
    async def fake_post(payload, api_key, client=None):
        deliveries.append(payload)
        return {"id": "re_pipeline"}

    monkeypatch.setattr(discovery, "SessionLocal", factory)
    monkeypatch.setattr(discovery, "TinyFishClient", FakeTinyFish)
    monkeypatch.setattr(discovery, "GeminiIntelligence", FakeGemini)
    monkeypatch.setattr(email, "_post_resend", fake_post)
    db = factory()
    run = ResearchRun(opportunity_type="hackathon")
    db.add(run)
    db.commit()
    run_id = run.id
    db.close()

    asyncio.run(discovery.execute_discovery(run_id, "hackathon", ["Online"], max_pages=4))

    check = factory()
    try:
        saved = check.scalars(select(Opportunity)).all()
        run_record = check.get(ResearchRun, run_id)
        delivery = check.scalar(select(EmailDelivery))
        assert len(saved) == 1 and saved[0].application_url == "https://devfolio.co/apply"
        assert run_record.records_saved == 1
        assert delivery.status == "sent" and delivery.opportunity_count == 1
        assert "Verified Build" in deliveries[0]["text"]
    finally:
        check.close()
