"""Call report endpoints."""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.database import get_db
from db.models import CallReport, Call

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/reports", summary="List recent call reports")
async def list_reports(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CallReport)
        .order_by(CallReport.created_at.desc())
        .limit(limit)
    )
    reports = result.scalars().all()
    return [
        {
            "id": r.id,
            "call_id": r.call_id,
            "outcome": r.outcome,
            "task_completed": r.task_completed,
            "sentiment": r.sentiment,
            "summary": r.summary,
            "follow_up_required": r.follow_up_required,
            "created_at": r.created_at.isoformat(),
        }
        for r in reports
    ]


@router.get("/reports/{call_id}", summary="Get full report for a call")
async def get_report(call_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CallReport).where(CallReport.call_id == call_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found for this call")

    call_result = await db.execute(select(Call).where(Call.id == call_id))
    call = call_result.scalar_one_or_none()

    return {
        "id": report.id,
        "call_id": report.call_id,
        "outcome": report.outcome,
        "task_completed": report.task_completed,
        "sentiment": report.sentiment,
        "summary": report.summary,
        "extracted_data": report.extracted_data,
        "transcript": report.transcript,
        "follow_up_required": report.follow_up_required,
        "follow_up_notes": report.follow_up_notes,
        "created_at": report.created_at.isoformat(),
        "call": {
            "mode": call.mode.value if call else None,
            "task_type": call.task_type if call else None,
            "duration_seconds": call.duration_seconds if call else None,
            "detected_language": call.detected_language.value if call else None,
            "started_at": call.started_at.isoformat() if call and call.started_at else None,
        } if call else None,
    }


@router.get("/calls", summary="List all calls")
async def list_calls(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Call).order_by(Call.created_at.desc()).limit(limit)
    )
    calls = result.scalars().all()
    return [
        {
            "id": c.id,
            "twilio_call_sid": c.twilio_call_sid,
            "mode": c.mode.value,
            "task_type": c.task_type,
            "status": c.status.value,
            "detected_language": c.detected_language.value,
            "duration_seconds": c.duration_seconds,
            "created_at": c.created_at.isoformat(),
        }
        for c in calls
    ]
