"""Note ingestion pipeline for the graph RAG system."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import chromadb
import networkx as nx
import ollama
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn

from .graph_models import GraphEdge, IndexedNote, KnowledgeGraph


def compute_content_hash(content: str) -> str:
    """Compute SHA256 hash of content for change detection."""
    return hashlib.sha256(content.encode()).hexdigest()


def get_file_timestamps(path: Path) -> tuple[datetime, datetime]:
    """Get creation and modification timestamps for a file."""
    stat = path.stat()
    # Use mtime as both created and modified (ctime is inode change time on Unix)
    modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    return modified, modified


def detect_changes(
    notes_dir: Path, graph: KnowledgeGraph
) -> tuple[list[Path], list[Path]]:
    """Detect new and modified notes.

    Returns:
        Tuple of (new_paths, modified_paths)
    """
    new_paths: list[Path] = []
    modified_paths: list[Path] = []

    # Get all existing note paths and their hashes
    existing_paths = {note.path: note.content_hash for note in graph.notes.values()}

    # Scan notes directory
    for md_file in notes_dir.rglob("*.md"):
        content = md_file.read_text()
        content_hash = compute_content_hash(content)

        if md_file not in existing_paths:
            new_paths.append(md_file)
        elif existing_paths[md_file] != content_hash:
            modified_paths.append(md_file)

    return new_paths, modified_paths


def generate_embedding(content: str, model: str = "nomic-embed-text") -> list[float]:
    """Generate embedding vector using Ollama."""
    response = ollama.embed(model=model, input=content)
    return response["embeddings"][0]


async def extract_title(content: str, model: str) -> str:
    """Extract a title from note content using LLM."""
    # Import here to avoid circular imports
    from .agents import title_agent

    result = await title_agent.run(content)
    return result.output


async def ingest_note(
    path: Path,
    chroma_collection: chromadb.Collection,
    model: str,
    embedding_model: str = "nomic-embed-text",
) -> tuple[IndexedNote, list[float]]:
    """Ingest a single note into the system.

    Args:
        path: Path to the markdown note
        chroma_collection: ChromaDB collection for vector storage
        model: LLM model name for title extraction
        embedding_model: Ollama model for embeddings

    Returns:
        Tuple of (IndexedNote with all metadata populated, embedding vector)
    """
    content = path.read_text()
    content_hash = compute_content_hash(content)
    created_at, _ = get_file_timestamps(path)
    indexed_at = datetime.now(UTC)

    # Generate embedding
    embedding = generate_embedding(content, model=embedding_model)

    # Extract title using LLM
    title = await extract_title(content, model)

    # Create the indexed note
    note = IndexedNote(
        path=path,
        content_hash=content_hash,
        created_at=created_at,
        indexed_at=indexed_at,
        title=title,
    )

    # Store in ChromaDB
    chroma_collection.add(
        ids=[str(note.id)],
        embeddings=[embedding],
        documents=[content],
        metadatas=[
            {
                "path": str(path),
                "title": title or "",
                "created_at": created_at.isoformat(),
                "indexed_at": indexed_at.isoformat(),
            }
        ],
    )
    note.embedding_id = str(note.id)

    return note, embedding


def build_semantic_edges(
    note: IndexedNote,
    embedding: list[float],
    graph: KnowledgeGraph,
    chroma_collection: chromadb.Collection,
    threshold: float = 0.7,
    max_edges: int = 10,
) -> list[GraphEdge]:
    """Build semantic similarity edges for a note.

    Args:
        note: The newly indexed note
        embedding: The embedding vector for the note
        graph: The knowledge graph
        chroma_collection: ChromaDB collection
        threshold: Minimum similarity score for creating an edge
        max_edges: Maximum number of edges to create

    Returns:
        List of new edges to add to the graph
    """
    if not graph.notes:
        return []

    # Query for similar notes (exclude self)
    results = chroma_collection.query(
        query_embeddings=[embedding],
        n_results=min(max_edges + 1, len(graph.notes)),
        where={"path": {"$ne": str(note.path)}},
        include=["distances"],
    )

    # ChromaDB returns distances (lower = more similar for cosine)
    # Convert to similarity: similarity = 1 - distance for normalized vectors
    edges: list[GraphEdge] = []

    if results and results["ids"] and results["ids"][0]:
        for note_id_str, distance in zip(
            results["ids"][0], results["distances"][0], strict=False
        ):
            # Convert distance to similarity (ChromaDB uses L2 by default)
            # For cosine distance: similarity = 1 - distance/2
            similarity = max(0, 1 - distance / 2)

            if similarity >= threshold:
                edges.append(
                    GraphEdge(
                        source_id=note.id,
                        target_id=UUID(note_id_str),
                        weight=similarity,
                    )
                )

    return edges[:max_edges]


def build_networkx_graph(graph: KnowledgeGraph) -> nx.Graph:
    """Build a NetworkX graph from the knowledge graph."""
    graph = nx.Graph()

    # Add nodes
    for note_id, note in graph.notes.items():
        graph.add_node(note_id, title=note.title, path=str(note.path))

    # Add edges
    for edge in graph.edges:
        graph.add_edge(str(edge.source_id), str(edge.target_id), weight=edge.weight)

    return graph


def save_graph(graph: KnowledgeGraph, index_dir: Path) -> None:
    """Save the knowledge graph to disk."""
    index_dir.mkdir(parents=True, exist_ok=True)
    graph_file = index_dir / "graph.json"

    # Serialize to JSON
    data = graph.model_dump(mode="json")
    graph_file.write_text(json.dumps(data, indent=2, default=str))


def load_graph(index_dir: Path) -> KnowledgeGraph:
    """Load the knowledge graph from disk."""
    graph_file = index_dir / "graph.json"

    if not graph_file.exists():
        return KnowledgeGraph()

    data = json.loads(graph_file.read_text())
    return KnowledgeGraph.model_validate(data)


def get_chroma_collection(index_dir: Path) -> chromadb.Collection:
    """Get or create the ChromaDB collection."""
    chroma_dir = index_dir / "chroma"
    chroma_dir.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(chroma_dir))
    collection = client.get_or_create_collection(
        name="notes",
        metadata={"hnsw:space": "cosine"},  # Use cosine similarity
    )
    return collection


async def _process_note(
    path: Path,
    graph: KnowledgeGraph,
    collection: chromadb.Collection,
    model: str,
    embedding_model: str,
    progress: Progress,
    task_id: TaskID,
) -> None:
    """Process a single note (ingest and build edges)."""
    progress.update(task_id, description=f"[cyan]Ingesting[/] {path.name}")
    note, embedding = await ingest_note(path, collection, model, embedding_model)
    edges = build_semantic_edges(note, embedding, graph, collection)
    graph.add_note(note)
    for edge in edges:
        graph.add_edge(edge)
    progress.advance(task_id)


async def _remove_old_note(
    path: Path,
    graph: KnowledgeGraph,
    collection: chromadb.Collection,
) -> None:
    """Remove an old note from the graph and ChromaDB."""
    old_note = next(
        (n for n in graph.notes.values() if n.path == path),
        None,
    )
    if old_note:
        del graph.notes[str(old_note.id)]
        graph.edges = [
            e
            for e in graph.edges
            if e.source_id != old_note.id and e.target_id != old_note.id
        ]
        collection.delete(ids=[str(old_note.id)])


async def run_ingestion_async(
    notes_dir: Path,
    index_dir: Path,
    model: str,
    embedding_model: str = "nomic-embed-text",
    reindex_all: bool = False,
    console: Console | None = None,
) -> tuple[int, int]:
    """Run the full ingestion pipeline (async version).

    Args:
        notes_dir: Directory containing raw markdown notes
        index_dir: Directory for storing index data
        model: LLM model name
        embedding_model: Embedding model name
        reindex_all: If True, reindex all notes
        console: Rich console for output (creates one if not provided)

    Returns:
        Tuple of (new_count, modified_count)
    """
    if console is None:
        console = Console()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total})"),
        console=console,
        transient=True,
    ) as progress:
        # Setup task
        setup_task = progress.add_task("[yellow]Setting up...", total=None)

        # Load existing graph (or create empty)
        if reindex_all:
            graph = KnowledgeGraph()
            # Clear ChromaDB
            chroma_dir = index_dir / "chroma"
            if chroma_dir.exists():
                import shutil

                shutil.rmtree(chroma_dir)
        else:
            graph = load_graph(index_dir)

        # Get ChromaDB collection
        collection = get_chroma_collection(index_dir)

        # Detect changes
        progress.update(setup_task, description="[yellow]Scanning for changes...")
        if reindex_all:
            new_paths = list(notes_dir.rglob("*.md"))
            modified_paths: list[Path] = []
        else:
            new_paths, modified_paths = detect_changes(notes_dir, graph)

        progress.remove_task(setup_task)

        total_to_process = len(new_paths) + len(modified_paths)
        if total_to_process == 0:
            console.print("[green]✓[/] No changes detected")
            return 0, 0

        # Process new notes
        if new_paths:
            new_task = progress.add_task(
                "[cyan]Processing new notes...", total=len(new_paths)
            )
            for path in new_paths:
                await _process_note(
                    path, graph, collection, model, embedding_model, progress, new_task
                )
            progress.update(new_task, description="[green]✓ New notes processed")

        # Process modified notes (remove old, add new)
        if modified_paths:
            mod_task = progress.add_task(
                "[cyan]Updating modified notes...", total=len(modified_paths)
            )
            for path in modified_paths:
                progress.update(mod_task, description=f"[cyan]Updating[/] {path.name}")
                await _remove_old_note(path, graph, collection)
                await _process_note(
                    path, graph, collection, model, embedding_model, progress, mod_task
                )
            progress.update(mod_task, description="[green]✓ Modified notes updated")

        # Save graph
        save_task = progress.add_task("[yellow]Saving graph...", total=None)
        save_graph(graph, index_dir)
        progress.update(save_task, description="[green]✓ Graph saved")

    # Print summary after progress bar is done
    console.print(
        f"[green]✓[/] Ingestion complete: "
        f"[cyan]{len(new_paths)}[/] new, [cyan]{len(modified_paths)}[/] modified"
    )

    return len(new_paths), len(modified_paths)


def run_ingestion(
    notes_dir: Path,
    index_dir: Path,
    model: str,
    embedding_model: str = "nomic-embed-text",
    reindex_all: bool = False,
    console: Console | None = None,
) -> tuple[int, int]:
    """Run the full ingestion pipeline (sync wrapper).

    Args:
        notes_dir: Directory containing raw markdown notes
        index_dir: Directory for storing index data
        model: LLM model name
        embedding_model: Embedding model name
        reindex_all: If True, reindex all notes
        console: Rich console for output (creates one if not provided)

    Returns:
        Tuple of (new_count, modified_count)
    """
    import asyncio

    return asyncio.run(
        run_ingestion_async(
            notes_dir=notes_dir,
            index_dir=index_dir,
            model=model,
            embedding_model=embedding_model,
            reindex_all=reindex_all,
            console=console,
        )
    )
