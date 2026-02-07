"""Configuration constants for Laibrary."""

import os

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

# Maximum number of retries for failed edit operations
MAX_RETRIES = 3

# Model settings for different agent types
# Using temperature-based control for determinism (don't use top_p with temperature)

ARCHITECT_SETTINGS: ModelSettings = {
    "temperature": 0.3,  # Deterministic output
    "max_tokens": 8192,  # Room for full document content
}

ROUTER_SETTINGS: ModelSettings = {
    "temperature": 0.0,  # Deterministic routing decisions
    "max_tokens": 64,  # Very brief intent classification
}

QUERY_SETTINGS: ModelSettings = {
    "temperature": 0.3,  # Slightly higher for natural responses
    "max_tokens": 1024,  # Room for detailed answers
}


def get_model_name() -> str:
    """Get the model name from environment."""
    return os.environ["MODEL"]


def create_agent(
    system_prompt: str,
    output_type: type | None = None,
    model_settings: ModelSettings | None = None,
) -> Agent:
    """Create a pydantic-ai Agent with standard configuration.

    Args:
        system_prompt: System prompt for the agent.
        output_type: Optional structured output type.
        model_settings: Optional model settings override.

    Returns:
        Configured Agent instance.
    """
    kwargs: dict = {
        "system_prompt": system_prompt,
        "retries": MAX_RETRIES,
    }
    if output_type is not None:
        kwargs["output_type"] = output_type
    if model_settings is not None:
        kwargs["model_settings"] = model_settings
    return Agent(get_model_name(), **kwargs)
