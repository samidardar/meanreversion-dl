"""
Greeting node — generates a personalized opening line for the call.
Outbound: "Hi, is this [Name]? Great, this is Alex calling from [Company]..."
Inbound: "Thank you for calling [Business], this is Alex, how can I help you?"
"""
import logging
from datetime import datetime
from agent.state import AgentState
from agent.prompts import get_prompt

logger = logging.getLogger(__name__)


def greeting_node(state: AgentState) -> dict:
    contact = state.get("contact", {})
    mode = state["mode"]
    language = state.get("detected_language", "en")
    task_type = state["task_type"]

    if mode == "outbound":
        first_name = contact.get("first_name", "")
        if language == "fr":
            greeting = (
                f"Bonjour, est-ce que je parle bien à {first_name}? "
                f"Excellent! Ici Alex qui vous appelle de la part de notre équipe de livraison. "
                f"J'espère que vous allez bien!"
            )
        else:
            greeting = (
                f"Hi there, is this {first_name}? "
                f"Hi {first_name}, this is Alex calling — how are you doing today?"
            )
    else:
        inbound_config = state.get("inbound_config", {}) or {}
        business_name = inbound_config.get("business_name", "us")
        agent_name = inbound_config.get("agent_name", "Alex")
        if language == "fr":
            greeting = f"Bienvenue chez {business_name}, ici {agent_name}, comment puis-je vous aider aujourd'hui?"
        else:
            greeting = f"Thank you for calling {business_name}, this is {agent_name}, how can I help you today?"

    entry = {
        "role": "agent",
        "content": greeting,
        "timestamp": datetime.utcnow().isoformat(),
    }

    return {
        "agent_response": greeting,
        "call_status": "greeting",
        "conversation_history": [entry],
        "turn_count": 0,
    }
