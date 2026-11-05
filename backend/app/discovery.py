import asyncio
import logging
import re
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

from sqlalchemy import func, select

from .agents import HackathonDiscoveryAgent, InternshipDiscoveryAgent
from .config import get_settings
from .database import SessionLocal
from .models import Opportunity, OpportunityMatch, RawDiscoveryResult, ResearchRun, UserProfile
from .schemas import ExtractedOpportunity, ProfilePayload
from .services.gemini import GeminiIntelligence, GeminiQuotaError
from .services.email import EmailConfigurationError, send_digest
from .services.matching import is_duplicate, normalize, score_opportunity
from .services.tinyfish import TinyFishClient

THIRD_PARTY_DOMAINS = {"unstop.com", "devfolio.co", "hackerearth.com", "devpost.com", "linkedin.com", "internshala.com", "wellfound.com"}
logger = logging.getLogger(__name__)


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
        raw_by_url = {}
        raw_candidate_urls = list(dict.fromkeys([*target_urls, *candidates.keys()]))
        for target_url in raw_candidate_urls:
            cached = source_cache.get(target_url.rstrip("/")) or next((value for key, value in source_cache.items() if key.rstrip("/") == target_url.rstrip("/")), None)
            if cached:
                page, candidate = cached
            else:
                page, candidate = {}, candidates[target_url]
            source_url = page.get("url") or page.get("final_url") or target_url
            host = urlparse(source_url).hostname or "Unknown source"
            raw_text = page.get("text") or ""
            raw = db.scalar(select(RawDiscoveryResult).where(RawDiscoveryResult.source_url == source_url))
            if raw is None:
                raw = RawDiscoveryResult(run_id=run_id, source_url=source_url)
                db.add(raw)
            raw.run_id = run_id
            raw.opportunity_type = candidate.get("kind") or "other"
            raw.source_title = (page.get("title") or candidate.get("title") or "Untitled source")[:500]
            raw.source_name = host.removeprefix("www.")[:300]
            raw.raw_text = raw_text[:30000]
            raw.snippet = (candidate.get("snippet") or raw_text[:500])[:2000]
            raw.extraction_status = "PENDING_EXTRACTION"
            raw.extraction_error = None
            raw.retry_after = None
            raw.discovered_at = datetime.now(timezone.utc)
            raw_by_url[source_url.rstrip("/")] = raw
        # Commit raw TinyFish content before making any Gemini calls. A provider
        # exception, quota limit, or process crash cannot discard the pages.
        db.commit()
        gemini_quota_exhausted = False
        gemini_cooldown_hours = 1
        for target_url in target_urls:
            if gemini_quota_exhausted:
                break
            cached = source_cache.get(target_url.rstrip("/")) or next((value for key, value in source_cache.items() if key.rstrip("/") == target_url.rstrip("/")), None)
            if not cached:
                continue
            page, candidate = cached
            url = page.get("url") or page.get("final_url") or target_url
            raw_result = raw_by_url.get(url.rstrip("/"))
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
                        if raw_result:
                            raw_result.opportunity_id = duplicate_record.id
                            raw_result.extraction_status = "EXTRACTED"
                            raw_result.extraction_error = None
                        if changed:
                            duplicate_record.notification_version += 1
                            apply_match_scores(duplicate_record, score_opportunity(item, profile))
                            if duplicate_record not in pending_opportunities:
                                pending_opportunities.append(duplicate_record)
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
                    if raw_result:
                        raw_result.opportunity = record
                        raw_result.extraction_status = "EXTRACTED"
                        raw_result.extraction_error = None
                    saved += 1
                if raw_result and not extraction.opportunities:
                    raw_result.extraction_status = "EXTRACTED"
                    raw_result.extraction_error = None
            except GeminiQuotaError as exc:
                errors.append(str(exc))
                gemini_quota_exhausted = True
                gemini_cooldown_hours = 24
                if raw_result:
                    raw_result.extraction_status = "PENDING_EXTRACTION"
                    raw_result.extraction_error = "Gemini daily quota exhausted"
                    raw_result.retry_after = datetime.now(timezone.utc) + timedelta(hours=24)
            except Exception as exc:
                errors.append(f"Gemini extraction {url}: {exc}")
                if raw_result:
                    raw_result.extraction_status = "PENDING_EXTRACTION"
                    raw_result.extraction_error = "Gemini temporarily unavailable or extraction failed"
                    raw_result.retry_after = datetime.now(timezone.utc) + timedelta(hours=1)
                if any(token in str(exc).lower() for token in ("429", "rate limit", "quota", "timeout", "temporarily unavailable")):
                    gemini_quota_exhausted = True
        if gemini_quota_exhausted:
            cooldown = datetime.now(timezone.utc) + timedelta(hours=gemini_cooldown_hours)
            for raw_result in raw_by_url.values():
                if raw_result.extraction_status == "PENDING_EXTRACTION":
                    raw_result.retry_after = cooldown
        db.commit()
        raw_pending = list(db.scalars(select(RawDiscoveryResult).where(RawDiscoveryResult.run_id == run_id, RawDiscoveryResult.extraction_status == "PENDING_EXTRACTION", RawDiscoveryResult.emailed_at.is_(None))).all())
        if pending_opportunities or raw_pending or found == 0:
            try:
                daily_subject = f"OpportunityOS | Raw Discovery Results | {datetime.now(timezone.utc).strftime('%d %b %Y')}" if raw_pending else digest_subject
                delivery = await send_digest(db, opportunities=pending_opportunities, raw_results=raw_pending, allow_empty=(found == 0), subject=daily_subject)
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


