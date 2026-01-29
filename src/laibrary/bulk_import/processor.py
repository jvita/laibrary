"""Bulk import processor - runs notes through existing workflow."""

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from ..workflow import run_workflow_with_state
from .parser import ParsedNote, deduplicate, parse_markdown_directory


async def process_bulk_import(
    import_path: Path,
    data_dir: Path,
    dry_run: bool = False,
) -> None:
    """Process a directory of markdown files through the PKM workflow.

    Args:
        import_path: Directory containing markdown files to import
        data_dir: PKM data directory
        dry_run: If True, preview without processing
    """
    console = Console()

    # 1. Parse files
    console.print("[cyan]Scanning for markdown files...[/cyan]")
    all_notes = parse_markdown_directory(import_path)

    if not all_notes:
        console.print("[yellow]No markdown files found.[/yellow]")
        return

    console.print(f"Found {len(all_notes)} markdown files")

    # 2. Deduplicate
    unique_notes, duplicates = deduplicate(all_notes)

    if duplicates:
        console.print(f"[yellow]Skipping {len(duplicates)} exact duplicates[/yellow]")

    # 3. Preview
    console.print(Panel("[bold]Notes to Import[/bold]", border_style="cyan"))

    for i, note in enumerate(unique_notes[:20], 1):  # Show first 20
        console.print(f"  {i}. {note.title}")

    if len(unique_notes) > 20:
        console.print(f"  ... and {len(unique_notes) - 20} more")

    if dry_run:
        console.print("\n[yellow]Dry run - no changes made.[/yellow]")
        return

    # 4. Confirm
    if not typer.confirm(f"\nProcess {len(unique_notes)} notes?", default=True):
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

            try:
                result = await run_workflow_with_state(
                    {
                        "user_input": note.content,
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

    if failures:
        console.print("\n[red]Failed notes:[/red]")
        for note, error in failures[:10]:  # Show first 10
            console.print(f"  \u2022 {note.title}: {error[:60]}...")
        if len(failures) > 10:
            console.print(f"  ... and {len(failures) - 10} more")
