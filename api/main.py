"""
FastAPI application — AI Call Center System.

Endpoints:
  POST /upload                         — CSV/Excel upload
  GET/POST /campaigns                  — Campaign management
  POST /campaigns/{id}/start           — Start outbound dialing
  GET /reports                         — Call reports
  GET /reports/{call_id}               — Single call report
  GET /calls                           — Call list
  GET/POST /inbound-configs            — Inbound mode configuration
  POST /webhooks/twilio/voice          — Twilio voice webhook
  POST /webhooks/twilio/status         — Twilio status callback
  WS  /media/{call_id}                 — Twilio Media Streams WebSocket
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from config.settings import settings
from db.database import init_db, AsyncSessionLocal
from db.models import Call, Contact, InboundConfig
from api.routes.upload import router as upload_router
from api.routes.campaigns import router as campaigns_router
from api.routes.reports import router as reports_router
from api.routes.webhooks import router as webhooks_router
from api.routes.inbound import router as inbound_router
from voice.session_manager import VoiceSession

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting AI Call Center — initializing database...")
    await init_db()
    logger.info("Database ready.")
    yield
    logger.info("Shutting down AI Call Center.")


app = FastAPI(
    title="AI Call Center",
    description="Production-grade AI call center with LangGraph, Deepgram, Cartesia, and Twilio",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(upload_router, tags=["Upload"])
app.include_router(campaigns_router, tags=["Campaigns"])
app.include_router(reports_router, tags=["Reports"])
app.include_router(webhooks_router, tags=["Webhooks"])
app.include_router(inbound_router, tags=["Inbound Config"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "AI Call Center"}


# ─── Twilio Media Streams WebSocket ───────────────────────────────────────────

@app.websocket("/media/{call_id}")
async def media_stream(call_id: str, websocket: WebSocket):
    """
    Twilio Media Streams WebSocket endpoint.
    One WebSocket per active call. Handles bidirectional audio streaming.
    """
    await websocket.accept()
    logger.info("Media stream WebSocket opened for call_id=%s", call_id)

    async with AsyncSessionLocal() as db:
        # Load call record
        result = await db.execute(select(Call).where(Call.id == call_id))
        call = result.scalar_one_or_none()

        if not call:
            logger.error("No call record for call_id=%s", call_id)
            await websocket.close()
            return

        # Load contact info
        contact_dict = {}
        if call.contact_id:
            contact_result = await db.execute(
                select(Contact).where(Contact.id == call.contact_id)
            )
            contact = contact_result.scalar_one_or_none()
            if contact:
                contact_dict = {
                    "first_name": contact.first_name,
                    "last_name": contact.last_name,
                    "phone": contact.phone,
                    "reason_for_call": contact.reason_for_call,
                    "custom_data": contact.custom_data,
                }

        # Load inbound config if applicable
        inbound_config_dict = None
        if call.mode.value == "inbound":
            ic_result = await db.execute(
                select(InboundConfig).where(InboundConfig.is_active == True).limit(1)
            )
            ic = ic_result.scalar_one_or_none()
            if ic:
                inbound_config_dict = {
                    "business_name": ic.business_name,
                    "task_type": ic.task_type,
                    "knowledge_base": ic.knowledge_base,
                    "custom_instructions": ic.custom_instructions,
                    "agent_name": ic.agent_name,
                }

        language = call.detected_language.value if call.detected_language else "en"

    try:
        session = VoiceSession(
            call_id=call_id,
            websocket=websocket,
            mode=call.mode.value,
            task_type=call.task_type,
            contact=contact_dict,
            inbound_config=inbound_config_dict,
            initial_language=language,
            detect_language=False,  # language is pre-set from contact CSV or detected at call creation
        )
        await session.run(websocket)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for call_id=%s", call_id)
    except Exception as e:
        logger.error("Session error for call_id=%s: %s", call_id, e, exc_info=True)
    finally:
        logger.info("Media stream closed for call_id=%s", call_id)
