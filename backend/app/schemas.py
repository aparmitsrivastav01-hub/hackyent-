from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Evidence(BaseModel):
    source_url: HttpUrl
    evidence_text: str = ""


class ExtractedOpportunity(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    opportunity_type: Literal["hackathon", "internship"]
    organization: str | None = None
    description: str | None = None
    summary: str | None = None
    source_url: HttpUrl
    application_url: HttpUrl | None = None
    location: str | None = None
    work_mode: Literal["remote", "hybrid", "onsite", "online", "unknown"] = "unknown"
    deadline: datetime | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    stipend: str | None = None
    prize_pool: str | None = None
    fees: str | None = None
    eligibility: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    team_size: str | None = None
    experience_requirements: str | None = None
    duration: str | None = None
    employment_type: str | None = None
    status: Literal["open", "upcoming", "closed", "unknown"] = "unknown"
    evidence: list[Evidence] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    opportunities: list[ExtractedOpportunity] = Field(default_factory=list)


class DiscoveryRequest(BaseModel):
    type: Literal["both", "hackathon", "internship"] = "both"
    location: list[str] = Field(default_factory=lambda: ["Delhi NCR", "India", "Online"])
    max_pages: int = Field(default=20, ge=1, le=20)


class ProfilePayload(BaseModel):
    name: str = "Aparmit Srivastav"
    preferred_domains: list[str] = Field(default_factory=list)
    preferred_technologies: list[str] = Field(default_factory=list)
    hackathon_preferences: list[str] = Field(default_factory=list)
    internship_preferences: list[str] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    decision: Literal["reviewed", "needs_review"]


class AutomationUpdate(BaseModel):
    enabled: bool


class OpportunityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    opportunity_type: str
    organization: str | None
    description: str | None
    summary: str | None
    source_url: str
    source_type: str
    application_url: str | None
    location: str | None
    work_mode: str | None
    deadline: datetime | None
    start_date: datetime | None
    end_date: datetime | None
    stipend: str | None
    prize_pool: str | None
    fees: str | None
    eligibility: str | None
    required_skills: list[str]
    themes: list[str]
    team_size: str | None
    experience_requirements: str | None
    duration: str | None
    employment_type: str | None
    status: str
    review_status: str
    first_discovered_at: datetime
    last_verified_at: datetime

