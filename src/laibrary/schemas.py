"""Pydantic models for the PKM system."""

from typing import Literal, TypedDict

from pydantic import BaseModel, Field


class DocumentUpdate(BaseModel):
    """Complete update specification for a single document."""

    target_file: str = Field(
        description="Relative path from data/ directory (e.g., 'notes/ideas.md')"
    )
    full_content: str = Field(description="Complete new content for the document")
    commit_message: str = Field(description="Git commit message describing the change")
    create_if_missing: bool = Field(
        default=False,
        description="If True, create the file if it doesn't exist.",
    )
    delete_file: bool = Field(
        default=False,
        description="If True, delete this file instead of editing it",
    )


class SelectionResult(BaseModel):
    """Result from the document selector agent."""

    selected_files: list[str] = Field(
        description="List of file paths that are relevant to the user's request"
    )
    reasoning: str = Field(
        description="Brief explanation of why these documents were selected"
    )


class FilePlan(BaseModel):
    """Plan for updating a single file."""

    target_file: str = Field(
        description="Relative path from data/ directory (e.g., 'notes/ideas.md')"
    )
    action: Literal["create", "modify", "delete"] = Field(
        description="Type of operation to perform on this file"
    )
    description: str = Field(description="What changes will be made to this file")


class UpdatePlan(BaseModel):
    """Plan for a multi-document update operation."""

    file_plans: list[FilePlan] = Field(
        description="List of files to update and what to do with each"
    )
    reasoning: str = Field(
        description="Why these files were selected and how they relate to the user's input"
    )
    commit_message: str = Field(
        description="Commit message that describes all changes as a cohesive unit"
    )


class ConfirmationResult(BaseModel):
    """Result of user confirmation for file creation."""

    action: Literal["confirm", "redirect", "cancel"]
    redirect_to: str | None = None  # Target file if action="redirect"


class MultiDocumentUpdate(BaseModel):
    """Multiple document updates to be committed atomically."""

    updates: list[DocumentUpdate] = Field(
        description="List of document updates to apply"
    )
    commit_message: str = Field(description="Git commit message describing all changes")


class PKMState(TypedDict, total=False):
    """LangGraph state for the PKM workflow."""

    user_input: str
    context_files: dict[str, str]  # {filepath: content}
    document_update: DocumentUpdate | None  # Single update (backward compat)
    document_updates: list[DocumentUpdate] | None  # Multi-document updates
    update_plan: UpdatePlan | None  # Plan for multi-document updates
    error: str | None
    committed: bool
    # Retry logic fields
    retry_count: int
    last_edit_error: str | None  # Error message from failed edit attempt
    retry_file_index: int | None  # Track which file failed in multi-update
    # Two-stage context loading fields
    summaries: dict[str, str]  # {filepath: summary}
    selected_files: list[str] | None  # None = load all docs
    # Confirmation fields
    user_confirmations: dict[str, ConfirmationResult] | None
    confirmation_mode: Literal["auto", "interactive"]
