"""Call report and stats endpoints."""
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from db.database import get_db
from db.models import CallReport, Call, CallStatus
from telephony.call_manager import get_active_session_count

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/stats", summary="Dashboard statistics")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Returns aggregated stats for the dashboard."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Total calls today
    calls_today_result = await db.execute(
        select(func.count(Call.id))
        .where(Call.created_at >= today_start.replace(tzinfo=None))
    )
    calls_today = calls_today_result.scalar() or 0

    # Active calls (in-process sessions)
    active_calls = get_active_session_count()

    # Completed calls today with reports
    reports_today = await db.execute(
        select(CallReport)
        .join(Call, Call.id == CallReport.call_id)
        .where(CallReport.created_at >= today_start.replace(tzinfo=None))
    )
    reports_list = reports_today.scalars().all()

    completed = len(reports_list)
    task_completed = sum(1 for r in reports_list if r.task_completed)
    success_rate = round((task_completed / completed) * 100) if completed > 0 else None

    # Outcome breakdown
    outcomes: dict[str, int] = {}
    for r in reports_list:
        o = r.outcome or "unknown"
        outcomes[o] = outcomes.get(o, 0) + 1

    # Avg duration
    durations = []
    for r in reports_list:
        call_result = await db.execute(select(Call).where(Call.id == r.call_id))
        c = call_result.scalar_one_or_none()
        if c and c.duration_seconds:
            durations.append(c.duration_seconds)
    avg_duration = round(sum(durations) / len(durations)) if durations else None

    # Cost estimate ($0.06 per call average)
    cost_today = round(completed * 0.06, 2) if completed > 0 else 0.0

    # Hourly breakdown (last 12 hours)
    hourly = []
    for i in range(12):
        hour_start = now - timedelta(hours=11 - i)
        hour_end = hour_start + timedelta(hours=1)
        result = await db.execute(
            select(func.count(Call.id)).where(
                and_(
                    Call.created_at >= hour_start.replace(tzinfo=None),
                    Call.created_at < hour_end.replace(tzinfo=None),
                )
            )
        )
        hourly.append(result.scalar() or 0)

    return {
        "calls_today": calls_today,
        "active_calls": active_calls,
        "completed_today": completed,
        "success_rate": success_rate,
        "avg_duration_seconds": avg_duration,
        "cost_today": cost_today,
        "outcomes": outcomes,
        "hourly_breakdown": hourly,
    }


@router.get("/reports", summary="List recent call reports")
async def list_reports(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CallReport)
        .order_by(CallReport.created_at.desc())
        .limit(min(limit, 200))
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

    call_data = None
    if call:
        contact = None
        if call.contact_id:
            from db.models import Contact
            cr = await db.execute(select(Contact).where(Contact.id == call.contact_id))
            c = cr.scalar_one_or_none()
            if c:
                contact = {"name": f"{c.first_name} {c.last_name}", "phone": c.phone}

        call_data = {
            "mode": call.mode.value,
            "task_type": call.task_type,
            "duration_seconds": call.duration_seconds,
            "detected_language": call.detected_language.value if call.detected_language else "en",
            "started_at": call.started_at.isoformat() if call.started_at else None,
            "contact": contact,
        }

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
        "call": call_data,
        # top-level aliases for the modal
        "detected_language": call_data["detected_language"] if call_data else "en",
        "task_type": call_data["task_type"] if call_data else "—",
        "contact": call_data["contact"] if call_data else None,
    }


@router.get("/calls", summary="List all calls")
async def list_calls(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Call).order_by(Call.created_at.desc()).limit(min(limit, 200))
    )
    calls = result.scalars().all()
    return [
        {
            "id": c.id,
            "twilio_call_sid": c.twilio_call_sid,
            "mode": c.mode.value,
            "task_type": c.task_type,
            "status": c.status.value,
            "detected_language": c.detected_language.value if c.detected_language else "en",
            "duration_seconds": c.duration_seconds,
            "started_at": c.started_at.isoformat() if c.started_at else None,
            "created_at": c.created_at.isoformat(),
        }
        for c in calls
    ]
