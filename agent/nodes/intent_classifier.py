"""
Intent classifier node — determines what the caller said and routes appropriately.
Uses fast model (Claude Haiku) to classify intent cheaply.

Possible intents:
- confirm: caller confirms/agrees
- deny: caller declines/disagrees
- question: caller is asking something
- provide_info: caller is giving information (name, date, etc.)
- reschedule: caller wants to reschedule
- language_switch: caller switched language
- unclear: couldn't understand
- end_call: caller wants to hang up
"""
import json
import logging
from datetime import datetime
from anthropic import AsyncAnthropic
from agent.state import AgentState
from config.settings import settings

logger = logging.getLogger(__name__)

anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key)

CLASSIFY_PROMPT = """Analyze this caller utterance and return a JSON object with:
- "intent": one of [confirm, deny, question, provide_info, reschedule, language_switch, end_call, unclear]
- "language": detected language of utterance ("en" or "fr")
- "key_info": any key information extracted (name, date, time, etc.) as a dict
- "requires_smart_model": boolean — true if the response needs complex reasoning

Caller said: "{transcript}"
Previous agent message: "{last_agent_msg}"
Call task: "{task_type}"

Respond ONLY with valid JSON, no extra text."""


async def intent_classifier_node(state: AgentState) -> dict:
    transcript = state.get("current_transcript", "").strip()
    history = state.get("conversation_history", [])
    last_agent_msg = next(
        (e["content"] for e in reversed(history) if e["role"] == "agent"), ""
    )

    if not transcript:
        return {"call_status": "listening"}

    try:
        response = await anthropic.messages.create(
            model=settings.llm_fast_model,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": CLASSIFY_PROMPT.format(
                    transcript=transcript,
                    last_agent_msg=last_agent_msg,
                    task_type=state["task_type"],
                ),
            }],
        )
        raw = response.content[0].text.strip()
        classification = json.loads(raw)
    except Exception as e:
        logger.warning("Intent classification failed: %s", e)
        classification = {
            "intent": "unclear",
            "language": state.get("detected_language", "en"),
            "key_info": {},
            "requires_smart_model": False,
        }

    # Detect language switch
    detected_lang = classification.get("language", state.get("detected_language", "en"))
    language_switched = detected_lang != state.get("detected_language", "en")
    if language_switched:
        logger.info("Language switch detected: %s → %s", state.get("detected_language"), detected_lang)

    # Add caller utterance to history
    caller_entry = {
        "role": "caller",
        "content": transcript,
        "timestamp": datetime.utcnow().isoformat(),
    }

    updates = {
        "conversation_history": [caller_entry],
        "use_smart_model": classification.get("requires_smart_model", False),
        "call_status": "processing",
    }

    if language_switched:
        updates["detected_language"] = detected_lang

    # Merge extracted key_info into extraction_data
    key_info = classification.get("key_info", {})
    if key_info:
        existing = state.get("extraction_data", {}) or {}
        updates["extraction_data"] = {**existing, **key_info}

    # Store intent for graph routing
    updates["_intent"] = classification.get("intent", "unclear")

    return updates
