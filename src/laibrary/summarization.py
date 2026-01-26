"""Summary generation for the graph RAG system."""

import re
from datetime import UTC, datetime
from pathlib import Path

from .agents import reconcile_agent, summary_agent
from .graph_models import IndexedNote, KnowledgeGraph, Summary
from .ingestion import load_graph, save_graph
from .retrieval import assemble_context, retrieve


def slugify(text: str) -> str:
    """Convert text to a filename-safe slug."""
    # Lowercase and replace spaces/special chars with hyphens
    slug = text.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug.strip("-")


def find_existing_summary(topic: str, graph: KnowledgeGraph) -> Summary | None:
    """Find an existing summary by topic."""
    return graph.get_summary_by_topic(topic)


def get_new_notes(
    summary: Summary,
    relevant_notes: list[IndexedNote],
) -> list[IndexedNote]:
    """Find notes that haven't been incorporated into a summary.

    Uses timestamp comparison: notes indexed after the summary's last update
    that aren't already in the incorporated_note_ids list.
    """
    incorporated_ids = set(summary.incorporated_note_ids)

    new_notes = [
        note
        for note in relevant_notes
        if note.id not in incorporated_ids and note.indexed_at > summary.last_updated
    ]

    return new_notes


async def generate_summary(
    topic: str,
    notes: list[IndexedNote],
    summaries_dir: Path,
) -> Summary:
    """Generate a new summary from notes.

    Args:
        topic: The topic being summarized
        notes: List of relevant notes
        summaries_dir: Directory to save summaries

    Returns:
        The generated Summary object
    """
    # Assemble context from notes
    context = assemble_context(notes)

    # Generate summary using LLM
    prompt = f"""Topic: {topic}

Please create a comprehensive summary synthesizing the following notes:

{context}
"""
    result = await summary_agent.run(prompt)
    content = result.output

    # Create summary object
    now = datetime.now(UTC)
    summary_path = (summaries_dir / f"{slugify(topic)}.md").resolve()
    summary = Summary(
        topic=topic,
        content=content,
        path=summary_path,
        created_at=now,
        last_updated=now,
        incorporated_note_ids=[note.id for note in notes],
        version=1,
    )

    # Save to file
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary.path.write_text(content)

    return summary


async def update_summary(
    summary: Summary,
    new_notes: list[IndexedNote],
) -> Summary:
    """Update an existing summary with new notes.

    Args:
        summary: The existing summary to update
        new_notes: New notes to incorporate

    Returns:
        The updated Summary object
    """
    # Assemble context from new notes
    new_context = assemble_context(new_notes)

    # Generate updated summary using reconcile agent
    prompt = f"""EXISTING_SUMMARY:
{summary.content}

NEW_NOTES:
{new_context}
"""
    result = await reconcile_agent.run(prompt)
    updated_content = result.output

    # Update summary object
    now = datetime.now(UTC)
    summary.content = updated_content
    summary.last_updated = now
    summary.version += 1
    summary.incorporated_note_ids.extend([note.id for note in new_notes])

    # Save to file
    summary.path.write_text(updated_content)

    return summary


async def summarize_topic(
    topic: str,
    index_dir: Path,
    summaries_dir: Path,
    update_existing: bool = False,
    top_k: int = 10,
    expand_hops: int = 1,
) -> tuple[Summary, bool]:
    """Generate or update a summary for a topic.

    Args:
        topic: The topic to summarize
        index_dir: Directory containing the index
        summaries_dir: Directory to save summaries
        update_existing: If True, update existing summary with new notes
        top_k: Number of notes to retrieve
        expand_hops: Graph expansion hops

    Returns:
        Tuple of (Summary, was_updated)
    """
    # Load graph
    graph = load_graph(index_dir)

    # Retrieve relevant notes
    notes = retrieve(topic, index_dir, top_k=top_k, expand_hops=expand_hops)

    if not notes:
        raise ValueError(f"No notes found related to topic: {topic}")

    # Check for existing summary
    existing = find_existing_summary(topic, graph)

    if existing and update_existing:
        # Find new notes and update
        new_notes = get_new_notes(existing, notes)
        if not new_notes:
            return existing, False  # No new notes to incorporate

        updated = await update_summary(existing, new_notes)
        graph.summaries[str(updated.id)] = updated
        save_graph(graph, index_dir)
        return updated, True

    elif existing and not update_existing:
        # Return existing without update
        return existing, False

    else:
        # Generate new summary
        summary = await generate_summary(topic, notes, summaries_dir)
        graph.add_summary(summary)
        save_graph(graph, index_dir)
        return summary, True
