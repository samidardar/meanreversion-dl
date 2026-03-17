"""
Twilio Media Streams WebSocket bridge.
Handles bidirectional mulaw audio between Twilio and our voice pipeline.

Flow:
  Twilio WS → decode base64 mulaw → feed to Deepgram STT
  Cartesia TTS → encode base64 mulaw → send to Twilio WS
"""
import asyncio
import base64
import json
import logging
from typing import Callable, Optional, Awaitable
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class TwilioMediaBridge:
    """
    Manages a single Twilio Media Streams WebSocket connection.

    Incoming audio from caller → on_audio_chunk callback
    Outgoing audio (TTS) → queue chunks via send_audio()
    Interruption detection → on_interruption_detected callback
    """

    def __init__(
        self,
        websocket: WebSocket,
        on_audio_chunk: Callable[[bytes], Awaitable[None]],
        on_call_started: Optional[Callable[[str, str], Awaitable[None]]] = None,
        on_call_ended: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self.websocket = websocket
        self.on_audio_chunk = on_audio_chunk
        self.on_call_started = on_call_started
        self.on_call_ended = on_call_ended

        self.call_sid: Optional[str] = None
        self.stream_sid: Optional[str] = None
        self._send_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self._is_playing_tts = False
        self._interruption_callbacks: list[Callable[[], Awaitable[None]]] = []
        self._vad_threshold = 3  # consecutive chunks with energy to detect speech
        self._vad_counter = 0
        self._active = False

    def on_interruption(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._interruption_callbacks.append(callback)

    async def run(self) -> None:
        """Main loop: accept and process Twilio Media Stream messages."""
        self._active = True
        send_task = asyncio.create_task(self._send_loop())
        try:
            async for raw in self._receive_messages():
                await self._handle_message(raw)
        except WebSocketDisconnect:
            logger.info("Twilio WebSocket disconnected (call_sid=%s)", self.call_sid)
        except Exception as e:
            logger.error("Bridge error: %s", e, exc_info=True)
        finally:
            self._active = False
            await self._send_queue.put(None)  # stop sender
            send_task.cancel()
            if self.on_call_ended:
                await self.on_call_ended()

    async def _receive_messages(self):
        while True:
            try:
                data = await self.websocket.receive_text()
                yield data
            except WebSocketDisconnect:
                return

    async def _handle_message(self, raw: str) -> None:
        msg = json.loads(raw)
        event = msg.get("event")

        if event == "connected":
            logger.info("Twilio stream connected")

        elif event == "start":
            start = msg.get("start", {})
            self.call_sid = start.get("callSid")
            self.stream_sid = start.get("streamSid")
            logger.info("Twilio stream started (call_sid=%s)", self.call_sid)
            if self.on_call_started:
                await self.on_call_started(self.call_sid, self.stream_sid)

        elif event == "media":
            payload = msg.get("media", {}).get("payload", "")
            if payload:
                audio_bytes = base64.b64decode(payload)
                # VAD: detect if caller is speaking (energy-based)
                if self._is_playing_tts:
                    energy = sum(abs(b - 128) for b in audio_bytes) / len(audio_bytes)
                    if energy > 10:  # caller speaking threshold
                        self._vad_counter += 1
                        if self._vad_counter >= self._vad_threshold:
                            self._vad_counter = 0
                            await self._fire_interruption()
                    else:
                        self._vad_counter = max(0, self._vad_counter - 1)
                # Always feed audio to STT
                await self.on_audio_chunk(audio_bytes)

        elif event == "stop":
            logger.info("Twilio stream stopped")

    async def _fire_interruption(self) -> None:
        logger.info("Interruption detected — stopping TTS")
        for cb in self._interruption_callbacks:
            try:
                await cb()
            except Exception as e:
                logger.warning("Interruption callback error: %s", e)

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Queue mulaw audio chunk to be sent to Twilio."""
        await self._send_queue.put(audio_bytes)

    async def _send_loop(self) -> None:
        """Drains the send queue and writes audio to Twilio."""
        while self._active:
            chunk = await self._send_queue.get()
            if chunk is None:
                break
            try:
                self._is_playing_tts = True
                payload = base64.b64encode(chunk).decode("utf-8")
                message = {
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": payload},
                }
                await self.websocket.send_text(json.dumps(message))
            except Exception as e:
                logger.warning("Audio send error: %s", e)

        self._is_playing_tts = False

    async def mark_tts_done(self) -> None:
        """Call after streaming all TTS audio to reset playback state."""
        self._is_playing_tts = False
        self._vad_counter = 0

    async def send_clear(self) -> None:
        """Clear Twilio audio buffer (used after interruption)."""
        try:
            clear_msg = {"event": "clear", "streamSid": self.stream_sid}
            await self.websocket.send_text(json.dumps(clear_msg))
        except Exception as e:
            logger.warning("Clear send error: %s", e)

    async def close(self) -> None:
        self._active = False
        try:
            await self.websocket.close()
        except Exception:
            pass
