"""
Voice Session Manager — orchestrates a live call session.

Ties together:
- TwilioMediaBridge (audio I/O)
- DeepgramSTT (speech-to-text)
- CartesiaTTS with InterruptibleTTS (text-to-speech)
- LangGraph agent (conversation logic)
- DB persistence (call records, reports)

This is the heart of the system. One VoiceSession per active call.
"""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional, Literal
from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

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
    """
    Manages the full lifecycle of one AI-powered call.
    """

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

        # Language handling
        self.initial_language = initial_language
        self.detect_language = detect_language

        # Streaming components (initialized in run())
        self.stt: Optional[DeepgramSTT] = None
        self.tts = InterruptibleTTS(language=initial_language)
        self.bridge: Optional[TwilioMediaBridge] = None
        self.lang_detector = LanguageDetector() if detect_language else None

        # Agent state
        self.agent_state: AgentState = self._init_agent_state()
        self._call_active = False
        self._language_resolved = not detect_language  # True if language pre-set from CSV

        # Queues and events
        self._transcript_ready = asyncio.Event()
        self._latest_transcript: str = ""
        self._speaking_lock = asyncio.Lock()

    def _init_agent_state(self) -> AgentState:
        return {
            "call_id": self.call_id,
            "call_sid": "",  # filled when Twilio starts stream
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
            "error_message": None,
            "_intent": "",
        }

    # ─── Main entry point ─────────────────────────────────────────────────────

    async def run(self, websocket: WebSocket) -> None:
        """Run the full voice session from WebSocket connection to call end."""
        self._call_active = True

        async with DeepgramSTT(language=self.initial_language) as stt:
            self.stt = stt
            self.bridge = TwilioMediaBridge(
                websocket=websocket,
                on_audio_chunk=self._on_audio_chunk,
                on_call_started=self._on_call_started,
                on_call_ended=self._on_call_ended,
            )
            self.bridge.on_interruption(self._on_interruption)
            register_session(self.call_id, {"session": self, "state": self.agent_state})

            # Run bridge and conversation loop concurrently
            await asyncio.gather(
                self.bridge.run(),
                self._conversation_loop(),
                return_exceptions=True,
            )

    # ─── Audio callbacks ───────────────────────────────────────────────────────

    async def _on_audio_chunk(self, audio: bytes) -> None:
        """Receive mulaw audio from caller → STT."""
        # Language detection during first 2 seconds
        if self.lang_detector and not self._language_resolved:
            detected = await self.lang_detector.detect(audio)
            if detected:
                self._language_resolved = True
                self.agent_state["detected_language"] = detected
                self.agent_state["language_confirmed"] = True
                self.tts = InterruptibleTTS(language=detected)
                # Restart STT with detected language
                await self.stt.close()
                self.stt = await DeepgramSTT(language=detected).__aenter__()
                logger.info("Switched STT/TTS to detected language: %s", detected)

        await self.stt.send_audio(audio)

    async def _on_call_started(self, call_sid: str, stream_sid: str) -> None:
        """Twilio stream started — update state and begin greeting."""
        logger.info("Call stream started: call_sid=%s", call_sid)
        self.agent_state["call_sid"] = call_sid
        await self._update_db_call_status(call_sid, CallStatus.active)
        # Trigger greeting
        self._transcript_ready.set()  # unblock conversation loop (special initial trigger)

    async def _on_call_ended(self) -> None:
        """Twilio stream ended — ensure report is generated."""
        logger.info("Call stream ended: call_id=%s", self.call_id)
        self._call_active = False
        # If report not yet generated, do it now
        if not self.agent_state.get("report"):
            await self._run_reporter()
        await self._save_report_to_db()
        remove_session(self.call_id)

    async def _on_interruption(self) -> None:
        """Caller started speaking — interrupt TTS immediately."""
        self.tts.interrupt()
        self.agent_state["interruption_detected"] = True
        logger.info("Interruption detected on call %s", self.call_id)

    # ─── Conversation loop ─────────────────────────────────────────────────────

    async def _conversation_loop(self) -> None:
        """
        Main loop:
        1. Wait for call to start
        2. Generate and speak greeting
        3. Listen for transcript
        4. Process with LangGraph
        5. Speak response
        6. Repeat until call ends
        """
        # Wait for call to connect (bridge sets event in _on_call_started)
        await asyncio.wait_for(self._transcript_ready.wait(), timeout=30)
        self._transcript_ready.clear()

        # Step 1: Greeting
        await self._speak_greeting()

        # Step 2: STT listen + agent turn loop
        async for transcript_item in self.stt.transcripts():
            if not self._call_active:
                break

            if not transcript_item.get("speech_final") and not transcript_item.get("is_final"):
                continue  # skip interim results

            transcript_text = transcript_item.get("text", "").strip()
            if not transcript_text:
                continue

            logger.info("Caller said: %s", transcript_text)

            # Process through LangGraph
            self.agent_state["current_transcript"] = transcript_text
            self.agent_state["interruption_detected"] = False

            updated_state = await conversation_graph.ainvoke(self.agent_state)
            self.agent_state.update(updated_state)

            reply = self.agent_state.get("agent_response", "")
            if reply:
                await self._speak(reply)

            # Check if call should end
            status = self.agent_state.get("call_status", "")
            if status == "completed" or self.agent_state.get("report"):
                logger.info("Call %s completed, hanging up", self.call_id)
                await asyncio.sleep(1.5)  # let final audio play
                await self.bridge.close()
                break

    async def _speak_greeting(self) -> None:
        """Generate and speak the opening greeting."""
        updated = await greeting_graph.ainvoke(self.agent_state)
        self.agent_state.update(updated)
        greeting = self.agent_state.get("agent_response", "")
        if greeting:
            await self._speak(greeting)

    async def _speak(self, text: str) -> None:
        """Synthesize and stream TTS audio to caller."""
        async with self._speaking_lock:
            lang = self.agent_state.get("detected_language", "en")
            # Update TTS language if it changed (mid-call language switch)
            if self.tts.language != lang:
                self.tts = InterruptibleTTS(language=lang)

            async for chunk in self.tts.stream(text):
                if not self._call_active:
                    break
                await self.bridge.send_audio(chunk)

            await self.bridge.mark_tts_done()

    # ─── Report and DB helpers ─────────────────────────────────────────────────

    async def _run_reporter(self) -> None:
        from agent.nodes.reporter import reporter_node
        try:
            updated = await reporter_node(self.agent_state)
            self.agent_state.update(updated)
        except Exception as e:
            logger.error("Reporter failed for call %s: %s", self.call_id, e)

    async def _update_db_call_status(self, call_sid: str, status: CallStatus) -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Call)
                .where(Call.id == self.call_id)
                .values(
                    twilio_call_sid=call_sid,
                    status=status,
                    started_at=datetime.utcnow() if status == CallStatus.active else None,
                )
            )
            await db.commit()

    async def _save_report_to_db(self) -> None:
        report = self.agent_state.get("report")
        if not report:
            return

        async with AsyncSessionLocal() as db:
            # Update call record
            duration = None
            start = self.agent_state.get("_started_at")
            if start:
                duration = int((datetime.utcnow() - start).total_seconds())

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

            # Create report record
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
            logger.info("Report saved to DB for call %s", self.call_id)
