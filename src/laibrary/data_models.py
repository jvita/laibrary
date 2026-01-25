"""Pydantic data models."""

from pathlib import Path

from pydantic import BaseModel


class Note(BaseModel):
    """A basic note. May be a raw user-provided note or an AI-generated idea."""

    path: Path


class Idea(Note):
    """An idea is a Note, but with additional `name` and `description` fields which are used when building the catalog of ideas."""

    name: str
    description: str


class Database(BaseModel):
    """A container for tracking database file path and idea categories."""

    path: Path
    ideas: list[Idea] | None = None
    notes: list[Note] | None = None
