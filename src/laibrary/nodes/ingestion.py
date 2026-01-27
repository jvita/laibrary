"""Ingestion node - validates and prepares user input."""

from ..schemas import PKMState


def ingestion_node(state: PKMState) -> PKMState:
    """Validate and normalize user input.

    Strips whitespace and ensures input is not empty.
    """
    user_input = state.get("user_input", "")

    if not user_input:
        return {**state, "error": "No user input provided"}

    cleaned = user_input.strip()

    if not cleaned:
        return {**state, "error": "User input is empty after stripping whitespace"}

    return {**state, "user_input": cleaned}
