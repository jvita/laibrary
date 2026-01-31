"""Configuration constants for Laibrary."""

from pydantic_ai.settings import ModelSettings

# Maximum number of retries for failed edit operations
MAX_RETRIES = 3

# Model settings for different agent types
# Using temperature-based control for determinism (don't use top_p with temperature)

ARCHITECT_SETTINGS: ModelSettings = {
    "temperature": 0.0,  # Deterministic output
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
