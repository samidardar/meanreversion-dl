"""
Outbound call manager — orchestrates dialing, retry logic, and campaign processing.
"""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from db.models import Call, Contact, Campaign, CallStatus, CampaignStatus
from telephony.twilio_client import make_outbound_call
from config.settings import settings

logger = logging.getLogger(__name__)

# Active call sessions keyed by call_id
# Maps call_id → AgentState dict (managed by voice session)
_active_sessions: dict[str, dict] = {}

MAX_RETRY_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 60  # wait before retry


async def initiate_call(
    contact: Contact,
    task_type: str,
    db: AsyncSession,
    campaign_id: Optional[str] = None,
) -> Call:
    """
    Create a Call record and dial the contact.
    Returns the Call object (status=dialing).
    """
    call_id = str(uuid.uuid4())

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
    return call


async def dial_campaign(campaign_id: str, db: AsyncSession, concurrency: int = 5) -> None:
    """
    Dial all pending contacts in a campaign with controlled concurrency.
    Updates campaign status as it progresses.
    """
    # Fetch campaign and its contacts
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        logger.error("Campaign not found: %s", campaign_id)
        return

    result = await db.execute(
        select(Contact).where(Contact.campaign_id == campaign_id)
    )
    contacts = result.scalars().all()

    await db.execute(
        update(Campaign)
        .where(Campaign.id == campaign_id)
        .values(status=CampaignStatus.running, total_contacts=len(contacts))
    )
    await db.commit()

    semaphore = asyncio.Semaphore(concurrency)

    async def dial_one(contact: Contact):
        async with semaphore:
            await initiate_call(contact, campaign.task_type, db, campaign_id=campaign_id)
            # Space calls slightly to avoid burst
            await asyncio.sleep(0.5)

    tasks = [dial_one(c) for c in contacts]
    await asyncio.gather(*tasks, return_exceptions=True)

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
