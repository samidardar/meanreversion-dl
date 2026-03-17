"""
Outbound call manager — orchestrates dialing, retry logic, and campaign processing.
Each dial operation gets its own DB session to avoid shared-session race conditions.
"""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import select, update
from db.models import Call, Contact, Campaign, CallStatus, CampaignStatus
from db.database import AsyncSessionLocal
from telephony.twilio_client import make_outbound_call

logger = logging.getLogger(__name__)

# Active call sessions keyed by call_id (in-process registry)
_active_sessions: dict[str, dict] = {}


async def initiate_call(
    contact: Contact,
    task_type: str,
    campaign_id: Optional[str] = None,
) -> Call:
    """
    Create a Call record and dial the contact.
    Uses its own DB session.
    Returns the Call object.
    """
    call_id = str(uuid.uuid4())

    async with AsyncSessionLocal() as db:
        call = Call(
            id=call_id,
            contact_id=contact.id,
            campaign_id=campaign_id,
            mode="outbound",
            task_type=task_type,
            status=CallStatus.dialing,
            detected_language=contact.language,
            created_at=datetime.utcnow(),
        )
        db.add(call)
        await db.flush()

        try:
            call_sid = make_outbound_call(
                to=contact.phone,
                call_id=call_id,
                task_type=task_type,
                language=contact.language.value,
            )
            call.twilio_call_sid = call_sid
            call.status = CallStatus.dialing
        except Exception as e:
            logger.error("Failed to initiate call to %s: %s", contact.phone, e)
            call.status = CallStatus.failed

        await db.commit()

    return call_id


async def dial_campaign(campaign_id: str, concurrency: int = 5) -> None:
    """
    Dial all pending contacts in a campaign with controlled concurrency.
    Creates its own DB sessions — safe to run in background tasks or Celery.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
        campaign = result.scalar_one_or_none()
        if not campaign:
            logger.error("Campaign not found: %s", campaign_id)
            return

        result = await db.execute(
            select(Contact).where(Contact.campaign_id == campaign_id)
        )
        contacts = result.scalars().all()
        task_type = campaign.task_type

        await db.execute(
            update(Campaign)
            .where(Campaign.id == campaign_id)
            .values(status=CampaignStatus.running, total_contacts=len(contacts))
        )
        await db.commit()

    semaphore = asyncio.Semaphore(concurrency)

    async def dial_one(contact: Contact):
        async with semaphore:
            try:
                await initiate_call(contact, task_type, campaign_id=campaign_id)
            except Exception as e:
                logger.error("dial_one error for contact %s: %s", contact.id, e)
            await asyncio.sleep(0.5)

    await asyncio.gather(*[dial_one(c) for c in contacts], return_exceptions=True)

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Campaign)
            .where(Campaign.id == campaign_id)
            .values(status=CampaignStatus.completed)
        )
        await db.commit()

    logger.info("Campaign %s completed (%d contacts)", campaign_id, len(contacts))


def register_session(call_id: str, session_data: dict) -> None:
    _active_sessions[call_id] = session_data


def get_session(call_id: str) -> Optional[dict]:
    return _active_sessions.get(call_id)


def remove_session(call_id: str) -> None:
    _active_sessions.pop(call_id, None)


def get_active_session_count() -> int:
    return len(_active_sessions)
