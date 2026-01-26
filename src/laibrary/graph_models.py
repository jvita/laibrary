"""Data models for the graph-based RAG system."""

from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class IndexedNote(BaseModel):
    """A note with metadata for graph indexing."""

    id: UUID = Field(default_factory=uuid4)
    path: Path
    content_hash: str  # SHA256 for change detection
    created_at: datetime  # File creation/modification time
    indexed_at: datetime  # When added to index
    title: str | None = None
    embedding_id: str | None = None  # Reference in ChromaDB


class GraphEdge(BaseModel):
    """Semantic similarity relationship between notes."""

    source_id: UUID
    target_id: UUID
    weight: float  # Cosine similarity score (0-1)


class Summary(BaseModel):
    """An on-demand generated summary for a topic."""

    id: UUID = Field(default_factory=uuid4)
    topic: str
    content: str
    path: Path
    created_at: datetime
    last_updated: datetime
    incorporated_note_ids: list[UUID] = Field(default_factory=list)
    version: int = 1


class VersionInfo(BaseModel):
    """Version information for a summary."""

    version: int
    git_commit: str
    created_at: datetime
    incorporated_note_ids: list[UUID]


class KnowledgeGraph(BaseModel):
    """The complete knowledge graph state."""

    notes: dict[str, IndexedNote] = Field(default_factory=dict)  # UUID str -> Note
    edges: list[GraphEdge] = Field(default_factory=list)
    summaries: dict[str, Summary] = Field(default_factory=dict)  # UUID str -> Summary

    def add_note(self, note: IndexedNote) -> None:
        """Add a note to the graph."""
        self.notes[str(note.id)] = note

    def get_note(self, note_id: UUID) -> IndexedNote | None:
        """Get a note by ID."""
        return self.notes.get(str(note_id))

    def add_edge(self, edge: GraphEdge) -> None:
        """Add an edge to the graph."""
        self.edges.append(edge)

    def get_edges_for_note(self, note_id: UUID) -> list[GraphEdge]:
        """Get all edges connected to a note."""
        note_id_str = str(note_id)
        return [
            e
            for e in self.edges
            if str(e.source_id) == note_id_str or str(e.target_id) == note_id_str
        ]

    def add_summary(self, summary: Summary) -> None:
        """Add a summary to the graph."""
        self.summaries[str(summary.id)] = summary

    def get_summary_by_topic(self, topic: str) -> Summary | None:
        """Find a summary by topic (case-insensitive partial match)."""
        topic_lower = topic.lower()
        for summary in self.summaries.values():
            if topic_lower in summary.topic.lower():
                return summary
        return None


# === LangGraph State Models ===


class IngestionState(BaseModel):
    """State for the ingestion workflow."""

    notes_dir: Path
    index_dir: Path
    pending_paths: list[Path] = Field(default_factory=list)
    processed_notes: list[IndexedNote] = Field(default_factory=list)
    new_edges: list[GraphEdge] = Field(default_factory=list)


class RetrievalState(BaseModel):
    """State for the retrieval workflow."""

    query: str
    query_embedding: list[float] | None = None
    candidate_note_ids: list[UUID] = Field(default_factory=list)
    expanded_note_ids: list[UUID] = Field(default_factory=list)
    retrieved_notes: list[IndexedNote] = Field(default_factory=list)


class SummaryState(BaseModel):
    """State for the summary generation workflow."""

    topic: str
    retrieved_notes: list[IndexedNote] = Field(default_factory=list)
    existing_summary: Summary | None = None
    new_notes: list[IndexedNote] = Field(default_factory=list)
    generated_content: str | None = None
    output_summary: Summary | None = None
