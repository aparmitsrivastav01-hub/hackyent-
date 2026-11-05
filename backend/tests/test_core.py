import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.schemas import ExtractedOpportunity, ProfilePayload
from app.services.matching import is_duplicate, normalize, score_opportunity
from app.services.tinyfish import TinyFishClient, TinyFishError
from app.models import Opportunity
from app.discovery import _is_detail_page, classify_source, derive_status, select_pages
from app.services.gemini import GeminiIntelligence, GeminiQuotaError


def test_extracted_opportunity_rejects_invalid_urls_and_type():
    base = dict(title="AI Hackathon", opportunity_type="hackathon", source_url="https://devfolio.co/e/ai", work_mode="online")
    assert ExtractedOpportunity(**base).status == "unknown"
    with pytest.raises(Exception): ExtractedOpportunity(**{**base, "source_url": "no-url"})
    with pytest.raises(Exception): ExtractedOpportunity(**{**base, "opportunity_type": "job"})


def test_duplicate_detection_checks_url_title_org_and_normalization():
    candidate = Opportunity(title="AI for Bharat", normalized_title="ai for bharat", source_url="https://devfolio.co/e/1", normalized_organization="acme labs")
    assert normalize("AI for Bharat!") == "ai for bharat"
    assert is_duplicate("AI for Bharat", "Acme Labs", "https://other.site/post", [candidate])
    assert is_duplicate("Something", "Other", "https://devfolio.co/e/1/", [candidate])
    assert not is_duplicate("Different event", "Other", "https://other.site", [candidate])


def test_matching_scores_missing_fields_without_rejecting_opportunity():
    item = ExtractedOpportunity(title="Python AI Intern", opportunity_type="internship", source_url="https://company.com/jobs/1", required_skills=["Python", "FastAPI"])
    profile = ProfilePayload(preferred_domains=["Artificial Intelligence"], preferred_technologies=["Python", "FastAPI"], internship_preferences=["Remote", "India"])
    score = score_opportunity(item, profile)
    assert 0 <= score["overall_score"] <= 100
    assert score["skill_score"] > 0
    assert "uncertain" in score["explanation"] or "unverified" in score["explanation"]


def test_tinyfish_search_uses_documented_get_endpoint_and_auth():
    response = httpx.Response(200, json={"results": []}, request=httpx.Request("GET", TinyFishClient.SEARCH_URL))
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
    tinyfish = TinyFishClient("test-key", client=client)
    async def run():
        assert await tinyfish.search("AI internship", ["wellfound.com"]) == {"results": []}
        await client.aclose()
    asyncio.run(run())


def test_tinyfish_missing_key_fails_clearly():
    async def run():
        with pytest.raises(TinyFishError, match="TINYFISH_API_KEY"):
            await TinyFishClient("").search("query")
    asyncio.run(run())


def test_status_detection_expires_past_deadlines_and_preserves_unknown():
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    assert derive_status("open", now - timedelta(days=1), now) == "closed"
    assert derive_status("upcoming", now + timedelta(days=1), now, "Registration starts next week") == "upcoming"
    assert derive_status("unknown", None, now) == "unknown"
    assert derive_status("open", None, now, "Registration is open now") == "open"
    assert derive_status("open", None, now, "We are excited to announce an event") == "unknown"


def test_source_classification_marks_known_repost_platforms():
    assert classify_source("https://www.devfolio.co/hackathons/test") == "third_party"
    assert classify_source("https://careers.example.org/roles/ai") == "unknown"


def test_page_selection_balances_hackathons_and_internships():
    candidates = {
        "https://events.test/1": {"kind": "hackathon"},
        "https://events.test/2": {"kind": "hackathon"},
        "https://jobs.test/1": {"kind": "internship"},
        "https://jobs.test/2": {"kind": "internship"},
    }
    selected = select_pages(candidates, ["hackathon", "internship"], 4)
    assert selected == ["https://events.test/1", "https://jobs.test/1", "https://events.test/2", "https://jobs.test/2"]


def test_detail_page_expansion_only_accepts_relevant_same_site_links():
    assert _is_detail_page("https://internshala.com/internship/detail/ai-intern", "internship", "https://internshala.com/internships/")
    assert _is_detail_page("https://unstop.com/hackathons/ai-build-123", "hackathon", "https://unstop.com/hackathons")
    assert not _is_detail_page("https://external.test/internship/detail/ai-intern", "internship", "https://internshala.com/internships/")
    assert not _is_detail_page("https://internshala.com/internships/", "internship", "https://internshala.com/internships/")


def test_gemini_extraction_keeps_only_source_verified_links(monkeypatch):
    from google import genai
    payload = {"opportunities": [{"title": "Build Night", "opportunity_type": "hackathon", "source_url": "https://fake.example/event", "application_url": "https://fake.example/apply", "deadline": "2026-12-01T00:00:00Z", "prize_pool": "₹100,000", "status": "unknown", "evidence": [{"source_url": "https://fake.example/evidence", "evidence_text": "Build Night"}]}]}
    class Models:
        def generate_content(self, **kwargs):
            return type("Response", (), {"text": json.dumps(payload)})()
    class Client:
        def __init__(self, **kwargs): self.models = Models()
        def close(self): pass
    monkeypatch.setattr(genai, "Client", Client)
    async def run():
        result = await GeminiIntelligence("unit-test", "test-model").extract({"url": "https://devfolio.co/e/build", "text": "Build Night is a hackathon."}, "hackathon")
        item = result.opportunities[0]
        assert str(item.source_url) == "https://devfolio.co/e/build"
        assert item.application_url is None
        assert item.deadline is None
        assert item.prize_pool is None
        assert str(item.evidence[0].source_url) == "https://devfolio.co/e/build"
    asyncio.run(run())


def test_gemini_daily_quota_is_reported_as_actionable_error(monkeypatch):
    from google import genai
    class Models:
        def generate_content(self, **kwargs):
            raise RuntimeError("429 RESOURCE_EXHAUSTED GenerateRequestsPerDayPerModel-FreeTier")
    class Client:
        def __init__(self, **kwargs): self.models = Models()
        def close(self): pass
    monkeypatch.setattr(genai, "Client", Client)
    async def run():
        with pytest.raises(GeminiQuotaError, match="daily request quota"):
            await GeminiIntelligence("unit-test", "test-model").extract({"url": "https://source.test/event", "text": "some source content"}, "hackathon")
    asyncio.run(run())
