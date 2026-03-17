"""
Unit tests for LangGraph agent nodes.
All tests use mocked LLM/API calls — no real API keys needed.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from agent.nodes.greeting import greeting_node
from agent.nodes.wrap_up import wrap_up_node
from agent.state import AgentState


def make_state(**overrides) -> AgentState:
    base = {
        "call_id": "test-call-1",
        "call_sid": "CAtest",
        "mode": "outbound",
        "task_type": "delivery",
        "contact": {"first_name": "John", "last_name": "Smith", "phone": "+15141234567"},
        "inbound_config": None,
        "detected_language": "en",
        "language_confirmed": True,
        "conversation_history": [],
        "current_transcript": "",
        "agent_response": "",
        "call_status": "initializing",
        "task_completed": False,
        "turn_count": 0,
        "interruption_detected": False,
        "max_turns": 15,
        "extraction_data": {},
        "report": None,
        "use_smart_model": False,
        "error_message": None,
        "_intent": "",
    }
    base.update(overrides)
    return base


class TestGreetingNode:
    def test_outbound_en_greeting(self):
        state = make_state(mode="outbound", task_type="delivery", detected_language="en")
        result = greeting_node(state)
        assert "John" in result["agent_response"]
        assert len(result["conversation_history"]) == 1
        assert result["conversation_history"][0]["role"] == "agent"

    def test_outbound_fr_greeting(self):
        state = make_state(mode="outbound", task_type="delivery", detected_language="fr")
        result = greeting_node(state)
        assert "John" in result["agent_response"]
        # French greeting should contain French words
        assert any(word in result["agent_response"].lower() for word in ["bonjour", "ici", "vous"])

    def test_inbound_en_greeting(self):
        state = make_state(
            mode="inbound",
            task_type="restaurant",
            detected_language="en",
            inbound_config={"business_name": "Le Bon Resto", "agent_name": "Alex"},
        )
        result = greeting_node(state)
        assert "Le Bon Resto" in result["agent_response"]

    def test_inbound_fr_greeting(self):
        state = make_state(
            mode="inbound",
            task_type="restaurant",
            detected_language="fr",
            inbound_config={"business_name": "Le Bon Resto", "agent_name": "Alex"},
        )
        result = greeting_node(state)
        assert "Le Bon Resto" in result["agent_response"]
        assert "bienvenue" in result["agent_response"].lower()


class TestWrapUpNode:
    def test_completed_delivery_en(self):
        state = make_state(
            task_type="delivery",
            detected_language="en",
            task_completed=True,
            contact={"first_name": "John", "last_name": "Smith"},
        )
        result = wrap_up_node(state)
        assert result["call_status"] == "completed"
        assert "John" in result["agent_response"]

    def test_completed_delivery_fr(self):
        state = make_state(
            task_type="delivery",
            detected_language="fr",
            task_completed=True,
            contact={"first_name": "Marie", "last_name": "Tremblay"},
        )
        result = wrap_up_node(state)
        assert result["call_status"] == "completed"
        assert "Marie" in result["agent_response"]

    def test_incomplete_call_en(self):
        state = make_state(
            task_type="delivery",
            detected_language="en",
            task_completed=False,
            contact={"first_name": "John", "last_name": "Smith"},
        )
        result = wrap_up_node(state)
        assert result["call_status"] == "completed"


class TestPromptSystem:
    def test_get_prompt_delivery_en(self):
        from agent.prompts import get_prompt
        prompt = get_prompt("delivery", "en")
        assert "{first_name}" in prompt
        assert len(prompt) > 100

    def test_get_prompt_delivery_fr(self):
        from agent.prompts import get_prompt
        prompt = get_prompt("delivery", "fr")
        assert "français" in prompt.lower() or "livraison" in prompt.lower()

    def test_get_prompt_unknown_task(self):
        from agent.prompts import get_prompt
        # Unknown task should fallback gracefully
        prompt = get_prompt("unknown_task", "en")
        assert prompt is not None
        assert len(prompt) > 0

    def test_all_tasks_have_both_languages(self):
        from agent.prompts import PROMPT_MAP
        for task_type, langs in PROMPT_MAP.items():
            assert "en" in langs, f"Missing EN prompt for {task_type}"
            assert "fr" in langs, f"Missing FR prompt for {task_type}"
