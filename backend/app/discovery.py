import asyncio
import re
from difflib import SequenceMatcher
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from sqlalchemy import select

from .agents import HackathonDiscoveryAgent, InternshipDiscoveryAgent
from .config import get_settings
from .database import SessionLocal
from .models import Opportunity, OpportunityMatch, ResearchRun, UserProfile
from .schemas import ExtractedOpportunity, ProfilePayload
from .services.gemini import GeminiIntelligence, GeminiQuotaError
from .services.email import EmailConfigurationError, send_digest
from .services.matching import is_duplicate, normalize, score_opportunity
from .services.tinyfish import TinyFishClient

THIRD_PARTY_DOMAINS = {"unstop.com", "devfolio.co", "hackerearth.com", "devpost.com", "linkedin.com", "internshala.com", "wellfound.com"}


def classify_source(source_url: str) -> str:
    host = (urlparse(source_url).hostname or "").lower().removeprefix("www.")
    if any(host == domain or host.endswith("." + domain) for domain in THIRD_PARTY_DOMAINS):
        return "third_party"
    return "unknown"


def derive_status(declared: str, deadline, now=None, evidence_text: str = "") -> str:
    normalized_evidence = evidence_text.lower()
    if deadline is not None:
        current = now or datetime.now(timezone.utc)
        comparable = deadline if deadline.tzinfo else deadline.replace(tzinfo=timezone.utc)
        if comparable < current:
            return "closed"
    if declared == "open" and re.search(r"registration\s+(is\s+)?open|applications?\s+(are\s+)?open|register\s+now|apply\s+now|open\s+for\s+applications", normalized_evidence):
        return "open"
    if declared == "upcoming" and re.search(r"coming\s+soon|opens?\s+(on|in)|upcoming|registration\s+starts", normalized_evidence):
        return "upcoming"
    if declared == "closed" and re.search(r"registration\s+(is\s+)?closed|applications?\s+(are\s+)?closed|registration\s+has\s+ended", normalized_evidence):
        return "closed"
    return "unknown"


def profile_or_default(db):
    saved = db.scalar(select(UserProfile).order_by(UserProfile.id).limit(1))
    if saved:
        return ProfilePayload.model_validate(saved.profile)
    return ProfilePayload(
        name="Aparmit Srivastav",
        preferred_domains=["Artificial Intelligence", "AI Agents", "Machine Learning", "Full Stack Development", "Software Engineering", "Python Development", "LLM Applications"],
        preferred_technologies=["Python", "FastAPI", "React", "Next.js", "TypeScript", "LangChain", "LLMs", "RAG", "Supabase", "PostgreSQL"],
        hackathon_preferences=["Delhi NCR", "India", "Online"],
        internship_preferences=["Remote", "India", "Delhi NCR", "Fresher", "Entry-level"],
    )


def _search_results(payload: dict) -> list[dict]:
    results = payload.get("results", [])
    if isinstance(results, dict):
        results = results.get("results", [])
    return [r for r in results if isinstance(r, dict) and isinstance(r.get("url"), str) and r["url"].startswith(("http://", "https://"))]


def select_pages(candidates: dict[str, dict], opportunity_types: list[str], limit: int) -> list[str]:
    """Select pages fairly across requested opportunity types."""
    grouped = {kind: [url for url, candidate in candidates.items() if candidate.get("kind") == kind] for kind in opportunity_types}
    selected = []
    while len(selected) < limit and any(grouped.values()):
        for kind in opportunity_types:
            if grouped[kind] and len(selected) < limit:
                selected.append(grouped[kind].pop(0))
    return selected


def apply_match_scores(opportunity: Opportunity, scores: dict):
    if opportunity.match is None:
        opportunity.match = OpportunityMatch(**scores)
        return
    for field, value in scores.items():
        setattr(opportunity.match, field, value)


def _is_detail_page(url: str, opportunity_type: str, parent_url: str) -> bool:
    parsed, parent = urlparse(url), urlparse(parent_url)
    if parsed.hostname != parent.hostname:
        return False
    path = parsed.path.lower().rstrip("/")
    if opportunity_type == "internship":
        return "/internship/detail/" in path or "/jobs/view/" in path
    if opportunity_type == "hackathon":
        if parsed.hostname and parsed.hostname.endswith("unstop.com"):
            return path.startswith("/hackathons/") and len(path.split("/")) >= 3 and path != "/hackathons"
        return any(path.startswith(prefix) and len(path.split("/")) >= 3 for prefix in ("/hackathons/", "/challenge/", "/challenges/", "/software/"))
    return False


