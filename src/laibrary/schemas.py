"""Pydantic models for the PKM system."""

from typing import Literal, TypedDict

from pydantic import BaseModel, Field


class SectionEdit(BaseModel):
    """Edit to a single section of a document."""

    section: Literal[
        "Description",
        "Instructions",
        "Current Status",
        "To Do",
        "Brainstorming",
        "Summary",
        "Notes",
        "Session History",  # Managed by Python, not LLM
    ] = Field(description="Which section to edit")
    content: str = Field(description="Full new content for this section")
    remove: bool = Field(
        default=False, description="True to remove this section entirely"
    )


class DocumentUpdate(BaseModel):
    """Update to a single document via section edits."""

    target_file: str = Field(
        description="Relative path from data/ directory (e.g., 'projects/webapp.md')"
    )
    section_edits: list[SectionEdit] = Field(
        description="List of section edits to apply"
    )
    commit_message: str = Field(description="Git commit message describing the change")


class PKMState(TypedDict, total=False):
    """LangGraph state for the PKM workflow."""

    # Input
    user_input: str
    target_project: str  # Path to project file (e.g., 'projects/webapp.md')

    # Context
    context_files: dict[str, str]  # {filepath: content}

    # Output
    document_update: DocumentUpdate | None

    # Control flow
    error: str | None
    committed: bool
    confirmation_mode: Literal["auto", "interactive"]

    # Special commands
    command: Literal["list", "note"] | None  # Parsed command type
    note_content: str | None  # Note content after stripping /project prefix

    # Session tracking
    session_id: str | None  # Current session ID for bidirectional linking
