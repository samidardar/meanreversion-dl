"""
Completion checker node — decides whether the task is done and the call should wrap up.
Uses fast model to check based on conversation history and extracted data.
"""
import json
import logging
from anthropic import AsyncAnthropic
from agent.state import AgentState
from config.settings import settings

logger = logging.getLogger(__name__)

anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key)

COMPLETION_PROMPT = """Review this call conversation and determine if the task is complete.

Task type: {task_type}
Extraction data so far: {extraction_data}
Turn count: {turn_count}
Max turns: {max_turns}
Last 3 exchanges:
{recent_history}

Return JSON:
- "task_completed": boolean — has the primary task been accomplished?
- "should_wrap_up": boolean — should the agent start closing the call?
- "reason": brief explanation

Task completion criteria:
- delivery: got confirmation (yes/no) and any special instructions
- survey: all survey questions answered
- reminder: got confirmation of attendance or reschedule request
- restaurant: reservation confirmed or question fully answered
- hotel: booking taken or question fully answered
- support: issue resolved or escalated with ticket

Respond ONLY with valid JSON."""


async def completion_checker_node(state: AgentState) -> dict:
    turn_count = state.get("turn_count", 0)
    max_turns = state.get("max_turns", 15)

    # Force wrap-up if we hit max turns — treat as completed to give natural closing
    if turn_count >= max_turns:
        logger.info("Max turns reached (%d), forcing wrap-up", turn_count)
        return {"task_completed": True, "call_status": "wrapping_up"}

    history = state.get("conversation_history", [])
    recent = history[-6:] if len(history) >= 6 else history
    recent_text = "\n".join(f"{e['role'].upper()}: {e['content']}" for e in recent)

    try:
        response = await anthropic.messages.create(
            model=settings.llm_fast_model,
            max_tokens=150,
            messages=[{
                "role": "user",
                "content": COMPLETION_PROMPT.format(
                    task_type=state.get("task_type", "support"),
                    extraction_data=json.dumps(state.get("extraction_data", {})),
                    turn_count=turn_count,
                    max_turns=max_turns,
                    recent_history=recent_text,
                ),
            }],
        )
        result = json.loads(response.content[0].text.strip())
        task_completed = result.get("task_completed", False)
        should_wrap_up = result.get("should_wrap_up", False)
    except Exception as e:
        logger.warning("Completion check failed: %s", e)
        task_completed = False
        should_wrap_up = False

    updates = {
        "task_completed": task_completed,
        "call_status": "wrapping_up" if (task_completed or should_wrap_up) else "listening",
    }
    return updates
