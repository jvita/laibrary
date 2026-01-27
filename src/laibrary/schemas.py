"""Pydantic models for the PKM system."""

from typing import TypedDict

from pydantic import BaseModel, Field


class DocumentEdit(BaseModel):
    """A single search/replace edit operation."""

    search_block: str = Field(
        description="Exact text to find in the document. Must match character-for-character."
    )
    replace_block: str = Field(
        description="Text to replace the search_block with. Can be empty to delete."
    )


class DocumentUpdate(BaseModel):
    """Complete update specification for a single document."""

    target_file: str = Field(
        description="Relative path from data/ directory (e.g., 'notes/ideas.md')"
    )
    edits: list[DocumentEdit] = Field(
        default_factory=list,
        description="List of search/replace edits to apply in order",
    )
    commit_message: str = Field(description="Git commit message describing the change")
    create_if_missing: bool = Field(
        default=False,
        description="If True, create the file if it doesn't exist. For new files, use empty search_block.",
    )


class PKMState(TypedDict, total=False):
    """LangGraph state for the PKM workflow."""

    user_input: str
    context_files: dict[str, str]  # {filepath: content}
    document_update: DocumentUpdate | None
    error: str | None
    committed: bool
    # Retry logic fields
    retry_count: int
    last_edit_error: str | None  # Error message from failed edit attempt
    failed_search_block: str | None  # The search_block that failed to match
