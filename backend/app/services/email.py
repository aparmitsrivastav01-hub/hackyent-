"""Resend delivery and accessible HTML/plain-text opportunity digests."""
import asyncio
import html
import re
import threading
from datetime import datetime, timezone
from email.utils import parseaddr

import httpx
from sqlalchemy import and_, select
from sqlalchemy.orm import Session, joinedload

from ..config import get_settings
from ..models import EmailDelivery, EmailDeliveryItem, Opportunity

RESEND_URL = "https://api.resend.com/emails"
SUBJECT = "OpportunityOS | New Hackathons & Internships Found"
EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")
_send_lock = threading.Lock()


class EmailConfigurationError(ValueError):
    pass


class EmailDeliveryError(RuntimeError):
    pass


def valid_address(value: str) -> bool:
    _, address = parseaddr(value or "")
    return bool(EMAIL_RE.fullmatch(address))


def email_configuration():
    settings = get_settings()
    missing = []
    if not settings.resend_api_key.strip():
        missing.append("RESEND_API_KEY")
    if not valid_address(settings.email_from):
        missing.append("EMAIL_FROM (valid sender address)")
    if not valid_address(settings.email_to):
        missing.append("EMAIL_TO (valid recipient address)")
    return settings, missing


def render_digest(opportunities: list[Opportunity], subject: str = SUBJECT) -> tuple[str, str]:
    """Return sanitized responsive HTML and a readable plain-text alternative."""
    sections = {"hackathon": [], "internship": []}
    for item in opportunities:
        if item.opportunity_type in sections:
            sections[item.opportunity_type].append(item)

    plain = ["OpportunityOS found new opportunities for you.", ""]
    html_sections = []
    if not opportunities:
        plain.extend(["No new opportunities were found since your last digest.", "Run discovery again later to check for new hackathons and internships."])
        html_sections.append('<div style="padding:20px;background:#f4f5f8;border-radius:12px;color:#475569">No new opportunities were found since your last digest. Run discovery again later to check for new hackathons and internships.</div>')
    for kind, label in (("hackathon", "Hackathons"), ("internship", "Internships")):
        entries = sections[kind]
        if not entries:
            continue
        plain.extend([label, "=" * len(label)])
        cards = []
        for item in entries:
            title = item.title or "Opportunity"
            org = item.organization or "Organization not listed"
            url = item.application_url or item.source_url
            score = item.match.overall_score if item.match else None
            deadline = item.deadline.strftime("%d %b %Y") if item.deadline else "Not listed"
            description = item.summary or item.description or "Details are available on the opportunity page."
            prize = item.prize_pool if kind == "hackathon" else item.stipend
            location = " · ".join(dict.fromkeys(value for value in (item.location, item.work_mode) if value)) or "Not listed"
            fields = [f"Organization: {org}", f"Deadline: {deadline}", f"Location / format: {location}"]
            if item.eligibility:
                fields.append(f"Eligibility: {item.eligibility}")
            if prize:
                fields.append(f"{'Prize' if kind == 'hackathon' else 'Stipend'}: {prize}")
            if score is not None:
                fields.append(f"Match: {score}%")
            plain.extend([title, *fields, description, f"Link: {url}", ""])
            safe_title, safe_org = html.escape(title), html.escape(org)
            safe_desc, safe_url = html.escape(description), html.escape(url, quote=True)
            detail_rows = "".join(f'<div style="margin-top:7px;color:#64748b;font-size:13px"><b>{html.escape(line.split(":", 1)[0])}:</b> {html.escape(line.split(":", 1)[1].strip())}</div>' for line in fields if not line.startswith("Match:"))
            score_badge = f'<span style="float:right;background:#ede9fe;color:#6d4aff;border-radius:20px;padding:5px 9px;font-size:12px;font-weight:700">{score}% match</span>' if score is not None else ""
            cta = "Register" if kind == "hackathon" else "Apply"
            cards.append(f'<article style="background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:18px;margin:12px 0">{score_badge}<div style="font-size:17px;font-weight:700;color:#172033;padding-right:90px">{safe_title}</div><div style="margin-top:5px;color:#475569;font-size:13px">{safe_org}</div>{detail_rows}<p style="color:#475569;font-size:13px;line-height:1.55">{safe_desc}</p><a href="{safe_url}" style="display:inline-block;background:#6d4aff;color:#fff;text-decoration:none;padding:10px 15px;border-radius:8px;font-size:13px;font-weight:700">{cta} →</a></article>')
        html_sections.append(f'<h2 style="font-size:18px;color:#172033;margin:26px 0 8px">{label}</h2>{"".join(cards)}')

    body = "".join(html_sections)
    html_body = f'''<!doctype html><html><body style="margin:0;background:#f4f6fa;font-family:Arial,Helvetica,sans-serif;color:#172033"><div style="max-width:680px;margin:0 auto;padding:24px 14px"><div style="background:#111827;border-radius:16px;padding:24px;color:white"><div style="font-size:12px;letter-spacing:2px;color:#b8a8ff;font-weight:700">OPPORTUNITYOS</div><h1 style="font-size:23px;margin:12px 0 4px">Your next opportunity, found.</h1><div style="font-size:13px;color:#cbd5e1">{len(opportunities)} new {'opportunities' if len(opportunities) != 1 else 'opportunity'} picked for you</div></div><main style="padding:4px 2px">{body}</main><footer style="padding:18px 4px;color:#94a3b8;font-size:11px">You received this digest because email notifications are enabled in OpportunityOS.</footer></div></body></html>'''
    return html_body, "\n".join(plain)


