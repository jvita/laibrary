"""Data models for the chat system."""

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, Field


class Intent(str, Enum):
    """Type of user intent."""

    UPDATE = "update"  # Any document modification (add, remove, cleanup, reorganize)
    QUERY = "query"  # Read/retrieve from existing docs
    CHAT = "chat"  # Just conversation, no doc action


class MessageRole(str, Enum):
    """Role of a message in the chat."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class ChatMessage:
    """A single message in the chat history."""

    role: MessageRole
    content: str


class RouterDecision(BaseModel):
    """Decision from the router agent about how to handle a user message."""

    intent: Intent = Field(
        description="The type of intent: UPDATE for document modifications, QUERY for reading/retrieving info, CHAT for conversation"
    )
    reasoning: str = Field(
        description="Brief explanation of why this decision was made"
    )
    response: str = Field(
        description="Conversational response to the user (used directly for CHAT intent)"
    )
    target_hint: str | None = Field(
        default=None,
        description="Natural language hint about which document(s) to target, e.g. 'PKM project' or 'to-do list'",
    )
