"""Typer CLI for the PKM system."""

import asyncio
from pathlib import Path

import logfire
import typer
from dotenv import load_dotenv

from .chat import run_chat_session
from .git_wrapper import IsolatedGitRepo
from .workflow import run_workflow_with_state

# Load environment variables from .env file
load_dotenv()

# Configure logfire (disable console output to avoid cluttering chat)
logfire.configure(console=False, send_to_logfire=False)
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

    # Create projects directory if it doesn't exist
    projects_dir = data_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    typer.echo("PKM system initialized successfully!")
    typer.echo("\nGet started:")
    typer.echo("  laibrary note '/my-project first note about the project'")
    typer.echo("  laibrary projects  # list all projects")


@app.command()
def note(
    content: str,
    auto_confirm: bool = typer.Option(
        False,
        "--auto-confirm",
        "-y",
        help="Skip confirmation prompts",
    ),
) -> None:
    """Process a note and update a project document.

    Content must start with /project-name followed by the note.

    Examples:
        laibrary note "/my-project added a new feature"
        laibrary note "/webapp fixed the login bug"
    """
    data_dir = get_data_dir()

    if not (data_dir / ".git").exists():
        typer.echo("Error: PKM not initialized. Run 'laibrary init' first.", err=True)
        raise typer.Exit(1)

    # Check for /list command
    if content.strip().lower() in ("/list", "/projects"):
        # Redirect to projects command
        projects()
        return

    # Validate that content starts with /project-name
    if not content.strip().startswith("/"):
        typer.echo(
            "Error: Note must start with /project-name. Example: /my-project your note here",
            err=True,
        )
        typer.echo("\nUse 'laibrary projects' to see available projects.", err=True)
        raise typer.Exit(1)

    typer.echo("Processing note...")

    initial_state = {
        "user_input": content,
        "confirmation_mode": "auto" if auto_confirm else "interactive",
    }
    result = asyncio.run(run_workflow_with_state(initial_state, data_dir))

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
def projects() -> None:
    """List all projects in the PKM system."""
    data_dir = get_data_dir()

    if not (data_dir / ".git").exists():
        typer.echo("Error: PKM not initialized. Run 'laibrary init' first.", err=True)
        raise typer.Exit(1)

    repo = IsolatedGitRepo(data_dir)

    # List only project files
    project_files = list(repo.list_files("projects/*.md"))

    if not project_files:
        typer.echo("No projects found.")
        typer.echo("\nCreate one with: laibrary note '/project-name your first note'")
        return

    typer.echo("Projects:")
    for f in sorted(project_files):
        # Extract project name
        name = f.replace("projects/", "").replace(".md", "")
        content = repo.get_file_content(f)
        lines = len(content.splitlines()) if content else 0
        typer.echo(f"  {name} ({lines} lines)")


@app.command()
def status() -> None:
    """Show status of the PKM system."""
    data_dir = get_data_dir()

    if not (data_dir / ".git").exists():
        typer.echo("Error: PKM not initialized. Run 'laibrary init' first.", err=True)
        raise typer.Exit(1)

    repo = IsolatedGitRepo(data_dir)
    files = list(repo.list_files())

    typer.echo(f"Data directory: {data_dir}")
    typer.echo(f"Total files: {len(files)}")

    # Count projects
    project_files = [f for f in files if f.startswith("projects/")]
    typer.echo(f"Projects: {len(project_files)}")


@app.command()
def chat() -> None:
    """Start an interactive chat session."""
    data_dir = get_data_dir()

    if not (data_dir / ".git").exists():
        typer.echo("Error: PKM not initialized. Run 'laibrary init' first.", err=True)
        raise typer.Exit(1)

    asyncio.run(run_chat_session(data_dir))


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Host to bind to"),
    port: int = typer.Option(8080, "--port", "-p", help="Port to bind to"),
    ssl_certfile: Path = typer.Option(
        None,
        "--ssl-certfile",
        envvar="SSL_CERTFILE",
        help="SSL certificate file (e.g. from tailscale cert)",
    ),
    ssl_keyfile: Path = typer.Option(
        None,
        "--ssl-keyfile",
        envvar="SSL_KEYFILE",
        help="SSL key file (e.g. from tailscale cert)",
    ),
) -> None:
    """Start the web server for PWA access."""
    import uvicorn

    from .web import create_app

    data_dir = get_data_dir()

    if not (data_dir / ".git").exists():
        typer.echo("Error: PKM not initialized. Run 'laibrary init' first.", err=True)
        raise typer.Exit(1)

    if bool(ssl_certfile) != bool(ssl_keyfile):
        typer.echo(
            "Error: --ssl-certfile and --ssl-keyfile must be used together.", err=True
        )
        raise typer.Exit(1)

    scheme = "https" if ssl_certfile else "http"
    typer.echo(f"Starting Laibrary web server at {scheme}://{host}:{port}")
    typer.echo("Press Ctrl+C to stop")

    app_instance = create_app(data_dir)
    uvicorn.run(
        app_instance,
        host=host,
        port=port,
        log_level="info",
        ssl_certfile=str(ssl_certfile) if ssl_certfile else None,
        ssl_keyfile=str(ssl_keyfile) if ssl_keyfile else None,
    )


@app.command(name="import")
def import_notes(
    path: Path = typer.Argument(..., help="File or directory to import"),
    project: str = typer.Option(
        None,
        "--project",
        "-p",
        help="Target project name (without /). All notes go to this project.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Preview notes without processing",
    ),
) -> None:
    """Import markdown notes.

    Can import a single file or all files in a directory.
    Use --project to specify the target project (required for single files).

    Examples:
        laibrary import ./note.md --project my-proj   # Single file -> project
        laibrary import ./notes --project my-proj     # All files -> one project
        laibrary import ./notes                       # Each file -> own project
    """
    from .bulk_import.processor import process_bulk_import

    data_dir = get_data_dir()

    if not (data_dir / ".git").exists():
        typer.echo("Error: PKM not initialized. Run 'laibrary init' first.", err=True)
        raise typer.Exit(1)

    if not path.exists():
        typer.echo(f"Error: Path does not exist: {path}", err=True)
        raise typer.Exit(1)

    # Single file import requires --project
    if path.is_file():
        if not project:
            typer.echo(
                "Error: --project is required when importing a single file.", err=True
            )
            typer.echo(
                "Example: laibrary import note.md --project my-project", err=True
            )
            raise typer.Exit(1)

    asyncio.run(process_bulk_import(path, data_dir, dry_run, project))


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