def _pending_opportunities(db: Session) -> list[Opportunity]:
    sent_ids = select(EmailDeliveryItem.opportunity_id).join(EmailDelivery).where(EmailDelivery.status == "sent", EmailDeliveryItem.opportunity_version == Opportunity.notification_version)
    query = select(Opportunity).options(joinedload(Opportunity.match)).where(~Opportunity.id.in_(sent_ids)).order_by(Opportunity.first_discovered_at, Opportunity.id)
    return list(db.scalars(query).unique().all())


async def _post_resend(payload: dict, api_key: str, client: httpx.AsyncClient | None = None) -> dict:
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=20)
    try:
        for attempt in range(3):
            try:
                response = await client.post(RESEND_URL, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt < 2:
                    await asyncio.sleep(0.15 * (2 ** attempt))
                    continue
                raise EmailDeliveryError("Email provider could not be reached after retries.") from exc
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    await asyncio.sleep(0.15 * (2 ** attempt))
                    continue
            if not response.is_success:
                # Do not return provider response bodies, which are not needed by clients.
                raise EmailDeliveryError(f"Email provider rejected the message (HTTP {response.status_code}).")
            try:
                result = response.json()
            except ValueError:
                result = {}
            return {"id": result.get("id")} if isinstance(result, dict) else {}
        raise EmailDeliveryError("Email provider is rate limiting requests or temporarily unavailable.")
    finally:
        if owns_client:
            await client.aclose()


async def _send_digest_unlocked(db: Session, *, opportunities: list[Opportunity] | None = None, allow_empty: bool = False, is_test: bool = False, client: httpx.AsyncClient | None = None, subject: str | None = None) -> EmailDelivery:
    settings, missing = email_configuration()
    if missing:
        raise EmailConfigurationError("Email is not configured. Set " + ", ".join(missing) + ".")

    selected = [] if is_test else (opportunities if opportunities is not None else _pending_opportunities(db))
    if not selected and not allow_empty and not is_test:
        return None
    subject = "OpportunityOS | Test email" if is_test else (subject or SUBJECT)
    html_body, text_body = render_digest(selected, subject)
    delivery = EmailDelivery(recipient=parseaddr(settings.email_to)[1], subject=subject, status="pending", opportunity_count=len(selected), is_test=is_test)
    db.add(delivery)
    db.flush()
    delivery.items = [EmailDeliveryItem(opportunity=item, opportunity_version=item.notification_version) for item in selected]
    db.commit()
    try:
        response = await _post_resend({"from": settings.email_from, "to": [parseaddr(settings.email_to)[1]], "subject": subject, "html": html_body, "text": text_body}, settings.resend_api_key.strip(), client)
        delivery.status = "sent"
        delivery.provider_message_id = response.get("id")
        delivery.sent_at = datetime.now(timezone.utc)
        delivery.error = None
    except EmailDeliveryError as exc:
        delivery.status = "failed"
        delivery.error = str(exc)
    except Exception:
        delivery.status = "failed"
        delivery.error = "Email delivery failed unexpectedly. Check the server configuration and provider status."
    db.commit()
    return delivery


async def send_digest(db: Session, *, opportunities: list[Opportunity] | None = None, allow_empty: bool = False, is_test: bool = False, client: httpx.AsyncClient | None = None, subject: str | None = None) -> EmailDelivery:
    # Serialize sends within this API process so overlapping discovery runs and
    # dashboard clicks cannot select and email the same unsent records at once.
    await asyncio.to_thread(_send_lock.acquire)
    try:
        return await _send_digest_unlocked(db, opportunities=opportunities, allow_empty=allow_empty, is_test=is_test, client=client, subject=subject)
    finally:
        _send_lock.release()


def serialize_delivery(delivery: EmailDelivery | None):
    if delivery is None:
        return None
    return {"id": delivery.id, "recipient": delivery.recipient, "subject": delivery.subject, "status": delivery.status, "provider": delivery.provider, "provider_message_id": delivery.provider_message_id, "error": delivery.error, "opportunity_count": delivery.opportunity_count, "is_test": delivery.is_test, "created_at": delivery.created_at, "sent_at": delivery.sent_at}
