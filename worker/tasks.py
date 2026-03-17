"""
Celery tasks for background processing.
Used for bulk outbound dialing and retry logic.
"""
import asyncio
import logging
from celery import Celery
from config.settings import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "callcenter",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
)


def _run_async(coro):
    """Run async code from Celery (sync) context."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, name="tasks.dial_campaign", max_retries=3)
def dial_campaign_task(self, campaign_id: str, concurrency: int = 5):
    """
    Celery task to dial all contacts in a campaign.
    Runs the async dial_campaign function in a new event loop.
    """
    from db.database import AsyncSessionLocal
    from telephony.call_manager import dial_campaign

    async def run():
        async with AsyncSessionLocal() as db:
            await dial_campaign(campaign_id, db, concurrency=concurrency)

    try:
        _run_async(run())
        logger.info("Campaign %s dialing completed", campaign_id)
        return {"status": "completed", "campaign_id": campaign_id}
    except Exception as exc:
        logger.error("Campaign %s dialing failed: %s", campaign_id, exc)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="tasks.generate_report")
def generate_report_task(call_id: str):
    """
    Generate a call report for a completed call.
    Called if the in-process reporter failed.
    """
    from db.database import AsyncSessionLocal
    from db.models import Call, CallReport
    from sqlalchemy import select

    async def run():
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Call).where(Call.id == call_id))
            call = result.scalar_one_or_none()
            if not call:
                logger.warning("generate_report_task: call not found %s", call_id)
                return

            existing = await db.execute(
                select(CallReport).where(CallReport.call_id == call_id)
            )
            if existing.scalar_one_or_none():
                logger.info("Report already exists for call %s", call_id)
                return

            logger.info("Generating report for call %s", call_id)
            # Minimal report for calls where session_manager reporter failed
            import uuid
            from datetime import datetime
            report = CallReport(
                id=str(uuid.uuid4()),
                call_id=call_id,
                outcome="unknown",
                task_completed=False,
                sentiment="neutral",
                summary="Report generated retroactively — transcript unavailable.",
                follow_up_required=True,
                follow_up_notes="Please review manually.",
                created_at=datetime.utcnow(),
            )
            db.add(report)
            await db.commit()

    _run_async(run())
