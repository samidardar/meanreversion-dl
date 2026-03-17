"""
LangGraph StateGraph — the full conversation state machine for AI call center.

Graph structure:
  greeting_graph:  START → greeting → END   (invoked once at call start)
  conversation_graph:  START → intent_classifier → ... → END   (invoked per caller utterance)

The VoiceSession manager drives the conversation loop externally:
  1. Call starts → invoke greeting_graph → speak greeting
  2. Caller speaks → update state.current_transcript → invoke conversation_graph
  3. conversation_graph returns → speak agent_response → wait for caller again
  4. Repeat until call_status == "completed" or report is populated
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


# ─── Sync wrappers ────────────────────────────────────────────────────────────

def _greeting(state: AgentState) -> dict:
    return greeting_node(state)


def _wrap_up(state: AgentState) -> dict:
    return wrap_up_node(state)


# ─── Routing functions ────────────────────────────────────────────────────────

def route_after_intent(state: AgentState) -> Literal["completion_checker", "wrap_up"]:
    """After intent classification: if caller wants to end, go to wrap_up directly."""
    intent = state.get("_intent", "")
    if intent == "end_call":
        return "wrap_up"
    return "completion_checker"


def route_after_completion_check(state: AgentState) -> Literal["responder", "wrap_up"]:
    """After checking task completion: wrap up or continue generating a response."""
    status = state.get("call_status", "listening")
    if status == "wrapping_up" or state.get("task_completed", False):
        return "wrap_up"
    return "responder"


def route_after_responder(state: AgentState) -> Literal["END"]:
    """
    After the responder generates a reply, always return END.
    The VoiceSession manager drives the outer loop — it will call the graph
    again when the next caller utterance arrives.
    This keeps the graph stateless across turns (state is passed each invocation).
    """
    return "END"


# ─── Greeting graph ───────────────────────────────────────────────────────────

def build_greeting_graph():
    """Simple graph: START → greeting → END. Used once at call start."""
    graph = StateGraph(AgentState)
    graph.add_node("greeting", _greeting)
    graph.set_entry_point("greeting")
    graph.add_edge("greeting", END)
    return graph.compile()


# ─── Per-turn conversation graph ──────────────────────────────────────────────

def build_conversation_graph():
    """
    Per-utterance graph: processes one caller utterance and produces one agent response.
    Entry: intent_classifier (caller has just spoken).
    Returns to END after each turn — VoiceSession re-invokes with next utterance.
    """
    graph = StateGraph(AgentState)

    graph.add_node("intent_classifier", intent_classifier_node)
    graph.add_node("completion_checker", completion_checker_node)
    graph.add_node("responder", responder_node)
    graph.add_node("wrap_up", _wrap_up)
    graph.add_node("reporter", reporter_node)

    graph.set_entry_point("intent_classifier")

    # After classifying intent: either handle end_call or check completion
    graph.add_conditional_edges(
        "intent_classifier",
        route_after_intent,
        {
            "completion_checker": "completion_checker",
            "wrap_up": "wrap_up",
        },
    )

    # After completion check: either wrap up or generate response
    graph.add_conditional_edges(
        "completion_checker",
        route_after_completion_check,
        {
            "responder": "responder",
            "wrap_up": "wrap_up",
        },
    )

    # After responder speaks: always return to the voice loop (END)
    graph.add_edge("responder", END)

    # Wrap-up leads to report generation, then end
    graph.add_edge("wrap_up", "reporter")
    graph.add_edge("reporter", END)

    return graph.compile()


# ─── Compiled singletons ──────────────────────────────────────────────────────
greeting_graph = build_greeting_graph()
conversation_graph = build_conversation_graph()
