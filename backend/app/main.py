from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, distinct, func, select
from sqlalchemy.orm import Session, joinedload

from .config import get_settings
from .database import get_db
from .discovery import execute_discovery, profile_or_default
from .models import EmailDelivery, EmailDeliveryItem, Opportunity, OpportunityMatch, ResearchRun, UserProfile
from .schemas import DiscoveryRequest, OpportunityOut, ProfilePayload, ReviewRequest
from .services.matching import score_opportunity
from .services.email import EmailConfigurationError, email_configuration, send_digest, serialize_delivery


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


settings = get_settings()
app = FastAPI(title="OpportunityOS API", description="AI-powered opportunity discovery and matching.", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin, "http://localhost:3000"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health():
    _, missing_email = email_configuration()
    return {"status": "ok", "services": {"tinyfish_configured": bool(settings.tinyfish_api_key), "gemini_configured": bool(settings.gemini_api_key), "email_configured": not missing_email}}


@app.get("/api/email/status")
def email_status(db: Session = Depends(get_db)):
    _, missing = email_configuration()
    latest = db.scalar(select(EmailDelivery).order_by(desc(EmailDelivery.created_at)).limit(1))
    sent_count = db.scalar(select(func.count(distinct(EmailDeliveryItem.opportunity_id))).join(EmailDelivery).where(EmailDelivery.status == "sent")) or 0
    settings, _ = email_configuration()
    return {"configured": not missing, "missing_configuration": missing, "recipient": settings.email_to if not missing else None, "provider": "Resend", "opportunities_sent": sent_count, "last_delivery": serialize_delivery(latest)}


@app.post("/api/email/test")
async def send_test_email(db: Session = Depends(get_db)):
    try:
        delivery = await send_digest(db, is_test=True)
    except EmailConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc
    return serialize_delivery(delivery)


@app.post("/api/email/digest")
async def send_latest_email_digest(db: Session = Depends(get_db)):
    try:
        delivery = await send_digest(db, allow_empty=True)
    except EmailConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc
    return serialize_delivery(delivery)


def serialize(record: Opportunity) -> dict:
    result = OpportunityOut.model_validate(record).model_dump(mode="json")
    result["match"] = ({"overall_score": record.match.overall_score, "skill_score": record.match.skill_score, "eligibility_score": record.match.eligibility_score, "location_score": record.match.location_score, "experience_score": record.match.experience_score, "explanation": record.match.explanation} if record.match else None)
    return result


@app.get("/api/opportunities")
def opportunities(type: str | None = Query(None, pattern="^(hackathon|internship)$"), location: str | None = None, work_mode: str | None = None, status: str | None = None, min_match_score: int = Query(0, ge=0, le=100), sort_by: str = Query("match", pattern="^(match|deadline|recent)$"), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    query = select(Opportunity)
    if type: query = query.where(Opportunity.opportunity_type == type)
    if location: query = query.where(func.lower(Opportunity.location).contains(location.lower()))
    if work_mode: query = query.where(Opportunity.work_mode == work_mode.lower())
    if status: query = query.where(Opportunity.status == status.lower())
    if min_match_score: query = query.join(OpportunityMatch).where(OpportunityMatch.overall_score >= min_match_score)
    if sort_by == "match":
        if not min_match_score: query = query.outerjoin(OpportunityMatch)
        query = query.order_by(desc(OpportunityMatch.overall_score), desc(Opportunity.first_discovered_at))
    elif sort_by == "deadline": query = query.order_by(Opportunity.deadline.asc().nulls_last())
    else: query = query.order_by(desc(Opportunity.first_discovered_at))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).unique().all()
    return {"items": [serialize(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@app.get("/api/opportunities/{opportunity_id}")
def opportunity_detail(opportunity_id: int, db: Session = Depends(get_db)):
    row = db.scalar(select(Opportunity).options(joinedload(Opportunity.match)).where(Opportunity.id == opportunity_id))
    if not row: raise HTTPException(404, "Opportunity not found")
    return serialize(row)


@app.get("/api/opportunities/{opportunity_id}/match")
def opportunity_match(opportunity_id: int, db: Session = Depends(get_db)):
    row = db.scalar(select(OpportunityMatch).where(OpportunityMatch.opportunity_id == opportunity_id))
    if not row: raise HTTPException(404, "Match not found")
    return {"overall_score": row.overall_score, "skill_score": row.skill_score, "eligibility_score": row.eligibility_score, "location_score": row.location_score, "experience_score": row.experience_score, "explanation": row.explanation}


@app.patch("/api/opportunities/{opportunity_id}/review")
def review_opportunity(opportunity_id: int, request: ReviewRequest, db: Session = Depends(get_db)):
    row = db.get(Opportunity, opportunity_id)
    if not row: raise HTTPException(404, "Opportunity not found")
    row.review_status = request.decision
    db.commit()
    return {"id": row.id, "review_status": row.review_status}


@app.post("/api/discovery/run", status_code=202)
def start_discovery(request: DiscoveryRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if not settings.tinyfish_api_key or not settings.gemini_api_key:
        raise HTTPException(503, "Configure TINYFISH_API_KEY and GEMINI_API_KEY before starting discovery.")
    run = ResearchRun(opportunity_type=request.type)
    db.add(run); db.commit(); db.refresh(run)
    background_tasks.add_task(execute_discovery, run.id, request.type, request.location, request.max_pages)
    return {"run_id": run.id, "status": run.status, "message": "Discovery started"}


@app.post("/api/discovery/run/{run_id}/status")
def discovery_status(run_id: int, db: Session = Depends(get_db)):
    run = db.get(ResearchRun, run_id)
    if not run: raise HTTPException(404, "Research run not found")
    return {"id": run.id, "status": run.status, "records_found": run.records_found, "records_saved": run.records_saved, "duplicates_detected": run.duplicates_detected, "sources_searched": run.sources_searched, "errors": run.errors, "started_at": run.started_at, "completed_at": run.completed_at}


@app.get("/api/research/runs")
def research_runs(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    rows = db.scalars(select(ResearchRun).order_by(desc(ResearchRun.started_at)).limit(limit)).all()
    return [{"id": r.id, "source": r.source, "opportunity_type": r.opportunity_type, "status": r.status, "started_at": r.started_at, "completed_at": r.completed_at, "records_found": r.records_found, "records_saved": r.records_saved, "duplicates_detected": r.duplicates_detected, "sources_searched": r.sources_searched, "errors": r.errors} for r in rows]


@app.get("/api/dashboard/stats")
def dashboard_stats(db: Session = Depends(get_db)):
    count = db.scalar(select(func.count(Opportunity.id))) or 0
    hackathons = db.scalar(select(func.count(Opportunity.id)).where(Opportunity.opportunity_type == "hackathon", Opportunity.status.in_(["open", "upcoming"]))) or 0
    internships = db.scalar(select(func.count(Opportunity.id)).where(Opportunity.opportunity_type == "internship", Opportunity.status.in_(["open", "upcoming"]))) or 0
    high = db.scalar(select(func.count(OpportunityMatch.id)).where(OpportunityMatch.overall_score >= 75)) or 0
    latest = db.scalar(select(ResearchRun.completed_at).where(ResearchRun.completed_at.is_not(None)).order_by(desc(ResearchRun.completed_at)).limit(1))
    return {"total_opportunities": count, "open_hackathons": hackathons, "active_internships": internships, "high_match_opportunities": high, "last_research_at": latest}


@app.get("/api/profile")
def get_profile(db: Session = Depends(get_db)):
    return profile_or_default(db).model_dump()


@app.put("/api/profile")
def put_profile(profile: ProfilePayload, db: Session = Depends(get_db)):
    row = db.scalar(select(UserProfile).order_by(UserProfile.id).limit(1))
    if not row:
        row = UserProfile(profile=profile.model_dump()); db.add(row)
    else: row.profile = profile.model_dump()
    for opportunity in db.scalars(select(Opportunity)).all():
        scores = score_opportunity(opportunity, profile)
        if not opportunity.match:
            opportunity.match = OpportunityMatch(**scores)
        else:
            for key, value in scores.items(): setattr(opportunity.match, key, value)
    db.commit(); return profile.model_dump()

