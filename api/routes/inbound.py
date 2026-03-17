"""Inbound configuration management endpoints."""
import logging
import uuid
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from pydantic import BaseModel
from typing import Optional
from db.database import get_db
from db.models import InboundConfig, Language

router = APIRouter()
logger = logging.getLogger(__name__)


class InboundConfigCreate(BaseModel):
    name: str
    task_type: str
    business_name: str
    agent_name: str = "Alex"
    language: str = "en"
    custom_instructions: str = ""
    knowledge_base: dict = {}


class InboundConfigUpdate(BaseModel):
    name: Optional[str] = None
    business_name: Optional[str] = None
    agent_name: Optional[str] = None
    is_active: Optional[bool] = None
    custom_instructions: Optional[str] = None
    knowledge_base: Optional[dict] = None


@router.get("/inbound-configs", summary="List inbound configurations")
async def list_inbound_configs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(InboundConfig).order_by(InboundConfig.created_at.desc()))
    configs = result.scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "task_type": c.task_type,
            "business_name": c.business_name,
            "agent_name": c.agent_name,
            "is_active": c.is_active,
            "language": c.language.value,
            "custom_instructions": c.custom_instructions,
            "knowledge_base": c.knowledge_base,
        }
        for c in configs
    ]


@router.post("/inbound-configs", summary="Create inbound call configuration")
async def create_inbound_config(
    payload: InboundConfigCreate,
    db: AsyncSession = Depends(get_db),
):
    lang = Language.fr if payload.language.lower() in ("fr", "french") else Language.en

    config = InboundConfig(
        id=str(uuid.uuid4()),
        name=payload.name,
        task_type=payload.task_type,
        business_name=payload.business_name,
        agent_name=payload.agent_name,
        is_active=True,
        language=lang,
        custom_instructions=payload.custom_instructions,
        knowledge_base=payload.knowledge_base or {},
    )
    db.add(config)
    await db.commit()
    return {"id": config.id, "message": "Inbound config created successfully"}


@router.patch("/inbound-configs/{config_id}", summary="Update inbound configuration")
async def update_inbound_config(
    config_id: str,
    payload: InboundConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(InboundConfig).where(InboundConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(config, field, value)

    await db.commit()
    return {"message": "Config updated"}


@router.delete("/inbound-configs/{config_id}", summary="Delete inbound configuration")
async def delete_inbound_config(config_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(InboundConfig).where(InboundConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    await db.execute(delete(InboundConfig).where(InboundConfig.id == config_id))
    await db.commit()
    return {"message": "Config deleted"}