async def execute_discovery(run_id: int, requested_type: str, locations: list[str], max_pages: int = 20, send_empty_digest: bool = False, digest_subject: str | None = None):
    settings = get_settings()
    db = SessionLocal()
    run = db.get(ResearchRun, run_id)
    if not run:
        db.close()
        return
    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    db.commit()
    errors, raw_output, saved, found, duplicates = [], [], 0, 0, 0
    pending_opportunities = []
    sources_searched = set()
    try:
        tf = TinyFishClient(settings.tinyfish_api_key)
        gemini = GeminiIntelligence(settings.gemini_api_key, settings.gemini_model)
        types = ["hackathon", "internship"] if requested_type == "both" else [requested_type]
        agents = {"hackathon": HackathonDiscoveryAgent(), "internship": InternshipDiscoveryAgent()}
        queries = [(kind, location, query, domains) for kind in types for location, query, domains in agents[kind].search_tasks(locations)]
        semaphore = asyncio.Semaphore(4)
        async def search_one(query, domains):
            async with semaphore:
                return await tf.search(query, domains)
        search_outcomes = await asyncio.gather(*(search_one(query, domains) for _, _, query, domains in queries), return_exceptions=True)
        candidates: dict[str, dict] = {}
        for (kind, location, query, domains), outcome in zip(queries, search_outcomes):
            if isinstance(outcome, Exception):
                errors.append(f"{kind} search ({location}): {outcome}")
                continue
            sources_searched.update(domains or ["web search"])
            raw_output.append({"stage": "search", "type": kind, "query": query, "response": outcome})
            for result in _search_results(outcome)[:8]:
                candidates.setdefault(result["url"], {"url": result["url"], "title": result.get("title"), "snippet": result.get("snippet", ""), "kind": kind})
        found = len(candidates)
        urls = select_pages(candidates, types, max_pages)
        source_pages = []
        for index in range(0, len(urls), 10):
            try:
                fetched = await tf.fetch(urls[index:index + 10])
                raw_output.append({"stage": "fetch", "response": fetched})
                errors.extend(f"Fetch {e.get('url')}: {e.get('error')}" for e in fetched.get("errors", []))
                source_pages.extend(fetched.get("results", []))
            except Exception as exc:
                errors.append(f"TinyFish fetch: {exc}")
        source_cache = {}
        detail_candidates: dict[str, dict] = {}
        for page in source_pages:
            page_url = page.get("url") or page.get("final_url") or ""
            candidate = candidates.get(page_url) or next((v for k, v in candidates.items() if k.rstrip("/") == page_url.rstrip("/")), None)
            if not candidate:
                continue
            source_cache[page_url.rstrip("/")] = (page, candidate)
            if _is_detail_page(page_url, candidate["kind"], page_url):
                detail_candidates.setdefault(page_url, {**candidate, "url": page_url})
            for link in page.get("links") or []:
                if not isinstance(link, str):
                    continue
                detail_url = urljoin(page_url, link)
                if _is_detail_page(detail_url, candidate["kind"], page_url):
                    detail_candidates.setdefault(detail_url, {"url": detail_url, "title": None, "snippet": "Discovered as a page link", "kind": candidate["kind"]})
        if not detail_candidates:
            # Keep useful source pages as a conservative fallback when no detail links are exposed.
            for page in source_pages:
                page_url = page.get("url") or page.get("final_url") or ""
                candidate = candidates.get(page_url) or next((v for k, v in candidates.items() if k.rstrip("/") == page_url.rstrip("/")), None)
                if candidate and len(page.get("text") or "") >= 1000:
                    detail_candidates.setdefault(page_url, {**candidate, "url": page_url})
        target_urls = select_pages(detail_candidates, types, max_pages)
        missing_detail_urls = [url for url in target_urls if url.rstrip("/") not in source_cache]
        for index in range(0, len(missing_detail_urls), 10):
            try:
                fetched = await tf.fetch(missing_detail_urls[index:index + 10])
                raw_output.append({"stage": "fetch_details", "response": fetched})
                errors.extend(f"Fetch {e.get('url')}: {e.get('error')}" for e in fetched.get("errors", []))
                for page in fetched.get("results", []):
                    page_url = page.get("url") or page.get("final_url") or ""
                    candidate = detail_candidates.get(page_url) or detail_candidates.get(page_url.rstrip("/"))
                    if candidate:
                        source_cache[page_url.rstrip("/")] = (page, candidate)
            except Exception as exc:
                errors.append(f"TinyFish detail fetch: {exc}")
        gemini_quota_exhausted = False
        for target_url in target_urls:
            if gemini_quota_exhausted:
                break
            cached = source_cache.get(target_url.rstrip("/")) or next((value for key, value in source_cache.items() if key.rstrip("/") == target_url.rstrip("/")), None)
            if not cached:
                continue
            page, candidate = cached
            url = page.get("url") or page.get("final_url") or target_url
            if not page.get("text"):
                continue
            try:
                extraction = await gemini.extract(page, candidate["kind"])
                raw_output.append({"stage": "extract", "url": url, "result": extraction.model_dump(mode="json")})
                for item in extraction.opportunities:
                    # Preserve TinyFish's exact verified source URL and reject off-page model citations.
                    item = type(item).model_validate({**item.model_dump(mode="json"), "source_url": url})
                    parsed = urlparse(url)
                    if parsed.scheme not in ("http", "https") or not parsed.netloc:
                        continue
                    found_item = db.scalars(select(Opportunity).where(Opportunity.opportunity_type == item.opportunity_type)).all()
                    profile = profile_or_default(db)
                    status = derive_status(item.status, item.deadline, evidence_text=page.get("text", ""))
                    candidates_to_check = [*found_item, *pending_opportunities]
                    title_n, org_n = normalize(item.title), normalize(item.organization)
                    duplicate_record = next((candidate for candidate in candidates_to_check if candidate.source_url.rstrip("/").lower() == url.rstrip("/").lower() or (SequenceMatcher(None, title_n, candidate.normalized_title).ratio() >= 0.92 and (SequenceMatcher(None, org_n, candidate.normalized_organization).ratio() >= 0.75 if org_n and candidate.normalized_organization else True))), None)
                    if duplicate_record:
                        duplicates += 1
                        changed = False
                        updates = {
                            "deadline": item.deadline, "application_url": str(item.application_url) if item.application_url else None,
                            "eligibility": item.eligibility, "location": item.location, "work_mode": item.work_mode,
                            "prize_pool": item.prize_pool, "stipend": item.stipend, "description": item.description,
                            "summary": item.summary, "status": status,
                        }
                        for field, value in updates.items():
                            if field == "status" and value == "unknown":
                                continue
                            if value is not None and getattr(duplicate_record, field) != value:
                                setattr(duplicate_record, field, value)
                                changed = True
                        duplicate_record.last_verified_at = datetime.now(timezone.utc)
                        if changed:
                            duplicate_record.notification_version += 1
                            apply_match_scores(duplicate_record, score_opportunity(item, profile))
                        continue
                    record = Opportunity(
                        title=item.title, normalized_title=normalize(item.title), opportunity_type=item.opportunity_type,
                        organization=item.organization, normalized_organization=normalize(item.organization),
                        description=item.description, summary=item.summary, source_url=url, source_type=classify_source(url),
                        application_url=str(item.application_url) if item.application_url else None,
                        location=item.location, work_mode=item.work_mode, deadline=item.deadline,
                        start_date=item.start_date, end_date=item.end_date, stipend=item.stipend,
                        prize_pool=item.prize_pool, fees=item.fees, eligibility=item.eligibility,
                        required_skills=item.required_skills, themes=item.themes, team_size=item.team_size,
                        experience_requirements=item.experience_requirements, duration=item.duration,
                        employment_type=item.employment_type, status=status,
                        review_status="needs_review" if status == "unknown" or not item.application_url or not item.eligibility else "pending",
                        raw_data={"source_search": candidate, "page": {"title": page.get("title"), "text": page.get("text", "")[:12000]}, "extraction": item.model_dump(mode="json")},
                        last_verified_at=datetime.now(timezone.utc),
                    )
                    record.match = OpportunityMatch(**score_opportunity(item, profile))
                    db.add(record)
                    pending_opportunities.append(record)
                    saved += 1
            except GeminiQuotaError as exc:
                errors.append(str(exc))
                gemini_quota_exhausted = True
            except Exception as exc:
                errors.append(f"Gemini extraction {url}: {exc}")
        db.commit()
        if saved or send_empty_digest:
            try:
                delivery = await send_digest(db, allow_empty=send_empty_digest, subject=digest_subject)
                if delivery and delivery.status == "failed":
                    errors.append(f"Email notification failed: {delivery.error}")
            except EmailConfigurationError as exc:
                raw_output.append({"stage": "email_notification", "status": "skipped", "reason": str(exc)})
        run.records_found = found
        run.records_saved = saved
        run.duplicates_detected = duplicates
        run.sources_searched = sorted(sources_searched)
        run.errors = errors
        run.raw_output = raw_output
        run.status = "completed" if not errors else ("partial" if saved or found else "failed")
    except Exception as exc:
        db.rollback()
        run = db.get(ResearchRun, run_id)
        run.status = "failed"
        run.errors = errors + [str(exc)]
        run.raw_output = raw_output
        run.duplicates_detected = duplicates
        run.sources_searched = sorted(sources_searched)
    finally:
        run = db.get(ResearchRun, run_id)
        if run:
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
        db.close()

