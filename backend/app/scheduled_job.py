"""Entry point used by the external GitHub Actions scheduler."""
import asyncio
import json
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal
from .discovery import execute_discovery
from .models import AutomationSetting, ResearchRun

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("opportunityos.scheduled")


async def main():
    settings = get_settings()
    if not settings.tinyfish_api_key or not settings.gemini_api_key:
        raise RuntimeError("TINYFISH_API_KEY and GEMINI_API_KEY must be configured")
    db = SessionLocal()
    try:
        config = db.get(AutomationSetting, 1)
        if not config or not config.enabled:
            logger.info("Daily automation is disabled; scheduled run skipped.")
            return
        active = db.scalar(select(ResearchRun).where(ResearchRun.status.in_(["queued", "running"])).limit(1))
        if active:
            logger.warning("Run %s is already active; overlapping scheduled run skipped.", active.id)
            return
        run = ResearchRun(opportunity_type="both", source="GitHub Actions scheduled run")
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    daily = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y")
    await execute_discovery(run_id, "both", ["India", "Online", "International online"], max_pages=20, send_empty_digest=True, digest_subject=f"OpportunityOS | Daily Hackathons & Internships | {daily}")
    db = SessionLocal()
    try:
        run = db.get(ResearchRun, run_id)
        result = {"run_id": run_id, "status": run.status, "pages_searched": run.records_found, "saved": run.records_saved, "duplicates": run.duplicates_detected, "errors": run.errors}
        logger.info("Daily discovery result: %s", json.dumps(result, default=str))
        if run.status == "failed":
            raise RuntimeError(f"Scheduled discovery run {run_id} failed")
    finally:
        db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        logger.exception("Daily discovery job failed")
        sys.exit(1)
