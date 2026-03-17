"""LangGraph AgentState — single source of truth for a call session."""
from datetime import datetime
from typing import Literal, Optional, TypedDict, Annotated
import operator


class TranscriptEntry(TypedDict):
    role: Literal["agent", "caller"]
    content: str
    timestamp: str


class AgentState(TypedDict):
    # Call identity
    call_id: str
    call_sid: str
    mode: Literal["outbound", "inbound"]
    task_type: str  # delivery, survey, reminder, restaurant, hotel, support

    # Contact / context
    contact: dict  # {first_name, last_name, phone, reason_for_call, ...}
    inbound_config: Optional[dict]  # for inbound: business info, knowledge_base, etc.

    # Language
    detected_language: Literal["en", "fr"]
    language_confirmed: bool  # True once we've detected or used CSV language

    # Conversation
    conversation_history: Annotated[list[TranscriptEntry], operator.add]
    current_transcript: str   # latest caller utterance (from STT)
    agent_response: str       # generated agent reply

    # Flow control
    call_status: Literal["initializing", "greeting", "listening", "processing", "speaking", "wrapping_up", "completed", "failed"]
    task_completed: bool
    turn_count: int
    interruption_detected: bool
    max_turns: int  # safety limit

    # Data extraction (populated as call progresses)
    extraction_data: dict  # task-specific: {confirmed: bool, booking_ref: str, etc.}

    # Report (populated at end of call)
    report: Optional[dict]

    # LLM routing
    use_smart_model: bool  # True for complex turns, False for simple ones

    # Internal routing signals (not persisted to DB)
    _intent: str          # set by intent_classifier, read by graph routing
    _started_at: Optional[str]  # ISO timestamp when call started

    # Internal
    error_message: Optional[str]
