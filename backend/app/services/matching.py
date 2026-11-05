import re
from difflib import SequenceMatcher

from ..schemas import ProfilePayload


def normalize(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def is_duplicate(title: str, organization: str | None, source_url: str, candidates: list) -> bool:
    title_n, org_n = normalize(title), normalize(organization)
    for candidate in candidates:
        if candidate.source_url.rstrip("/").lower() == source_url.rstrip("/").lower():
            return True
        title_ratio = SequenceMatcher(None, title_n, candidate.normalized_title).ratio()
        org_ratio = SequenceMatcher(None, org_n, candidate.normalized_organization).ratio() if org_n and candidate.normalized_organization else 0
        if title_ratio >= 0.92 and (org_ratio >= 0.75 or not org_n or not candidate.normalized_organization):
            return True
    return False


def score_opportunity(item, profile: ProfilePayload) -> dict:
    text = " ".join([item.title, item.description or "", item.organization or "", " ".join(item.required_skills), " ".join(getattr(item, "themes", []))]).lower()
    skills = [x for x in profile.preferred_technologies if x.lower() in text]
    domains = [x for x in profile.preferred_domains if x.lower() in text]
    skill_score = min(100, round(100 * (len(set(skills + domains)) / max(1, min(6, len(profile.preferred_technologies) + len(profile.preferred_domains))))))
    place = (item.location or "").lower()
    pref = profile.hackathon_preferences if item.opportunity_type == "hackathon" else profile.internship_preferences
    location_score = 85 if any(p.lower() in place for p in pref) else (65 if not place or place == "unknown" else 30)
    eligibility_score = 75 if not item.eligibility else (90 if any(x in item.eligibility.lower() for x in ["student", "fresher", "open to all", "undergraduate"]) else 65)
    experience_score = 75 if not item.eligibility else (90 if any(x in item.eligibility.lower() for x in ["entry", "fresher", "no experience"]) else 60)
    overall = round(skill_score * .45 + eligibility_score * .2 + location_score * .2 + experience_score * .15)
    explanation = (f"Matches {', '.join((skills + domains)[:3])}. " if skills or domains else "Potential fit based on your profile. ") + ("Some details are still unverified." if not item.eligibility or not item.location else "Location and eligibility details are available.")
    return {"overall_score": overall, "skill_score": skill_score, "eligibility_score": eligibility_score, "location_score": location_score, "experience_score": experience_score, "explanation": explanation}

