"""Pydantic data models."""

from pathlib import Path

from pydantic import BaseModel


class Note(BaseModel):
    """A basic note. May be a raw user-provided note or an AI-generated idea."""

    path: Path


class Idea(BaseModel):
    """An idea/concept/topic with name and description."""

    name: str
    description: str
    is_new: bool


class Database(BaseModel):
    """A container for tracking database file path and idea categories."""

    path: Path
    ideas: list[Idea] | None = None
    notes: list[Note] | None = None


class NoteProcessingState(BaseModel):
    """For tracking the status of note processing (categorization)."""

    note: Note
    existing_ideas: list[Idea]
    database_path: Path


class IdeaUpdateState(BaseModel):
    """State for creating or updating an idea."""

    database_path: Path
    idea: Idea
    note: Note
