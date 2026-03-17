"""
Responder node — generates the agent's next reply using LangChain + Anthropic.
Routes to fast model (Haiku) for simple turns, smart model (Sonnet) for complex ones.
"""
import logging
from datetime import datetime
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from agent.state import AgentState
from agent.prompts import get_prompt
from config.settings import settings

logger = logging.getLogger(__name__)

_fast_llm = ChatAnthropic(
    model=settings.llm_fast_model,
    api_key=settings.anthropic_api_key,
    max_tokens=150,
    temperature=0.7,
)

_smart_llm = ChatAnthropic(
    model=settings.llm_smart_model,
    api_key=settings.anthropic_api_key,
    max_tokens=300,
    temperature=0.7,
)


def _build_system_prompt(state: AgentState) -> str:
    contact = state.get("contact") or {}
    inbound_config = state.get("inbound_config") or {}
    task_type = state.get("task_type", "support")
    language = state.get("detected_language", "en")

    raw_prompt = get_prompt(task_type, language)

    custom_data = contact.get("custom_data") or {}

    template_vars = {
        "first_name": contact.get("first_name", ""),
        "last_name": contact.get("last_name", ""),
        "phone": contact.get("phone", ""),
        "company_name": inbound_config.get("business_name", "our company"),
        "business_name": inbound_config.get("business_name", "our company"),
        "agent_name": inbound_config.get("agent_name", "Alex"),
        "knowledge_base": _format_knowledge_base(inbound_config.get("knowledge_base") or {}),
        "order_ref": custom_data.get("order_ref", "N/A"),
        "address": custom_data.get("address", "N/A"),
        "notes": contact.get("reason_for_call") or "",
        "appointment_details": contact.get("reason_for_call") or "your upcoming appointment",
        "appointment_date": custom_data.get("appointment_date", "N/A"),
        "appointment_type": custom_data.get("appointment_type", "N/A"),
        "appointment_ref": custom_data.get("appointment_ref", "N/A"),
        "survey_topic": custom_data.get("survey_topic", "your recent experience"),
        "survey_questions": custom_data.get(
            "survey_questions",
            "1. How would you rate your overall experience? (1-5)\n2. Would you recommend us to a friend?"
        ),
    }

    try:
        return raw_prompt.format(**template_vars)
    except KeyError as e:
        logger.warning("Prompt template missing key: %s", e)
        return raw_prompt


def _format_knowledge_base(kb: dict) -> str:
    if not kb:
        return "No specific information provided."
    return "\n".join(f"- {k}: {v}" for k, v in kb.items())


def _build_langchain_messages(state: AgentState, system_prompt: str) -> list:
    messages = [SystemMessage(content=system_prompt)]
    for entry in state.get("conversation_history", []):
        if entry["role"] == "agent":
            messages.append(AIMessage(content=entry["content"]))
        else:
            messages.append(HumanMessage(content=entry["content"]))
    return messages


async def responder_node(state: AgentState) -> dict:
    system_prompt = _build_system_prompt(state)
    messages = _build_langchain_messages(state, system_prompt)

    llm = _smart_llm if state.get("use_smart_model", False) else _fast_llm

    try:
        response = await llm.ainvoke(messages)
        reply = response.content.strip()
    except Exception as e:
        logger.error("LLM response failed: %s", e)
        lang = state.get("detected_language", "en")
        reply = (
            "Je suis désolé, pouvez-vous répéter?"
            if lang == "fr"
            else "I'm sorry, could you repeat that?"
        )

    agent_entry = {
        "role": "agent",
        "content": reply,
        "timestamp": datetime.utcnow().isoformat(),
    }

    return {
        "agent_response": reply,
        "conversation_history": [agent_entry],
        "call_status": "speaking",
        "turn_count": state.get("turn_count", 0) + 1,
    }
