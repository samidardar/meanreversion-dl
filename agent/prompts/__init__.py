from typing import Literal
from .outbound.delivery import DELIVERY_EN, DELIVERY_FR
from .outbound.survey import SURVEY_EN, SURVEY_FR
from .outbound.reminder import REMINDER_EN, REMINDER_FR
from .inbound.restaurant import RESTAURANT_EN, RESTAURANT_FR
from .inbound.hotel import HOTEL_EN, HOTEL_FR
from .inbound.support import SUPPORT_EN, SUPPORT_FR


PROMPT_MAP = {
    "delivery": {"en": DELIVERY_EN, "fr": DELIVERY_FR},
    "survey": {"en": SURVEY_EN, "fr": SURVEY_FR},
    "reminder": {"en": REMINDER_EN, "fr": REMINDER_FR},
    "restaurant": {"en": RESTAURANT_EN, "fr": RESTAURANT_FR},
    "hotel": {"en": HOTEL_EN, "fr": HOTEL_FR},
    "support": {"en": SUPPORT_EN, "fr": SUPPORT_FR},
}


def get_prompt(task_type: str, language: Literal["en", "fr"]) -> str:
    prompts = PROMPT_MAP.get(task_type)
    if not prompts:
        # Fallback: use support prompt for unknown task types
        prompts = PROMPT_MAP["support"]
    return prompts.get(language, prompts["en"])
