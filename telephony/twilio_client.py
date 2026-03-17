"""
Twilio REST API wrapper — make outbound calls and generate TwiML.
"""
import logging
from typing import Optional
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
from config.settings import settings

logger = logging.getLogger(__name__)

_client: Optional[Client] = None


def get_twilio_client() -> Client:
    global _client
    if _client is None:
        _client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    return _client


def make_outbound_call(
    to: str,
    call_id: str,
    task_type: str,
    language: str = "en",
) -> str:
    """
    Initiate an outbound call.
    Returns the Twilio CallSid.

    When Twilio answers, it will hit our /webhooks/twilio/voice endpoint
    which returns TwiML to establish a Media Stream WebSocket.
    """
    client = get_twilio_client()

    # Twilio calls our voice endpoint, which opens a Media Stream WebSocket
    voice_url = f"{settings.base_url}/webhooks/twilio/voice"
    status_url = f"{settings.base_url}/webhooks/twilio/status"

    call = client.calls.create(
        to=to,
        from_=settings.twilio_phone_number,
        url=voice_url,
        status_callback=status_url,
        status_callback_method="POST",
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        method="POST",
    )

    logger.info("Outbound call initiated: call_sid=%s to=%s call_id=%s", call.sid, to, call_id)
    return call.sid


def generate_media_stream_twiml(call_id: str) -> str:
    """
    Generate TwiML that opens a Media Stream WebSocket to our server.
    Twilio will stream audio to/from ws://{base_url}/media/{call_id}
    """
    response = VoiceResponse()

    # Brief pause to let WebSocket establish
    response.pause(length=1)

    connect = Connect()
    stream = Stream(
        url=f"{settings.base_url.replace('https://', 'wss://').replace('http://', 'ws://')}/media/{call_id}",
    )
    stream.parameter(name="call_id", value=call_id)
    connect.append(stream)
    response.append(connect)

    return str(response)


def generate_inbound_twiml(call_id: str) -> str:
    """
    TwiML for inbound calls — same as outbound, just different call_id source.
    """
    return generate_media_stream_twiml(call_id)


def end_call(call_sid: str) -> None:
    """Hang up a call programmatically."""
    try:
        client = get_twilio_client()
        client.calls(call_sid).update(status="completed")
        logger.info("Call ended: %s", call_sid)
    except Exception as e:
        logger.warning("Failed to end call %s: %s", call_sid, e)


def get_call_details(call_sid: str) -> dict:
    """Fetch call details from Twilio."""
    client = get_twilio_client()
    call = client.calls(call_sid).fetch()
    return {
        "sid": call.sid,
        "status": call.status,
        "duration": call.duration,
        "start_time": str(call.start_time),
        "end_time": str(call.end_time),
        "price": call.price,
        "price_unit": call.price_unit,
    }
