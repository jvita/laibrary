"""Retrieval pipeline for the graph RAG system."""

from pathlib import Path
from uuid import UUID

import chromadb
import networkx as nx

from .graph_models import IndexedNote, KnowledgeGraph
from .ingestion import generate_embedding, get_chroma_collection, load_graph


def vector_search(
    query: str,
    collection: chromadb.Collection,
    k: int = 10,
    embedding_model: str = "nomic-embed-text",
) -> list[tuple[str, float]]:
    """Search for similar notes using vector similarity.

    Args:
        query: The search query
        collection: ChromaDB collection
        k: Number of results to return
        embedding_model: Model to use for query embedding

    Returns:
        List of (note_id, similarity_score) tuples
    """
    # Generate query embedding
    query_embedding = generate_embedding(query, model=embedding_model)

    # Query ChromaDB
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=k,
        include=["distances"],
    )

    if not results or not results["ids"] or not results["ids"][0]:
        return []

    # Convert distances to similarities
    # ChromaDB with cosine space returns cosine distance (1 - similarity)
    scored_results = []
    for note_id, distance in zip(
        results["ids"][0], results["distances"][0], strict=False
    ):
        similarity = max(0, 1 - distance)  # Convert distance to similarity
        scored_results.append((note_id, similarity))

    return scored_results


def graph_expand(
    note_ids: list[UUID],
    graph: KnowledgeGraph,
    hops: int = 1,
    min_weight: float = 0.5,
) -> set[UUID]:
    """Expand a set of notes by traversing graph edges.

    Args:
        note_ids: Starting note IDs
        graph: The knowledge graph
        hops: Number of hops to traverse
        min_weight: Minimum edge weight to follow

    Returns:
        Set of expanded note IDs (including originals)
    """
    # Build NetworkX graph for traversal
    graph = nx.Graph()
    for note_id in graph.notes:
        graph.add_node(note_id)
    for edge in graph.edges:
        if edge.weight >= min_weight:
            graph.add_edge(str(edge.source_id), str(edge.target_id), weight=edge.weight)

    # Expand from each starting node
    expanded = set(str(nid) for nid in note_ids)

    for _ in range(hops):
        new_nodes = set()
        for node_id in expanded:
            if node_id in graph:
                neighbors = graph.neighbors(node_id)
                new_nodes.update(neighbors)
        expanded.update(new_nodes)

    return {UUID(nid) for nid in expanded if nid in graph.notes}


def retrieve(
    query: str,
    index_dir: Path,
    top_k: int = 10,
    expand_hops: int = 1,
    min_similarity: float = 0.3,
    embedding_model: str = "nomic-embed-text",
) -> list[IndexedNote]:
    """Retrieve relevant notes for a query.

    Args:
        query: The search query
        index_dir: Directory containing the index
        top_k: Number of initial vector search results
        expand_hops: Number of graph hops to expand
        min_similarity: Minimum similarity threshold
        embedding_model: Model for embeddings

    Returns:
        List of relevant IndexedNote objects
    """
    # Load graph and collection
    graph = load_graph(index_dir)
    collection = get_chroma_collection(index_dir)

    # Vector search
    vector_results = vector_search(
        query, collection, k=top_k, embedding_model=embedding_model
    )

    # Filter by similarity threshold
    candidate_ids = [
        UUID(note_id)
        for note_id, similarity in vector_results
        if similarity >= min_similarity
    ]

    if not candidate_ids:
        return []

    # Expand via graph
    expanded_ids = graph_expand(candidate_ids, graph, hops=expand_hops)

    # Get note objects
    retrieved_notes = []
    for note_id in expanded_ids:
        note = graph.get_note(note_id)
        if note:
            retrieved_notes.append(note)

    # Sort by indexed_at (most recent first) for context assembly
    retrieved_notes.sort(key=lambda n: n.indexed_at, reverse=True)

    return retrieved_notes


def assemble_context(
    notes: list[IndexedNote],
    max_chars: int = 50000,
) -> str:
    """Assemble note contents into a context string for LLM.

    Args:
        notes: List of notes to include
        max_chars: Maximum total characters

    Returns:
        Formatted context string
    """
    context_parts = []
    total_chars = 0

    for note in notes:
        content = note.path.read_text()
        header = f"--- Note ID: {note.id} ---\nTitle: {note.title or 'Untitled'}\nDate: {note.created_at.isoformat()}\n\n"
        note_text = header + content + "\n\n"

        if total_chars + len(note_text) > max_chars:
            # Truncate this note to fit
            remaining = max_chars - total_chars
            if remaining > len(header) + 100:  # At least some content
                note_text = (
                    header
                    + content[: remaining - len(header) - 20]
                    + "\n...[truncated]\n\n"
                )
                context_parts.append(note_text)
            break

        context_parts.append(note_text)
        total_chars += len(note_text)

    return "".join(context_parts)
