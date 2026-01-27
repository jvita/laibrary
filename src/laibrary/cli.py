"""Typer CLI for the PKM system."""

import asyncio
from pathlib import Path

import logfire
import typer
from dotenv import load_dotenv

from .git_wrapper import IsolatedGitRepo
from .workflow import run_workflow

# Load environment variables from .env file
load_dotenv()

# Configure logfire
logfire.configure()
logfire.instrument_pydantic_ai()

app = typer.Typer(
    name="laibrary",
    help="Evolutionary PKM system with LLM-powered document updates",
)


def get_data_dir() -> Path:
    """Get the data directory path."""
    return Path("data")


@app.command()
def init() -> None:
    """Initialize the PKM data directory with git tracking."""
    data_dir = get_data_dir()
    repo = IsolatedGitRepo(data_dir)

    # Initialize git repo
    repo.init()
    typer.echo(f"Initialized git repository at {data_dir}/.git")

    # Create welcome document if it doesn't exist
    welcome_file = "welcome.md"
    if not repo.file_exists(welcome_file):
        welcome_content = """\
# Welcome to Laibrary

This is your personal knowledge management system.

## Getting Started

Add notes using the command:
```
laibrary note "Your note here"
```

Your notes will be automatically organized and integrated into your documents.
"""
        repo.write_file(welcome_file, welcome_content)
        repo.add_and_commit(welcome_file, "Initialize PKM with welcome document")
        typer.echo(f"Created {welcome_file}")

    typer.echo("PKM system initialized successfully!")


@app.command()
def note(content: str) -> None:
    """Process a note and update documents accordingly."""
    data_dir = get_data_dir()

    if not (data_dir / ".git").exists():
        typer.echo("Error: PKM not initialized. Run 'laibrary init' first.", err=True)
        raise typer.Exit(1)

    typer.echo("Processing note...")

    result = asyncio.run(run_workflow(content, data_dir))

    if result.get("error"):
        typer.echo(f"Error: {result['error']}", err=True)
        raise typer.Exit(1)

    if result.get("committed"):
        update = result.get("document_update")
        if update:
            typer.echo(f"Updated: {update.target_file}")
            typer.echo(f"Commit: {update.commit_message}")
    else:
        typer.echo("No changes made.")


@app.command()
def status() -> None:
    """List all documents in the PKM system."""
    data_dir = get_data_dir()

    if not (data_dir / ".git").exists():
        typer.echo("Error: PKM not initialized. Run 'laibrary init' first.", err=True)
        raise typer.Exit(1)

    repo = IsolatedGitRepo(data_dir)
    files = repo.list_files()

    if not files:
        typer.echo("No documents found.")
        return

    typer.echo("Documents:")
    for f in files:
        content = repo.get_file_content(f)
        lines = len(content.splitlines()) if content else 0
        typer.echo(f"  {f} ({lines} lines)")


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
