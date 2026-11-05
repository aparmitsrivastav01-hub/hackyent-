from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Opportunity, OpportunityMatch


def test_automation_next_run_is_noon_ist():
    from app.main import next_ist_run
    from zoneinfo import ZoneInfo
    next_run = next_ist_run()
    assert next_run.hour == 12 and next_run.minute == 0
    assert next_run.tzinfo == ZoneInfo("Asia/Kolkata")


def test_api_profile_and_opportunity_persistence():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    def override_db():
        session = TestingSession()
        try: yield session
        finally: session.close()
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            profile = client.get("/api/profile")
            assert profile.status_code == 200
            updated = {**profile.json(), "preferred_domains": ["AI Agents"]}
            assert client.put("/api/profile", json=updated).status_code == 200
            with TestingSession() as session:
                row = Opportunity(title="Verified Build Night", normalized_title="verified build night", opportunity_type="hackathon", organization="Open Lab", normalized_organization="open lab", source_url="https://events.example.org/build-night", status="unknown", raw_data={}, required_skills=["Python"], themes=["AI agents"], team_size="2-4")
                row.match = OpportunityMatch(overall_score=82, skill_score=80, eligibility_score=70, location_score=90, experience_score=80, explanation="Strong Python fit.")
                session.add(row); session.commit()
            listing = client.get("/api/opportunities?min_match_score=80")
            assert listing.status_code == 200
            assert listing.json()["total"] == 1
            assert listing.json()["items"][0]["title"] == "Verified Build Night"
            assert listing.json()["items"][0]["themes"] == ["AI agents"]
            assert listing.json()["items"][0]["team_size"] == "2-4"
            row_id = listing.json()["items"][0]["id"]
            reviewed = client.patch(f"/api/opportunities/{row_id}/review", json={"decision": "reviewed"})
            assert reviewed.json()["review_status"] == "reviewed"
            updated["preferred_technologies"] = ["Python"]
            client.put("/api/profile", json=updated)
            assert client.get(f"/api/opportunities/{row_id}/match").json()["skill_score"] == 100
            assert client.get("/api/dashboard/stats").json()["total_opportunities"] == 1
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_email_status_and_missing_configuration_endpoints_do_not_expose_secrets(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "resend_api_key", "private-test-token")
    monkeypatch.setattr(settings, "email_from", "")
    monkeypatch.setattr(settings, "email_to", "")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            status = client.get("/api/email/status")
            assert status.status_code == 200
            assert status.json()["configured"] is False
            assert status.json()["recipient"] is None
            assert "private-test-token" not in status.text
            response = client.post("/api/email/test")
            assert response.status_code == 503
            assert "RESEND_API_KEY" not in response.text
            assert "private-test-token" not in response.text
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()
