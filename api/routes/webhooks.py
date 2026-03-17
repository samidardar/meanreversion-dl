"""
Twilio webhook endpoints.
POST /webhooks/twilio/voice  — called when Twilio connects to a call (in/outbound)
POST /webhooks/twilio/status — call status updates
"""
import logging
import uuid
from fastapi import APIRouter, Request, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.database import get_db
from db.models import Call, CallStatus, InboundConfig
from telephony.twilio_client import generate_media_stream_twiml, generate_inbound_twiml
from telephony.call_manager import get_session

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/webhooks/twilio/voice", summary="Twilio voice webhook")
async def twilio_voice_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Called by Twilio when a call connects.
    Returns TwiML that opens a Media Stream WebSocket to our /media/{call_id} endpoint.
    """
    form = await request.form()
    call_sid = form.get("CallSid", "")
    direction = form.get("Direction", "outbound-api")
    from_number = form.get("From", "")
    to_number = form.get("To", "")

    logger.info("Twilio voice webhook: call_sid=%s direction=%s", call_sid, direction)

    if direction == "inbound":
        # Create a new call record for inbound
        call_id = str(uuid.uuid4())

        # Find active inbound config
        result = await db.execute(
            select(InboundConfig).where(InboundConfig.is_active == True).limit(1)
        )
        inbound_config = result.scalar_one_or_none()
        task_type = inbound_config.task_type if inbound_config else "support"

        call = Call(
            id=call_id,
            twilio_call_sid=call_sid,
            mode="inbound",
            task_type=task_type,
            status=CallStatus.active,
        )
        db.add(call)
        await db.commit()

        twiml = generate_inbound_twiml(call_id)
    else:
        # Outbound: look up call by matching approach
        # The call_id was passed to Twilio as a parameter in the stream URL
        # Here we find the call by call_sid
        result = await db.execute(
            select(Call).where(Call.twilio_call_sid == call_sid)
        )
        call = result.scalar_one_or_none()

        if not call:
            # Fallback: find pending call for this number
            result = await db.execute(
                select(Call)
                .where(Call.status == CallStatus.dialing)
                .limit(1)
            )
            call = result.scalar_one_or_none()

        if not call:
            logger.error("No call record found for call_sid=%s", call_sid)
            return Response(content="<Response><Hangup/></Response>", media_type="text/xml")

        call_id = call.id
        twiml = generate_media_stream_twiml(call_id)

    return Response(content=twiml, media_type="text/xml")


@router.post("/webhooks/twilio/status", summary="Twilio call status callback")
async def twilio_status_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Called by Twilio with call status updates (initiated, ringing, answered, completed).
    """
    form = await request.form()
    call_sid = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")
    call_duration = form.get("CallDuration", "0")

    logger.info("Call status update: call_sid=%s status=%s", call_sid, call_status)

    status_map = {
        "initiated": CallStatus.dialing,
        "ringing": CallStatus.dialing,
        "in-progress": CallStatus.active,
        "completed": CallStatus.completed,
        "busy": CallStatus.busy,
        "no-answer": CallStatus.no_answer,
        "failed": CallStatus.failed,
    }
    db_status = status_map.get(call_status, CallStatus.failed)

    result = await db.execute(select(Call).where(Call.twilio_call_sid == call_sid))
    call = result.scalar_one_or_none()
    if call:
        call.status = db_status
        if call_status == "completed":
            call.duration_seconds = int(call_duration) if call_duration else None
        await db.commit()

    return Response(content="", status_code=204)
