"""
Reporter node — generates a structured call report after every call.
Uses smart model to extract all relevant information from conversation history.
"""
import json
import logging
from datetime import datetime
from anthropic import AsyncAnthropic
from agent.state import AgentState
from config.settings import settings

logger = logging.getLogger(__name__)

anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key)

REPORT_PROMPT = """You are a call center analyst. Generate a structured report from this call transcript.

Call details:
- Task type: {task_type}
- Mode: {mode}
- Language: {language}
- Duration turns: {turn_count}
- Contact: {contact_name}

Full transcript:
{transcript}

Extracted data so far: {extraction_data}

Return a JSON report with these exact fields:
{{
  "outcome": "string (confirmed/declined/booked/rescheduled/resolved/escalated/no_answer/voicemail/other)",
  "task_completed": boolean,
  "sentiment": "positive|neutral|negative",
  "summary": "1-2 sentence human-readable summary of what happened",
  "extracted_data": {{
    // task-specific fields:
    // delivery: confirmed (bool), special_instructions (str), preferred_time (str)
    // survey: answers (dict of question->answer), nps_score (int or null)
    // reminder: confirmed_attendance (bool), reschedule_requested (bool), new_preferred_time (str)
    // restaurant: reservation_name (str), date (str), time (str), party_size (int), special_requests (str)
    // hotel: guest_name (str), check_in (str), check_out (str), room_type (str), special_requests (str)
    // support: issue_type (str), resolution (str), ticket_created (bool), escalated (bool)
  }},
  "follow_up_required": boolean,
  "follow_up_notes": "string or null"
}}

Respond ONLY with valid JSON."""


async def reporter_node(state: AgentState) -> dict:
    history = state.get("conversation_history", [])
    contact = state.get("contact", {})
    contact_name = f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip()

    transcript_text = "\n".join(
        f"{e['role'].upper()} [{e.get('timestamp', '')}]: {e['content']}"
        for e in history
    )

    try:
        response = await anthropic.messages.create(
            model=settings.llm_smart_model,  # always use smart model for reports
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": REPORT_PROMPT.format(
                    task_type=state["task_type"],
                    mode=state["mode"],
                    language=state.get("detected_language", "en"),
                    turn_count=state.get("turn_count", 0),
                    contact_name=contact_name,
                    transcript=transcript_text,
                    extraction_data=json.dumps(state.get("extraction_data", {})),
                ),
            }],
        )
        report_data = json.loads(response.content[0].text.strip())
    except Exception as e:
        logger.error("Report generation failed: %s", e)
        report_data = {
            "outcome": "unknown",
            "task_completed": state.get("task_completed", False),
            "sentiment": "neutral",
            "summary": "Report generation failed — see transcript.",
            "extracted_data": state.get("extraction_data", {}),
            "follow_up_required": True,
            "follow_up_notes": f"Auto-report failed: {str(e)}",
        }

    report = {
        "call_id": state["call_id"],
        "call_sid": state.get("call_sid", ""),
        "contact": {
            "name": contact_name,
            "phone": contact.get("phone", ""),
        },
        "task_type": state["task_type"],
        "mode": state["mode"],
        "detected_language": state.get("detected_language", "en"),
        "turn_count": state.get("turn_count", 0),
        "transcript": history,
        "generated_at": datetime.utcnow().isoformat(),
        **report_data,
    }

    logger.info("Call report generated: %s (outcome=%s)", state["call_id"], report_data.get("outcome"))

    return {
        "report": report,
        "call_status": "completed",
    }
