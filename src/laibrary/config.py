"""Configuration constants for Laibrary."""

from pydantic_ai.settings import ModelSettings

# Maximum number of retries for failed edit operations
MAX_RETRIES = 3

# Model settings for different agent types
# Using temperature-based control for determinism (don't use top_p with temperature)

ARCHITECT_SETTINGS: ModelSettings = {
    "temperature": 0.0,  # Deterministic for exact search/replace matching
    "max_tokens": 2048,  # Prevent rambling and schema drift
}

PLANNER_SETTINGS: ModelSettings = {
    "temperature": 0.1,  # Very low for structured planning
    "max_tokens": 512,  # Concise plans
}

SELECTOR_SETTINGS: ModelSettings = {
    "temperature": 0.2,  # Low for consistent selection
    "max_tokens": 256,  # Brief selection results
}

ROUTER_SETTINGS: ModelSettings = {
    "temperature": 0.0,  # Deterministic routing decisions
    "max_tokens": 64,  # Very brief intent classification
}

QUERY_SETTINGS: ModelSettings = {
    "temperature": 0.3,  # Slightly higher for natural responses
    "max_tokens": 1024,  # Room for detailed answers
}

SUMMARY_SETTINGS: ModelSettings = {
    "temperature": 0.4,  # Allow some creativity in summaries
    "max_tokens": 128,  # Concise summaries
}
