"""Bulk import processor - runs notes through existing workflow."""

import re
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..workflow import run_workflow_with_state
from .parser import ParsedNote, deduplicate, parse_markdown_path


def _title_to_project_name(title: str) -> str:
    """Convert a title to a valid project name.

    Examples:
        "My Project Idea" -> "my-project-idea"
        "Web App v2.0" -> "web-app-v2-0"
    """
    # Convert to lowercase
    name = title.lower()
    # Replace spaces and underscores with hyphens
    name = re.sub(r"[\s_]+", "-", name)
    # Remove any characters that aren't alphanumeric or hyphen
    name = re.sub(r"[^a-z0-9-]", "", name)
    # Remove multiple consecutive hyphens
    name = re.sub(r"-+", "-", name)
    # Remove leading/trailing hyphens
    name = name.strip("-")
    # Limit length
    if len(name) > 50:
        name = name[:50].rstrip("-")
    return name or "imported"


async def process_bulk_import(
    import_path: Path,
    data_dir: Path,
    dry_run: bool = False,
    target_project: str | None = None,
) -> None:
    """Process markdown file(s) through the PKM workflow.

    Args:
        import_path: File or directory containing markdown files to import
        data_dir: PKM data directory
        dry_run: If True, preview without processing
        target_project: If specified, all notes go to this project.
                       Otherwise, each file becomes its own project.
    """
    console = Console()

    # 1. Parse files
    if import_path.is_file():
        console.print(f"[cyan]Reading {import_path.name}...[/cyan]")
    else:
        console.print("[cyan]Scanning for markdown files...[/cyan]")
    all_notes = parse_markdown_path(import_path)

    if not all_notes:
        console.print("[yellow]No markdown files found.[/yellow]")
        return

    console.print(f"Found {len(all_notes)} markdown files")

    # 2. Deduplicate
    unique_notes, duplicates = deduplicate(all_notes)

    if duplicates:
        console.print(f"[yellow]Skipping {len(duplicates)} exact duplicates[/yellow]")

    # 3. Preview
    if target_project:
        console.print(
            Panel(
                f"[bold]Importing to project: {target_project}[/bold]",
                border_style="cyan",
            )
        )
        for i, note in enumerate(unique_notes[:20], 1):
            console.print(f"  {i}. {note.title}")
    else:
        console.print(Panel("[bold]Notes to Import[/bold]", border_style="cyan"))
        for i, note in enumerate(unique_notes[:20], 1):
            project_name = _title_to_project_name(note.title)
            console.print(f"  {i}. {note.title} -> projects/{project_name}.md")

    if len(unique_notes) > 20:
        console.print(f"  ... and {len(unique_notes) - 20} more")

    if dry_run:
        console.print("\n[yellow]Dry run - no changes made.[/yellow]")
        return

    # 4. Confirm
    if target_project:
        confirm_msg = f"\nImport {len(unique_notes)} notes into '{target_project}'?"
    else:
        confirm_msg = f"\nCreate {len(unique_notes)} projects?"

    if not typer.confirm(confirm_msg, default=True):
        console.print("[yellow]Import cancelled.[/yellow]")
        return

    # 5. Process each note
    console.print()

    successes: list[tuple[ParsedNote, dict]] = []
    failures: list[tuple[ParsedNote, str]] = []
    skipped: list[ParsedNote] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Processing...", total=len(unique_notes))

        for note in unique_notes:
            progress.update(task, description=f"Processing: {note.title[:40]}...")

            # Determine project name
            if target_project:
                project_name = target_project
            else:
                project_name = _title_to_project_name(note.title)

            # Format as /project-name followed by the note content
            user_input = f"/{project_name} {note.content}"

            try:
                result = await run_workflow_with_state(
                    {
                        "user_input": user_input,
                        "confirmation_mode": "auto",  # Auto-confirm during bulk
                    },
                    data_dir,
                )

                if result.get("error"):
                    failures.append((note, result["error"]))
                elif result.get("committed"):
                    successes.append((note, result))
                else:
                    # No changes needed (not an error)
                    skipped.append(note)

            except Exception as e:
                failures.append((note, str(e)))

            progress.advance(task)

    # 6. Report
    console.print()
    console.print(Panel("[bold]Import Complete[/bold]", border_style="green"))
    console.print(f"  [green]\u2713[/green] Processed: {len(successes)}")
    console.print(f"  [blue]\u25cb[/blue] No changes needed: {len(skipped)}")
    console.print(f"  [yellow]\u25cb[/yellow] Skipped duplicates: {len(duplicates)}")
    console.print(f"  [red]\u2717[/red] Failed: {len(failures)}")

    if target_project:
        console.print(f"\n  Target: projects/{target_project}.md")

    if failures:
        console.print("\n[red]Failed notes:[/red]")
        for note, error in failures[:10]:  # Show first 10
            console.print(f"  \u2022 {note.title}: {error[:60]}...")
        if len(failures) > 10:
            console.print(f"  ... and {len(failures) - 10} more")