async def retry_pending_extractions(limit: int = 20):
    """Retry only raw pages whose persisted cooldown has expired."""
    settings = get_settings()
    db = SessionLocal()
    now = datetime.now(timezone.utc)
    run = ResearchRun(opportunity_type="both", source="Pending Gemini extraction retry", status="running")
    db.add(run)
    db.commit()
    run_id = run.id
    try:
        pending = list(db.scalars(select(RawDiscoveryResult).where(
            RawDiscoveryResult.extraction_status == "PENDING_EXTRACTION",
            (RawDiscoveryResult.retry_after.is_(None)) | (RawDiscoveryResult.retry_after <= now),
        ).order_by(RawDiscoveryResult.discovered_at).limit(limit)).all())
        if not pending:
            run.status = "completed"
            run.errors = []
            run.raw_output = [{"stage": "retry", "status": "nothing_due"}]
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            return {"run_id": run_id, "status": run.status, "processed": 0, "saved": 0, "pending": 0}
        gemini = GeminiIntelligence(settings.gemini_api_key, settings.gemini_model)
        profile = profile_or_default(db)
        changed_opportunities = []
        errors = []
        for index, raw in enumerate(pending):
            try:
                extraction = await gemini.extract({"url": raw.source_url, "title": raw.source_title, "text": raw.raw_text, "links": []}, raw.opportunity_type if raw.opportunity_type in ("hackathon", "internship") else "hackathon")
                matched = None
                for item in extraction.opportunities:
                    item = type(item).model_validate({**item.model_dump(mode="json"), "source_url": raw.source_url})
                    status = derive_status(item.status, item.deadline, evidence_text=raw.raw_text)
                    existing = db.scalars(select(Opportunity).where(Opportunity.opportunity_type == item.opportunity_type)).all()
                    title_n, org_n = normalize(item.title), normalize(item.organization)
                    matched = next((row for row in existing if row.source_url.rstrip("/").lower() == raw.source_url.rstrip("/").lower() or (SequenceMatcher(None, title_n, row.normalized_title).ratio() >= .92 and (SequenceMatcher(None, org_n, row.normalized_organization).ratio() >= .75 if org_n and row.normalized_organization else True))), None)
                    if matched:
                        changed = False
                        updates = {"deadline": item.deadline, "application_url": str(item.application_url) if item.application_url else None, "eligibility": item.eligibility, "location": item.location, "work_mode": item.work_mode, "prize_pool": item.prize_pool, "stipend": item.stipend, "description": item.description, "summary": item.summary}
                        for field, value in updates.items():
                            if value is not None and getattr(matched, field) != value:
                                setattr(matched, field, value); changed = True
                        if changed:
                            matched.notification_version += 1
                            apply_match_scores(matched, score_opportunity(item, profile))
                            changed_opportunities.append(matched)
                    else:
                        matched = Opportunity(title=item.title, normalized_title=title_n, opportunity_type=item.opportunity_type, organization=item.organization, normalized_organization=org_n, description=item.description, summary=item.summary, source_url=raw.source_url, source_type=classify_source(raw.source_url), application_url=str(item.application_url) if item.application_url else None, location=item.location, work_mode=item.work_mode, deadline=item.deadline, start_date=item.start_date, end_date=item.end_date, stipend=item.stipend, prize_pool=item.prize_pool, fees=item.fees, eligibility=item.eligibility, required_skills=item.required_skills, themes=item.themes, team_size=item.team_size, experience_requirements=item.experience_requirements, duration=item.duration, employment_type=item.employment_type, status=status, review_status="needs_review" if status == "unknown" or not item.application_url or not item.eligibility else "pending", raw_data={"recovered_from_raw_result": raw.id, "page": {"title": raw.source_title, "text": raw.raw_text[:12000]}})
                        matched.match = OpportunityMatch(**score_opportunity(item, profile))
                        db.add(matched); db.flush(); changed_opportunities.append(matched)
                    raw.opportunity_id = matched.id if matched else None
                raw.extraction_status = "EXTRACTED"
                raw.extraction_error = None
                raw.retry_after = None
            except GeminiQuotaError as exc:
                errors.append(str(exc))
                raw.extraction_error = "Gemini daily quota exhausted"
                raw.retry_after = datetime.now(timezone.utc) + timedelta(hours=24)
                for remaining in pending[index + 1:]:
                    remaining.retry_after = raw.retry_after
                break
            except Exception as exc:
                errors.append(f"Raw result {raw.id}: {exc}")
                raw.extraction_error = "Gemini temporarily unavailable or extraction failed"
                raw.retry_after = datetime.now(timezone.utc) + timedelta(hours=1)
                if any(token in str(exc).lower() for token in ("429", "rate limit", "quota", "timeout", "temporarily unavailable")):
                    cooldown = datetime.now(timezone.utc) + timedelta(hours=1)
                    for remaining in pending[index + 1:]:
                        remaining.retry_after = cooldown
                    break
        db.commit()
        delivery = None
        if changed_opportunities:
            delivery = await send_digest(db, opportunities=changed_opportunities)
        run.records_found = len(pending)
        run.records_saved = len(changed_opportunities)
        run.errors = errors
        run.raw_output = [{"stage": "retry", "processed": len(pending), "structured_updates": len(changed_opportunities), "delivery_status": delivery.status if delivery else None}]
        run.status = "partial" if errors else "completed"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        remaining = db.scalar(select(func.count(RawDiscoveryResult.id)).where(RawDiscoveryResult.extraction_status == "PENDING_EXTRACTION")) or 0
        return {"run_id": run_id, "status": run.status, "processed": len(pending), "saved": len(changed_opportunities), "pending": remaining, "email_status": delivery.status if delivery else None}
    except Exception:
        db.rollback()
        logger.exception("Pending Gemini extraction retry failed (run %s)", run_id)
        run = db.get(ResearchRun, run_id)
        if run:
            run.status = "failed"; run.completed_at = datetime.now(timezone.utc); run.errors = ["Retry processing failed; raw results remain saved."]
            db.commit()
        raise
    finally:
        db.close()

