"""Confirmation node - handles user confirmation for file creation."""

from pathlib import Path

import logfire
import typer
from rich.console import Console
from rich.panel import Panel

from ..schemas import ConfirmationResult, FilePlan, PKMState, UpdatePlan


def _get_smart_suggestions(
    note_content: str, summaries: dict[str, str], max_suggestions: int = 5
) -> list[str]:
    """Rank existing projects by relevance to note content.

    Uses simple keyword-based ranking for MVP. Future enhancement:
    use LLM for semantic similarity.

    Args:
        note_content: The user's note content
        summaries: Dict mapping file paths to summaries
        max_suggestions: Maximum number of suggestions to return

    Returns:
        List of file paths ranked by relevance
    """
    # Only include projects (not notes or other docs)
    project_summaries = {
        path: summary
        for path, summary in summaries.items()
        if path.startswith("projects/")
    }

    if not project_summaries:
        return []

    # Simple keyword overlap scoring
    note_words = set(note_content.lower().split())
    scores = []

    for path, summary in project_summaries.items():
        summary_words = set(summary.lower().split())
        overlap = len(note_words & summary_words)
        scores.append((overlap, path))

    # Sort by score descending, take top N
    scores.sort(reverse=True, key=lambda x: x[0])
    return [path for _, path in scores[:max_suggestions]]


def _confirm_single_file(
    file_plan: FilePlan, note_content: str, existing_summaries: dict[str, str]
) -> ConfirmationResult:
    """Prompt user to confirm creation of a single file.

    Args:
        file_plan: The file creation plan
        note_content: The user's note content (for smart suggestions)
        existing_summaries: Dict of existing document summaries

    Returns:
        ConfirmationResult with user's decision
    """
    console = Console()

    # Display confirmation prompt
    prompt_text = (
        f"[bold]New Project Creation[/bold]\n\n"
        f"File: [cyan]{file_plan.target_file}[/cyan]\n"
        f"Action: {file_plan.description}\n\n"
        f"What would you like to do?"
    )

    console.print(Panel(prompt_text, border_style="yellow", title="Confirmation"))

    # Get smart suggestions
    suggestions = _get_smart_suggestions(note_content, existing_summaries)

    # Build options
    console.print("\n[bold]Options:[/bold]")
    console.print("  [green]1.[/green] Create new project (confirm)")
    console.print("  [yellow]2.[/yellow] Add to existing project (redirect)")
    console.print("  [red]3.[/red] Cancel operation")

    if suggestions:
        console.print("\n[bold]Smart Suggestions:[/bold]")
        for i, path in enumerate(suggestions, start=1):
            summary = existing_summaries.get(path, "No summary available")
            console.print(f"  [cyan]{i}.[/cyan] {path}")
            console.print(f"     {summary[:80]}...")

    # Get user input
    while True:
        choice = typer.prompt("\nYour choice", type=str).strip()

        if choice == "1":
            return ConfirmationResult(action="confirm")

        elif choice == "2":
            # Redirect - ask which file
            if suggestions:
                console.print("\n[bold]Choose a project:[/bold]")
                for i, path in enumerate(suggestions, start=1):
                    console.print(f"  {i}. {path}")
                console.print(f"  {len(suggestions) + 1}. Enter custom path")

                redirect_choice = typer.prompt("Project number", type=str).strip()

                try:
                    choice_num = int(redirect_choice)
                    if 1 <= choice_num <= len(suggestions):
                        target = suggestions[choice_num - 1]
                        return ConfirmationResult(action="redirect", redirect_to=target)
                    elif choice_num == len(suggestions) + 1:
                        custom_path = typer.prompt("Enter file path", type=str).strip()
                        return ConfirmationResult(
                            action="redirect", redirect_to=custom_path
                        )
                    else:
                        console.print(
                            "[red]Invalid choice. Please try again.[/red]", err=True
                        )
                        continue
                except ValueError:
                    console.print(
                        "[red]Invalid input. Please enter a number.[/red]", err=True
                    )
                    continue
            else:
                # No suggestions, ask for custom path
                custom_path = typer.prompt(
                    "Enter file path to redirect to", type=str
                ).strip()
                return ConfirmationResult(action="redirect", redirect_to=custom_path)

        elif choice == "3":
            return ConfirmationResult(action="cancel")

        else:
            console.print(
                "[red]Invalid choice. Please enter 1, 2, or 3.[/red]", err=True
            )


def _process_confirmations(state: PKMState, data_dir: Path) -> PKMState:
    """Process confirmations for all file creation plans.

    Args:
        state: Current workflow state
        data_dir: Path to data directory (unused, kept for consistency)

    Returns:
        Updated state with modified plan or error
    """
    update_plan = state.get("update_plan")
    if not update_plan:
        return state

    user_input = state.get("user_input", "")
    summaries = state.get("summaries", {})

    # Find all create actions
    create_plans = [fp for fp in update_plan.file_plans if fp.action == "create"]

    if not create_plans:
        # No creates to confirm, pass through
        return state

    console = Console()
    confirmations: dict[str, ConfirmationResult] = {}
    modified_file_plans: list[FilePlan] = []

    # Process each creation plan
    for file_plan in update_plan.file_plans:
        if file_plan.action != "create":
            # Keep non-create plans as-is
            modified_file_plans.append(file_plan)
            continue

        # Confirm this creation
        logfire.info("Requesting confirmation", file=file_plan.target_file)
        result = _confirm_single_file(file_plan, user_input, summaries)
        confirmations[file_plan.target_file] = result

        if result.action == "confirm":
            # Keep the create plan as-is
            modified_file_plans.append(file_plan)
            console.print(f"[green]✓[/green] Will create {file_plan.target_file}\n")

        elif result.action == "redirect":
            # Change to modify the redirect target instead
            modified_plan = FilePlan(
                target_file=result.redirect_to,
                action="modify",
                description=f"Add content redirected from {file_plan.target_file}: {file_plan.description}",
            )
            modified_file_plans.append(modified_plan)
            console.print(
                f"[yellow]↪[/yellow] Will add to {result.redirect_to} instead\n"
            )

        elif result.action == "cancel":
            # User cancelled
            logfire.info("User cancelled operation")
            console.print("[red]✗[/red] Operation cancelled\n")
            return {**state, "error": "User cancelled operation"}

    # Update the plan with modified file plans
    modified_plan = UpdatePlan(
        file_plans=modified_file_plans,
        reasoning=update_plan.reasoning,
        commit_message=update_plan.commit_message,
    )

    return {**state, "update_plan": modified_plan, "user_confirmations": confirmations}


@logfire.instrument("confirmation_node")
def confirmation_node(state: PKMState, data_dir: Path) -> PKMState:
    """Handle user confirmation for file creation.

    In auto mode: pass through unchanged
    In interactive mode: prompt for confirmations and modify plan

    Args:
        state: Current workflow state
        data_dir: Path to data directory

    Returns:
        Updated state (possibly with modified plan or error)
    """
    if state.get("error"):
        return state

    confirmation_mode = state.get("confirmation_mode", "interactive")

    if confirmation_mode == "auto":
        # Auto-confirm mode - pass through unchanged
        logfire.info("Auto-confirm mode, skipping confirmation prompts")
        return state

    # Interactive mode - process confirmations
    logfire.info("Interactive mode, processing confirmations")
    return _process_confirmations(state, data_dir)
