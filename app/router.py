"""Decide which executor handles an event type. Default is n8n."""

ROUTES = {
    "ai.": "groq",  # AI-only events run directly in Python + Groq
}


def resolve_executor(event_type: str) -> str:
    for prefix, name in ROUTES.items():
        if event_type.startswith(prefix):
            return name
    return "n8n"