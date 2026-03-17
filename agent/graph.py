"""
LangGraph StateGraph — the full conversation state machine for AI call center.

Graph flow:
  initialize → greeting → [listen loop] → wrap_up → reporter → END

The "listen loop" is:
  listen → intent_classifier → completion_checker → responder → back to listen
  (or → wrap_up if task complete)

This graph does NOT handle audio I/O directly.
The caller (voice session manager) invokes graph nodes by feeding transcripts
and receiving agent_response text back, then handles TTS/STT externally.
"""
import logging
from typing import Literal
from langgraph.graph import StateGraph, END
from agent.state import AgentState
from agent.nodes import (
    greeting_node,
    intent_classifier_node,
    responder_node,
    completion_checker_node,
    wrap_up_node,
    reporter_node,
)

logger = logging.getLogger(__name__)


# ─── Sync wrapper for greeting (it's sync) ───────────────────────────────────
def _greeting(state: AgentState) -> dict:
    return greeting_node(state)


def _wrap_up(state: AgentState) -> dict:
    return wrap_up_node(state)


# ─── Routing functions ────────────────────────────────────────────────────────

def route_after_completion_check(state: AgentState) -> Literal["responder", "wrap_up"]:
    """After checking task completion: wrap up or continue conversation."""
    status = state.get("call_status", "listening")
    if status == "wrapping_up" or state.get("task_completed", False):
        return "wrap_up"
    return "responder"


def route_after_intent(state: AgentState) -> Literal["completion_checker", "wrap_up"]:
    """After intent classification: if caller wants to end, go to wrap_up."""
    intent = state.get("_intent", "")
    if intent == "end_call":
        return "wrap_up"
    return "completion_checker"


def route_after_wrap_up(state: AgentState) -> Literal["reporter", END]:
    """After wrap-up speech: always go to reporter."""
    return "reporter"


def should_continue_listening(state: AgentState) -> Literal["intent_classifier", END]:
    """After speaking: loop back to listen for caller response, or end if call is done."""
    status = state.get("call_status", "")
    if status == "completed":
        return END
    # Check safety limit
    if state.get("turn_count", 0) >= state.get("max_turns", 15):
        return END
    return "intent_classifier"


# ─── Build graph ──────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """
    Builds and compiles the LangGraph conversation state machine.

    Note: This graph is designed for single-turn invocations driven by the
    voice session manager. Each time the caller speaks, the manager calls
    `graph.ainvoke()` with the current state + new transcript.
    """
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("greeting", _greeting)
    graph.add_node("intent_classifier", intent_classifier_node)
    graph.add_node("completion_checker", completion_checker_node)
    graph.add_node("responder", responder_node)
    graph.add_node("wrap_up", _wrap_up)
    graph.add_node("reporter", reporter_node)

    # Entry point
    graph.set_entry_point("greeting")

    # After greeting → wait for caller (external loop handles this)
    # The caller feeds transcripts by calling intent_classifier directly
    graph.add_edge("greeting", END)

    # Main conversation loop (invoked per caller utterance)
    graph.add_conditional_edges(
        "intent_classifier",
        route_after_intent,
        {"completion_checker": "completion_checker", "wrap_up": "wrap_up"},
    )

    graph.add_conditional_edges(
        "completion_checker",
        route_after_completion_check,
        {"responder": "responder", "wrap_up": "wrap_up"},
    )

    graph.add_conditional_edges(
        "responder",
        should_continue_listening,
        {"intent_classifier": END, END: END},  # END = return to voice loop
    )

    graph.add_edge("wrap_up", "reporter")
    graph.add_edge("reporter", END)

    return graph.compile()


# ─── Two separate compiled graphs ─────────────────────────────────────────────
# greeting_graph: used once at call start to produce the first greeting
# conversation_graph: used per caller utterance (entry = intent_classifier)

def build_conversation_turn_graph() -> StateGraph:
    """
    Simplified per-turn graph: takes current state + transcript → produces agent_response.
    Entry point is intent_classifier (caller already spoke).
    """
    graph = StateGraph(AgentState)

    graph.add_node("intent_classifier", intent_classifier_node)
    graph.add_node("completion_checker", completion_checker_node)
    graph.add_node("responder", responder_node)
    graph.add_node("wrap_up", _wrap_up)
    graph.add_node("reporter", reporter_node)

    graph.set_entry_point("intent_classifier")

    graph.add_conditional_edges(
        "intent_classifier",
        route_after_intent,
        {"completion_checker": "completion_checker", "wrap_up": "wrap_up"},
    )

    graph.add_conditional_edges(
        "completion_checker",
        route_after_completion_check,
        {"responder": "responder", "wrap_up": "wrap_up"},
    )

    graph.add_edge("responder", END)
    graph.add_edge("wrap_up", "reporter")
    graph.add_edge("reporter", END)

    return graph.compile()


# Singleton compiled graphs
greeting_graph = build_graph()
conversation_graph = build_conversation_turn_graph()
