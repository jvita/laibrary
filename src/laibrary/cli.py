"""CLI interface for laibrary."""

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="laibrary",
    help="An AI-curated library of your thoughts and ideas using graph-based RAG.",
)
console = Console()

# Default paths
DEFAULT_DATA_DIR = Path("./data")
DEFAULT_NOTES_DIR = DEFAULT_DATA_DIR / "raw"
DEFAULT_INDEX_DIR = DEFAULT_DATA_DIR / "index"
DEFAULT_SUMMARIES_DIR = DEFAULT_DATA_DIR / "summaries"


@app.command()
def ingest(
    notes_dir: Path = typer.Option(
        DEFAULT_NOTES_DIR, "--notes", "-n", help="Directory containing notes"
    ),
    index_dir: Path = typer.Option(
        DEFAULT_INDEX_DIR, "--index", "-i", help="Directory for index data"
    ),
    all_notes: bool = typer.Option(
        False, "--all", "-a", help="Re-index all notes (not just new/modified)"
    ),
    embedding_model: str = typer.Option(
        "qwen3-embedding:8b", "--embed-model", "-e", help="Ollama embedding model"
    ),
) -> None:
    """Ingest notes into the knowledge graph."""
    import os

    from dotenv import load_dotenv

    from .ingestion import run_ingestion

    load_dotenv()
    model = os.environ.get("MODEL", "qwen3:14b")

    run_ingestion(
        notes_dir=notes_dir,
        index_dir=index_dir,
        model=model,
        embedding_model=embedding_model,
        reindex_all=all_notes,
        console=console,
    )


@app.command()
def query(
    query_text: str = typer.Argument(..., help="Search query"),
    index_dir: Path = typer.Option(
        DEFAULT_INDEX_DIR, "--index", "-i", help="Directory for index data"
    ),
    top_k: int = typer.Option(10, "--top", "-k", help="Number of results"),
    expand: int = typer.Option(0, "--expand", "-x", help="Graph expansion hops"),
) -> None:
    """Query the knowledge graph for relevant notes."""
    from .retrieval import retrieve

    with console.status("[bold green]Searching..."):
        notes = retrieve(
            query=query_text,
            index_dir=index_dir,
            top_k=top_k,
            expand_hops=expand,
        )

    if not notes:
        console.print("[yellow]No relevant notes found.[/yellow]")
        return

    table = Table(title=f"Results for: {query_text}")
    table.add_column("Title", style="cyan")
    table.add_column("Path", style="dim")
    table.add_column("Date", style="green")

    for note in notes:
        table.add_row(
            note.title or "Untitled",
            str(note.path),
            note.created_at.strftime("%Y-%m-%d"),
        )

    console.print(table)


@app.command()
def summarize(
    topic: str = typer.Argument(..., help="Topic to summarize"),
    index_dir: Path = typer.Option(
        DEFAULT_INDEX_DIR, "--index", "-i", help="Directory for index data"
    ),
    summaries_dir: Path = typer.Option(
        DEFAULT_SUMMARIES_DIR, "--summaries", "-s", help="Directory for summaries"
    ),
    update: bool = typer.Option(
        False, "--update", "-u", help="Update existing summary with new notes"
    ),
    top_k: int = typer.Option(10, "--top", "-k", help="Number of notes to consider"),
    commit: bool = typer.Option(
        True, "--commit/--no-commit", help="Commit changes to git"
    ),
) -> None:
    """Generate or update a summary for a topic."""
    from .summarization import summarize_topic
    from .versioning import commit_summary_update

    with console.status(
        f"[bold green]{'Updating' if update else 'Generating'} summary..."
    ):
        try:
            summary, was_updated = asyncio.run(
                summarize_topic(
                    topic=topic,
                    index_dir=index_dir,
                    summaries_dir=summaries_dir,
                    update_existing=update,
                    top_k=top_k,
                )
            )
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1) from e

    if was_updated:
        console.print(f"[green]Summary {'updated' if update else 'created'}![/green]")
        console.print(f"  Topic: {summary.topic}")
        console.print(f"  Version: {summary.version}")
        console.print(f"  Path: {summary.path}")
        console.print(f"  Notes incorporated: {len(summary.incorporated_note_ids)}")

        if commit:
            repo_path = Path.cwd()  # Use current working directory as repo root
            try:
                commit_hash = commit_summary_update(summary, repo_path)
                console.print(f"  Git commit: {commit_hash[:8]}")
            except Exception as e:
                console.print(f"  [yellow]Git commit skipped: {e}[/yellow]")
                console.print("  [dim]You can manually commit the changes.[/dim]")
    else:
        console.print("[yellow]No updates needed - summary is current.[/yellow]")
        console.print(f"  Path: {summary.path}")


@app.command()
def history(
    summary_name: str = typer.Argument(..., help="Summary name (without .md)"),
    summaries_dir: Path = typer.Option(
        DEFAULT_SUMMARIES_DIR, "--summaries", "-s", help="Directory for summaries"
    ),
    diff: tuple[str, str] = typer.Option(
        (None, None), "--diff", "-d", help="Show diff between two commits"
    ),
) -> None:
    """View version history for a summary."""
    from .versioning import SummaryVersionControl

    summary_path = summaries_dir / f"{summary_name}.md"
    if not summary_path.exists():
        console.print(f"[red]Summary not found: {summary_path}[/red]")
        raise typer.Exit(1)

    repo_path = summaries_dir.parent
    vc = SummaryVersionControl(repo_path)

    if diff[0] and diff[1]:
        # Show diff between two versions
        diff_text = vc.diff_versions(summary_path, diff[0], diff[1])
        if diff_text:
            console.print(diff_text)
        else:
            console.print("[yellow]No differences or commits not found.[/yellow]")
        return

    # Show version history
    versions = vc.get_history(summary_path)

    if not versions:
        console.print("[yellow]No version history found.[/yellow]")
        return

    table = Table(title=f"History: {summary_name}")
    table.add_column("Version", style="cyan")
    table.add_column("Commit", style="dim")
    table.add_column("Date", style="green")

    for v in versions:
        table.add_row(
            str(v.version),
            v.git_commit[:8],
            v.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@app.command()
def status(
    index_dir: Path = typer.Option(
        DEFAULT_INDEX_DIR, "--index", "-i", help="Directory for index data"
    ),
    summaries_dir: Path = typer.Option(
        DEFAULT_SUMMARIES_DIR, "--summaries", "-s", help="Directory for summaries"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed stats"),
) -> None:
    """Show knowledge graph status."""
    from .ingestion import load_graph

    graph = load_graph(index_dir)

    console.print("[bold]Knowledge Graph Status[/bold]")
    console.print(f"  Notes indexed: {len(graph.notes)}")
    console.print(f"  Edges: {len(graph.edges)}")
    console.print(f"  Summaries: {len(graph.summaries)}")

    if verbose and graph.notes:
        console.print("\n[bold]Recent Notes:[/bold]")
        sorted_notes = sorted(
            graph.notes.values(), key=lambda n: n.indexed_at, reverse=True
        )
        for note in sorted_notes[:5]:
            console.print(f"  - {note.title or 'Untitled'} ({note.path.name})")

    if verbose and graph.summaries:
        console.print("\n[bold]Summaries:[/bold]")
        for summary in graph.summaries.values():
            console.print(f"  - {summary.topic} (v{summary.version})")


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
