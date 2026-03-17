"""Campaign management endpoints."""
import logging
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from db.database import get_db
from db.models import Campaign, Contact, CampaignStatus
from telephony.call_manager import dial_campaign

router = APIRouter()
logger = logging.getLogger(__name__)


class InboundConfigCreate(BaseModel):
    name: str
    task_type: str  # restaurant, hotel, support, custom
    business_name: str
    agent_name: str = "Alex"
    language: str = "en"
    custom_instructions: str = ""
    knowledge_base: dict = {}


@router.get("/campaigns", summary="List all campaigns")
async def list_campaigns(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).order_by(Campaign.created_at.desc()))
    campaigns = result.scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "task_type": c.task_type,
            "status": c.status.value,
            "total_contacts": c.total_contacts,
            "completed_calls": c.completed_calls,
            "created_at": c.created_at.isoformat(),
        }
        for c in campaigns
    ]


@router.get("/campaigns/{campaign_id}", summary="Get campaign details")
async def get_campaign(campaign_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    contacts_result = await db.execute(
        select(Contact).where(Contact.campaign_id == campaign_id)
    )
    contacts = contacts_result.scalars().all()

    return {
        "id": campaign.id,
        "name": campaign.name,
        "task_type": campaign.task_type,
        "status": campaign.status.value,
        "total_contacts": campaign.total_contacts,
        "completed_calls": campaign.completed_calls,
        "created_at": campaign.created_at.isoformat(),
        "contacts": [
            {
                "id": c.id,
                "name": f"{c.first_name} {c.last_name}",
                "phone": c.phone,
                "language": c.language.value,
            }
            for c in contacts
        ],
    }


@router.post("/campaigns/{campaign_id}/start", summary="Start dialing a campaign")
async def start_campaign(
    campaign_id: str,
    background_tasks: BackgroundTasks,
    concurrency: int = 5,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status == CampaignStatus.running:
        raise HTTPException(status_code=409, detail="Campaign already running")
    if campaign.status == CampaignStatus.completed:
        raise HTTPException(status_code=409, detail="Campaign already completed")

    # Dial in background so API returns immediately
    background_tasks.add_task(dial_campaign, campaign_id, db, concurrency)

    return {"message": f"Campaign {campaign_id} started", "concurrency": concurrency}


@router.post("/campaigns/{campaign_id}/pause", summary="Pause a running campaign")
async def pause_campaign(campaign_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign.status = CampaignStatus.paused
    await db.commit()
    return {"message": "Campaign paused"}
