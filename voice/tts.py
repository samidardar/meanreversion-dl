"""
Cartesia Sonic streaming Text-to-Speech.
Ultra-low latency (<100ms), highly natural voices, bilingual EN/FR.
"""
import asyncio
import json
import logging
from typing import AsyncGenerator, Literal, Optional
import httpx
import websockets
from config.settings import settings

logger = logging.getLogger(__name__)

CARTESIA_WS_URL = "wss://api.cartesia.ai/tts/websocket"
CARTESIA_REST_URL = "https://api.cartesia.ai/tts/bytes"
CARTESIA_API_VERSION = "2024-06-10"

# Output format matching Twilio Media Streams (mulaw 8kHz)
OUTPUT_FORMAT = {
    "container": "raw",
    "encoding": "pcm_mulaw",
    "sample_rate": 8000,
}


def _get_voice_id(language: Literal["en", "fr"]) -> str:
    if language == "fr":
        return settings.cartesia_voice_id_fr
    return settings.cartesia_voice_id_en


def _add_ssml_naturalness(text: str, language: Literal["en", "fr"]) -> str:
    """
    Insert natural pauses and emphasis via Cartesia-compatible markup.
    Cartesia uses its own prosody controls, so we pre-process the text.
    """
    # Add a brief pause after sentence-ending punctuation for natural pacing
    text = text.replace(". ", ".  ")   # double space = slight pause
    text = text.replace("! ", "!  ")
    text = text.replace("? ", "?  ")
    return text


class CartesiaTTS:
    """
    Streaming TTS via Cartesia WebSocket API.
    Yields raw mulaw audio chunks suitable for Twilio Media Streams.
    """

    def __init__(self, language: Literal["en", "fr"] = "en"):
        self.language = language
        self.voice_id = _get_voice_id(language)
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._context_id: Optional[str] = None

    async def __aenter__(self):
        headers = {
            "X-API-Key": settings.cartesia_api_key,
            "Cartesia-Version": CARTESIA_API_VERSION,
        }
        self._ws = await websockets.connect(
            CARTESIA_WS_URL,
            extra_headers=headers,
            max_size=10 * 1024 * 1024,
        )
        logger.info("Cartesia TTS connected (language=%s, voice=%s)", self.language, self.voice_id)
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        """
        Stream synthesized audio for `text`.
        Yields raw mulaw audio chunks.
        """
        import uuid
        self._context_id = str(uuid.uuid4())
        text = _add_ssml_naturalness(text, self.language)

        request = {
            "model_id": settings.cartesia_model_id,
            "transcript": text,
            "voice": {
                "mode": "id",
                "id": self.voice_id,
            },
            "output_format": OUTPUT_FORMAT,
            "context_id": self._context_id,
            "continue": False,
        }

        await self._ws.send(json.dumps(request))

        async for message in self._ws:
            data = json.loads(message)

            if data.get("type") == "error":
                logger.error("Cartesia TTS error: %s", data)
                break

            if data.get("type") == "chunk":
                audio_b64 = data.get("data", "")
                if audio_b64:
                    import base64
                    yield base64.b64decode(audio_b64)

            if data.get("done") or data.get("type") == "done":
                break

    async def close(self) -> None:
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass


async def synthesize_once(text: str, language: Literal["en", "fr"] = "en") -> bytes:
    """
    Simple helper: synthesize full text, return complete audio bytes.
    Use CartesiaTTS.synthesize() for streaming.
    """
    chunks = []
    async with CartesiaTTS(language=language) as tts:
        async for chunk in tts.synthesize(text):
            chunks.append(chunk)
    return b"".join(chunks)


class InterruptibleTTS:
    """
    Wraps CartesiaTTS with interruption support.
    When interrupted, stops sending audio immediately.
    """

    def __init__(self, language: Literal["en", "fr"] = "en"):
        self.language = language
        self._interrupted = asyncio.Event()

    def interrupt(self) -> None:
        """Signal that the caller has started speaking — stop TTS."""
        self._interrupted.set()

    def reset(self) -> None:
        self._interrupted.clear()

    async def stream(self, text: str) -> AsyncGenerator[bytes, None]:
        """Stream audio, stopping early if interrupted."""
        self.reset()
        async with CartesiaTTS(language=self.language) as tts:
            async for chunk in tts.synthesize(text):
                if self._interrupted.is_set():
                    logger.info("TTS interrupted by caller")
                    return
                yield chunk
