"""
FastAPI application — AI Call Center System.

REST endpoints:
  POST   /upload                      — CSV/Excel upload
  GET    /campaigns                   — List campaigns
  GET    /campaigns/{id}              — Campaign detail
  POST   /campaigns/{id}/start        — Start dialing
  POST   /campaigns/{id}/pause        — Pause campaign
  GET    /stats                       — Dashboard stats
  GET    /reports                     — Call reports list
  GET    /reports/{call_id}           — Full report + transcript
  GET    /calls                       — Calls list
  GET    /inbound-configs             — Inbound configurations
  POST   /inbound-configs             — Create inbound config
  PATCH  /inbound-configs/{id}        — Update inbound config
  DELETE /inbound-configs/{id}        — Delete inbound config
  POST   /webhooks/twilio/voice       — Twilio voice webhook
  POST   /webhooks/twilio/status      — Twilio status callback

WebSocket:
  WS     /media/{call_id}             — Twilio Media Streams

Frontend:
  GET    /                            — Redirect to dashboard
  STATIC /app/*                       — SPA files
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from config.settings import settings
from db.database import init_db, AsyncSessionLocal
from db.models import Call, Contact, InboundConfig
from api.middleware import SecurityHeadersMiddleware
from api.security import verify_api_key, api_rate_limit
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
    description="Production-grade AI call center — LangGraph + Deepgram + Cartesia + Twilio",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# ── Middleware ─────────────────────────────────────────────────────────────────
app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.environment == "development" else [settings.base_url],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# ── API routes (all require auth + rate limit) ────────────────────────────────
_api_deps = [Depends(verify_api_key), Depends(api_rate_limit)]

app.include_router(upload_router,    tags=["Upload"],        dependencies=_api_deps)
app.include_router(campaigns_router, tags=["Campaigns"],     dependencies=_api_deps)
app.include_router(reports_router,   tags=["Reports"],       dependencies=_api_deps)
app.include_router(inbound_router,   tags=["Inbound Config"],dependencies=_api_deps)

# Twilio webhooks do NOT use API key auth (Twilio signs them with its own signature)
app.include_router(webhooks_router,  tags=["Webhooks"])


# ── Health (public) ────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "service": "AI Call Center", "version": "1.0.0"}


# ── Root redirect to SPA ───────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/app/index.html")


# ── Twilio Media Streams WebSocket ─────────────────────────────────────────────
@app.websocket("/media/{call_id}")
async def media_stream(call_id: str, websocket: WebSocket):
    """
    Twilio Media Streams WebSocket — one per active call.
    Handles bidirectional mulaw audio streaming.
    """
    await websocket.accept()
    logger.info("Media stream opened: call_id=%s", call_id)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Call).where(Call.id == call_id))
        call = result.scalar_one_or_none()

        if not call:
            logger.error("No call record for call_id=%s — closing", call_id)
            await websocket.close(code=4004)
            return

        contact_dict = {}
        if call.contact_id:
            cr = await db.execute(select(Contact).where(Contact.id == call.contact_id))
            contact = cr.scalar_one_or_none()
            if contact:
                contact_dict = {
                    "first_name": contact.first_name,
                    "last_name": contact.last_name,
                    "phone": contact.phone,
                    "reason_for_call": contact.reason_for_call,
                    "custom_data": contact.custom_data,
                }

        inbound_config_dict = None
        if call.mode.value == "inbound":
            icr = await db.execute(
                select(InboundConfig).where(InboundConfig.is_active == True).limit(1)
            )
            ic = icr.scalar_one_or_none()
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
            detect_language=False,
        )
        await session.run(websocket)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: call_id=%s", call_id)
    except Exception as e:
        logger.error("Session error call_id=%s: %s", call_id, e, exc_info=True)
    finally:
        logger.info("Media stream closed: call_id=%s", call_id)


# ── Static files — SPA (mounted last so API routes take priority) ──────────────
_frontend_dir = Path(__file__).parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/app", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
else:
    logger.warning("Frontend directory not found at %s", _frontend_dir)
