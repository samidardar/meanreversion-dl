"""
Wrap-up node — generates a warm, natural closing for the call.
"""
import logging
from datetime import datetime
from agent.state import AgentState

logger = logging.getLogger(__name__)


def wrap_up_node(state: AgentState) -> dict:
    language = state.get("detected_language", "en")
    task_type = state["task_type"]
    task_completed = state.get("task_completed", False)
    contact = state.get("contact", {})
    first_name = contact.get("first_name", "")

    if language == "fr":
        if task_completed:
            closings = {
                "delivery": f"Parfait {first_name}! Tout est bien noté. Bonne journée et à bientôt!",
                "survey": f"Merci beaucoup {first_name} pour vos précieux retours! Bonne journée!",
                "reminder": f"Excellent {first_name}! On vous attend. Bonne journée!",
                "restaurant": "Parfait, votre réservation est bien enregistrée! Nous avons hâte de vous accueillir. Bonne journée!",
                "hotel": "Merveilleux! Votre réservation est confirmée. Nous vous souhaitons un agréable séjour. À bientôt!",
                "support": "Je suis content qu'on ait pu résoudre ça. N'hésitez pas à nous rappeler si vous avez besoin de quoi que ce soit. Bonne journée!",
            }
        else:
            closings = {
                "delivery": f"D'accord {first_name}, j'ai bien noté ça. Nous reviendrons vers vous. Bonne journée!",
                "survey": f"Pas de problème {first_name}! Merci pour votre temps. Bonne journée!",
                "reminder": f"Très bien {first_name}, notre équipe vous recontactera. Bonne journée!",
                "restaurant": "Pas de souci! N'hésitez pas à nous rappeler. Bonne journée!",
                "hotel": "Bien sûr! Notre équipe vous recontactera. Bonne journée!",
                "support": "Je comprends. Notre équipe va vous recontacter très bientôt. Bonne journée!",
            }
    else:
        if task_completed:
            closings = {
                "delivery": f"Wonderful {first_name}! Everything is all noted. Have a great day!",
                "survey": f"Thank you so much {first_name} for your valuable feedback! Have a wonderful day!",
                "reminder": f"Perfect {first_name}! We look forward to seeing you. Have a great day!",
                "restaurant": "Perfect! Your reservation is all set. We look forward to seeing you. Have a wonderful evening!",
                "hotel": "Wonderful! Your reservation is confirmed. We look forward to welcoming you. Have a great day!",
                "support": "I'm glad we could sort that out! Don't hesitate to call us if you need anything else. Have a great day!",
            }
        else:
            closings = {
                "delivery": f"No worries {first_name}, I've made a note of that. We'll be in touch. Have a great day!",
                "survey": f"No problem {first_name}! Thank you for your time. Have a great day!",
                "reminder": f"Understood {first_name}, our team will reach out to you. Have a great day!",
                "restaurant": "Of course! Feel free to call us back anytime. Have a wonderful day!",
                "hotel": "Of course! Our team will follow up with you shortly. Have a great day!",
                "support": "I understand. Our team will follow up with you very soon. Have a great day!",
            }

    closing = closings.get(task_type, "Thank you for your time. Have a wonderful day!" if language == "en" else "Merci pour votre temps. Bonne journée!")

    entry = {
        "role": "agent",
        "content": closing,
        "timestamp": datetime.utcnow().isoformat(),
    }

    return {
        "agent_response": closing,
        "conversation_history": [entry],
        "call_status": "completed",
    }
