"""Interactive CLI chat runner with message queuing."""

import asyncio
from pathlib import Path

from ..projects import list_projects
from .session import ChatSession


async def _display_completed_messages(
    queue_manager,
    displayed_messages: set,
    console,
):
    """Background task to poll for and display completed messages."""
    from prompt_toolkit import print_formatted_text
    from prompt_toolkit.formatted_text import FormattedText

    from ..queue_manager import MessageStatus

    while True:
        await asyncio.sleep(0.5)  # Poll every 500ms

        for msg_id, msg in queue_manager.messages.items():
            if msg_id in displayed_messages:
                continue

            if msg.status == MessageStatus.COMPLETED:
                displayed_messages.add(msg_id)
                # Use prompt_toolkit's print instead of Rich to avoid ANSI conflicts
                print_formatted_text(
                    FormattedText([("ansigreen", f"\n✓ Message #{msg_id}:\n")])
                )
                print_formatted_text(msg.result["response"])
                if msg.result.get("update_details"):
                    print_formatted_text(
                        FormattedText(
                            [
                                (
                                    "",
                                    f"Committed: {msg.result['update_details']['commit_message']}\n",
                                )
                            ]
                        )
                    )

            elif msg.status == MessageStatus.FAILED:
                displayed_messages.add(msg_id)
                print_formatted_text(
                    FormattedText([("ansired", f"\n✗ Message #{msg_id} failed:\n")])
                )
                print_formatted_text(FormattedText([("ansired", f"{msg.error}\n")]))


def _display_queue_status(console, queue_manager):
    """Display /status command output."""
    status = queue_manager.get_queue_status()

    console.print("\n[bold]Queue Status:[/bold]")
    console.print(f"  Total messages: {status['total_messages']}")
    console.print(f"  Queued: {len(status['queued_messages'])}")
    console.print(f"  Processing: {len(status['processing_messages'])}")
    console.print(f"  Completed: {status['completed_count']}")
    console.print(f"  Failed: {status['failed_count']}")

    if status["queued_messages"]:
        console.print("\n[bold]Queued:[/bold]")
        for msg in status["queued_messages"]:
            preview = (
                msg["content"][:50] + "..."
                if len(msg["content"]) > 50
                else msg["content"]
            )
            console.print(f"  #{msg['id']}: {preview}")

    if status["processing_messages"]:
        console.print("\n[bold]Processing:[/bold]")
        for msg in status["processing_messages"]:
            preview = (
                msg["content"][:50] + "..."
                if len(msg["content"]) > 50
                else msg["content"]
            )
            console.print(f"  #{msg['id']}: {preview}")


async def run_chat_session(data_dir: Path) -> None:
    """Run an interactive chat session with message queuing.

    This is designed to be called from the CLI.
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.patch_stdout import patch_stdout
    from rich.console import Console
    from rich.panel import Panel

    from ..queue_manager import MessageQueueManager

    console = Console()
    session = ChatSession(data_dir=data_dir)
    queue_manager = MessageQueueManager(session, data_dir)
    displayed_messages = set()  # Track what we've shown
    prompt_session = PromptSession()  # Create prompt_toolkit session

    # Start background task to display results
    display_task = asyncio.create_task(
        _display_completed_messages(queue_manager, displayed_messages, console)
    )

    console.print(
        Panel(
            "[bold green]Laibrary Chat[/bold green]\n\n"
            "Commands:\n"
            "  [bold]/use project[/bold] - Select a project\n"
            "  [bold]/list[/bold] - Show available projects\n"
            "  [bold]/read [project][/bold] - Print project document\n"
            "  [bold]/project note[/bold] - Add note to specific project\n"
            "  [bold]/status[/bold] - Show queue status\n"
            "  [bold]/quit[/bold] - Exit\n"
            "  [bold]/clear[/bold] - Clear history\n\n"
            "Once a project is selected, just type your notes!\n"
            "Messages are queued and processed sequentially.",
            title="Welcome",
            border_style="green",
        )
    )

    try:
        while True:
            # Show current project and queue status in prompt
            pending = queue_manager.get_pending_count()

            try:
                # Build styled prompt with colors
                if session.current_project:
                    project_part = (
                        f"<ansiblue><b>({session.current_project})</b></ansiblue>"
                    )
                else:
                    project_part = "<ansigray>(no project)</ansigray>"

                if pending > 0:
                    prompt_html = f"<ansigray>({pending} pending)</ansigray> {project_part} <ansiblue><b>></b></ansiblue> "
                else:
                    prompt_html = f"{project_part} <ansiblue><b>></b></ansiblue> "

                # Get input with proper line editing support
                with patch_stdout():
                    user_input = await prompt_session.prompt_async(
                        HTML(prompt_html),
                        multiline=False,
                    )
                user_input = user_input.strip()
            except (EOFError, KeyboardInterrupt):
                # End session on interrupt
                session_path = await session.end_session()
                if session_path:
                    console.print(f"\n[dim]Session saved to {session_path}[/dim]")
                console.print("[dim]Goodbye![/dim]")
                break

            if not user_input:
                continue

            # Handle special commands immediately (not queued)
            if user_input.lower() == "/quit":
                # End session and persist before exiting
                session_path = await session.end_session()
                if session_path:
                    console.print(f"[dim]Session saved to {session_path}[/dim]")
                console.print("[dim]Goodbye![/dim]")
                break

            elif user_input.lower() == "/status":
                _display_queue_status(console, queue_manager)
                continue

            elif user_input.lower() == "/clear":
                # End current session and start a new one
                session_path = await session.end_session()
                if session_path:
                    console.print(f"[dim]Session saved to {session_path}[/dim]")
                session.clear_history()
                displayed_messages.clear()
                console.print("[dim]Chat history cleared. New session started.[/dim]")
                continue

            elif user_input.lower() in ("/list", "/projects"):
                # List projects immediately
                projects = list_projects(data_dir)
                if projects:
                    console.print("\n[bold]Available projects:[/bold]")
                    for project_name in projects:
                        console.print(f"  - {project_name}")
                else:
                    console.print("[dim]No projects found.[/dim]")
                continue

            elif user_input.lower() == "/read" or user_input.lower().startswith(
                "/read "
            ):
                # Print project document immediately
                result = await session.send_message(user_input)
                from rich.markdown import Markdown

                console.print(Markdown(result["response"]))
                continue

            elif user_input.lower().startswith("/use "):
                # Handle project selection immediately (don't queue)
                result = await session.send_message(user_input)
                from rich.markdown import Markdown

                console.print(Markdown(result["response"]))
                continue

            elif user_input.startswith("/") and " " not in user_input:
                # Handle /<project> (no note) - just project switch, immediate
                result = await session.send_message(user_input)
                from rich.markdown import Markdown

                console.print(Markdown(result["response"]))
                continue

            # Queue the message for processing
            msg_id = await queue_manager.enqueue_message(user_input)
            console.print(f"[dim]Queued message #{msg_id}[/dim]")

    finally:
        # Graceful shutdown
        pending = queue_manager.get_pending_count()
        if pending > 0:
            console.print(
                f"\n[yellow]Waiting for {pending} pending messages...[/yellow]"
            )
        await queue_manager.shutdown(timeout=30.0)
        display_task.cancel()
        try:
            await display_task
        except asyncio.CancelledError:
            pass
