"""
Deepgram Nova-2 streaming Speech-to-Text.
Supports English and French with automatic language detection.
"""
import asyncio
import json
import logging
from typing import AsyncGenerator, Callable, Literal, Optional
import websockets
from config.settings import settings

logger = logging.getLogger(__name__)

DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"

# Audio params from Twilio Media Streams: mulaw 8kHz mono
TWILIO_ENCODING = "mulaw"
TWILIO_SAMPLE_RATE = 8000
TWILIO_CHANNELS = 1


def _build_deepgram_url(language: Literal["en", "fr"] = "en") -> str:
    params = {
        "model": "nova-2",
        "language": language,
        "encoding": TWILIO_ENCODING,
        "sample_rate": TWILIO_SAMPLE_RATE,
        "channels": TWILIO_CHANNELS,
        "punctuate": "true",
        "interim_results": "true",
        "endpointing": "300",       # 300ms silence = end of utterance
        "utterance_end_ms": "1000", # force final after 1s
        "smart_format": "true",
        "no_delay": "true",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{DEEPGRAM_WS_URL}?{query}"


class DeepgramSTT:
    """
    Streaming STT client for a single call.
    Usage:
        async with DeepgramSTT(language="fr") as stt:
            async for transcript in stt.transcripts():
                print(transcript.text)
    """

    def __init__(self, language: Literal["en", "fr"] = "en"):
        self.language = language
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._transcript_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._closed = False

    async def __aenter__(self):
        url = _build_deepgram_url(self.language)
        headers = {"Authorization": f"Token {settings.deepgram_api_key}"}
        self._ws = await websockets.connect(url, extra_headers=headers)
        asyncio.ensure_future(self._receive_loop())
        logger.info("Deepgram STT connected (language=%s)", self.language)
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Send raw mulaw audio chunk to Deepgram."""
        if self._ws and not self._closed:
            try:
                await self._ws.send(audio_bytes)
            except Exception as e:
                logger.warning("Deepgram send error: %s", e)

    async def _receive_loop(self) -> None:
        try:
            async for message in self._ws:
                data = json.loads(message)
                channel = data.get("channel", {})
                alternatives = channel.get("alternatives", [])
                if not alternatives:
                    continue
                transcript = alternatives[0].get("transcript", "").strip()
                is_final = data.get("is_final", False)
                speech_final = data.get("speech_final", False)
                if transcript:
                    await self._transcript_queue.put({
                        "text": transcript,
                        "is_final": is_final,
                        "speech_final": speech_final,
                        "confidence": alternatives[0].get("confidence", 0.0),
                    })
        except websockets.ConnectionClosed:
            pass
        except Exception as e:
            logger.error("Deepgram receive error: %s", e)
        finally:
            await self._transcript_queue.put(None)  # sentinel

    async def transcripts(self) -> AsyncGenerator[dict, None]:
        """Yields transcript dicts until connection closes."""
        while True:
            item = await self._transcript_queue.get()
            if item is None:
                break
            yield item

    async def close(self) -> None:
        self._closed = True
        if self._ws:
            try:
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await self._ws.close()
            except Exception:
                pass


class LanguageDetector:
    """
    Run STT in both EN and FR for first N bytes, pick higher confidence.
    Used when CSV has no language column.
    """

    def __init__(self, sample_bytes: int = 16000):
        """
        sample_bytes: how many audio bytes to collect before deciding.
        At 8kHz mulaw, 1 byte = 1ms, so 16000 = 2 seconds.
        """
        self.sample_bytes = sample_bytes
        self._buffer: bytes = b""
        self._decided = False
        self._detected_language: Optional[Literal["en", "fr"]] = None

    async def detect(self, audio_chunk: bytes) -> Optional[Literal["en", "fr"]]:
        """
        Feed audio chunks. Returns detected language once enough audio collected.
        Returns None until decision is made.
        """
        if self._decided:
            return self._detected_language

        self._buffer += audio_chunk
        if len(self._buffer) < self.sample_bytes:
            return None

        # Probe both languages
        results = await asyncio.gather(
            self._probe_language(self._buffer, "en"),
            self._probe_language(self._buffer, "fr"),
            return_exceptions=True,
        )

        en_conf = results[0] if isinstance(results[0], float) else 0.0
        fr_conf = results[1] if isinstance(results[1], float) else 0.0
        self._detected_language = "fr" if fr_conf > en_conf else "en"
        self._decided = True
        logger.info("Language detected: %s (en=%.2f, fr=%.2f)", self._detected_language, en_conf, fr_conf)
        return self._detected_language

    async def _probe_language(self, audio: bytes, language: str) -> float:
        """Send a small audio buffer and return avg confidence of transcripts."""
        url = _build_deepgram_url(language)  # type: ignore
        headers = {"Authorization": f"Token {settings.deepgram_api_key}"}
        total_conf = 0.0
        count = 0
        try:
            async with websockets.connect(url, extra_headers=headers) as ws:
                await ws.send(audio)
                await ws.send(json.dumps({"type": "CloseStream"}))
                async for message in ws:
                    data = json.loads(message)
                    if data.get("is_final"):
                        alts = data.get("channel", {}).get("alternatives", [])
                        if alts and alts[0].get("transcript"):
                            total_conf += alts[0].get("confidence", 0)
                            count += 1
        except Exception as e:
            logger.debug("Language probe (%s) error: %s", language, e)
        return total_conf / count if count else 0.0
