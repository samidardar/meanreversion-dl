"""
Voice Session Manager — orchestrates a live call session.

Ties together:
- TwilioMediaBridge (audio I/O)
- DeepgramSTT (speech-to-text)
- CartesiaTTS with InterruptibleTTS (text-to-speech)
- LangGraph agent (conversation logic)
- DB persistence (call records, reports)
"""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional, Literal
from fastapi import WebSocket
from sqlalchemy import update

from voice.audio_bridge import TwilioMediaBridge
from voice.stt import DeepgramSTT, LanguageDetector
from voice.tts import InterruptibleTTS
from agent.graph import greeting_graph, conversation_graph
from agent.state import AgentState
from db.models import Call, CallReport, CallStatus
from db.database import AsyncSessionLocal
from telephony.call_manager import register_session, remove_session

logger = logging.getLogger(__name__)


class VoiceSession:
    """Manages the full lifecycle of one AI-powered call."""

    def __init__(
        self,
        call_id: str,
        websocket: WebSocket,
        mode: Literal["outbound", "inbound"],
        task_type: str,
        contact: dict,
        inbound_config: Optional[dict] = None,
        initial_language: Literal["en", "fr"] = "en",
        detect_language: bool = False,
    ):
        self.call_id = call_id
        self.mode = mode
        self.task_type = task_type
        self.contact = contact
        self.inbound_config = inbound_config
        self.initial_language: Literal["en", "fr"] = initial_language
        self.detect_language = detect_language

        self.tts = InterruptibleTTS(language=initial_language)
        self.bridge: Optional[TwilioMediaBridge] = None
        self.lang_detector = LanguageDetector() if detect_language else None

        self.agent_state: AgentState = self._init_agent_state()
        self._call_active = False
        self._language_resolved = not detect_language
        self._call_started_event = asyncio.Event()
        self._speaking_lock = asyncio.Lock()
        self._current_stt: Optional[DeepgramSTT] = None

    def _init_agent_state(self) -> AgentState:
        return {
            "call_id": self.call_id,
            "call_sid": "",
            "mode": self.mode,
            "task_type": self.task_type,
            "contact": self.contact,
            "inbound_config": self.inbound_config,
            "detected_language": self.initial_language,
            "language_confirmed": not self.detect_language,
            "conversation_history": [],
            "current_transcript": "",
            "agent_response": "",
            "call_status": "initializing",
            "task_completed": False,
            "turn_count": 0,
            "interruption_detected": False,
            "max_turns": 15,
            "extraction_data": {},
            "report": None,
            "use_smart_model": False,
            "_intent": "",
            "_started_at": None,
            "error_message": None,
        }

    # ─── Main entry point ─────────────────────────────────────────────────────

    async def run(self, websocket: WebSocket) -> None:
        self._call_active = True

        async with DeepgramSTT(language=self.initial_language) as stt:
            self._current_stt = stt
            self.bridge = TwilioMediaBridge(
                websocket=websocket,
                on_audio_chunk=self._on_audio_chunk,
                on_call_started=self._on_call_started,
                on_call_ended=self._on_call_ended,
            )
            self.bridge.on_interruption(self._on_interruption)
            register_session(self.call_id, {"session": self})

            await asyncio.gather(
                self.bridge.run(),
                self._conversation_loop(),
                return_exceptions=True,
            )

    # ─── Audio callbacks ───────────────────────────────────────────────────────

    async def _on_audio_chunk(self, audio: bytes) -> None:
        if self.lang_detector and not self._language_resolved:
            detected = await self.lang_detector.detect(audio)
            if detected:
                self._language_resolved = True
                self.agent_state["detected_language"] = detected
                self.agent_state["language_confirmed"] = True
                self.tts = InterruptibleTTS(language=detected)
                logger.info("Language detected and TTS switched to: %s", detected)

        if self._current_stt:
            await self._current_stt.send_audio(audio)

    async def _on_call_started(self, call_sid: str, stream_sid: str) -> None:
        logger.info("Call stream started: call_sid=%s", call_sid)
        self.agent_state["call_sid"] = call_sid
        self.agent_state["_started_at"] = datetime.utcnow().isoformat()
        await self._update_db_call_started(call_sid)
        self._call_started_event.set()

    async def _on_call_ended(self) -> None:
        logger.info("Call stream ended: call_id=%s", self.call_id)
        self._call_active = False
        if not self.agent_state.get("report"):
            await self._run_reporter()
        await self._save_report_to_db()
        remove_session(self.call_id)

    async def _on_interruption(self) -> None:
        self.tts.interrupt()
        self.agent_state["interruption_detected"] = True
        if self.bridge:
            await self.bridge.send_clear()
        logger.info("Interruption detected on call %s", self.call_id)

    # ─── Conversation loop ─────────────────────────────────────────────────────

    async def _conversation_loop(self) -> None:
        try:
            await asyncio.wait_for(self._call_started_event.wait(), timeout=30)
        except asyncio.TimeoutError:
            logger.warning("Call %s never connected, aborting", self.call_id)
            return

        # Greeting
        await self._speak_greeting()

        # Per-utterance loop driven by STT transcripts
        if self._current_stt:
            async for transcript_item in self._current_stt.transcripts():
                if not self._call_active:
                    break

                if not transcript_item.get("speech_final") and not transcript_item.get("is_final"):
                    continue

                text = transcript_item.get("text", "").strip()
                if not text:
                    continue

                logger.info("[%s] Caller: %s", self.call_id, text)

                self.agent_state["current_transcript"] = text
                self.agent_state["interruption_detected"] = False

                updated = await conversation_graph.ainvoke(self.agent_state)
                self.agent_state.update(updated)

                reply = self.agent_state.get("agent_response", "")
                if reply:
                    logger.info("[%s] Agent: %s", self.call_id, reply)
                    await self._speak(reply)

                status = self.agent_state.get("call_status", "")
                if status == "completed" or self.agent_state.get("report"):
                    await asyncio.sleep(1.5)
                    if self.bridge:
                        await self.bridge.close()
                    break

    async def _speak_greeting(self) -> None:
        updated = await greeting_graph.ainvoke(self.agent_state)
        self.agent_state.update(updated)
        greeting = self.agent_state.get("agent_response", "")
        if greeting:
            logger.info("[%s] Agent greeting: %s", self.call_id, greeting)
            await self._speak(greeting)

    async def _speak(self, text: str) -> None:
        if not self.bridge:
            return
        async with self._speaking_lock:
            lang = self.agent_state.get("detected_language", "en")
            if self.tts.language != lang:
                self.tts = InterruptibleTTS(language=lang)

            async for chunk in self.tts.stream(text):
                if not self._call_active:
                    break
                await self.bridge.send_audio(chunk)

            await self.bridge.mark_tts_done()

    # ─── DB helpers ───────────────────────────────────────────────────────────

    async def _update_db_call_started(self, call_sid: str) -> None:
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(Call)
                    .where(Call.id == self.call_id)
                    .values(
                        twilio_call_sid=call_sid,
                        status=CallStatus.active,
                        started_at=datetime.utcnow(),
                    )
                )
                await db.commit()
        except Exception as e:
            logger.error("Failed to update call started: %s", e)

    async def _run_reporter(self) -> None:
        from agent.nodes.reporter import reporter_node
        try:
            updated = await reporter_node(self.agent_state)
            self.agent_state.update(updated)
        except Exception as e:
            logger.error("Reporter failed for call %s: %s", self.call_id, e)

    async def _save_report_to_db(self) -> None:
        report = self.agent_state.get("report")

        try:
            async with AsyncSessionLocal() as db:
                # Calculate duration
                duration = None
                started_str = self.agent_state.get("_started_at")
                if started_str:
                    try:
                        started = datetime.fromisoformat(started_str)
                        duration = int((datetime.utcnow() - started).total_seconds())
                    except Exception:
                        pass

                await db.execute(
                    update(Call)
                    .where(Call.id == self.call_id)
                    .values(
                        status=CallStatus.completed,
                        ended_at=datetime.utcnow(),
                        duration_seconds=duration,
                        detected_language=self.agent_state.get("detected_language", "en"),
                    )
                )

                if report:
                    db_report = CallReport(
                        id=str(uuid.uuid4()),
                        call_id=self.call_id,
                        outcome=report.get("outcome", "unknown"),
                        sentiment=report.get("sentiment"),
                        task_completed=report.get("task_completed", False),
                        extracted_data=report.get("extracted_data"),
                        transcript=report.get("transcript"),
                        summary=report.get("summary"),
                        follow_up_required=report.get("follow_up_required", False),
                        follow_up_notes=report.get("follow_up_notes"),
                    )
                    db.add(db_report)

                await db.commit()
                logger.info("Call %s saved to DB", self.call_id)
        except Exception as e:
            logger.error("Failed to save call %s to DB: %s", self.call_id, e)
